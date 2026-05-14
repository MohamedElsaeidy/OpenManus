import { ChatInput } from '@/components/features/chat/input';
import { ChatMessages } from '@/components/features/chat/messages';
import { ChatPreview } from '@/components/features/chat/preview';
import { usePreviewData } from '@/components/features/chat/preview/store';
import { useAutoScroll } from '@/hooks/use-auto-scroll';
import { useConversations } from '@/hooks/use-conversations';
import { aggregateMessages } from '@/libs/chat-messages';
import type { Message } from '@/libs/chat-messages/types';
import { getConversationHistory, sendConversationMessage } from '@/services/conversations';
import { createTask, getTask, getTaskEvents, sendTaskMessage, terminateTask } from '@/services/tasks';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { toast } from 'sonner';

export default function TaskDetailPage({ selectedModel }: { selectedModel?: string }) {
  const params = useParams();
  const routeTaskId = params.taskId as string | undefined;
  const routeConversationId = params.conversationId as string | undefined;
  const navigate = useNavigate();

  const { setData: setPreviewData } = usePreviewData();
  const { refreshConversations, setActiveConversationId } = useConversations();

  const [messages, setMessages] = useState<Message[]>([]);
  const [activeTaskId, setActiveTaskId] = useState<string | undefined>(routeTaskId);
  const [isThinking, setIsThinking] = useState(false);
  const [isTerminating, setIsTerminating] = useState(false);
  const [isTaskCompleted, setIsTaskCompleted] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();

  const eventSourceRef = useRef<EventSource | null>(null);
  const isTaskCompletedRef = useRef(false);
  const shouldAutoScrollRef = useRef(false);
  const seenEventKeysRef = useRef<Set<string>>(new Set());

  const { containerRef: messagesContainerRef, shouldAutoScroll, handleScroll, scrollToBottom } = useAutoScroll();

  useEffect(() => {
    shouldAutoScrollRef.current = shouldAutoScroll;
  }, [shouldAutoScroll]);

  const toMessage = useCallback((data: any): Message => {
    const key = data.id || `${data.task_id || ''}:${data.name}:${JSON.stringify(data.content || {})}`;
    return {
      ...data,
      index: key,
      createdAt: data.created_at ? new Date(data.created_at) : undefined,
      type: data.name as Message['type'],
      role: 'assistant' as const,
    };
  }, []);

  const applyPreviewFromMessage = useCallback((newMessage: Message) => {
    if (newMessage.type === 'agent:lifecycle:step:think:browser:browse:complete') {
      setPreviewData({
        type: 'browser',
        url: newMessage.content.url,
        title: newMessage.content.title,
        screenshot: newMessage.content.screenshot,
      });
    }
    if (newMessage.type === 'agent:lifecycle:step:act:tool:execute:start') {
      setPreviewData({ type: 'tool', toolId: newMessage.content.id });
    }
    if (newMessage.type === 'agent:lifecycle:step:act:tool:file:updated') {
      const path = String(newMessage.content.path || '').replace(/^\/app\/workspace\/?/, '');
      setPreviewData({ type: 'workspace', path });
    }
  }, [setPreviewData]);

  const appendLocalUserMessage = useCallback((prompt: string) => {
    const key = `local:user:${Date.now()}:${Math.random().toString(36).slice(2)}`;
    seenEventKeysRef.current.add(key);
    setMessages(prevMessages => [
      ...prevMessages,
      {
        index: key,
        role: 'user',
        content: prompt,
        createdAt: new Date(),
      },
    ]);
  }, []);

  const setupEventSource = useCallback((taskId?: string) => {
    if (!taskId) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const eventSource = getTaskEvents({ taskId });
    eventSourceRef.current = eventSource;

    eventSource.onmessage = event => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'progress') {
          const key = data.id || `${taskId}:${data.name}:${JSON.stringify(data.content || {})}`;
          if (seenEventKeysRef.current.has(key)) return;
          seenEventKeysRef.current.add(key);
          const newMessage = toMessage({ ...data, id: key, task_id: taskId });

          setMessages(prevMessages => {
            const request = String(newMessage.content?.request || '').trim();
            const baseMessages =
              newMessage.type === 'agent:lifecycle:start' && request
                ? prevMessages.filter(
                    message =>
                      !(
                        !message.type &&
                        message.role === 'user' &&
                        String(message.content || '').trim() === request
                      ),
                  )
                : prevMessages;
            const updatedMessages = [...baseMessages, newMessage];

            if (shouldAutoScrollRef.current) {
              applyPreviewFromMessage(newMessage);
            }

            return updatedMessages;
          });

          if (data.name === 'agent:lifecycle:complete') {
            setIsThinking(false);
            setIsTerminating(false);
            setIsTaskCompleted(true);
            isTaskCompletedRef.current = true;
            if (eventSourceRef.current) {
              eventSourceRef.current.close();
              eventSourceRef.current = null;
            }
          } else if (data.name === 'agent:lifecycle:terminated') {
            setIsThinking(false);
            setIsTerminating(false);
            setIsTaskCompleted(true);
            isTaskCompletedRef.current = true;
            if (eventSourceRef.current) {
              eventSourceRef.current.close();
              eventSourceRef.current = null;
            }
          } else if (data.name === 'agent:lifecycle:terminating') {
            setIsTerminating(true);
          } else {
            setIsThinking(true);
          }
        }
      } catch (error) {
        console.error('Error parsing event data:', error);
      }
    };

    eventSource.onerror = error => {
      console.error('EventSource error:', error);
      console.error('EventSource readyState:', eventSource.readyState);
      console.error('EventSource URL:', eventSource.url);

      if (isTaskCompletedRef.current) {
        console.log('Task completed, ignoring connection error');
        return;
      }

      let errorMessage = 'Connection error';

      if (eventSource.readyState === EventSource.CONNECTING) {
        console.error('Connection failed - trying to reconnect...');
        errorMessage = 'Connection failed, trying to reconnect...';
      } else if (eventSource.readyState === EventSource.CLOSED) {
        console.error('Connection closed');
        errorMessage = 'Connection closed';
      }

      toast.error(errorMessage);
      setIsThinking(false);

      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };

    eventSource.onopen = () => {
      console.log('EventSource connected');
      if (isTaskCompletedRef.current) {
        return;
      }
      setIsThinking(true);
    };
  }, [applyPreviewFromMessage, toMessage]);

  const loadConversation = useCallback(async (targetConversationId: string) => {
    const history = await getConversationHistory(targetConversationId);
    const nextMessages = history.events.map(event => {
      const key = event.id || `${event.task_id || ''}:${event.name}:${JSON.stringify(event.content || {})}`;
      seenEventKeysRef.current.add(key);
      return toMessage({ ...event, id: key });
    });
    const latestTask = [...history.tasks].reverse().find(Boolean);
    const activeTask = [...history.tasks].reverse().find(task => !['COMPLETED', 'FAILED', 'INTERRUPTED'].includes(String(task.status || '')));
    setMessages(nextMessages);
    setConversationId(targetConversationId);
    setActiveTaskId(activeTask?.task_id || latestTask?.task_id);
    setActiveConversationId(targetConversationId);
    setIsTaskCompleted(!activeTask && Boolean(latestTask));
    isTaskCompletedRef.current = !activeTask && Boolean(latestTask);
    setIsThinking(Boolean(activeTask));
    setIsTerminating(false);
    if (nextMessages.length) {
      const lastPreviewMessage = [...nextMessages].reverse().find(message =>
        message.type === 'agent:lifecycle:step:think:browser:browse:complete' ||
        message.type === 'agent:lifecycle:step:act:tool:execute:start' ||
        message.type === 'agent:lifecycle:step:act:tool:file:updated'
      );
      if (lastPreviewMessage) applyPreviewFromMessage(lastPreviewMessage);
    }
    if (activeTask?.task_id) {
      setupEventSource(activeTask.task_id);
    }
  }, [applyPreviewFromMessage, setActiveConversationId, setupEventSource, toMessage]);

  useEffect(() => {
    let cancelled = false;

    setMessages([]);
    setActiveTaskId(undefined);
    seenEventKeysRef.current = new Set();
    setPreviewData(null);
    setIsTaskCompleted(false);
    setConversationId(undefined);
    isTaskCompletedRef.current = false;
    setIsThinking(false);
    setIsTerminating(false);
    if (!routeTaskId && !routeConversationId) return;

    const initializeTask = async () => {
      if (routeConversationId) {
        await loadConversation(routeConversationId);
        return;
      }

      const task = await getTask({ taskId: routeTaskId! });
      if (cancelled) return;

      const status = task.data?.status;
      if (task.data?.conversation_id) {
        navigate(`/conversations/${task.data.conversation_id}`, { replace: true });
        return;
      }

      setActiveTaskId(routeTaskId);
      if (status === 'COMPLETED' || status === 'FAILED' || status === 'INTERRUPTED') {
        setIsTaskCompleted(true);
        isTaskCompletedRef.current = true;
      } else {
        setupEventSource(routeTaskId);
      }
    };

    initializeTask();

    return () => {
      cancelled = true;
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, [loadConversation, navigate, routeConversationId, routeTaskId, setPreviewData, setupEventSource]);

  useEffect(() => {
    if (shouldAutoScroll) {
      requestAnimationFrame(scrollToBottom);
    }
  }, [messages, shouldAutoScroll, scrollToBottom]);

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  const handleSubmit = async (value: { prompt: string }) => {
    const prompt = value.prompt.trim();
    if (!prompt) return;
    appendLocalUserMessage(prompt);
    setIsThinking(true);
    setIsTaskCompleted(false);
    isTaskCompletedRef.current = false;
    try {
      if (isThinking && !isTaskCompleted && activeTaskId) {
        const res = await sendTaskMessage({ taskId: activeTaskId, message: prompt });
        if (res.error) {
          toast.error(res.error);
          setIsThinking(false);
        }
        return;
      }

      if (conversationId) {
        const sent = await sendConversationMessage(conversationId, prompt, selectedModel);
        await refreshConversations();
        if (sent.task_id) {
          setActiveTaskId(sent.task_id);
          setupEventSource(sent.task_id);
        }
        if (sent.created_task) {
          setIsThinking(true);
        }
        return;
      }

      const res = await createTask({
        prompt,
        conversationId,
        model: selectedModel,
      });
      if (res.status === 409 && activeTaskId) {
        const inboxRes = await sendTaskMessage({ taskId: activeTaskId, message: prompt });
        if (inboxRes.error) {
          toast.error(inboxRes.error);
          setIsThinking(false);
          return;
        }
        toast.success('Sent to running task');
        return;
      }
      if (res.error || !res.data?.task_id) {
        console.error('Error restarting task:', res.error);
        toast.error(res.error || 'Could not start follow-up task');
        setIsThinking(false);
        return;
      }
      await refreshConversations();
      setIsThinking(true);
      setActiveTaskId(res.data.task_id);
      if (res.data.conversation_id && res.data.conversation_id !== conversationId) {
        navigate(`/conversations/${res.data.conversation_id}`, { replace: true });
      } else if (conversationId) {
        await loadConversation(conversationId);
      }
    } catch (error) {
      console.error('Error submitting task:', error);
      toast.error(error instanceof Error ? error.message : 'Could not send message');
      setIsThinking(false);
    }
  };

  const handleTerminate = async () => {
    try {
      if (!activeTaskId) return;
      const res = await terminateTask({ taskId: activeTaskId });
      if (res.error) {
        console.error('Error terminating task:', res.error);
      }
      if (conversationId) await loadConversation(conversationId);
    } catch (error) {
      console.error('Error terminating task:', error);
    }
  };

  return (
    <div className="flex h-full w-full flex-row justify-between">
      <div className="flex min-w-0 flex-1 flex-col border-r bg-background">
        <div className="flex h-12 items-center gap-2 border-b px-5">
          <div className="font-semibold">OpenManus v2.0</div>
          {isThinking && <div className="text-xs text-muted-foreground">Working</div>}
        </div>
        <div className="relative flex h-full flex-col">
          <div
            ref={messagesContainerRef}
            className="h-full space-y-4 overflow-y-auto p-4 pb-60"
            style={{
              scrollBehavior: 'smooth',
              overscrollBehavior: 'contain',
            }}
            onScroll={handleScroll}
          >
            <ChatMessages messages={aggregateMessages(messages)} />
          </div>
          <ChatInput
            status={isTerminating ? 'terminating' : isThinking ? 'thinking' : isTaskCompleted ? 'completed' : 'idle'}
            onSubmit={handleSubmit}
            onTerminate={handleTerminate}
            taskId={activeTaskId}
          />
        </div>
      </div>
      <div className="hidden w-[44vw] min-w-[420px] max-w-[760px] items-center justify-center bg-muted/30 p-2 lg:flex">
        <ChatPreview taskId={activeTaskId || conversationId || 'workspace'} conversationId={conversationId} messages={messages} />
      </div>
    </div>
  );
}
