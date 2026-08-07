/**
 * TimelinePanel — the agent's work as a scrubbable filmstrip.
 *
 * The panel follows live work by default. Touching the strip pins a moment and
 * stops the follow, so you can read something that already scrolled past
 * without the agent yanking the view away; "Live" resumes.
 */
import { ACTIVITY } from '@/libs/activity';
import { deriveMoments, shortPath, type Moment, type PlanStep } from '@/libs/moments';
import { getImageUrl } from '@/libs/image';
import { cn } from '@/libs/utils';
import { CircleDotIcon, RadioIcon } from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Message } from '@/libs/chat-messages/types';

export const TimelinePanel = ({
  messages,
  isRunning = false,
}: {
  messages: Message[];
  isRunning?: boolean;
}) => {
  const moments = useMemo(() => deriveMoments(messages), [messages]);
  const [pinnedId, setPinnedId] = useState<string | null>(null);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const stripRef = useRef<HTMLDivElement>(null);

  const isLive = pinnedId === null;
  const selected = isLive
    ? moments[moments.length - 1]
    : (moments.find(moment => moment.id === pinnedId) ?? moments[moments.length - 1]);

  // Follow the newest moment while live, but never fight the user mid-drag.
  useEffect(() => {
    if (!isLive || isScrubbing) return;
    const strip = stripRef.current;
    if (strip) strip.scrollLeft = strip.scrollWidth;
  }, [isLive, isScrubbing, moments.length]);

  const selectFromPoint = useCallback((clientX: number, clientY: number) => {
    const element = document.elementFromPoint(clientX, clientY);
    const thumb = element?.closest('[data-moment-id]');
    const id = thumb?.getAttribute('data-moment-id');
    if (id) setPinnedId(id);
  }, []);

  // Drag anywhere across the strip to scrub. Listening on the window means the
  // pointer can leave the strip vertically without dropping the gesture.
  useEffect(() => {
    if (!isScrubbing) return;
    const onMove = (event: PointerEvent) => selectFromPoint(event.clientX, event.clientY);
    const onUp = () => setIsScrubbing(false);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
  }, [isScrubbing, selectFromPoint]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const current = selected ? moments.findIndex(moment => moment.id === selected.id) : -1;
    const next = Math.min(
      moments.length - 1,
      Math.max(0, (current < 0 ? moments.length - 1 : current) + (event.key === 'ArrowRight' ? 1 : -1)),
    );
    const target = moments[next];
    if (!target) return;
    setPinnedId(next === moments.length - 1 ? null : target.id);
    stripRef.current
      ?.querySelector(`[data-moment-id="${CSS.escape(target.id)}"]`)
      ?.scrollIntoView({ block: 'nearest', inline: 'center' });
  };

  if (!moments.length) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-2 text-center">
        <CircleDotIcon className="text-muted-foreground/50 h-7 w-7" />
        <p className="text-sm font-medium">Nothing captured yet</p>
        <p className="text-muted-foreground max-w-[32ch] text-xs">
          Screenshots, file writes and command output appear here as the agent works.
        </p>
      </div>
    );
  }

  return (
    // min-w-0 all the way down: grid and flex children default to
    // min-width:auto, so one long unbroken path or URL widens the whole panel
    // and the overflow gets clipped rather than scrolled.
    <div className="grid h-full min-h-0 min-w-0 grid-rows-[1fr_auto] gap-2">
      <div className="min-h-0 min-w-0 overflow-auto">
        {selected && <MomentDetail key={selected.id} moment={selected} isRunning={isRunning} />}
      </div>

      <div className="min-w-0 flex-none space-y-1.5">
        <div className="flex items-center justify-between gap-2 px-0.5">
          <span className="text-muted-foreground text-[11px]">
            {isLive ? 'Following live' : 'Drag to scrub · ← → to step'}
          </span>
          <button
            onClick={() => setPinnedId(null)}
            disabled={isLive}
            className={cn(
              'inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium transition-colors',
              isLive
                ? 'border-brand/40 bg-brand/10 text-brand'
                : 'hover:bg-accent hover:text-accent-foreground',
            )}
          >
            <RadioIcon className={cn('h-3 w-3', isLive && isRunning && 'live-dot')} />
            Live
          </button>
        </div>

        <div
          ref={stripRef}
          role="listbox"
          tabIndex={0}
          aria-label="Work timeline"
          onKeyDown={onKeyDown}
          onPointerDown={event => {
            setIsScrubbing(true);
            selectFromPoint(event.clientX, event.clientY);
          }}
          className="bg-muted/40 flex gap-1.5 overflow-x-auto rounded-lg border p-1.5 select-none"
        >
          {moments.map(moment => (
            <MomentThumb
              key={moment.id}
              moment={moment}
              isSelected={selected?.id === moment.id}
            />
          ))}
        </div>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Filmstrip thumbnail
// ---------------------------------------------------------------------------

const MomentThumb = ({ moment, isSelected }: { moment: Moment; isSelected: boolean }) => {
  const style = ACTIVITY[moment.kind];
  const Icon = style.icon;

  return (
    <button
      data-moment-id={moment.id}
      role="option"
      aria-selected={isSelected}
      title={`${moment.title}${moment.detail ? ` — ${moment.detail}` : ''}`}
      className={cn(
        'relative h-14 w-[4.5rem] shrink-0 overflow-hidden rounded-md border text-left transition-[box-shadow,transform]',
        style.surface,
        isSelected ? 'ring-brand ring-2 ring-offset-1 ring-offset-background' : style.border,
      )}
    >
      <ThumbBody moment={moment} />
      {/* Kind rail — readable even when the body is a screenshot. */}
      <span className={cn('absolute inset-y-0 left-0 w-[3px]', style.solid)} />
      <span className="absolute right-0.5 bottom-0.5 rounded-sm bg-background/80 p-[2px]">
        <Icon className={cn('h-2.5 w-2.5', style.text)} />
      </span>
      {moment.running && (
        <span className="bg-brand live-dot absolute top-1 right-1 h-1.5 w-1.5 rounded-full" />
      )}
    </button>
  );
};

const ThumbBody = ({ moment }: { moment: Moment }) => {
  const payload = moment.payload;

  if (payload.type === 'screenshot') {
    return (
      <img
        src={getImageUrl(payload.screenshot)}
        alt=""
        loading="lazy"
        // A capture that no longer resolves should leave the tinted tile and
        // its kind icon, not a broken-image glyph.
        onError={event => {
          event.currentTarget.style.visibility = 'hidden';
        }}
        className="h-full w-full object-cover object-top"
      />
    );
  }

  if (payload.type === 'diff') {
    return (
      <div className="flex h-full flex-col justify-center gap-0.5 pl-2.5 pr-1">
        <div className="truncate text-[9px] leading-tight font-medium">
          {shortPath(payload.path)}
        </div>
        <div className="font-mono text-[9px] leading-tight">
          <span className="text-activity-file">+{payload.added}</span>{' '}
          <span className="text-activity-error">−{payload.deleted}</span>
        </div>
      </div>
    );
  }

  const terminalText =
    payload.type === 'terminal'
      ? payload.text
      : payload.type === 'tool'
        ? payload.output
        : undefined;

  if (terminalText !== undefined) {
    const tail = terminalText.trimEnd().split('\n').slice(-3);
    return (
      <div className="h-full overflow-hidden py-1 pr-1 pl-2.5 font-mono text-[8px] leading-[1.35] opacity-80">
        {tail.map((line, index) => (
          <div key={index} className="truncate">
            {line}
          </div>
        ))}
      </div>
    );
  }

  if (payload.type === 'plan') {
    return (
      <div className="flex h-full flex-col justify-center gap-1 pl-2.5 pr-1.5">
        <div className="text-[9px] leading-tight font-medium">
          {payload.progress.completed}/{payload.progress.total} steps
        </div>
        <div className="bg-background/70 h-1 overflow-hidden rounded-full">
          <div
            className="bg-activity-think h-full rounded-full"
            style={{ width: `${payload.progress.pct}%` }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full items-center pl-2.5 pr-1.5">
      <span className="line-clamp-3 text-[9px] leading-tight font-medium">{moment.title}</span>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Detail pane
// ---------------------------------------------------------------------------

const MomentDetail = ({ moment, isRunning }: { moment: Moment; isRunning: boolean }) => {
  const style = ACTIVITY[moment.kind];
  const Icon = style.icon;

  return (
    <div className="moment-in flex h-full min-h-0 min-w-0 flex-col">
      <header className="flex min-w-0 flex-none items-center gap-2 pb-2">
        <span className={cn('rounded-md border p-1.5', style.surface, style.border)}>
          <Icon className={cn('h-3.5 w-3.5', style.text)} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold">{moment.title}</div>
          {moment.detail && (
            <div className="text-muted-foreground truncate text-xs">{moment.detail}</div>
          )}
        </div>
        {moment.at && (
          <time className="text-muted-foreground flex-none text-[11px] tabular-nums">
            {moment.at.toLocaleTimeString()}
          </time>
        )}
      </header>
      {/* Media fills the pane; text bodies scroll from the top. Without this
          a short note or a single screenshot left most of the panel blank. */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto">
        <MomentBody moment={moment} isRunning={isRunning} />
      </div>
    </div>
  );
};

const MomentBody = ({ moment, isRunning }: { moment: Moment; isRunning: boolean }) => {
  const payload = moment.payload;

  if (payload.type === 'screenshot') {
    return (
      <figure className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border">
        <figcaption className="bg-muted/50 flex flex-none items-center gap-2 border-b px-3 py-1.5">
          <span className="flex gap-1">
            <span className="bg-activity-error/60 h-2 w-2 rounded-full" />
            <span className="bg-activity-tool/60 h-2 w-2 rounded-full" />
            <span className="bg-activity-file/60 h-2 w-2 rounded-full" />
          </span>
          <span className="text-muted-foreground truncate text-[11px]">
            {payload.url || payload.title || 'Browser'}
          </span>
        </figcaption>
        <ScreenshotImage src={getImageUrl(payload.screenshot)} />
      </figure>
    );
  }

  if (payload.type === 'diff') {
    return <DiffBody payload={payload} isWriting={isRunning && moment.running} />;
  }

  if (payload.type === 'terminal') {
    return <TerminalBody text={payload.text} name={payload.name} isRunning={moment.running} />;
  }

  if (payload.type === 'plan') {
    return <PlanBody steps={payload.steps} />;
  }

  if (payload.type === 'tool') {
    return <ToolBody payload={payload} />;
  }

  return (
    <p
      className={cn(
        // break-words so an unbroken URL or token wraps instead of widening
        // the pane and getting clipped by the panel's overflow-hidden.
        'min-w-0 rounded-lg border p-3 text-sm break-words',
        payload.tone === 'error'
          ? 'border-activity-error-border bg-activity-error-surface text-activity-error'
          : 'bg-muted/40',
      )}
    >
      {payload.text}
    </p>
  );
};

/** Captures are fetched on demand, so failure is a normal state to render. */
const ScreenshotImage = ({ src }: { src: string }) => {
  const [failed, setFailed] = useState(false);

  if (failed || !src) {
    return (
      <p className="text-muted-foreground flex flex-1 items-center justify-center p-6 text-center text-xs">
        This capture is no longer available.
      </p>
    );
  }
  return (
    <img
      src={src}
      alt="Page capture"
      onError={() => setFailed(true)}
      // Contain rather than stretch: a tall page capture should still show its
      // top, and a short one should not be blown up past its real resolution.
      className="min-h-0 w-full flex-1 bg-neutral-50 object-contain object-top dark:bg-neutral-900"
    />
  );
};

const DiffBody = ({
  payload,
  isWriting,
}: {
  payload: Extract<Moment['payload'], { type: 'diff' }>;
  isWriting: boolean;
}) => {
  if (!payload.lines.length) {
    return (
      <div className="text-muted-foreground rounded-lg border p-3 text-sm">
        {payload.path} changed, but no diff preview was captured.
      </div>
    );
  }
  const lastAdded = payload.lines.reduce(
    (last, line, index) => (line.startsWith('+') ? index : last),
    -1,
  );

  return (
    <pre className="overflow-x-auto rounded-lg border font-mono text-xs leading-5">
      {payload.lines.map((line, index) => (
        <div
          key={index}
          className={cn(
            'px-3',
            line.startsWith('+') && 'bg-activity-file-surface text-activity-file',
            line.startsWith('-') && 'bg-activity-error-surface text-activity-error',
            line.startsWith('@@') && 'bg-muted text-muted-foreground',
            isWriting && index === lastAdded && 'writing-caret',
          )}
        >
          {line || ' '}
        </div>
      ))}
    </pre>
  );
};

const TerminalBody = ({
  text,
  name,
  command,
  isRunning,
}: {
  text: string;
  name?: string;
  command?: string;
  isRunning: boolean;
}) => (
  // A terminal reads as a terminal when it owns its box, so this fills the pane.
  <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border bg-neutral-950 text-neutral-100">
    <div className="flex flex-none items-center justify-between border-b border-neutral-800 px-3 py-1.5">
      <span className="font-mono text-[11px] text-neutral-400">
        {name === 'python_execute' ? 'python' : name || 'terminal'}
      </span>
      {isRunning && <span className="live-dot text-[11px] text-emerald-400">running</span>}
    </div>
    <div className="min-h-0 flex-1 overflow-auto p-3 font-mono text-xs leading-5">
      {command && (
        <pre className="mb-2 whitespace-pre-wrap text-emerald-300">
          <span className="text-neutral-500">$ </span>
          {command}
        </pre>
      )}
      <pre className="whitespace-pre-wrap">{text || 'No output yet.'}</pre>
    </div>
  </div>
);

const STEP_MARK: Record<string, string> = {
  not_started: '○',
  in_progress: '→',
  completed: '✓',
  blocked: '!',
};

const STEP_TONE: Record<string, string> = {
  not_started: 'text-muted-foreground',
  in_progress: 'text-activity-tool',
  completed: 'text-activity-file',
  blocked: 'text-activity-error',
};

const PlanBody = ({ steps }: { steps: PlanStep[] }) => (
  <ol className="space-y-1">
    {steps.map(step => (
      <li
        key={step.index}
        className={cn(
          'flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs',
          step.active
            ? 'border-activity-think-border bg-activity-think-surface'
            : 'border-transparent',
        )}
      >
        <span className={cn('mt-px font-mono font-bold', STEP_TONE[step.status])}>
          {STEP_MARK[step.status] ?? '○'}
        </span>
        <div className="min-w-0 flex-1">
          <div className={step.status === 'completed' ? 'text-muted-foreground line-through' : ''}>
            {step.text}
          </div>
          {step.notes && <div className="text-muted-foreground mt-0.5">{step.notes}</div>}
        </div>
      </li>
    ))}
  </ol>
);

/** The command a terminal-shaped tool was asked to run. */
const commandOf = (payload: Extract<Moment['payload'], { type: 'tool' }>): string => {
  const args =
    typeof payload.args === 'string'
      ? (() => {
          try {
            return JSON.parse(payload.args);
          } catch {
            return null;
          }
        })()
      : payload.args;
  if (!args || typeof args !== 'object') return '';
  const record = args as Record<string, unknown>;
  return String(record.command ?? record.code ?? '');
};

const ToolBody = ({ payload }: { payload: Extract<Moment['payload'], { type: 'tool' }> }) => {
  const args =
    typeof payload.args === 'string' ? payload.args : JSON.stringify(payload.args ?? {}, null, 2);

  // A command that streamed output is a terminal session, not a JSON blob.
  if (payload.output !== undefined) {
    return (
      <TerminalBody
        text={payload.output}
        name={payload.name}
        command={commandOf(payload)}
        isRunning={payload.running}
      />
    );
  }

  return (
    <div className="space-y-3">
      {args && args !== '{}' && (
        <section className="space-y-1">
          <h4 className="text-muted-foreground text-[11px] font-medium">Parameters</h4>
          <pre className="bg-muted/40 max-h-56 overflow-auto rounded-lg border p-3 font-mono text-xs leading-5 whitespace-pre-wrap">
            {args}
          </pre>
        </section>
      )}
      {payload.result != null && (
        <section className="space-y-1">
          <h4 className="text-muted-foreground text-[11px] font-medium">Result</h4>
          <pre className="bg-muted/40 overflow-auto rounded-lg border p-3 font-mono text-xs leading-5 whitespace-pre-wrap">
            {String(payload.result)}
          </pre>
        </section>
      )}
      {payload.running && (
        <div className="text-muted-foreground flex items-center gap-2 rounded-lg border p-3 text-sm">
          <span className="bg-brand live-dot h-2 w-2 rounded-full" />
          Waiting for the tool to finish…
        </div>
      )}
    </div>
  );
};
