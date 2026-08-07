import { Button } from '@/components/ui/button';
import { useAsync } from '@/hooks/use-async';
import { usePreviewData } from '../store';
import {
  ChevronLeftIcon,
  DownloadIcon,
  FileIcon,
  FolderIcon,
  HomeIcon,
  LoaderIcon,
} from 'lucide-react';
import { useEffect, useState } from 'react';
import SyntaxHighlighter from 'react-syntax-highlighter';
import { githubGist } from 'react-syntax-highlighter/dist/esm/styles/hljs';

// ---------------------------------------------------------------------------
// Workspace browser (directory listing + file viewer)
// ---------------------------------------------------------------------------

export const WorkspacePanel = () => {
  const { data, setData } = usePreviewData();
  const [isDownloading, setIsDownloading] = useState(false);

  const workspacePath = normalizeWorkspacePath(data?.type === 'workspace' ? data.path : '');
  const isRootDirectory = !workspacePath || workspacePath.split('/').length <= 1;

  const handleBackClick = () => {
    if (isRootDirectory) return;
    const parts = workspacePath.split('/');
    parts.pop();
    setData({ type: 'workspace', path: parts.join('/') });
  };

  const handleItemClick = (item: { name: string; type: 'file' | 'directory' }) => {
    setData({ type: 'workspace', path: `${workspacePath}/${item.name}` });
  };

  const handleDownload = () => {
    if (data?.type !== 'workspace') return;
    setIsDownloading(true);
    try {
      const a = document.createElement('a');
      a.href = `/api/workspace/download/${encodeWorkspacePath(workspacePath)}`;
      // No download attribute: the server sends Content-Disposition: attachment,
      // and its filename is the authoritative one — a directory downloads as
      // "<name>.zip", which a client-side name would clobber.
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } finally {
      setTimeout(() => setIsDownloading(false), 1000);
    }
  };

  const { data: workspace, isLoading } = useAsync(
    async () => {
      if (data?.type !== 'workspace') return;
      const res = await fetch(`/api/workspace/${encodeWorkspacePath(workspacePath)}`);
      if (!res.ok) return;
      if (res.headers.get('content-type')?.includes('application/json')) {
        return (await res.json()) as {
          name: string;
          type: 'file' | 'directory';
          size: number;
          modifiedTime: string;
        }[];
      }
      return res.blob();
    },
    [],
    { deps: [workspacePath, data?.type] },
  );

  if (isLoading) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="flex flex-col items-center gap-2">
          <LoaderIcon className="text-primary h-5 w-5 animate-spin" />
          <span className="text-muted-foreground text-sm">Loading workspace…</span>
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

  // Directory listing
  if (Array.isArray(workspace)) {
    return (
      <div className="h-full min-h-0 overflow-auto">
        <section>
          <header className="bg-card sticky top-0 z-10 pb-2">
            <div className="flex items-center justify-between">
              <div className="flex min-w-0 items-center gap-2">
                {isRootDirectory ? (
                  <HomeIcon className="text-muted-foreground h-4 w-4 flex-none" />
                ) : (
                  <Button variant="ghost" size="icon" onClick={handleBackClick} className="h-6 w-6 flex-none">
                    <ChevronLeftIcon className="h-4 w-4" />
                  </Button>
                )}
                <h3 className="truncate text-sm font-semibold">
                  {workspacePath || 'Root Directory'}
                </h3>
              </div>
              <DownloadButton isDownloading={isDownloading} onDownload={handleDownload} />
            </div>
          </header>
          <div>
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
                    <div className="flex min-w-0 items-center gap-2">
                      {item.type === 'directory' ? (
                        <FolderIcon className="text-activity-browser h-4 w-4 flex-none" />
                      ) : (
                        <FileIcon className="text-muted-foreground h-4 w-4 flex-none" />
                      )}
                      <span className="truncate text-sm font-medium">{item.name}</span>
                    </div>
                    <div className="flex flex-none items-center gap-4">
                      <span className="text-muted-foreground text-xs tabular-nums">
                        {formatFileSize(item.size)}
                      </span>
                      <span className="text-muted-foreground text-xs">
                        {new Date(item.modifiedTime).toLocaleDateString()}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>
      </div>
    );
  }

  // File viewer
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex flex-none items-center justify-between pb-2">
        <div className="flex min-w-0 items-center gap-2">
          {isRootDirectory ? (
            <HomeIcon className="text-muted-foreground h-4 w-4 flex-none" />
          ) : (
            <Button variant="ghost" size="icon" onClick={handleBackClick} className="h-6 w-6 flex-none">
              <ChevronLeftIcon className="h-4 w-4" />
            </Button>
          )}
          <h3 className="truncate text-sm font-semibold">{workspacePath}</h3>
        </div>
        <DownloadButton isDownloading={isDownloading} onDownload={handleDownload} />
      </header>
      <div className="min-h-0 flex-1 overflow-auto rounded-lg border">
        {!(workspace instanceof Blob) ? (
          <div className="text-muted-foreground p-4 text-center">
            This file type cannot be previewed
          </div>
        ) : isPdf(workspace, workspacePath) ? (
          <PdfPreview blob={workspace} />
        ) : isImage(workspace, workspacePath) ? (
          <ImagePreview blob={workspace} alt={workspacePath || 'File preview'} />
        ) : (
          <FileContent blob={workspace} path={workspacePath} />
        )}
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Changes panel (file diff cards)
// ---------------------------------------------------------------------------

export const ChangesPanel = ({ messages }: { messages: import('@/libs/chat-messages/types').Message[] }) => {
  const changes = messages.filter(m => m.type === 'agent:lifecycle:step:act:tool:file:updated');
  const completions = messages.filter(m => m.type === 'agent:lifecycle:complete');
  const uniqueFiles = Array.from(
    new Set(changes.map(m => String(m.content.path || '')).filter(Boolean)),
  );
  const totalAdded = changes.reduce((sum, m) => sum + Number(m.content?.added_lines || 0), 0);
  const totalDeleted = changes.reduce((sum, m) => sum + Number(m.content?.deleted_lines || 0), 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <section className="flex h-full min-h-0 flex-col overflow-hidden">
        <header className="flex-none pb-2">
          <h3 className="text-sm font-semibold">Changes</h3>
        </header>
        <div className="min-h-0 flex-1 overflow-auto">
          <div className="space-y-2">
            {/* Summary row */}
            {uniqueFiles.length > 0 && (
              <div className="bg-muted/30 rounded-md border p-2">
                <div className="text-sm font-medium">
                  {uniqueFiles.length} file{uniqueFiles.length === 1 ? '' : 's'} changed
                  <span className="text-activity-file ml-2 font-mono">+{totalAdded}</span>
                  <span className="text-activity-error ml-1 font-mono">−{totalDeleted}</span>
                </div>
                <div className="mt-1 space-y-1">
                  {uniqueFiles.slice(0, 8).map(path => (
                    <div key={path} className="font-mono text-xs text-muted-foreground">{path}</div>
                  ))}
                  {uniqueFiles.length > 8 && (
                    <div className="text-xs text-muted-foreground">+{uniqueFiles.length - 8} more files</div>
                  )}
                </div>
              </div>
            )}

            {/* Per-file change cards with inline diff */}
            {changes.map((message, index) => (
              <div key={message.index || index} className="rounded-md border p-2">
                <div className="font-mono text-sm">
                  {String(message.content.path || '')}
                  <span className="text-activity-file ml-2">
                    +{Number(message.content?.added_lines || 0)}
                  </span>
                  <span className="text-activity-error ml-1">
                    −{Number(message.content?.deleted_lines || 0)}
                  </span>
                </div>
                <div className="text-muted-foreground text-xs">{String(message.content.tool || 'tool')}</div>

                {/* Inline diff preview */}
                {Array.isArray(message.content?.diff_preview?.lines) &&
                  message.content.diff_preview.lines.length > 0 && (
                    <details className="mt-2">
                      <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
                        Show diff ({message.content.diff_preview.lines.length} lines)
                      </summary>
                      <pre className="mt-1 overflow-auto rounded-md border font-mono text-xs leading-5">
                        {message.content.diff_preview.lines.map((line: string, li: number) => {
                          const cls = line.startsWith('+')
                            ? 'bg-activity-file-surface text-activity-file'
                            : line.startsWith('-')
                              ? 'bg-activity-error-surface text-activity-error'
                              : line.startsWith('@@')
                                ? 'bg-muted text-muted-foreground'
                                : '';
                          return (
                            <div key={`${message.index || index}-${li}`} className={`px-2 ${cls}`}>
                              {line || ' '}
                            </div>
                          );
                        })}
                      </pre>
                    </details>
                  )}
              </div>
            ))}

            {/* Completion artifacts */}
            {completions.map((message, index) => {
              const workspace = message.content.workspace as {
                pdfs?: string[];
                logs?: string[];
                tex?: string[];
                warning?: string;
              } | undefined;
              if (!workspace) return null;
              return (
                <div key={`completion-${index}`} className="rounded-md border p-2 text-sm">
                  <div className="font-medium">Artifacts</div>
                  {workspace.pdfs?.length ? (
                    <div className="text-muted-foreground">PDF: {workspace.pdfs.join(', ')}</div>
                  ) : null}
                  {workspace.logs?.length ? (
                    <div className="text-muted-foreground">Logs: {workspace.logs.join(', ')}</div>
                  ) : null}
                  {workspace.warning ? (
                    <div className="text-activity-tool mt-1">{workspace.warning}</div>
                  ) : null}
                </div>
              );
            })}

            {!changes.length && !completions.length && (
              <div className="text-muted-foreground text-sm">No file changes recorded yet.</div>
            )}
          </div>
        </div>
      </section>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

const DownloadButton = ({
  isDownloading,
  onDownload,
}: {
  isDownloading: boolean;
  onDownload: () => void;
}) => (
  <Button onClick={onDownload} variant="outline" size="sm" disabled={isDownloading}>
    {isDownloading ? (
      <>
        <LoaderIcon className="mr-2 h-4 w-4 animate-spin" />
        Downloading…
      </>
    ) : (
      <>
        <DownloadIcon className="mr-2 h-4 w-4" />
        Download
      </>
    )}
  </Button>
);

/**
 * The agent reports absolute paths as seen from inside its sandbox
 * ("/workspace/paper.tex") or from inside the API container
 * ("/app/workspace/paper.tex"). The workspace API is rooted at the workspace
 * itself, so both prefixes have to come off before the path is used in a URL.
 */
const normalizeWorkspacePath = (path: string | undefined): string =>
  (path ?? '').replace(/^\/?(app\/)?workspace\/?/, '').replace(/^\/+/, '');

/** Encode each segment so spaces and "#"/"?" in filenames survive the URL. */
const encodeWorkspacePath = (path: string): string =>
  path.split('/').filter(Boolean).map(encodeURIComponent).join('/');

/** Object URL that is revoked when the blob changes or the panel unmounts. */
const useObjectUrl = (blob: Blob): string | undefined => {
  const [url, setUrl] = useState<string>();
  useEffect(() => {
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);
  return url;
};

const isPdf = (blob: Blob, path: string) =>
  blob.type.includes('pdf') || /\.pdf$/i.test(path);

const isImage = (blob: Blob, path: string) =>
  blob.type.includes('image') || /\.(jpg|jpeg|png|gif|bmp|svg|webp)$/i.test(path);

const PdfPreview = ({ blob }: { blob: Blob }) => {
  const url = useObjectUrl(blob);
  if (!url) return null;
  return (
    <object data={url} type="application/pdf" className="h-[600px] w-full">
      <div className="text-muted-foreground p-4 text-center text-sm">
        This browser cannot display PDFs inline.{' '}
        <a href={url} download className="text-primary underline">
          Download the PDF
        </a>
      </div>
    </object>
  );
};

const ImagePreview = ({ blob, alt }: { blob: Blob; alt: string }) => {
  const url = useObjectUrl(blob);
  if (!url) return null;
  return <img src={url} alt={alt} className="h-auto w-full object-contain" />;
};

const FileContent = ({ blob, path }: { blob: Blob; path: string }) => {
  const { data: content, isLoading } = useAsync(async () => blob.text(), [], { deps: [blob] });

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

  const hasBinary = [...content.substring(0, 1000)].some(char => {
    const code = char.charCodeAt(0);
    return (code >= 0 && code <= 8) || (code >= 14 && code <= 31);
  });

  if (content.length > 100000 || hasBinary) {
    return (
      <div className="p-4 text-center">
        <p className="text-muted-foreground mb-2">File is too large or contains binary content</p>
      </div>
    );
  }

  return (
    <SyntaxHighlighter
      language={getFileLanguage(path)}
      showLineNumbers
      style={githubGist}
      customStyle={{ fontSize: '0.875rem', lineHeight: '1.5', margin: 0, borderRadius: 0, maxHeight: '500px' }}
    >
      {content}
    </SyntaxHighlighter>
  );
};

const formatFileSize = (size: number): string => {
  if (size < 1024) return `${size} B`;
  const kb = size / 1024;
  if (kb < 1024) return `${Math.round(kb)} KB`;
  return `${(kb / 1024).toFixed(1)} MB`;
};

const getFileLanguage = (path: string): string => {
  const ext = path.split('.').pop()?.toLowerCase();
  const map: Record<string, string> = {
    js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
    py: 'python', java: 'java', c: 'c', cpp: 'cpp', cs: 'csharp',
    go: 'go', rb: 'ruby', php: 'php', swift: 'swift', kt: 'kotlin',
    rs: 'rust', sh: 'bash', bash: 'bash', zsh: 'bash',
    html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', yaml: 'yaml', yml: 'yaml', xml: 'xml',
    sql: 'sql', md: 'markdown', txt: 'text', log: 'text',
    ini: 'ini', toml: 'toml', conf: 'conf', env: 'env',
    dockerfile: 'dockerfile', csv: 'csv',
  };
  return map[ext || ''] || 'text';
};
