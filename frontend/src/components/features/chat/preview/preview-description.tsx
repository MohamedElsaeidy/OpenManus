import { Badge } from '@/components/ui/badge';
import type { Message } from '@/libs/chat-messages/types';
import { usePreviewData } from './store';

interface ChatPreviewDescriptionProps {
  messages: Message[];
}

/**
 * Context line for the detour views reached from the chat.
 *
 * The tabbed views say what they are in the tab itself, and the runtime pill
 * already reports whether the agent is working — a standing "Manus is not
 * using the computer right now" under a timeline full of captured work was
 * both redundant and wrong.
 */
export const PreviewDescription = ({ messages }: ChatPreviewDescriptionProps) => {
  const { data } = usePreviewData();

  if (data?.type === 'tool') {
    const executionStart = messages.find(
      message =>
        message.type === 'agent:lifecycle:step:act:tool:execute:start' &&
        message.content.id === data.toolId,
    );
    return (
      <p className="text-muted-foreground flex-none border-b px-3 py-1.5 text-xs">
        Tool <Badge variant="outline">{executionStart?.content.name}</Badge>{' '}
        <code className="text-[11px]">{executionStart?.content.id}</code>
      </p>
    );
  }

  if (data?.type === 'browser') {
    return (
      <p className="text-muted-foreground flex-none truncate border-b px-3 py-1.5 text-xs">
        Captured{' '}
        <a
          href={data.url}
          target="_blank"
          rel="noopener noreferrer"
          className="text-activity-browser hover:underline"
        >
          {data.title || data.url}
        </a>
      </p>
    );
  }

  return null;
};
