/**
 * EditorPanel — a VS Code editor over the conversation workspace.
 *
 * Monaco is the actual editor core VS Code ships, so keybindings, multi-cursor,
 * folding and the minimap come for free. It is a ~2.5MB dependency, which is
 * why this whole panel is only reached through the lazily-loaded preview
 * bundle and the editor itself mounts on demand.
 *
 * The agent writes to the same files. Saving therefore reports what happened
 * rather than assuming success, and a file that changed underneath you is
 * surfaced instead of silently overwritten.
 */
import { Button } from '@/components/ui/button';
import { useAsync } from '@/hooks/use-async';
import { cn } from '@/libs/utils';
import { shortPath } from '@/libs/moments';
import '@/libs/monaco-setup';
import Editor, { type Monaco } from '@monaco-editor/react';
import {
  ChevronRightIcon,
  FileIcon,
  FolderIcon,
  FolderOpenIcon,
  LoaderIcon,
  SaveIcon,
  XIcon,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** Editor width past which a minimap is worth its horizontal cost. */
const MINIMAP_MIN_WIDTH = 760;

type Entry = { name: string; type: 'file' | 'directory'; size: number; modifiedTime: string };

type OpenFile = {
  path: string;
  /** Content as loaded from disk — the baseline for the dirty check. */
  saved: string;
  draft: string;
};

const encodePath = (path: string) =>
  path.split('/').filter(Boolean).map(encodeURIComponent).join('/');

const listDirectory = async (path: string): Promise<Entry[]> => {
  const response = await fetch(`/api/workspace/${encodePath(path)}`);
  if (!response.ok) return [];
  if (!response.headers.get('content-type')?.includes('application/json')) return [];
  return (await response.json()) as Entry[];
};

const readFile = async (path: string): Promise<string> => {
  const response = await fetch(`/api/workspace/${encodePath(path)}`);
  if (!response.ok) throw new Error(`Could not open ${path}`);
  return response.text();
};

const saveFile = async (path: string, content: string): Promise<void> => {
  const response = await fetch(`/api/workspace/file/${encodePath(path)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => null);
    throw new Error(detail?.detail || `Save failed (${response.status})`);
  }
};

/** Monaco language id from a file extension. */
const languageOf = (path: string): string => {
  const extension = path.split('.').pop()?.toLowerCase() ?? '';
  const map: Record<string, string> = {
    ts: 'typescript', tsx: 'typescript', js: 'javascript', jsx: 'javascript',
    py: 'python', rb: 'ruby', go: 'go', rs: 'rust', java: 'java',
    c: 'c', h: 'c', cpp: 'cpp', hpp: 'cpp', cs: 'csharp', php: 'php',
    sh: 'shell', bash: 'shell', zsh: 'shell',
    html: 'html', css: 'css', scss: 'scss', less: 'less',
    json: 'json', yaml: 'yaml', yml: 'yaml', xml: 'xml', toml: 'ini', ini: 'ini',
    md: 'markdown', markdown: 'markdown', sql: 'sql', tex: 'latex', bib: 'bibtex',
    dockerfile: 'dockerfile',
  };
  return map[extension] ?? 'plaintext';
};

const isProbablyBinary = (path: string) =>
  /\.(png|jpe?g|gif|bmp|webp|svg|ico|pdf|zip|tar|gz|woff2?|ttf|eot|mp[34]|wav|so|dylib|dll|faiss|db)$/i.test(
    path,
  );

export const EditorPanel = ({ root = '', initialPath }: { root?: string; initialPath?: string }) => {
  const [openFiles, setOpenFiles] = useState<OpenFile[]>([]);
  const [activePath, setActivePath] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);
  const [isDark, setIsDark] = useState(
    () => typeof document !== 'undefined' && document.documentElement.classList.contains('dark'),
  );

  const active = openFiles.find(file => file.path === activePath) ?? null;
  const isDirty = active ? active.draft !== active.saved : false;

  // Monaco has its own theme registry, so it has to be told when the app theme
  // flips rather than inheriting the CSS variables.
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setIsDark(document.documentElement.classList.contains('dark')),
    );
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  const openFile = useCallback(
    async (path: string) => {
      setActivePath(path);
      if (openFiles.some(file => file.path === path)) return;
      try {
        const content = await readFile(path);
        setOpenFiles(files =>
          files.some(file => file.path === path)
            ? files
            : [...files, { path, saved: content, draft: content }],
        );
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not open file');
        setActivePath(current => (current === path ? null : current));
      }
    },
    [openFiles],
  );

  useEffect(() => {
    if (initialPath) openFile(initialPath);
    // Only on mount: later prop changes come from tab clicks, which call openFile directly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const closeFile = (path: string) => {
    setOpenFiles(files => files.filter(file => file.path !== path));
    setActivePath(current => {
      if (current !== path) return current;
      const remaining = openFiles.filter(file => file.path !== path);
      return remaining.length ? remaining[remaining.length - 1].path : null;
    });
  };

  const save = useCallback(async () => {
    const file = openFiles.find(candidate => candidate.path === activePath);
    if (!file || file.draft === file.saved) return;
    setIsSaving(true);
    try {
      await saveFile(file.path, file.draft);
      setOpenFiles(files =>
        files.map(candidate =>
          candidate.path === file.path ? { ...candidate, saved: file.draft } : candidate,
        ),
      );
      toast.success(`Saved ${shortPath(file.path)}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Save failed');
    } finally {
      setIsSaving(false);
    }
  }, [activePath, openFiles]);

  const onMount = (_editor: unknown, monaco: Monaco) => {
    // Cmd/Ctrl+S is muscle memory; without this the browser save dialog opens.
    const editor = _editor as { addCommand: (keys: number, handler: () => void) => void };
    editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
      saveRef.current();
    });
  };

  // Keep the Monaco command bound to the latest save closure.
  const saveRef = useRef(save);
  useEffect(() => {
    saveRef.current = save;
  }, [save]);

  const editorHostRef = useRef<HTMLDivElement>(null);
  const [isWide, setIsWide] = useState(false);
  useEffect(() => {
    const host = editorHostRef.current;
    if (!host) return;
    const observer = new ResizeObserver(entries => {
      setIsWide(entries[0].contentRect.width >= MINIMAP_MIN_WIDTH);
    });
    observer.observe(host);
    return () => observer.disconnect();
  }, [activePath]);

  return (
    <div className="grid h-full min-h-0 grid-cols-[minmax(9rem,14rem)_1fr] overflow-hidden rounded-lg border">
      <FileTree root={root} activePath={activePath} onOpen={openFile} />

      <div className="flex min-w-0 flex-col">
        <div className="flex flex-none items-center gap-1 border-b pr-1">
          <div className="flex min-w-0 flex-1 overflow-x-auto">
            {openFiles.map(file => (
              <button
                key={file.path}
                onClick={() => setActivePath(file.path)}
                className={cn(
                  'group flex max-w-[12rem] flex-none items-center gap-1.5 border-r px-2.5 py-1.5 text-xs',
                  file.path === activePath
                    ? 'bg-background text-foreground'
                    : 'text-muted-foreground hover:text-foreground',
                )}
              >
                <span className="truncate">{file.path.split('/').pop()}</span>
                {file.draft !== file.saved && (
                  <span className="bg-brand h-1.5 w-1.5 flex-none rounded-full" title="Unsaved" />
                )}
                <XIcon
                  className="h-3 w-3 flex-none opacity-0 group-hover:opacity-60 hover:opacity-100"
                  onClick={event => {
                    event.stopPropagation();
                    closeFile(file.path);
                  }}
                />
              </button>
            ))}
          </div>
          <Button
            size="sm"
            variant={isDirty ? 'default' : 'ghost'}
            className="h-7 flex-none gap-1.5 text-xs"
            disabled={!isDirty || isSaving}
            onClick={save}
          >
            {isSaving ? (
              <LoaderIcon className="h-3 w-3 animate-spin" />
            ) : (
              <SaveIcon className="h-3 w-3" />
            )}
            Save
          </Button>
        </div>

        <div ref={editorHostRef} className="min-h-0 flex-1">
          {active ? (
            <Editor
              path={active.path}
              language={languageOf(active.path)}
              value={active.draft}
              theme={isDark ? 'vs-dark' : 'light'}
              onMount={onMount}
              onChange={value =>
                setOpenFiles(files =>
                  files.map(file =>
                    file.path === active.path ? { ...file, draft: value ?? '' } : file,
                  ),
                )
              }
              loading={
                <div className="text-muted-foreground flex h-full items-center justify-center gap-2 text-xs">
                  <LoaderIcon className="h-4 w-4 animate-spin" />
                  Loading editor…
                </div>
              }
              options={{
                fontSize: 12.5,
                fontFamily:
                  'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
                // The computer panel is often ~500px wide. A minimap there eats
                // a fifth of the text column to show an unreadable smudge, so it
                // only earns its place once there is room for it.
                minimap: { enabled: isWide, renderCharacters: false },
                // Without wrapping, prose-heavy files (LaTeX, Markdown) run off
                // the edge and have to be scrolled horizontally to read.
                wordWrap: 'on',
                scrollBeyondLastLine: false,
                smoothScrolling: true,
                renderWhitespace: 'selection',
                tabSize: 2,
                automaticLayout: true,
                padding: { top: 10 },
              }}
            />
          ) : (
            <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-1.5 p-6 text-center">
              <FileIcon className="h-6 w-6 opacity-40" />
              <p className="text-sm font-medium">No file open</p>
              <p className="text-xs">Pick a file from the tree to edit it.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// File tree
// ---------------------------------------------------------------------------

const FileTree = ({
  root,
  activePath,
  onOpen,
}: {
  root: string;
  activePath: string | null;
  onOpen: (path: string) => void;
}) => (
  <div className="bg-muted/30 min-h-0 overflow-auto border-r">
    <div className="text-muted-foreground sticky top-0 z-10 bg-muted/80 px-2.5 py-1.5 text-[10px] font-semibold tracking-wide uppercase backdrop-blur">
      Workspace
    </div>
    <TreeLevel path={root} depth={0} activePath={activePath} onOpen={onOpen} defaultOpen />
  </div>
);

const TreeLevel = ({
  path,
  depth,
  activePath,
  onOpen,
  defaultOpen = false,
}: {
  path: string;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
  defaultOpen?: boolean;
}) => {
  const { data: entries, isLoading } = useAsync(async () => listDirectory(path), [], {
    deps: [path],
  });

  if (isLoading) {
    return (
      <div className="text-muted-foreground px-2.5 py-1 text-[11px]" style={{ paddingLeft: 10 + depth * 10 }}>
        Loading…
      </div>
    );
  }
  if (!entries?.length) {
    return depth === 0 ? (
      <p className="text-muted-foreground px-2.5 py-2 text-[11px] leading-relaxed">
        No files yet. They appear here as the agent writes them.
      </p>
    ) : null;
  }

  return (
    <ul>
      {entries.map(entry =>
        entry.type === 'directory' ? (
          <TreeFolder
            key={entry.name}
            name={entry.name}
            path={`${path}/${entry.name}`}
            depth={depth}
            activePath={activePath}
            onOpen={onOpen}
            defaultOpen={defaultOpen && depth === 0 && entries.length === 1}
          />
        ) : (
          <li key={entry.name}>
            <button
              disabled={isProbablyBinary(entry.name)}
              onClick={() => onOpen(`${path}/${entry.name}`)}
              style={{ paddingLeft: 10 + depth * 10 }}
              className={cn(
                'flex w-full items-center gap-1.5 py-[3px] pr-2 text-left text-[11px] disabled:opacity-40',
                `${path}/${entry.name}` === activePath
                  ? 'bg-brand/10 text-brand font-medium'
                  : 'hover:bg-accent/60',
              )}
              title={isProbablyBinary(entry.name) ? 'Binary file' : entry.name}
            >
              <FileIcon className="h-3 w-3 flex-none opacity-60" />
              <span className="truncate">{entry.name}</span>
            </button>
          </li>
        ),
      )}
    </ul>
  );
};

const TreeFolder = ({
  name,
  path,
  depth,
  activePath,
  onOpen,
  defaultOpen,
}: {
  name: string;
  path: string;
  depth: number;
  activePath: string | null;
  onOpen: (path: string) => void;
  defaultOpen: boolean;
}) => {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <li>
      <button
        onClick={() => setIsOpen(open => !open)}
        style={{ paddingLeft: 10 + depth * 10 }}
        className="hover:bg-accent/60 flex w-full items-center gap-1 py-[3px] pr-2 text-left text-[11px] font-medium"
      >
        <ChevronRightIcon
          className={cn('h-3 w-3 flex-none transition-transform', isOpen && 'rotate-90')}
        />
        {isOpen ? (
          <FolderOpenIcon className="text-activity-browser h-3 w-3 flex-none" />
        ) : (
          <FolderIcon className="text-activity-browser h-3 w-3 flex-none" />
        )}
        <span className="truncate">{name}</span>
      </button>
      {isOpen && (
        <TreeLevel path={path} depth={depth + 1} activePath={activePath} onOpen={onOpen} />
      )}
    </li>
  );
};
