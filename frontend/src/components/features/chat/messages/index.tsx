import { Markdown } from '@/components/block/markdown';
import type { AggregatedMessage, Message } from '@/libs/chat-messages/types';
import { cn, formatNumber } from '@/libs/utils';
import {
  AlertTriangle,
  Check,
  ChevronDown,
  CircleStop,
  Coins,
  FileDiff,
  LoaderCircle,
  Wrench,
} from 'lucide-react';
import { useMemo, useState } from 'react';
import { ElapsedTime } from '../elapsed-time';
import { ToolMessageContent } from './tools';

interface ChatMessageProps {
  messages: AggregatedMessage[];
  activeTaskId?: string;
  isTaskRunning?: boolean;
}

type LifecycleStep = AggregatedMessage & { type: 'agent:lifecycle:step' };

type ToolCallSummary = {
  id: string;
  function: {
    name: string;
    arguments?: unknown;
  };
};

const humanizeToolName = (name: string) => {
  const labels: Record<string, string> = {
    apply_patch_editor: 'Editing files',
    ask_human: 'Waiting for input',
    bash: 'Running a command',
    browser_use: 'Using the browser',
    codebase_overview: 'Mapping the codebase',
    glob: 'Finding files',
    grep: 'Searching the codebase',
    line_edit: 'Editing a file',
    memory_recall: 'Checking memory',
    memory_save: 'Saving context',
    planning: 'Updating the plan',
    python_execute: 'Running Python',
    read_files: 'Reading files',
    skill_playbook: 'Loading guidance',
    wait_for_user_input: 'Waiting for input',
    web_search: 'Searching the web',
  };
  if (labels[name]) return labels[name];
  return name
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());
};

const getThinkMessage = (step: LifecycleStep) =>
  step.messages.find(message => message.type === 'agent:lifecycle:step:think') as
    | (AggregatedMessage & { type: 'agent:lifecycle:step:think' })
    | undefined;

const getToolSelection = (step: LifecycleStep) =>
  getThinkMessage(step)?.messages.find(
    (message): message is Message =>
      'type' in message && message.type === 'agent:lifecycle:step:think:tool:selected',
  );

const getToolCalls = (step: LifecycleStep) => {
  const calls = getToolSelection(step)?.content?.tool_calls;
  return (Array.isArray(calls) ? calls : []) as ToolCallSummary[];
};

const getVisibleToolCalls = (step: LifecycleStep) =>
  getToolCalls(step).filter(call => call.function?.name !== 'terminate');

const UserMessage = ({ message }: { message: Message<{ request: string }> }) => (
  <div className="ml-auto max-w-[min(82%,42rem)] rounded-2xl bg-muted px-4 py-3">
    <Markdown className="chat">{message.content.request}</Markdown>
  </div>
);

interface CompletionMessageProps {
  message: Message<{
    results?: string[];
    message?: string;
    status?: string;
    total_input_tokens?: number;
    total_completion_tokens?: number;
    reason?: string;
    workspace?: {
      pdfs?: string[];
      tex?: string[];
      logs?: string[];
      warning?: string | null;
    };
    plan_progress?: {
      completed: number;
      total: number;
      remaining: string[];
    } | null;
    change_summary?: {
      files: number;
      added: number;
      deleted: number;
      paths: string[];
    } | null;
  }>;
  startedAt?: Date;
  finishedAt?: Date;
}

const CompletionMessage = ({ message, startedAt, finishedAt }: CompletionMessageProps) => {
  const inputTokens = Number(message.content.total_input_tokens || 0);
  const completionTokens = Number(message.content.total_completion_tokens || 0);
  const showTokenCount = inputTokens > 0 || completionTokens > 0;
  const workspace = message.content.workspace;
  const pdfCount = workspace?.pdfs?.length || 0;
  const logCount = workspace?.logs?.length || 0;
  const planProgress = message.content.plan_progress || null;
  const changeSummary = message.content.change_summary || null;

  return (
    <div className="space-y-2 pt-1">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <Check className="h-3.5 w-3.5 text-emerald-600" />
          Completed
        </span>
        {startedAt && finishedAt && (
          <ElapsedTime startedAt={startedAt} finishedAt={finishedAt} running={false} />
        )}
        {showTokenCount && (
          <span className="inline-flex items-center gap-1.5 font-mono">
            <Coins className="h-3.5 w-3.5" />
            {formatNumber(inputTokens + completionTokens, { autoUnit: true })} tokens
          </span>
        )}
        {changeSummary && changeSummary.files > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <FileDiff className="h-3.5 w-3.5" />
            {changeSummary.files} file{changeSummary.files === 1 ? '' : 's'} changed
            <span className="font-mono text-emerald-600">+{changeSummary.added}</span>
            <span className="font-mono text-rose-600">-{changeSummary.deleted}</span>
          </span>
        )}
      </div>

      {workspace?.warning && (
        <div className="border-l-2 border-amber-500 px-3 py-1 text-sm text-amber-800 dark:text-amber-300">
          {workspace.warning}
          {logCount > 0 && <span className="ml-1">Found {logCount} log file{logCount === 1 ? '' : 's'}.</span>}
        </div>
      )}
      {planProgress && planProgress.total > 0 && planProgress.remaining.length > 0 && (
        <div className="border-l-2 border-amber-500 px-3 py-1 text-sm">
          Completed {planProgress.completed} of {planProgress.total} planned items. Remaining:{' '}
          {planProgress.remaining.slice(0, 4).join(' · ')}
          {planProgress.remaining.length > 4 ? ` · +${planProgress.remaining.length - 4} more` : ''}
        </div>
      )}
      {pdfCount > 0 && (
        <div className="text-xs text-muted-foreground">
          Output: {workspace?.pdfs?.slice(0, 3).join(', ')}
          {pdfCount > 3 ? ` and ${pdfCount - 3} more` : ''}
        </div>
      )}
    </div>
  );
};

interface TerminatedMessageProps {
  message: Message<{
    total_input_tokens?: number;
    total_completion_tokens?: number;
    status?: string;
    reason?: string;
    message?: string;
    detail?: string;
    plan_progress?: {
      completed: number;
      total: number;
      remaining: string[];
    } | null;
  }>;
  startedAt?: Date;
  finishedAt?: Date;
}

const TerminatedMessage = ({ message, startedAt, finishedAt }: TerminatedMessageProps) => {
  const planProgress = message.content.plan_progress || null;
  const reason = message.content.reason || message.content.detail;
  return (
    <div className="space-y-2 pt-1">
      <div className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
        <CircleStop className="h-3.5 w-3.5" />
        Stopped before completion
      </div>
      {startedAt && finishedAt && (
        <ElapsedTime
          startedAt={startedAt}
          finishedAt={finishedAt}
          running={false}
          finishedLabel="Stopped after"
        />
      )}
      {reason && <div className="border-l-2 border-amber-500 px-3 py-1 text-sm">{reason}</div>}
      {planProgress && planProgress.total > 0 && (
        <div className="text-xs text-muted-foreground">
          Plan progress: {planProgress.completed}/{planProgress.total}
          {planProgress.remaining.length > 0 ? ` · ${planProgress.remaining.slice(0, 4).join(' · ')}` : ''}
        </div>
      )}
    </div>
  );
};

const StepMessage = ({ message }: { message: LifecycleStep }) => {
  const selection = getToolSelection(message);
  const toolCalls = getVisibleToolCalls(message);
  const stepStart = message.messages.find(item => item.type === 'agent:lifecycle:step:start') as Message | undefined;
  const stepComplete = message.messages.find(item => item.type === 'agent:lifecycle:step:complete') as Message | undefined;
  const stepError = message.messages.find(item => item.type === 'agent:lifecycle:step:error') as Message | undefined;
  const stepCount = Number(stepStart?.content?.step || message.step || 0);
  const isRunning = !stepComplete && !stepError;
  const headline = toolCalls.length
    ? toolCalls.length === 1
      ? humanizeToolName(toolCalls[0].function.name)
      : `${toolCalls.length} actions`
    : isRunning
      ? 'Analyzing the request'
      : 'Reasoning complete';
  const modelNote = String(selection?.content?.content || '').trim();

  return (
    <details className="group border-b border-border/70 last:border-b-0" open={isRunning}>
      <summary className="flex cursor-pointer list-none items-center gap-3 py-2.5 text-sm">
        <span className="flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground">
          {stepError ? (
            <AlertTriangle className="h-4 w-4 text-rose-500" />
          ) : isRunning ? (
            <LoaderCircle className="h-4 w-4 animate-spin" />
          ) : (
            <Check className="h-4 w-4" />
          )}
        </span>
        <span className="min-w-0 flex-1 truncate">
          <span className="font-medium">{headline}</span>
          {stepCount > 0 && <span className="ml-2 text-xs text-muted-foreground">Step {stepCount}</span>}
        </span>
        <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      <div className="space-y-3 pb-3 pl-8">
        {modelNote && toolCalls.length > 0 && (
          <div className="text-sm leading-6 text-muted-foreground">{modelNote}</div>
        )}
        <ToolMessageContent message={message} />
        {stepError?.content?.message && (
          <div className="border-l-2 border-rose-500 px-3 py-1 text-sm text-rose-700 dark:text-rose-300">
            {stepError.content.message}
          </div>
        )}
      </div>
    </details>
  );
};

const LifecycleMessage = ({
  message,
  activeTaskId,
  isTaskRunning,
}: {
  message: AggregatedMessage;
  activeTaskId?: string;
  isTaskRunning?: boolean;
}) => {
  if (!('messages' in message)) return null;
  const startMessage = message.messages.find(item => item.type === 'agent:lifecycle:start') as
    | Message<{ request: string; task_id?: string }>
    | undefined;
  const completeMessage = message.messages.find(item => item.type === 'agent:lifecycle:complete') as
    | CompletionMessageProps['message']
    | undefined;
  const terminatedMessage = message.messages.find(item => item.type === 'agent:lifecycle:terminated') as
    | TerminatedMessageProps['message']
    | undefined;
  const structuredFinish = [...message.messages]
    .reverse()
    .find(
      item =>
        item.type === 'agent:lifecycle:state:change' &&
        typeof item.content?.final_response === 'string' &&
        item.content.final_response.trim(),
    ) as Message | undefined;
  const stepMessages = message.messages.filter(
    (item): item is LifecycleStep => item.type === 'agent:lifecycle:step',
  );
  const latestTokenCount = getLatestTokenCount(stepMessages);
  const planProgress = getLatestPlanProgress(stepMessages);
  if (completeMessage && latestTokenCount) {
    completeMessage.content.total_input_tokens = latestTokenCount.total_input;
    completeMessage.content.total_completion_tokens = latestTokenCount.total_completion;
  }
  if (completeMessage) {
    completeMessage.content.plan_progress = planProgress;
    completeMessage.content.change_summary = getChangeSummary(stepMessages);
  }
  if (terminatedMessage && latestTokenCount) {
    terminatedMessage.content.total_input_tokens = latestTokenCount.total_input;
    terminatedMessage.content.total_completion_tokens = latestTokenCount.total_completion;
  }
  if (terminatedMessage) terminatedMessage.content.plan_progress = planProgress;

  const lifecycleTaskId = String(
    startMessage?.task_id || message.task_id || startMessage?.content?.task_id || message.content?.task_id || '',
  );
  const isRunning = Boolean(
    !completeMessage &&
      !terminatedMessage &&
      isTaskRunning &&
      activeTaskId &&
      lifecycleTaskId === activeTaskId,
  );
  const startedAt = startMessage?.createdAt || message.createdAt;
  const finishedAt = completeMessage?.createdAt || terminatedMessage?.createdAt;
  const directResponse = getDirectResponse(stepMessages);
  const completionText = String(completeMessage?.content?.message || '').trim();
  const terminatedText = String(terminatedMessage?.content?.message || '').trim();
  const structuredFinalText = String(structuredFinish?.content?.final_response || '').trim();
  const finalText =
    structuredFinalText ||
    (completionText && !/^Task (completed|already completed)\.?$/i.test(completionText) ? completionText : '') ||
    terminatedText ||
    directResponse;
  const traceSteps = stepMessages.filter((step, index) => {
    const hasTools = getVisibleToolCalls(step).length > 0;
    const hasError = step.messages.some(item => item.type === 'agent:lifecycle:step:error');
    return hasTools || hasError || (isRunning && index === stepMessages.length - 1 && !directResponse);
  });
  const toolCount = traceSteps.reduce((count, step) => count + getVisibleToolCalls(step).length, 0);
  const latestTools = traceSteps.length ? getVisibleToolCalls(traceSteps[traceSteps.length - 1]) : [];
  const currentActivity = latestTools.length ? humanizeToolName(latestTools[latestTools.length - 1].function.name) : 'Analyzing the request';
  const showActivity = traceSteps.length > 0;

  return (
    <div className="container mx-auto max-w-4xl space-y-3">
      {startMessage && <UserMessage message={startMessage} />}
      <div className="flex items-start gap-3 pt-1">
        <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-background text-xs font-semibold">
          M
        </div>
        <div className="min-w-0 flex-1 space-y-3">
          <div className="flex h-7 items-center gap-2">
            <span className="text-sm font-semibold">Manus</span>
            {isRunning && startedAt && <ElapsedTime startedAt={startedAt} running />}
          </div>

          {showActivity && (
            <details className="group rounded-md border bg-muted/20" open={isRunning}>
              <summary className="flex cursor-pointer list-none items-center gap-3 px-3 py-2.5 text-sm">
                <Wrench className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span className="min-w-0 flex-1 truncate">
                  <span className="font-medium">{isRunning ? currentActivity : 'Activity'}</span>
                  <span className="ml-2 text-xs text-muted-foreground">
                    {toolCount > 0
                      ? `${toolCount} action${toolCount === 1 ? '' : 's'} across ${traceSteps.length} step${traceSteps.length === 1 ? '' : 's'}`
                      : 'Preparing'}
                  </span>
                </span>
                <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" />
              </summary>
              <div className="border-t px-3">
                {traceSteps.map((step, index) => (
                  <StepMessage key={String(step.index || index)} message={step} />
                ))}
              </div>
            </details>
          )}

          {finalText ? (
            <Markdown className="chat">{finalText}</Markdown>
          ) : isRunning && !showActivity ? (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              Analyzing the request
            </div>
          ) : null}

          {completeMessage && (
            <CompletionMessage message={completeMessage} startedAt={startedAt} finishedAt={finishedAt} />
          )}
          {terminatedMessage && (
            <TerminatedMessage message={terminatedMessage} startedAt={startedAt} finishedAt={finishedAt} />
          )}
        </div>
      </div>
    </div>
  );
};

const getLatestTokenCount = (steps: LifecycleStep[]) => {
  for (const step of [...steps].reverse()) {
    const tokenMessages =
      getThinkMessage(step)?.messages.filter(
        (message): message is Message =>
          'type' in message && message.type === 'agent:lifecycle:step:think:token:count',
      ) || [];
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

const getChangeSummary = (steps: LifecycleStep[]) => {
  const map = new Map<string, { added: number; deleted: number }>();
  for (const step of steps) {
    const actMessage = step.messages.find(item => item.type === 'agent:lifecycle:step:act') as
      | (AggregatedMessage & { type: 'agent:lifecycle:step:act' })
      | undefined;
    const toolMessages = (actMessage?.messages.filter(item => item.type === 'agent:lifecycle:step:act:tool') || []) as (
      | AggregatedMessage
    )[];
    for (const tool of toolMessages) {
      if (!('messages' in tool)) continue;
      const updates = tool.messages.filter(item => item.type === 'agent:lifecycle:step:act:tool:file:updated');
      for (const update of updates) {
        const path = String(update.content?.path || '').trim();
        if (!path) continue;
        const previous = map.get(path) || { added: 0, deleted: 0 };
        previous.added += Number(update.content?.added_lines || 0);
        previous.deleted += Number(update.content?.deleted_lines || 0);
        map.set(path, previous);
      }
    }
  }
  const paths = Array.from(map.keys());
  const totals = Array.from(map.values()).reduce(
    (accumulator, current) => ({
      added: accumulator.added + current.added,
      deleted: accumulator.deleted + current.deleted,
    }),
    { added: 0, deleted: 0 },
  );
  return { files: paths.length, added: totals.added, deleted: totals.deleted, paths };
};

const getDirectResponse = (steps: LifecycleStep[]) => {
  for (const step of [...steps].reverse()) {
    const selection = getToolSelection(step);
    const content = String(selection?.content?.content || '').trim();
    if (content && getToolCalls(step).length === 0) return content;
  }
  return '';
};

const getLatestPlanProgress = (steps: LifecycleStep[]) => {
  const progressPattern = /Progress:\s*(\d+)\s*\/\s*(\d+)\s*steps completed/i;
  const stepLinePattern = /^\s*\d+\.\s*\[(.| )\]\s*(.+)$/gm;
  for (const step of [...steps].reverse()) {
    const actMessage = step.messages.find(item => item.type === 'agent:lifecycle:step:act') as
      | (AggregatedMessage & { type: 'agent:lifecycle:step:act' })
      | undefined;
    for (const tool of (actMessage?.messages || []) as AggregatedMessage[]) {
      if (!('messages' in tool)) continue;
      const done = tool.messages.find(
        item => item.type === 'agent:lifecycle:step:act:tool:execute:complete',
      ) as Message | undefined;
      const resultText = String(done?.content?.result || '');
      const progress = progressPattern.exec(resultText);
      if (!progress) continue;
      const remaining: string[] = [];
      for (const match of resultText.matchAll(stepLinePattern)) {
        const mark = (match[1] || '').trim();
        const title = (match[2] || '').trim();
        if (mark !== '✓' && title) remaining.push(title);
      }
      return { completed: Number(progress[1] || 0), total: Number(progress[2] || 0), remaining };
    }
  }
  return null;
};

const ChatMessage = ({
  message,
  activeTaskId,
  isTaskRunning,
}: {
  message: AggregatedMessage;
  activeTaskId?: string;
  isTaskRunning?: boolean;
}) => {
  if (!message.type?.startsWith('agent:lifecycle')) {
    return (
      <div className={cn('container mx-auto flex max-w-4xl', message.role === 'user' ? 'justify-end' : 'justify-start')}>
        <div className={cn('max-w-[min(82%,42rem)]', message.role === 'user' && 'rounded-2xl bg-muted px-4 py-3')}>
          <Markdown className="chat">{String(message.content || '')}</Markdown>
        </div>
      </div>
    );
  }

  return (
    <LifecycleMessage
      message={message}
      activeTaskId={activeTaskId}
      isTaskRunning={isTaskRunning}
    />
  );
};

export const ChatMessages = ({
  messages = [],
  activeTaskId,
  isTaskRunning,
}: ChatMessageProps) => {
  const [showAll, setShowAll] = useState(false);
  const cappedMessages = useMemo(() => {
    const hardCap = 160;
    if (showAll || messages.length <= hardCap) return messages;
    return messages.slice(messages.length - hardCap);
  }, [messages, showAll]);
  const hiddenCount = Math.max(0, messages.length - cappedMessages.length);

  return (
    <div className="space-y-6">
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
          <ChatMessage
            message={message}
            activeTaskId={activeTaskId}
            isTaskRunning={isTaskRunning}
          />
        </div>
      ))}
    </div>
  );
};
