/**
 * ScrubBar — media-player transport for the work timeline.
 *
 * A run is a recording: you watch it live, drag back to something that scrolled
 * past, and jump forward again. The bar therefore behaves like a player rather
 * than a list — a filled track up to the selected moment, a draggable knob,
 * step buttons, and a Live indicator that reads as "following" or "behind".
 *
 * Ticks along the track are coloured by activity hue, so the shape of a run is
 * legible before you read anything: a cyan cluster is research, a long emerald
 * stretch is writing, a rose tick is where it went wrong.
 */
import { ACTIVITY } from '@/libs/activity';
import type { Moment } from '@/libs/moments';
import { cn } from '@/libs/utils';
import { PlayIcon, SkipBackIcon, SkipForwardIcon } from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

/** Keep the hover card clear of the panel edges. */
const clampPercent = (percent: number) => Math.max(14, Math.min(86, percent));

export const ScrubBar = ({
  moments,
  selectedIndex,
  isLive,
  isRunning,
  onSelect,
  onGoLive,
  renderPreview,
}: {
  moments: Moment[];
  selectedIndex: number;
  isLive: boolean;
  isRunning: boolean;
  onSelect: (index: number) => void;
  onGoLive: () => void;
  /** Snap preview shown above the bar while hovering or scrubbing. */
  renderPreview: (moment: Moment) => React.ReactNode;
}) => {
  const trackRef = useRef<HTMLDivElement>(null);
  const [isScrubbing, setIsScrubbing] = useState(false);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  const lastIndex = Math.max(0, moments.length - 1);
  /** Fraction of the run that has been played, 0–1. */
  const progress = lastIndex === 0 ? 1 : selectedIndex / lastIndex;

  const indexFromClientX = useCallback(
    (clientX: number) => {
      const track = trackRef.current;
      if (!track || moments.length === 0) return 0;
      const rect = track.getBoundingClientRect();
      const ratio = (clientX - rect.left) / Math.max(1, rect.width);
      return Math.round(Math.max(0, Math.min(1, ratio)) * lastIndex);
    },
    [lastIndex, moments.length],
  );

  useEffect(() => {
    if (!isScrubbing) return;
    const onMove = (event: PointerEvent) => {
      event.preventDefault();
      const index = indexFromClientX(event.clientX);
      setHoverIndex(index);
      onSelect(index);
    };
    const stop = () => {
      setIsScrubbing(false);
      setHoverIndex(null);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);
    const previousSelect = document.body.style.userSelect;
    document.body.style.userSelect = 'none';
    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      document.body.style.userSelect = previousSelect;
    };
  }, [isScrubbing, indexFromClientX, onSelect]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const step = (delta: number) => {
      event.preventDefault();
      onSelect(Math.max(0, Math.min(lastIndex, selectedIndex + delta)));
    };
    if (event.key === 'ArrowLeft') step(-1);
    else if (event.key === 'ArrowRight') step(1);
    else if (event.key === 'Home') step(-selectedIndex);
    else if (event.key === 'End') onGoLive();
  };

  const previewIndex = hoverIndex ?? (isScrubbing ? selectedIndex : null);
  const previewMoment = previewIndex == null ? null : moments[previewIndex];

  return (
    <div className="relative flex-none">
      {/* Snap preview — the filmstrip thumbnail, now surfacing only where you
          are actually pointing rather than as a permanent strip. */}
      {previewMoment && (
        <div
          className="pointer-events-none absolute bottom-full z-20 mb-2 w-40 -translate-x-1/2"
          // Clamped away from the ends so the card stays inside the panel
          // instead of being clipped at the first and last moment.
          style={{ left: `${clampPercent((previewIndex! / Math.max(1, lastIndex)) * 100)}%` }}
        >
          <div className="bg-popover overflow-hidden rounded-lg border shadow-lg">
            {renderPreview(previewMoment)}
            <p className="truncate border-t px-2 py-1 text-[10px] font-medium">
              {previewMoment.title}
            </p>
          </div>
        </div>
      )}

      {/* Jump to live, floating above the bar exactly like a player. */}
      {!isLive && (
        <div className="absolute bottom-full left-1/2 z-10 mb-3 -translate-x-1/2">
          <button
            onClick={onGoLive}
            className="bg-foreground/85 text-background hover:bg-foreground flex items-center gap-1.5 rounded-full px-3 py-1.5 text-xs font-medium shadow-lg backdrop-blur transition-colors"
          >
            <PlayIcon className="h-3 w-3 fill-current" />
            Jump to live
          </button>
        </div>
      )}

      <div className="flex items-center gap-2">
        <TransportButton
          icon={SkipBackIcon}
          label="Previous moment"
          disabled={selectedIndex <= 0}
          onClick={() => onSelect(Math.max(0, selectedIndex - 1))}
        />
        <TransportButton
          icon={SkipForwardIcon}
          label="Next moment"
          disabled={selectedIndex >= lastIndex}
          onClick={() => onSelect(Math.min(lastIndex, selectedIndex + 1))}
        />

        <div
          ref={trackRef}
          role="slider"
          tabIndex={0}
          aria-label="Work timeline"
          aria-valuemin={0}
          aria-valuemax={lastIndex}
          aria-valuenow={selectedIndex}
          aria-valuetext={moments[selectedIndex]?.title}
          onKeyDown={onKeyDown}
          onPointerDown={event => {
            setIsScrubbing(true);
            onSelect(indexFromClientX(event.clientX));
          }}
          onPointerMove={event => !isScrubbing && setHoverIndex(indexFromClientX(event.clientX))}
          onPointerLeave={() => !isScrubbing && setHoverIndex(null)}
          className="group relative h-8 min-w-0 flex-1 cursor-pointer touch-none"
        >
          {/* Track */}
          <span className="bg-muted absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full" />
          {/* Played portion */}
          <span
            className="bg-brand absolute left-0 top-1/2 h-1 -translate-y-1/2 rounded-full"
            style={{ width: `${progress * 100}%` }}
          />

          {/* Per-moment ticks, coloured by what kind of work it was. */}
          {moments.map((moment, index) => (
            <span
              key={moment.id}
              title={moment.title}
              className={cn(
                'absolute top-1/2 h-2.5 w-[3px] -translate-x-1/2 -translate-y-1/2 rounded-full transition-transform',
                ACTIVITY[moment.kind].solid,
                index === selectedIndex ? 'scale-y-[1.6]' : 'opacity-70 group-hover:opacity-100',
              )}
              style={{ left: `${(index / Math.max(1, lastIndex)) * 100}%` }}
            />
          ))}

          {/* Knob */}
          <span
            className={cn(
              'border-background bg-brand absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 shadow transition-transform',
              isScrubbing && 'scale-125',
            )}
            style={{ left: `${progress * 100}%` }}
          />
        </div>

        <button
          onClick={onGoLive}
          disabled={isLive}
          title={isLive ? 'Following live' : 'Jump to the newest moment'}
          className={cn(
            'flex flex-none items-center gap-1.5 rounded-full px-2 py-1 text-[11px] font-medium transition-colors',
            isLive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground',
          )}
        >
          <span
            className={cn(
              'h-1.5 w-1.5 rounded-full',
              isLive ? 'bg-brand' : 'bg-muted-foreground/50',
              isLive && isRunning && 'live-dot',
            )}
          />
          Live
        </button>
      </div>

      <div className="text-muted-foreground mt-0.5 flex items-center justify-between px-0.5 text-[10px] tabular-nums">
        <span>{moments[0]?.at ? moments[0].at.toLocaleTimeString() : ''}</span>
        <span>
          {selectedIndex + 1} / {moments.length}
        </span>
        <span>{moments[lastIndex]?.at ? moments[lastIndex].at!.toLocaleTimeString() : ''}</span>
      </div>
    </div>
  );
};

const TransportButton = ({
  icon: Icon,
  label,
  disabled,
  onClick,
}: {
  icon: typeof PlayIcon;
  label: string;
  disabled: boolean;
  onClick: () => void;
}) => (
  <button
    onClick={onClick}
    disabled={disabled}
    title={label}
    aria-label={label}
    className="text-muted-foreground hover:text-foreground hover:bg-accent flex h-6 w-6 flex-none items-center justify-center rounded transition-colors disabled:pointer-events-none disabled:opacity-30"
  >
    <Icon className="h-3.5 w-3.5" />
  </button>
);
