import { Markdown } from '@/components/block/markdown';
import { Badge } from '@/components/ui/badge';
import type { AggregatedMessage, Message } from '@/libs/chat-messages/types';
import { cn, formatNumber } from '@/libs/utils';
import '@/styles/animations.css';
import { ChevronDown, CircleCheck, CircleStop, LoaderIcon } from 'lucide-react';
import { useMemo, useState } from 'react';
import { StepBadge } from './step';
import { ToolMessageContent } from './tools';

interface ChatMessageProps {
  messages: AggregatedMessage[];
}

const UserMessage = ({ message }: { message: Message<{ request: string }> }) => (
  <div className="ml-auto max-w-[78%] rounded-2xl bg-muted px-4 py-3">
    <Markdown className="chat">{message.content.request}</Markdown>
  </div>
);

interface CompletionMessageProps {
  message: Message<{
    results?: string[];
    message?: string;
    total_input_tokens?: number;
    total_completion_tokens?: number;
    reason?: string;
    workspace?: {
      pdfs?: string[];
      tex?: string[];
      logs?: string[];
      warning?: string | null;
    };
  }>;
}

const CompletionMessage = ({ message }: CompletionMessageProps) => {
  const showTokenCount = message.content.total_input_tokens || message.content.total_completion_tokens;
  const workspace = message.content.workspace;
  const pdfCount = workspace?.pdfs?.length || 0;
  const logCount = workspace?.logs?.length || 0;
  return (
    <div className="inline-flex max-w-full flex-col gap-2">
      <Badge className="w-fit font-mono" variant="outline">
        <CircleCheck className="h-3.5 w-3.5 text-emerald-500" />
        Completed{' '}
        {showTokenCount && (
          <>
            (
            <span>
              {formatNumber(message.content.total_input_tokens || 0, { autoUnit: true })} input;{' '}
              {formatNumber(message.content.total_completion_tokens || 0, { autoUnit: true })} completion
            </span>
            )
          </>
        )}
      </Badge>
      {message.content.message && message.content.message !== 'Task completed' && <Markdown className="chat">{message.content.message}</Markdown>}
      {message.content.reason && <div className="text-muted-foreground text-xs">Reason: {message.content.reason}</div>}
      {workspace?.warning && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {workspace.warning}
          {logCount > 0 && <span className="ml-1">Found {logCount} log file{logCount === 1 ? '' : 's'}.</span>}
        </div>
      )}
      {pdfCount > 0 && (
        <div className="text-muted-foreground text-xs">
          PDF output: {workspace?.pdfs?.slice(0, 3).join(', ')}
          {pdfCount > 3 ? ` and ${pdfCount - 3} more` : ''}
        </div>
      )}
    </div>
  );
};

interface TerminatedMessageProps {
  message: Message<{ total_input_tokens?: number; total_completion_tokens?: number; reason?: string; message?: string; detail?: string }>;
}

const TerminatedMessage = ({ message }: TerminatedMessageProps) => {
  const showTokenCount = message.content.total_input_tokens || message.content.total_completion_tokens;
  return (
    <div className="inline-flex max-w-full flex-col gap-2">
      <Badge className="font-mono" variant="outline">
        <CircleStop className="h-3.5 w-3.5 text-amber-500" />
        Terminated{' '}
        {showTokenCount && (
          <>
            (
            <span>
              {formatNumber(message.content.total_input_tokens || 0, { autoUnit: true })} input;{' '}
              {formatNumber(message.content.total_completion_tokens || 0, { autoUnit: true })} completion
            </span>
            )
          </>
        )}
      </Badge>
      {(message.content.reason || message.content.detail || message.content.message) && (
        <div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          {message.content.reason || message.content.detail || message.content.message}
        </div>
      )}
    </div>
  );
};

const StepMessage = ({ message }: { message: AggregatedMessage & { type: 'agent:lifecycle:step' } }) => {
  if (!('messages' in message)) return null;

  const thinkMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:step:think') as
    | (AggregatedMessage & { type: 'agent:lifecycle:step:think' })
    | undefined;

  const toolSelectedMessage = thinkMessage?.messages.find(
    (msg): msg is Message => 'type' in msg && msg.type === 'agent:lifecycle:step:think:tool:selected',
  ) as (AggregatedMessage & { type: 'agent:lifecycle:step:think:tool:selected' }) | undefined;

  const stepStartMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:step:start') as Message | undefined;
  const stepCompleteMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:step:complete') as Message | undefined;
  const stepErrorMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:step:error') as Message | undefined;
  const stepCount = stepStartMessage?.content?.step || message.step || 0;
  const isRunning = !stepCompleteMessage && !stepErrorMessage;

  return (
    <details className="group rounded-md border bg-background/80" open={isRunning}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm">
        <div className="flex min-w-0 items-center gap-2">
          {isRunning ? <LoaderIcon className="h-3.5 w-3.5 animate-spin text-primary" /> : <CircleCheck className="h-3.5 w-3.5 text-muted-foreground" />}
          <span className="shrink-0 font-medium">Step {stepCount || ''}</span>
          <span className="text-muted-foreground truncate">{toolSelectedMessage?.content.content || toolSelectedMessage?.content.tool || 'Thinking'}</span>
        </div>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-3 border-t px-3 py-3">
        <StepBadge message={message} />
        {toolSelectedMessage?.content.content && <Markdown className="chat text-sm">{toolSelectedMessage.content.content}</Markdown>}
        <ToolMessageContent message={message} />
        {stepErrorMessage?.content?.message && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-sm">{stepErrorMessage.content.message}</div>}
      </div>
    </details>
  );
};

const LifecycleMessage = ({ message }: { message: AggregatedMessage }) => {
  if (!('messages' in message)) return null;
  const startMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:start') as Message<{ request: string }> | undefined;
  const completeMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:complete') as CompletionMessageProps['message'] | undefined;
  const terminatedMessage = message.messages.find(msg => msg.type === 'agent:lifecycle:terminated') as TerminatedMessageProps['message'] | undefined;
  const stepMessages = message.messages.filter(
    (msg): msg is AggregatedMessage & { type: 'agent:lifecycle:step' } => msg.type === 'agent:lifecycle:step',
  );
  const latestTokenCount = getLatestTokenCount(stepMessages);
  if (completeMessage && latestTokenCount) {
    completeMessage.content.total_input_tokens = latestTokenCount.total_input;
    completeMessage.content.total_completion_tokens = latestTokenCount.total_completion;
  }
  if (terminatedMessage && latestTokenCount) {
    terminatedMessage.content.total_input_tokens = latestTokenCount.total_input;
    terminatedMessage.content.total_completion_tokens = latestTokenCount.total_completion;
  }
  const isRunning = !completeMessage && !terminatedMessage;
  const assistantText = getAssistantText(stepMessages);

  return (
    <div className="container mx-auto max-w-4xl space-y-3">
      {startMessage && <UserMessage message={startMessage} />}
      <div className="space-y-3">
        <div className="flex items-start gap-3">
          <div className="mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-background text-xs font-semibold">M</div>
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex items-center gap-2">
              <div className="font-semibold">Manus</div>
              <Badge variant={isRunning ? 'default' : 'outline'} className="font-mono">
                {isRunning ? 'Working' : terminatedMessage ? 'Stopped' : 'Done'}
              </Badge>
            </div>
            {assistantText ? (
              <Markdown className="chat">{assistantText}</Markdown>
            ) : (
              <div className={cn('text-sm text-muted-foreground', isRunning && 'flex items-center gap-2')}>
                {isRunning && <LoaderIcon className="h-3.5 w-3.5 animate-spin" />}
                {isRunning ? 'Working on it...' : 'Run finished.'}
              </div>
            )}
            <details className="group rounded-lg border bg-muted/20" open={isRunning}>
              <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-3 py-2 text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium">Thinking and actions</span>
                  <span className="text-muted-foreground">{stepMessages.length} step{stepMessages.length === 1 ? '' : 's'}</span>
                </div>
                <ChevronDown className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="space-y-2 border-t p-2">
                {stepMessages.length ? stepMessages.map((step, index) => <StepMessage key={String(step.index || index)} message={step} />) : <div className="p-2 text-sm text-muted-foreground">No trace yet.</div>}
              </div>
            </details>
            {completeMessage && <CompletionMessage message={completeMessage} />}
            {terminatedMessage && <TerminatedMessage message={terminatedMessage} />}
          </div>
        </div>
      </div>
    </div>
  );
};

const getLatestTokenCount = (steps: (AggregatedMessage & { type: 'agent:lifecycle:step' })[]) => {
  for (const step of [...steps].reverse()) {
    const thinkMessage = step.messages.find(msg => msg.type === 'agent:lifecycle:step:think') as
      | (AggregatedMessage & { type: 'agent:lifecycle:step:think' })
      | undefined;
    const tokenMessages = thinkMessage?.messages.filter((msg): msg is Message => 'type' in msg && msg.type === 'agent:lifecycle:step:think:token:count') || [];
    const token = tokenMessages[tokenMessages.length - 1];
    if (token?.content) {
      return {
        total_input: Number(token.content.total_input || 0),
        total_completion: Number(token.content.total_completion || 0),
      };
    }
  }
  return null;
};

const getAssistantText = (steps: (AggregatedMessage & { type: 'agent:lifecycle:step' })[]) => {
  for (const step of [...steps].reverse()) {
    const thinkMessage = step.messages.find(msg => msg.type === 'agent:lifecycle:step:think') as
      | (AggregatedMessage & { type: 'agent:lifecycle:step:think' })
      | undefined;
    const selected = thinkMessage?.messages.find(
      (msg): msg is Message => 'type' in msg && msg.type === 'agent:lifecycle:step:think:tool:selected',
    );
    const content = String(selected?.content?.content || '').trim();
    if (content) return content;
  }
  return '';
};

const ChatMessage = ({ message }: { message: AggregatedMessage }) => {
  if (!message.type?.startsWith('agent:lifecycle')) {
    return (
      <div className={cn('container mx-auto flex max-w-4xl', message.role === 'user' ? 'justify-end' : 'justify-start')}>
        <div className={cn('max-w-[78%]', message.role === 'user' && 'rounded-2xl bg-muted px-4 py-3')}>
          <Markdown>{message.content}</Markdown>
        </div>
      </div>
    );
  }

  return <LifecycleMessage message={message} />;
};

export const ChatMessages = ({ messages = [] }: ChatMessageProps) => {
  const [showAll, setShowAll] = useState(false);
  const cappedMessages = useMemo(() => {
    const HARD_CAP = 160;
    if (showAll || messages.length <= HARD_CAP) return messages;
    return messages.slice(messages.length - HARD_CAP);
  }, [messages, showAll]);
  const hiddenCount = Math.max(0, messages.length - cappedMessages.length);

  return (
    <div className="space-y-4">
      {hiddenCount > 0 && (
        <div className="container mx-auto max-w-4xl">
          <button
            className="rounded-md border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
            onClick={() => setShowAll(true)}
          >
            Show {hiddenCount} older messages
          </button>
        </div>
      )}
      {cappedMessages.map((message, index) => (
        <div key={message.index || index} className="first:pt-0">
          <ChatMessage message={message} />
        </div>
      ))}
    </div>
  );
};
