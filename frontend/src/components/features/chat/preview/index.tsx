/**
 * The Manus computer — the window onto what the agent is doing.
 *
 * Destinations are labelled tabs rather than a row of identical icon buttons:
 * there are six of them, and an icon alone cannot distinguish "changes" from
 * "files" from "timeline". Rarely-used controls (skills, vault, pause) stay as
 * icons on the right where they do not compete with navigation.
 */
import { Button } from '@/components/ui/button';
import type { Message } from '@/libs/chat-messages/types';
import { cn } from '@/libs/utils';
import {
  getConversationRuntime,
  pauseConversationSandbox,
  resumeConversationSandbox,
  type ConversationRuntime,
} from '@/services/conversations';
import type { IntegrationsHealth } from '@/services/conversations';
import {
  ActivityIcon,
  ArrowLeftIcon,
  BookOpenIcon,
  Code2Icon,
  FileClockIcon,
  FolderIcon,
  type LucideIcon,
  NetworkIcon,
  PauseIcon,
  PlayIcon,
  SquareTerminalIcon,
  WavesIcon,
} from 'lucide-react';
import { Suspense, lazy, useEffect, useRef, useState } from 'react';
import { PreviewDescription } from './preview-description';
import { usePreviewData, type PreviewData } from './store';

interface ChatPreviewProps {
  messages: Message[];
  taskId: string;
  conversationId?: string;
  integrationsHealth?: IntegrationsHealth | null;
  className?: string;
  performanceMode?: boolean;
  pollRuntime?: boolean;
  isRunning?: boolean;
}

const PreviewContent = lazy(() =>
  import('./preview-content').then(mod => ({ default: mod.PreviewContent })),
);

type TabId = PreviewData['type'];

const TABS: { id: TabId; label: string; icon: LucideIcon; needsConversation?: boolean }[] = [
  { id: 'timeline', label: 'Timeline', icon: WavesIcon },
  { id: 'editor', label: 'Editor', icon: Code2Icon },
  { id: 'workspace', label: 'Files', icon: FolderIcon },
  { id: 'terminal', label: 'Terminal', icon: SquareTerminalIcon },
  { id: 'changes', label: 'Changes', icon: FileClockIcon },
  { id: 'runtime', label: 'Runtime', icon: ActivityIcon, needsConversation: true },
];

/** Views reached by clicking something in the chat rather than by a tab. */
const DETOUR_LABELS: Partial<Record<TabId, string>> = {
  tool: 'Tool call',
  browser: 'Page capture',
  skills: 'Skills',
  vault: 'Vault sync',
  live: 'Live activity',
};

export const ChatPreview = ({
  messages,
  taskId,
  conversationId,
  integrationsHealth,
  className,
  performanceMode = false,
  pollRuntime = false,
  isRunning = false,
}: ChatPreviewProps) => {
  const { data, setData } = usePreviewData();
  const [runtime, setRuntime] = useState<ConversationRuntime | null>(null);
  const runtimeDigestRef = useRef('');
  const workspacePath = `conversations/${conversationId || taskId}`;

  const active: TabId = data?.type ?? 'timeline';
  const detourLabel = DETOUR_LABELS[active];

  useEffect(() => {
    if (!conversationId) return;
    let cancelled = false;
    const runtimeDigest = (value: ConversationRuntime | null) =>
      value
        ? JSON.stringify({
            status: value.status,
            running_count: value.running_count,
            sandbox_status: value.sandbox?.status,
            process_count: value.processes?.length || 0,
            container_count: value.containers?.length || 0,
            url_count: value.urls?.length || 0,
          })
        : '';
    runtimeDigestRef.current = '';
    const loadRuntime = async () => {
      try {
        const nextRuntime = await getConversationRuntime(conversationId);
        if (!cancelled) {
          const nextDigest = runtimeDigest(nextRuntime);
          if (nextDigest !== runtimeDigestRef.current) {
            runtimeDigestRef.current = nextDigest;
            setRuntime(nextRuntime);
          }
        }
      } catch {
        if (!cancelled) setRuntime(null);
      }
    };
    if (!pollRuntime) {
      return () => {
        cancelled = true;
      };
    }
    loadRuntime();
    if (performanceMode) {
      return () => {
        cancelled = true;
      };
    }
    const interval = window.setInterval(() => {
      if (!document.hidden) loadRuntime();
    }, 12000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [conversationId, performanceMode, pollRuntime]);

  const openTab = (id: TabId) => {
    if (id === 'runtime') {
      if (conversationId) setData({ type: 'runtime', conversationId, tab: 'processes' });
      return;
    }
    if (id === 'workspace') {
      setData({ type: 'workspace', path: workspacePath });
      return;
    }
    if (id === 'editor') {
      setData({ type: 'editor' });
      return;
    }
    setData({ type: id } as PreviewData);
  };

  const isPaused = runtime?.sandbox?.status === 'paused';

  return (
    <section
      className={cn(
        'bg-card flex h-full w-full min-w-0 flex-col overflow-hidden rounded-xl border shadow-sm',
        className,
      )}
    >
      {/* Title row */}
      <header className="flex flex-none items-center gap-2 border-b px-3 py-2">
        <h2 className="text-sm font-semibold tracking-tight">Manus's Computer</h2>

        <RuntimePill
          runtime={runtime}
          isRunning={isRunning}
          onClick={() =>
            conversationId && setData({ type: 'runtime', conversationId, tab: 'processes' })
          }
        />

        {integrationsHealth && (
          <div className="hidden items-center gap-1 lg:flex">
            <HealthChip label="Memory" live={Boolean(integrationsHealth.agentmemory?.live)} />
            <HealthChip label="Obsidian" live={Boolean(integrationsHealth.obsidian?.live)} />
          </div>
        )}

        <div className="ml-auto flex items-center gap-1">
          {conversationId && (
            <>
              <IconAction
                icon={BookOpenIcon}
                label="Skills"
                onClick={() => setData({ type: 'skills', conversationId })}
              />
              <IconAction
                icon={NetworkIcon}
                label="Vault sync"
                onClick={() => setData({ type: 'vault', conversationId })}
              />
              <IconAction
                icon={isPaused ? PlayIcon : PauseIcon}
                label={isPaused ? 'Resume computer' : 'Pause computer'}
                onClick={async () => {
                  if (isPaused) await resumeConversationSandbox(conversationId);
                  else await pauseConversationSandbox(conversationId);
                  setRuntime(await getConversationRuntime(conversationId));
                }}
              />
            </>
          )}
        </div>
      </header>

      {/* Tabs */}
      <nav
        role="tablist"
        aria-label="Computer views"
        className="flex flex-none items-center gap-0.5 overflow-x-auto border-b px-2"
      >
        {TABS.filter(tab => !tab.needsConversation || conversationId).map(tab => {
          const isActive = active === tab.id;
          return (
            <button
              key={tab.id}
              role="tab"
              aria-selected={isActive}
              onClick={() => openTab(tab.id)}
              className={cn(
                'relative flex items-center gap-1.5 px-2.5 py-2 text-xs font-medium whitespace-nowrap transition-colors',
                isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
              )}
            >
              <tab.icon className="h-3.5 w-3.5" />
              {tab.label}
              {isActive && (
                /* The one gradient in the product: identity, not decoration. */
                <span className="from-brand to-brand-accent absolute inset-x-1 -bottom-px h-[2px] rounded-full bg-gradient-to-r" />
              )}
            </button>
          );
        })}

        {detourLabel && (
          <button
            onClick={() => setData({ type: 'timeline' })}
            className="text-muted-foreground hover:text-foreground ml-auto flex items-center gap-1 px-2 py-2 text-xs"
          >
            <ArrowLeftIcon className="h-3 w-3" />
            {detourLabel}
          </button>
        )}
      </nav>

      <PreviewDescription messages={messages} />

      <div className="min-h-0 min-w-0 flex-1 overflow-hidden p-3">
        <Suspense
          fallback={<div className="text-muted-foreground p-3 text-xs">Loading preview…</div>}
        >
          <PreviewContent
            messages={messages}
            performanceMode={performanceMode}
            isRunning={isRunning}
            workspacePath={workspacePath}
          />
        </Suspense>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Header pieces
// ---------------------------------------------------------------------------

const RuntimePill = ({
  runtime,
  isRunning,
  onClick,
}: {
  runtime: ConversationRuntime | null;
  isRunning: boolean;
  onClick: () => void;
}) => {
  if (!runtime) return null;
  const busy = runtime.running_count > 0 || isRunning;

  return (
    <button
      onClick={onClick}
      title="Runtime processes"
      className={cn(
        'flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors',
        busy
          ? 'border-brand/40 bg-brand/10 text-brand'
          : 'text-muted-foreground hover:text-foreground',
      )}
    >
      <span
        className={cn(
          'h-1.5 w-1.5 rounded-full',
          busy ? 'bg-brand live-dot' : 'bg-muted-foreground/50',
        )}
      />
      {runtime.running_count > 0 ? `${runtime.running_count} running` : busy ? 'working' : 'idle'}
    </button>
  );
};

const HealthChip = ({ label, live }: { label: string; live: boolean }) => (
  <span
    className={cn(
      'rounded border px-1.5 py-0.5 text-[10px]',
      live
        ? 'border-activity-file-border bg-activity-file-surface text-activity-file'
        : 'border-activity-tool-border bg-activity-tool-surface text-activity-tool',
    )}
  >
    {label} {live ? 'live' : 'down'}
  </span>
);

const IconAction = ({
  icon: Icon,
  label,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  onClick: () => void;
}) => (
  <Button variant="ghost" size="icon" className="h-7 w-7" onClick={onClick} title={label} aria-label={label}>
    <Icon className="h-3.5 w-3.5" />
  </Button>
);
