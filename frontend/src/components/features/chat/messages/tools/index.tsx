import type { AggregatedMessage, Message } from '@/libs/chat-messages/types';
import { getImageUrl } from '@/libs/image';
import useAgentTools from '@/hooks/use-tools';
import {
  Check,
  FileSearch,
  Globe2,
  Image as ImageIcon,
  LoaderCircle,
  PencilLine,
  Search,
  SquareTerminal,
  TriangleAlert,
  Wrench,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { usePreviewData } from '../../preview/store';

type ToolCall = {
  id: string;
  type: 'function';
  function: {
    name: string;
    arguments: Record<string, unknown> | string;
  };
};

const TOOL_ICONS: Array<[RegExp, LucideIcon]> = [
  [/bash|python|terminal|execute/i, SquareTerminal],
  [/read|glob|file|codebase/i, FileSearch],
  [/grep|search/i, Search],
  [/edit|patch|write/i, PencilLine],
  [/browser|web/i, Globe2],
];

const getToolIcon = (name: string) =>
  TOOL_ICONS.find(([pattern]) => pattern.test(name))?.[1] || Wrench;

const fallbackToolLabel = (name: string) =>
  name
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, character => character.toUpperCase());

const getArgumentHint = (value: ToolCall['function']['arguments']) => {
  let argumentsValue: Record<string, unknown> = {};
  try {
    argumentsValue = typeof value === 'string' ? JSON.parse(value) : value || {};
  } catch {
    return '';
  }
  for (const key of ['path', 'file_path', 'query', 'pattern', 'command', 'url']) {
    const candidate = argumentsValue[key];
    if (typeof candidate === 'string' && candidate.trim()) {
      const compact = candidate.trim().replace(/\s+/g, ' ');
      return compact.length > 96 ? `${compact.slice(0, 96)}…` : compact;
    }
  }
  return '';
};

export const ToolMessageContent = ({
  message,
}: {
  message: AggregatedMessage & { type: 'agent:lifecycle:step' };
}) => {
  const { setData } = usePreviewData();
  const { getToolByPrefix } = useAgentTools();
  if (message.type !== 'agent:lifecycle:step') return null;

  const thinkMessage = message.messages.find(item => item.type === 'agent:lifecycle:step:think') as
    | (AggregatedMessage & { type: 'agent:lifecycle:step:think' })
    | undefined;
  const actMessage = message.messages.find(item => item.type === 'agent:lifecycle:step:act') as
    | (AggregatedMessage & { type: 'agent:lifecycle:step:act' })
    | undefined;
  const toolSelectedMessage = thinkMessage?.messages.find(
    (item): item is Message =>
      'type' in item && item.type === 'agent:lifecycle:step:think:tool:selected',
  );
  const browserMessage = thinkMessage?.messages.find(
    (item): item is Message =>
      'type' in item && item.type === 'agent:lifecycle:step:think:browser:browse:complete',
  );
  const toolCalls = ((toolSelectedMessage?.content?.tool_calls || []) as ToolCall[]).filter(
    toolCall => toolCall.function.name !== 'terminate',
  );
  const actToolMessages = (actMessage?.messages.filter(
    item => item.type === 'agent:lifecycle:step:act:tool',
  ) || []) as (AggregatedMessage & { type: 'agent:lifecycle:step:act:tool' })[];

  const renderDiff = (lines: string[]) => {
    if (!lines.length) return null;
    return (
      <div className="mt-2 max-h-72 overflow-auto rounded-md bg-zinc-950 p-2 font-mono text-[11px] leading-5">
        {lines.map((line, index) => {
          const className = line.startsWith('+')
            ? 'text-emerald-300'
            : line.startsWith('-')
              ? 'text-rose-300'
              : line.startsWith('@@') || line.startsWith('---') || line.startsWith('+++')
                ? 'text-cyan-300'
                : 'text-zinc-300';
          return (
            <div key={index} className={className}>
              {line || ' '}
            </div>
          );
        })}
      </div>
    );
  };

  return (
    <div className="space-y-1">
      {toolCalls.map(toolCall => {
        const toolEvents = actToolMessages.find(item => item.content?.id === toolCall.id)?.messages || [];
        const executeComplete = toolEvents.find(
          item => item.type === 'agent:lifecycle:step:act:tool:execute:complete',
        );
        const fileUpdates = toolEvents.filter(
          item => item.type === 'agent:lifecycle:step:act:tool:file:updated',
        );
        const resultText = String(executeComplete?.content?.result || '').trim();
        const hasError = Boolean(executeComplete?.content?.error) || /^Error:/i.test(resultText);
        const diffLines =
          (fileUpdates.find(item => Array.isArray(item.content?.diff_preview?.lines))?.content?.diff_preview
            ?.lines as string[] | undefined) || [];
        const added = fileUpdates.reduce((sum, item) => sum + Number(item.content?.added_lines || 0), 0);
        const deleted = fileUpdates.reduce((sum, item) => sum + Number(item.content?.deleted_lines || 0), 0);
        const shortResult = resultText.replace(/\s+/g, ' ').slice(0, 220);
        const argumentHint = getArgumentHint(toolCall.function.arguments);
        const { toolName, functionName } = getToolByPrefix(toolCall.function.name);
        const displayName = toolName === toolCall.function.name
          ? fallbackToolLabel(toolCall.function.name)
          : `${toolName}${functionName ? ` ${fallbackToolLabel(functionName)}` : ''}`;
        const ToolIcon = getToolIcon(toolCall.function.name);

        return (
          <div key={toolCall.id} className="py-2 first:pt-0 last:pb-0">
            <button
              type="button"
              className="flex w-full items-start gap-2 text-left"
              onClick={() => setData({ type: 'tool', toolId: toolCall.id })}
              title="Open tool details"
            >
              <ToolIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1">
                <span className="block truncate text-xs font-medium">{displayName}</span>
                {argumentHint && (
                  <span className="mt-0.5 block truncate font-mono text-[11px] text-muted-foreground">
                    {argumentHint}
                  </span>
                )}
              </span>
              <span className="flex shrink-0 items-center gap-1 text-[11px] text-muted-foreground">
                {hasError ? (
                  <TriangleAlert className="h-3.5 w-3.5 text-rose-500" />
                ) : executeComplete ? (
                  <Check className="h-3.5 w-3.5 text-emerald-600" />
                ) : (
                  <LoaderCircle className="h-3.5 w-3.5 animate-spin" />
                )}
                {hasError ? 'Failed' : executeComplete ? 'Done' : 'Running'}
              </span>
            </button>

            {shortResult && (
              <div className={`mt-1.5 pl-5 text-xs leading-5 ${hasError ? 'text-rose-600 dark:text-rose-300' : 'text-muted-foreground'}`}>
                {shortResult}
                {resultText.length > 220 ? '…' : ''}
              </div>
            )}
            {fileUpdates.length > 0 && (
              <div className="mt-1 pl-5 text-[11px] text-muted-foreground">
                {fileUpdates
                  .map(item => String(item.content?.path || ''))
                  .filter(Boolean)
                  .slice(0, 3)
                  .join(' · ')}
                {fileUpdates.length > 3 ? ` · +${fileUpdates.length - 3} more` : ''}
                <span className="ml-2 font-mono text-emerald-600">+{added}</span>
                <span className="ml-1 font-mono text-rose-600">-{deleted}</span>
              </div>
            )}
            {diffLines.length > 0 && <div className="pl-5">{renderDiff(diffLines)}</div>}
          </div>
        );
      })}

      {browserMessage && (
        <button
          type="button"
          className="group relative mt-2 block h-28 w-44 overflow-hidden rounded-md border bg-muted"
          onClick={() =>
            setData({
              type: 'browser',
              url: browserMessage.content.url,
              title: browserMessage.content.title,
              screenshot: browserMessage.content.screenshot,
            })
          }
          title="Open browser snapshot"
        >
          <img
            src={getImageUrl(browserMessage.content.screenshot)}
            alt={browserMessage.content.title || 'Browser snapshot'}
            className="h-full w-full object-cover object-top"
            onError={event => {
              event.currentTarget.style.display = 'none';
            }}
          />
          <span className="absolute inset-x-0 bottom-0 flex items-center gap-1 bg-background/90 px-2 py-1 text-[11px]">
            <ImageIcon className="h-3 w-3" />
            Browser snapshot
          </span>
        </button>
      )}
    </div>
  );
};
