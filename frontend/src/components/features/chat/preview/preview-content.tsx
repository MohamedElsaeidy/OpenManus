/**
 * PreviewContent — thin router that delegates to focused panel components.
 *
 * This file used to be 1,087 lines containing all panel logic inline.
 * It is now ~120 lines. Each panel lives in ./panels/ and can be edited,
 * tested, and lazy-loaded independently.
 */
import type { Message } from '@/libs/chat-messages/types';
import { useAsync } from '@/hooks/use-async';
import { listSkills } from '@/services/conversations';
import { Badge } from '@/components/ui/badge';
import { Suspense, lazy } from 'react';
import { usePreviewData } from './store';
import { BrowserPanel } from './panels/BrowserPanel';
import { RuntimePanel } from './panels/RuntimePanel';
import { TimelinePanel } from './panels/TimelinePanel';
import { LiveActivityPanel, TerminalOutputPanel, ToolPanel } from './panels/ToolsPanel';
import { WorkspacePanel, ChangesPanel } from './panels/WorkspacePanel';
import { VaultPanel } from './panels/VaultPanel';

/**
 * Monaco is ~2.5MB. Keeping the editor in its own chunk means opening a
 * conversation and watching the timeline never downloads it — only clicking
 * the Editor tab does.
 */
const EditorPanel = lazy(() =>
  import('./panels/EditorPanel').then(mod => ({ default: mod.EditorPanel })),
);

// ---------------------------------------------------------------------------
// Router
// ---------------------------------------------------------------------------

export const PreviewContent = ({
  messages,
  performanceMode = false,
  isRunning = false,
  workspacePath,
}: {
  messages: Message[];
  performanceMode?: boolean;
  isRunning?: boolean;
  workspacePath?: string;
}) => {
  const { data } = usePreviewData();

  // Default view: the scrubbable record of what the agent has done.
  if (!data || data.type === 'timeline') {
    return <TimelinePanel messages={messages} isRunning={isRunning} />;
  }

  if (data.type === 'editor') {
    return (
      <Suspense
        fallback={
          <div className="text-muted-foreground flex h-full items-center justify-center text-xs">
            Loading editor…
          </div>
        }
      >
        <EditorPanel root={workspacePath} initialPath={data.path} />
      </Suspense>
    );
  }

  // Tool execution detail
  if (data?.type === 'tool') {
    const start = messages.find(
      m => m.type === 'agent:lifecycle:step:act:tool:execute:start' && m.content.id === data.toolId,
    );
    const complete = messages.find(
      m => m.type === 'agent:lifecycle:step:act:tool:execute:complete' && m.content.id === data.toolId,
    );
    const liveOutput = messages
      .filter(m => m.type === 'agent:lifecycle:step:act:tool:terminal:output' && m.content.id === data.toolId)
      .map(m => m.content.chunk)
      .join('');

    return (
      <ToolPanel
        name={start?.content.name}
        toolId={data.toolId}
        args={start?.content.arguments ?? start?.content.args}
        result={complete?.content.result}
        liveOutput={liveOutput}
        isExecuting={Boolean(start && !complete)}
      />
    );
  }

  if (data?.type === 'browser') {
    return (
      <BrowserPanel url={data.url} title={data.title} screenshot={data.screenshot} />
    );
  }

  if (data?.type === 'workspace') {
    return <WorkspacePanel />;
  }

  if (data?.type === 'live') {
    return <LiveActivityPanel messages={messages} />;
  }

  if (data?.type === 'runtime') {
    return (
      <RuntimePanel
        conversationId={data.conversationId}
        initialTab={data.tab}
        performanceMode={performanceMode}
      />
    );
  }

  if (data?.type === 'terminal') {
    return <TerminalOutputPanel messages={messages} />;
  }

  if (data?.type === 'changes') {
    return <ChangesPanel messages={messages} />;
  }

  if (data?.type === 'skills') {
    return <SkillsPanel conversationId={data.conversationId} />;
  }

  if (data?.type === 'vault') {
    return <VaultPanel conversationId={data.conversationId} />;
  }

  return <TimelinePanel messages={messages} isRunning={isRunning} />;
};

// ---------------------------------------------------------------------------
// Skills panel (small enough to keep here inline)
// ---------------------------------------------------------------------------

const SkillsPanel = ({ conversationId }: { conversationId?: string }) => {
  const { data: skills, isLoading } = useAsync(async () => listSkills(conversationId), [], {
    deps: [conversationId],
  });
  return (
    <div className="flex h-full min-h-0 flex-col">
      <section className="flex h-full min-h-0 flex-col overflow-hidden">
        <header className="flex-none pb-2">
          <h3 className="text-sm font-semibold">Skills</h3>
          <p className="text-muted-foreground text-xs">
            OpenHands-style skills available to this conversation.
          </p>
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          {isLoading ? (
            <div className="text-muted-foreground text-sm">Loading skills…</div>
          ) : skills?.skills.length ? (
            <div className="space-y-2">
              {skills.skills.map(skill => (
                <div key={skill.path} className="rounded-md border p-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium">{skill.name}</div>
                    <Badge variant="outline">{skill.type}</Badge>
                  </div>
                  <div className="text-muted-foreground mt-1 truncate font-mono text-xs">{skill.path}</div>
                  {skill.triggers.length ? (
                    <div className="text-muted-foreground mt-1 text-xs">
                      Triggers: {skill.triggers.join(', ')}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-muted-foreground text-sm">No skills found.</div>
          )}
        </div>
      </section>
    </div>
  );
};
