import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { useAsync } from '@/hooks/use-async';
import type { Message } from '@/libs/chat-messages/types';
import { getImageUrl } from '@/libs/image';
import { getConversationRuntime, getObsidianGraph, killConversationProcess, listSkills, stopConversationContainer } from '@/services/conversations';
import {
  ArrowRightIcon,
  ChevronLeftIcon,
  DownloadIcon,
  FileIcon,
  FolderIcon,
  HashIcon,
  HomeIcon,
  LoaderIcon,
  PackageIcon,
  SquareTerminalIcon,
  StopCircleIcon,
  WrenchIcon,
  ExternalLinkIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import SyntaxHighlighter from 'react-syntax-highlighter';
import { githubGist } from 'react-syntax-highlighter/dist/esm/styles/hljs';
import { usePreviewData } from './store';

export const PreviewContent = ({
  messages,
  performanceMode = false,
}: {
  messages: Message[];
  performanceMode?: boolean;
}) => {
  const { data } = usePreviewData();

  if (data?.type === 'tool') {
    const executionStart = messages.find(m => m.type === 'agent:lifecycle:step:act:tool:execute:start' && m.content.id === data.toolId);
    const executionComplete = messages.find(m => m.type === 'agent:lifecycle:step:act:tool:execute:complete' && m.content.id === data.toolId);

    const name = executionStart?.content.name;
    const args = executionStart?.content.arguments ?? executionStart?.content.args;
    const result = executionComplete?.content.result;
    const liveOutput = messages
      .filter(m => m.type === 'agent:lifecycle:step:act:tool:terminal:output' && m.content.id === data.toolId)
      .map(m => m.content.chunk)
      .join('');
    const toolId = data.toolId;
    const isExecuting = executionStart && !executionComplete;
    const parsedArgs = normalizeToolArguments(args);
    const isTerminalTool = name === 'python_execute' || name === 'bash';
    const terminalInput = getTerminalInput(name, parsedArgs);

    return (
      <div className="h-full min-h-0 p-4">
        <Card className="flex h-full min-h-0 flex-col overflow-hidden">
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isTerminalTool ? <SquareTerminalIcon className="text-primary h-5 w-5" /> : <WrenchIcon className="text-primary h-5 w-5" />}
                <CardTitle className="text-base">{isTerminalTool ? 'Terminal' : 'Tool Execution'}</CardTitle>
              </div>
              {isExecuting && (
                <div className="flex items-center gap-1 text-amber-500">
                  <LoaderIcon className="h-4 w-4 animate-spin" />
                  <span className="text-xs font-medium">Running...</span>
                </div>
              )}
            </div>
            <CardDescription className="flex items-center gap-2">
              <HashIcon className="h-3.5 w-3.5" />
              <span>ID: {toolId}</span>
            </CardDescription>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 space-y-4 overflow-auto">
            <div className="space-y-2">
              <div className="text-muted-foreground text-sm font-medium">Tool Name</div>
              <Badge variant="outline" className="font-mono text-sm">
                <PackageIcon className="mr-1 h-3.5 w-3.5" />
                {name}
              </Badge>
            </div>

            {isTerminalTool ? (
              <TerminalPanel command={name} input={terminalInput} output={formatTerminalOutput(result)} liveOutput={liveOutput} isExecuting={Boolean(isExecuting)} />
            ) : (
              <>
                {hasToolArguments(parsedArgs) && (
                  <div className="space-y-2">
                    <div className="text-muted-foreground text-sm font-medium">Parameters</div>
                    <CodeBlock value={formatToolArguments(parsedArgs)} language="json" maxHeight="18rem" />
                  </div>
                )}

                {result ? (
                  <div className="space-y-2">
                    <div className="text-muted-foreground flex items-center gap-2 text-sm font-medium">
                      <ArrowRightIcon className="h-3.5 w-3.5" />
                      <span>Result</span>
                    </div>
                    <CodeBlock value={String(result)} language="text" maxHeight="24rem" />
                  </div>
                ) : (
                  isExecuting && <ProcessingPanel />
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  if (data?.type === 'browser') {
    return (
      <div className="flex h-full flex-col overflow-hidden rounded-md border bg-black">
        <div className="flex min-h-10 items-center gap-2 border-b bg-background px-3 py-2">
          <div className="flex gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full bg-red-400" />
            <span className="h-2.5 w-2.5 rounded-full bg-amber-400" />
            <span className="h-2.5 w-2.5 rounded-full bg-emerald-400" />
          </div>
          <div className="bg-muted text-muted-foreground min-w-0 flex-1 rounded px-2 py-1 text-xs">
            <div className="truncate">{data.url || data.title || 'Browser preview'}</div>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto bg-neutral-950">
          <img src={getImageUrl(data.screenshot)} alt="Manus's Computer Screen" className="mx-auto h-auto w-full" />
        </div>
      </div>
    );
  }

  if (data?.type === 'workspace') {
    return <WorkspacePreview />;
  }

  if (data?.type === 'live') {
    return <LiveActivityPreview messages={messages} />;
  }

  if (data?.type === 'runtime') {
    return (
      <RuntimePreview
        conversationId={data.conversationId}
        initialTab={data.tab}
        performanceMode={performanceMode}
      />
    );
  }

  if (data?.type === 'terminal') {
    return <TerminalOutputPreview messages={messages} />;
  }

  if (data?.type === 'changes') {
    return <ChangesPreview messages={messages} />;
  }

  if (data?.type === 'skills') {
    return <SkillsPreview conversationId={data.conversationId} />;
  }

  if (data?.type === 'vault') {
    return <VaultPreview conversationId={data.conversationId} />;
  }

  return <LiveActivityPreview messages={messages} />;
};

const LiveActivityPreview = ({ messages }: { messages: Message[] }) => {
  const recent = [...messages]
    .filter(
      message =>
        Boolean(message.type) &&
        message.type !== 'agent:lifecycle:step:act:tool:terminal:output',
    )
    .slice(-24)
    .reverse();

  const terminalTail = messages
    .filter(message => message.type === 'agent:lifecycle:step:act:tool:terminal:output')
    .map(message => String(message.content.chunk || ''))
    .join('')
    .slice(-5000);

  const status = recent.find(message => message.type === 'agent:lifecycle:complete')
    ? 'Completed'
    : recent.find(message => message.type === 'agent:lifecycle:terminated')
      ? 'Terminated'
      : recent.find(message => message.type === 'agent:lifecycle:step:error')
        ? 'Error'
        : 'Running';

  return (
    <div className="h-full min-h-0 p-4">
      <Card className="flex h-full min-h-0 flex-col overflow-hidden">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base">Live Activity</CardTitle>
            <Badge variant={status === 'Running' ? 'default' : status === 'Completed' ? 'outline' : 'destructive'}>
              {status}
            </Badge>
          </div>
          <CardDescription>What Manus is doing right now.</CardDescription>
        </CardHeader>
        <CardContent className="grid min-h-0 flex-1 grid-rows-[1fr_auto] gap-3 overflow-hidden">
          <div className="min-h-0 overflow-auto rounded-md border">
            {recent.length ? (
              recent.map((message, index) => (
                <div key={message.index || index} className="border-b p-2 last:border-b-0">
                  <div className="flex items-center justify-between gap-2">
                    <div className="truncate text-xs font-medium">{String(message.type || 'event')}</div>
                    <div className="text-muted-foreground text-[11px]">
                      {message.createdAt ? new Date(message.createdAt).toLocaleTimeString() : ''}
                    </div>
                  </div>
                  {message.content?.name ? (
                    <div className="text-muted-foreground mt-0.5 truncate font-mono text-[11px]">
                      {String(message.content.name)}
                    </div>
                  ) : null}
                  {message.content?.message ? (
                    <div className="mt-1 text-xs">{String(message.content.message)}</div>
                  ) : null}
                </div>
              ))
            ) : (
              <div className="text-muted-foreground p-3 text-sm">No live events yet.</div>
            )}
          </div>
          <div className="overflow-hidden rounded-md border bg-neutral-950 text-neutral-100">
            <div className="border-b border-neutral-800 px-3 py-1.5 text-xs font-medium">Terminal tail</div>
            <pre className="max-h-36 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-[11px] leading-5">
              {terminalTail || 'No terminal output yet.'}
            </pre>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const RuntimePreview = ({
  conversationId,
  initialTab = 'processes',
  performanceMode = false,
}: {
  conversationId: string;
  initialTab?: 'processes' | 'ports' | 'containers';
  performanceMode?: boolean;
}) => {
  const [refreshTick, setRefreshTick] = useState(0);
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [tab, setTab] = useState<'processes' | 'ports' | 'containers'>(initialTab);
  const { data: runtime, isLoading } = useAsync(
    async () => getConversationRuntime(conversationId),
    [],
    { deps: [conversationId, refreshTick] },
  );

  useEffect(() => {
    const interval = window.setInterval(
      () => setRefreshTick(tick => tick + 1),
      performanceMode ? 12000 : 3000,
    );
    return () => window.clearInterval(interval);
  }, [performanceMode]);

  const killProcess = async (pid: number) => {
    setBusyKey(`process:${pid}`);
    try {
      await killConversationProcess(conversationId, pid);
      setRefreshTick(tick => tick + 1);
    } finally {
      setBusyKey(null);
    }
  };

  const stopContainer = async (containerId: string) => {
    setBusyKey(`container:${containerId}`);
    try {
      await stopConversationContainer(conversationId, containerId);
      setRefreshTick(tick => tick + 1);
    } finally {
      setBusyKey(null);
    }
  };

  return (
    <div className="h-full min-h-0 p-4">
      <Card className="flex h-full min-h-0 flex-col overflow-hidden">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ActivityGlyph />
              <CardTitle className="text-base">Runtime</CardTitle>
            </div>
            <Badge variant={runtime?.running_count ? 'default' : 'outline'}>
              {runtime?.running_count ? `${runtime.running_count} running` : isLoading ? 'loading' : 'idle'}
            </Badge>
          </div>
          <CardDescription>Processes and containers for this conversation computer.</CardDescription>
          <div className="mt-2 flex gap-1">
            {(['processes', 'ports', 'containers'] as const).map(item => (
              <Button key={item} size="sm" variant={tab === item ? 'default' : 'outline'} onClick={() => setTab(item)} className="h-7 capitalize">
                {item}
              </Button>
            ))}
          </div>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 space-y-4 overflow-auto">
          {tab === 'ports' && (
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium">Exposed URLs</h3>
                <span className="text-muted-foreground text-xs">{runtime?.urls?.length || 0}</span>
              </div>
              <div className="overflow-hidden rounded-md border">
                {runtime?.urls?.length ? (
                  runtime.urls.map(item => (
                    <div key={`${item.pid}:${item.port}`} className="grid grid-cols-[80px_1fr_auto] gap-3 border-b p-2 last:border-b-0">
                      <div className="font-mono text-xs">:{item.port}</div>
                      <div className="min-w-0">
                        <a href={item.url} target="_blank" rel="noreferrer" className="block truncate text-sm font-medium text-primary hover:underline">
                          {item.url}
                        </a>
                        <div className="text-muted-foreground truncate font-mono text-xs">{item.command || item.args}</div>
                      </div>
                      <Button size="sm" variant="outline" asChild title="Open URL">
                        <a href={item.url} target="_blank" rel="noreferrer">
                          <ExternalLinkIcon className="h-3.5 w-3.5" />
                        </a>
                      </Button>
                    </div>
                  ))
                ) : (
                  <div className="text-muted-foreground p-3 text-sm">{isLoading ? 'Scanning ports...' : 'No listening web ports detected.'}</div>
                )}
              </div>
            </section>
          )}

          {tab === 'processes' && <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">Processes</h3>
              <span className="text-muted-foreground text-xs">{runtime?.processes.length || 0}</span>
            </div>
            <div className="overflow-hidden rounded-md border">
              {runtime?.processes.length ? (
                runtime.processes.map(process => (
                  <div key={process.pid} className="grid grid-cols-[72px_1fr_auto] gap-3 border-b p-2 last:border-b-0">
                    <div className="font-mono text-xs">
                      <div>{process.pid}</div>
                      <div className="text-muted-foreground">{process.elapsed}</div>
                    </div>
                    <div className="min-w-0">
                      <div className="flex min-w-0 items-center gap-2">
                        <div className="truncate text-sm font-medium">{process.command}</div>
                        {process.ports?.map(port => (
                          <Badge key={port} variant="outline" className="font-mono text-[10px]">
                            :{port}
                          </Badge>
                        ))}
                        {process.zombie && (
                          <Badge variant="outline" className="font-mono text-[10px]">
                            zombie
                          </Badge>
                        )}
                      </div>
                      <div className="text-muted-foreground max-h-10 overflow-hidden break-all font-mono text-xs">{process.args}</div>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={process.protected || busyKey === `process:${process.pid}`}
                      onClick={() => killProcess(process.pid)}
                      title={process.zombie ? 'Zombie processes are already dead and will disappear when reaped' : process.protected ? 'Protected system process' : 'Kill process'}
                    >
                      {busyKey === `process:${process.pid}` ? <LoaderIcon className="h-3.5 w-3.5 animate-spin" /> : <StopCircleIcon className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                ))
              ) : (
                <div className="text-muted-foreground p-3 text-sm">{isLoading ? 'Loading processes...' : 'No sandbox processes found.'}</div>
              )}
            </div>
          </section>}

          {tab === 'containers' && <section className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium">Docker Containers</h3>
              <span className="text-muted-foreground text-xs">{runtime?.containers.length || 0}</span>
            </div>
            {runtime?.hidden_system_containers ? (
              <div className="text-muted-foreground text-xs">
                {runtime.hidden_system_containers} OpenManus system container{runtime.hidden_system_containers === 1 ? '' : 's'} hidden.
              </div>
            ) : null}
            <div className="overflow-hidden rounded-md border">
              {runtime?.containers.length ? (
                runtime.containers.map(container => (
                  <div key={container.id} className="grid grid-cols-[84px_1fr_auto] gap-3 border-b p-2 last:border-b-0">
                    <div className="font-mono text-xs">
                      <div>{container.id}</div>
                      <div className="text-muted-foreground truncate">{container.status}</div>
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{container.name}</div>
                      <div className="text-muted-foreground truncate text-xs">{container.image}</div>
                    </div>
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={container.protected || busyKey === `container:${container.id}`}
                      onClick={() => stopContainer(container.id)}
                      title={container.protected ? 'Protected OpenManus container' : 'Stop container'}
                    >
                      {busyKey === `container:${container.id}` ? <LoaderIcon className="h-3.5 w-3.5 animate-spin" /> : <StopCircleIcon className="h-3.5 w-3.5" />}
                    </Button>
                  </div>
                ))
              ) : (
                <div className="text-muted-foreground p-3 text-sm">{isLoading ? 'Loading containers...' : 'No Docker containers found.'}</div>
              )}
            </div>
          </section>}
        </CardContent>
      </Card>
    </div>
  );
};

const TerminalOutputPreview = ({ messages }: { messages: Message[] }) => {
  const output = messages
    .filter(message => message.type === 'agent:lifecycle:step:act:tool:terminal:output')
    .map(message => {
      const stream = message.content.stream === 'stderr' ? 'stderr' : 'stdout';
      return `[${message.content.name || 'terminal'}:${stream}] ${message.content.chunk || ''}`;
    })
    .join('');
  return (
    <div className="h-full min-h-0 p-4">
      <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-md border bg-neutral-950 text-neutral-100">
        <div className="border-b border-neutral-800 px-3 py-2 text-sm font-medium">Terminal</div>
        <pre className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap break-words p-3 font-mono text-xs leading-5">
          {output || 'No terminal output yet.'}
        </pre>
      </div>
    </div>
  );
};

const ChangesPreview = ({ messages }: { messages: Message[] }) => {
  const changes = messages.filter(message => message.type === 'agent:lifecycle:step:act:tool:file:updated');
  const completions = messages.filter(message => message.type === 'agent:lifecycle:complete');
  const uniqueFiles = Array.from(new Set(changes.map(message => String(message.content.path || '')).filter(Boolean)));
  const totalAdded = changes.reduce((sum, message) => sum + Number(message.content?.added_lines || 0), 0);
  const totalDeleted = changes.reduce((sum, message) => sum + Number(message.content?.deleted_lines || 0), 0);
  return (
    <div className="h-full min-h-0 p-4">
      <Card className="flex h-full min-h-0 flex-col overflow-hidden">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Changes</CardTitle>
          <CardDescription>Files and artifacts touched in this conversation.</CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-auto">
          <div className="space-y-2">
            {uniqueFiles.length > 0 ? (
              <div className="rounded-md border bg-muted/30 p-2">
                <div className="text-sm font-medium">
                  {uniqueFiles.length} file{uniqueFiles.length === 1 ? '' : 's'} changed
                  <span className="ml-2 font-mono text-emerald-600">+{totalAdded}</span>
                  <span className="ml-1 font-mono text-rose-600">-{totalDeleted}</span>
                </div>
                <div className="mt-1 space-y-1">
                  {uniqueFiles.slice(0, 8).map(path => (
                    <div key={path} className="font-mono text-xs text-muted-foreground">
                      {path}
                    </div>
                  ))}
                  {uniqueFiles.length > 8 ? (
                    <div className="text-xs text-muted-foreground">+{uniqueFiles.length - 8} more files</div>
                  ) : null}
                </div>
              </div>
            ) : null}
            {changes.map((message, index) => (
              <div key={message.index || index} className="rounded-md border p-2">
                <div className="font-mono text-sm">
                  {String(message.content.path || '')}
                  <span className="ml-2 text-emerald-600">+{Number(message.content?.added_lines || 0)}</span>
                  <span className="ml-1 text-rose-600">-{Number(message.content?.deleted_lines || 0)}</span>
                </div>
                <div className="text-muted-foreground text-xs">{String(message.content.tool || 'tool')}</div>
                {Array.isArray(message.content?.diff_preview?.lines) && message.content.diff_preview.lines.length > 0 ? (
                  <pre className="mt-2 overflow-auto rounded-md border bg-zinc-950 p-2 font-mono text-xs leading-5 text-zinc-100">
                    {message.content.diff_preview.lines.map((line: string, lineIndex: number) => {
                      const cls = line.startsWith('+')
                        ? 'bg-emerald-900/30 text-emerald-200'
                        : line.startsWith('-')
                          ? 'bg-red-900/30 text-red-200'
                          : line.startsWith('@@')
                            ? 'bg-zinc-800 text-zinc-300'
                            : 'text-zinc-100';
                      return (
                        <div key={`${message.index || index}-${lineIndex}`} className={cls}>
                          {line || ' '}
                        </div>
                      );
                    })}
                  </pre>
                ) : null}
              </div>
            ))}
            {completions.map((message, index) => {
              const workspace = message.content.workspace as { pdfs?: string[]; logs?: string[]; tex?: string[]; warning?: string } | undefined;
              if (!workspace) return null;
              return (
                <div key={`completion-${index}`} className="rounded-md border p-2 text-sm">
                  <div className="font-medium">Artifacts</div>
                  {workspace.pdfs?.length ? <div className="text-muted-foreground">PDF: {workspace.pdfs.join(', ')}</div> : null}
                  {workspace.logs?.length ? <div className="text-muted-foreground">Logs: {workspace.logs.join(', ')}</div> : null}
                  {workspace.warning ? <div className="mt-1 text-amber-700">{workspace.warning}</div> : null}
                </div>
              );
            })}
            {!changes.length && !completions.length && <div className="text-muted-foreground text-sm">No file changes recorded yet.</div>}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const SkillsPreview = ({ conversationId }: { conversationId?: string }) => {
  const { data: skills, isLoading } = useAsync(async () => listSkills(conversationId), [], { deps: [conversationId] });
  return (
    <div className="h-full min-h-0 p-4">
      <Card className="flex h-full min-h-0 flex-col overflow-hidden">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Skills</CardTitle>
          <CardDescription>OpenHands-style skills available to this conversation.</CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-auto">
          {isLoading ? (
            <div className="text-muted-foreground text-sm">Loading skills...</div>
          ) : skills?.skills.length ? (
            <div className="space-y-2">
              {skills.skills.map(skill => (
                <div key={skill.path} className="rounded-md border p-2">
                  <div className="flex items-center justify-between gap-2">
                    <div className="font-medium">{skill.name}</div>
                    <Badge variant="outline">{skill.type}</Badge>
                  </div>
                  <div className="text-muted-foreground mt-1 truncate font-mono text-xs">{skill.path}</div>
                  {skill.triggers.length ? <div className="text-muted-foreground mt-1 text-xs">Triggers: {skill.triggers.join(', ')}</div> : null}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-muted-foreground text-sm">No skills found.</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

const VaultPreview = ({ conversationId }: { conversationId: string }) => {
  const { data: graph, isLoading } = useAsync(async () => getObsidianGraph(conversationId), [], { deps: [conversationId] });
  const lastSync =
    graph?.nodes
      ?.map(node => node.updated_at)
      .filter(Boolean)
      .sort()
      .at(-1) ?? null;

  return (
    <div className="h-full min-h-0 p-4">
      <Card className="flex h-full min-h-0 flex-col overflow-hidden">
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Vault Sync Status</CardTitle>
          <CardDescription>Automated Obsidian markdown sync + graph memory.</CardDescription>
        </CardHeader>
        <CardContent className="min-h-0 flex-1 overflow-auto">
          {isLoading ? (
            <div className="text-muted-foreground text-sm">Loading vault status...</div>
          ) : graph ? (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-md border p-2">
                  <div className="text-muted-foreground text-xs">Notes</div>
                  <div className="text-lg font-semibold">{graph.node_count}</div>
                </div>
                <div className="rounded-md border p-2">
                  <div className="text-muted-foreground text-xs">Graph Links</div>
                  <div className="text-lg font-semibold">{graph.edge_count}</div>
                </div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-muted-foreground text-xs">Last Sync</div>
                <div className="text-sm font-medium">{lastSync ? new Date(lastSync).toLocaleString() : 'No synced notes yet'}</div>
              </div>
              <div className="rounded-md border p-2">
                <div className="text-muted-foreground mb-1 text-xs">Recent Notes</div>
                {graph.nodes.length ? (
                  <div className="space-y-1">
                    {graph.nodes.slice(0, 8).map(node => (
                      <div key={node.id} className="flex items-center justify-between gap-2">
                        <div className="truncate text-sm">{node.title}</div>
                        <div className="text-muted-foreground truncate text-xs">{node.path}</div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="text-muted-foreground text-sm">No notes found in this conversation workspace.</div>
                )}
              </div>
            </div>
          ) : (
            <div className="text-muted-foreground text-sm">Could not load vault status.</div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

const ActivityGlyph = () => <SquareTerminalIcon className="text-primary h-5 w-5" />;

const normalizeToolArguments = (args: unknown): unknown => {
  if (typeof args !== 'string') return args;
  try {
    return JSON.parse(args);
  } catch {
    return args;
  }
};

const hasToolArguments = (args: unknown): boolean => {
  if (!args) return false;
  if (typeof args === 'string') return args.trim().length > 0;
  if (typeof args === 'object') return Object.keys(args).length > 0;
  return true;
};

const formatToolArguments = (args: unknown): string => {
  if (typeof args === 'string') return args;
  return JSON.stringify(args, null, 2);
};

const getTerminalInput = (name: string | undefined, args: unknown): string => {
  if (!args || typeof args !== 'object') return '';
  const record = args as Record<string, unknown>;
  if (name === 'python_execute') return String(record.code || '');
  if (name === 'bash') return String(record.command || '');
  return formatToolArguments(args);
};

const CodeBlock = ({ value, language, maxHeight }: { value: string; language: string; maxHeight: string }) => (
  <div className="overflow-auto rounded-md border bg-muted/40" style={{ maxHeight }}>
    <SyntaxHighlighter
      language={language}
      showLineNumbers
      wrapLongLines
      style={githubGist}
      customStyle={{
        color: 'inherit',
        backgroundColor: 'inherit',
        fontSize: '0.8125rem',
        lineHeight: '1.5',
        margin: 0,
        borderRadius: 0,
        whiteSpace: 'pre-wrap',
        overflowWrap: 'anywhere',
      }}
    >
      {value}
    </SyntaxHighlighter>
  </div>
);

const TerminalPanel = ({
  command,
  input,
  output,
  liveOutput,
  isExecuting,
}: {
  command?: string;
  input: string;
  output?: unknown;
  liveOutput?: string;
  isExecuting: boolean;
}) => {
  const visibleOutput = liveOutput || (output ? String(output) : '');

  return (
    <div className="overflow-hidden rounded-md border bg-neutral-950 text-neutral-100">
      <div className="flex items-center justify-between border-b border-neutral-800 px-3 py-2">
        <div className="flex items-center gap-2 text-xs font-medium">
          <SquareTerminalIcon className="h-3.5 w-3.5" />
          <span>{command === 'python_execute' ? 'python' : command || 'terminal'}</span>
        </div>
        {isExecuting && (
          <div className="flex items-center gap-1 text-xs text-amber-300">
            <LoaderIcon className="h-3.5 w-3.5 animate-spin" />
            running
          </div>
        )}
      </div>
      <div className="max-h-[34rem] overflow-auto p-3 font-mono text-xs leading-5">
        {input && (
          <pre className="mb-3 whitespace-pre text-emerald-300">
            <span className="text-neutral-500">$ </span>
            {input}
          </pre>
        )}
        {visibleOutput ? <pre className="whitespace-pre text-neutral-100">{visibleOutput}</pre> : <div className="text-neutral-500">Waiting for output...</div>}
      </div>
    </div>
  );
};

const formatTerminalOutput = (output: unknown): string => {
  if (!output) return '';
  if (typeof output !== 'string') return JSON.stringify(output, null, 2);

  const match = output.match(/Observed output of cmd `[^`]+` executed:\n([\s\S]*)$/);
  if (match?.[1]) return match[1];
  return output;
};

const ProcessingPanel = () => (
  <div className="bg-muted/20 flex items-center justify-center rounded-md border p-6">
    <div className="text-muted-foreground flex flex-col items-center gap-2">
      <LoaderIcon className="h-5 w-5 animate-spin" />
      <span className="text-sm">Processing...</span>
    </div>
  </div>
);

const WorkspacePreview = () => {
  const { data, setData } = usePreviewData();
  const [isDownloading, setIsDownloading] = useState(false);

  const workspacePath = data?.type === 'workspace' ? data.path || '' : '';

  const isShare = workspacePath.startsWith('/share');

  // Helper to check if we're in root directory
  const isRootDirectory = !workspacePath || workspacePath.split('/').length <= 1;

  // Handle back button click - navigate to parent directory
  const handleBackClick = () => {
    if (isRootDirectory) return;

    const pathParts = workspacePath.split('/');
    pathParts.pop(); // Remove the last path segment
    const parentPath = pathParts.join('/');

    setData({
      type: 'workspace',
      path: parentPath,
    });
  };

  const handleItemClick = (item: { name: string; type: 'file' | 'directory' }) => {
    setData({
      type: 'workspace',
      path: `${workspacePath}/${item.name}`,
    });
  };

  const handleDownload = async () => {
    if (data?.type !== 'workspace') return;
    setIsDownloading(true);
    try {
      const downloadUrl = isShare ? `/api/share/download/${workspacePath}` : `/api/workspace/download/${workspacePath}`;
      const a = document.createElement('a');
      a.href = downloadUrl;
      a.download = workspacePath.split('/').pop() || 'workspace';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (error) {
      console.error('Download error:', error);
    } finally {
      // Add a small delay to show loading state
      setTimeout(() => {
        setIsDownloading(false);
      }, 1000);
    }
  };

  const { data: workspace, isLoading } = useAsync(
    async () => {
      if (data?.type !== 'workspace') return;
      const workspaceRes = await fetch(isShare ? `/api/share/workspace/${workspacePath}` : `/api/workspace/${workspacePath}`);
      if (!workspaceRes.ok) return;
      if (workspaceRes.headers.get('content-type')?.includes('application/json')) {
        return (await workspaceRes.json()) as {
          name: string;
          type: 'file' | 'directory';
          size: number;
          modifiedTime: string;
        }[];
      }
      return workspaceRes.blob();
    },
    [],
    {
      deps: [workspacePath, data?.type],
    },
  );

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="flex flex-col items-center gap-2">
          <LoaderIcon className="text-primary h-5 w-5 animate-spin" />
          <span className="text-muted-foreground text-sm">Loading workspace...</span>
        </div>
      </div>
    );
  }

  if (!workspace) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="text-muted-foreground">Could not load workspace content</div>
      </div>
    );
  }

  if (Array.isArray(workspace)) {
    return (
      <div className="p-4">
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isRootDirectory ? (
                  <HomeIcon className="text-muted-foreground h-4 w-4" />
                ) : (
                  <Button variant="ghost" size="icon" onClick={handleBackClick} className="h-6 w-6" title="Return to parent directory">
                    <ChevronLeftIcon className="h-4 w-4" />
                  </Button>
                )}
                <CardTitle className="text-base">Workspace: {data?.type === 'workspace' && data.path ? data.path : 'Root Directory'}</CardTitle>
              </div>
              <Button onClick={handleDownload} variant="outline" size="sm" disabled={isDownloading} title="Download current directory">
                {isDownloading ? (
                  <>
                    <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
                    Downloading...
                  </>
                ) : (
                  <>
                    <DownloadIcon className="mr-2 h-4 w-4" />
                    Download
                  </>
                )}
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            <div className="space-y-1">
              {workspace.length === 0 ? (
                <div className="text-muted-foreground py-4 text-center">This directory is empty</div>
              ) : (
                workspace.map(item => (
                  <div
                    key={item.name}
                    className="hover:bg-muted/40 flex cursor-pointer items-center justify-between rounded-md border p-2"
                    onClick={() => handleItemClick(item)}
                  >
                    <div className="flex items-center gap-2">
                      {item.type === 'directory' ? <FolderIcon className="h-4 w-4 text-blue-500" /> : <FileIcon className="h-4 w-4 text-gray-500" />}
                      <span className="text-sm font-medium">{item.name}</span>
                    </div>
                    <div className="flex items-center gap-4">
                      <span className="text-muted-foreground text-xs">{formatFileSize(item.size)}</span>
                      <span className="text-muted-foreground text-xs">{new Date(item.modifiedTime).toLocaleDateString()}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4">
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              {isRootDirectory ? (
                <HomeIcon className="text-muted-foreground h-5 w-5" />
              ) : (
                <Button variant="ghost" size="icon" onClick={handleBackClick} className="h-6 w-6" title="Return to parent directory">
                  <ChevronLeftIcon className="h-4 w-4" />
                </Button>
              )}
              <CardTitle className="text-base">File: {data?.type === 'workspace' ? data.path : ''}</CardTitle>
            </div>
            <Button onClick={handleDownload} variant="outline" size="sm" disabled={isDownloading} title="Download file">
              {isDownloading ? (
                <>
                  <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
                  Downloading...
                </>
              ) : (
                <>
                  <DownloadIcon className="mr-2 h-4 w-4" />
                  Download
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="overflow-hidden rounded-md border">
            {workspace instanceof Blob &&
            (workspace.type.includes('image') || (data?.type === 'workspace' && data.path?.match(/\.(jpg|jpeg|png|gif|bmp|svg|webp)$/i))) ? (
              <img
                src={URL.createObjectURL(workspace)}
                alt={data?.type === 'workspace' ? data.path || 'File preview' : 'File preview'}
                className="h-auto w-full object-contain"
              />
            ) : workspace instanceof Blob ? (
              <FileContent blob={workspace} path={data?.type === 'workspace' ? data.path : ''} />
            ) : (
              <div className="text-muted-foreground p-4 text-center">This file type cannot be previewed</div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

const FileContent = ({ blob, path }: { blob: Blob; path: string }) => {
  const [isDownloading, setIsDownloading] = useState(false);

  const { data: content, isLoading } = useAsync(
    async () => {
      return await blob.text();
    },
    [],
    { deps: [blob] },
  );

  // File download function
  const handleDownload = () => {
    setIsDownloading(true);
    try {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = path.split('/').pop() || 'download';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } catch (error) {
      console.error('Download error:', error);
    } finally {
      // Add a small delay to show loading state
      setTimeout(() => {
        setIsDownloading(false);
      }, 1000);
    }
  };

  if (isLoading) {
    return (
      <div className="flex h-40 items-center justify-center">
        <LoaderIcon className="text-primary h-5 w-5 animate-spin" />
      </div>
    );
  }

  if (!content) {
    return <div className="text-muted-foreground p-4 text-center">Could not load file content</div>;
  }

  const hasBinaryControlCharacters = [...content.substring(0, 1000)].some(char => {
    const code = char.charCodeAt(0);
    return (code >= 0 && code <= 8) || (code >= 14 && code <= 31);
  });

  // For binary files or very large files, show a simplified view
  if (content.length > 100000 || hasBinaryControlCharacters) {
    return (
      <div className="p-4 text-center">
        <p className="text-muted-foreground mb-2">File is too large or contains binary content</p>
        <Button onClick={handleDownload} disabled={isDownloading}>
          {isDownloading ? (
            <>
              <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
              Downloading...
            </>
          ) : (
            'Download'
          )}
        </Button>
      </div>
    );
  }

  const language = getFileLanguage(path);
  return (
    <SyntaxHighlighter
      language={language}
      showLineNumbers
      style={githubGist}
      customStyle={{
        fontSize: '0.875rem',
        lineHeight: '1.5',
        margin: 0,
        borderRadius: 0,
        maxHeight: '500px',
      }}
    >
      {content}
    </SyntaxHighlighter>
  );
};

// Format file size helper function
const formatFileSize = (size: number): string => {
  if (size < 1024) return `${size} B`;
  const kbSize = size / 1024;
  if (kbSize < 1024) return `${Math.round(kbSize)} KB`;
  const mbSize = kbSize / 1024;
  return `${mbSize.toFixed(1)} MB`;
};

const getFileLanguage = (path: string): string => {
  const ext = path.split('.').pop()?.toLowerCase();
  const languageMap: Record<string, string> = {
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    py: 'python',
    java: 'java',
    c: 'c',
    cpp: 'cpp',
    cs: 'csharp',
    go: 'go',
    rb: 'ruby',
    php: 'php',
    swift: 'swift',
    kt: 'kotlin',
    rs: 'rust',
    sh: 'bash',
    bash: 'bash',
    zsh: 'bash',
    html: 'html',
    css: 'css',
    scss: 'scss',
    less: 'less',
    json: 'json',
    yaml: 'yaml',
    yml: 'yaml',
    xml: 'xml',
    sql: 'sql',
    md: 'markdown',
    txt: 'text',
    log: 'text',
    ini: 'ini',
    toml: 'toml',
    conf: 'conf',
    env: 'env',
    dockerfile: 'dockerfile',
    'docker-compose': 'yaml',
    csv: 'csv',
  };
  return languageMap[ext || ''] || 'text';
};
