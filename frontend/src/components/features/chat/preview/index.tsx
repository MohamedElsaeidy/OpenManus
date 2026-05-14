import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import type { Message } from '@/libs/chat-messages/types';
import { cn } from '@/libs/utils';
import { getConversationRuntime, pauseConversationSandbox, resumeConversationSandbox, type ConversationRuntime } from '@/services/conversations';
import { ActivityIcon, FileClockIcon, FolderIcon, GlobeIcon, ListChecksIcon, NetworkIcon, PauseIcon, PlayIcon, SquareTerminalIcon } from 'lucide-react';
import { useEffect, useState } from 'react';
import { PreviewContent } from './preview-content';
import { PreviewDescription } from './preview-description';
import { usePreviewData } from './store';

interface ChatPreviewProps {
  messages: Message[];
  taskId: string;
  conversationId?: string;
  className?: string;
}

export const ChatPreview = ({ messages, taskId, conversationId, className }: ChatPreviewProps) => {
  const { setData } = usePreviewData();
  const [runtime, setRuntime] = useState<ConversationRuntime | null>(null);
  const workspacePath = `conversations/${conversationId || taskId}`;

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    const loadRuntime = async () => {
      try {
        const nextRuntime = await getConversationRuntime(conversationId);
        if (!cancelled) setRuntime(nextRuntime);
      } catch {
        if (!cancelled) setRuntime(null);
      }
    };
    loadRuntime();
    const interval = window.setInterval(loadRuntime, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [conversationId]);

  return (
    <Card className={cn('flex h-full w-full flex-col gap-0 px-2', className)}>
      <CardHeader className="flex-none p-2 py-1">
        <div className="flex items-center justify-between">
          <div className="flex min-w-0 items-center gap-2">
            <CardTitle className="text-normal">Manus's Computer</CardTitle>
            {runtime && (
              <button
                className={cn(
                  'rounded-full border px-2 py-0.5 text-xs',
                  runtime.running_count > 0 ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-700' : 'text-muted-foreground',
                )}
                onClick={() => conversationId && setData({ type: 'runtime', conversationId, tab: 'processes' })}
                title="Runtime processes"
              >
                {runtime.running_count > 0 ? `${runtime.running_count} running` : 'idle'}
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {conversationId && (
              <>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'runtime', conversationId, tab: 'processes' })} title="Processes">
                  <ActivityIcon className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'runtime', conversationId, tab: 'ports' })} title="Ports and URLs">
                  <GlobeIcon className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'terminal' })} title="Terminal">
                  <SquareTerminalIcon className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'changes' })} title="Changes">
                  <FileClockIcon className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'skills', conversationId })} title="Skills">
                  <ListChecksIcon className="h-3.5 w-3.5" />
                </Button>
                <Button variant="outline" size="icon" className="h-8 w-8" onClick={() => setData({ type: 'vault', conversationId })} title="Vault Sync">
                  <NetworkIcon className="h-3.5 w-3.5" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  onClick={async () => {
                    if (runtime?.sandbox?.status === 'paused') await resumeConversationSandbox(conversationId);
                    else await pauseConversationSandbox(conversationId);
                    setRuntime(await getConversationRuntime(conversationId));
                  }}
                  title={runtime?.sandbox?.status === 'paused' ? 'Resume computer' : 'Pause computer'}
                >
                  {runtime?.sandbox?.status === 'paused' ? <PlayIcon className="h-3.5 w-3.5" /> : <PauseIcon className="h-3.5 w-3.5" />}
                </Button>
              </>
            )}
            <Button
              variant="outline"
              size="icon"
              className="h-8 w-8 hover:bg-accent/80"
              onClick={() => setData({ type: 'workspace', path: workspacePath })}
              title="Workspace"
            >
              <FolderIcon className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <PreviewDescription messages={messages} />
      </CardHeader>
      <CardContent className="flex-1 overflow-hidden p-2">
        <PreviewContent messages={messages} />
      </CardContent>
    </Card>
  );
};
