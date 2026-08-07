/**
 * ResizeHandle — a draggable divider between two panes.
 *
 * Reports the pointer's x position rather than a delta, so the pane cannot
 * drift away from the cursor over a long drag. The owner converts that to a
 * width and clamps it.
 *
 * Keyboard users get the same control: the handle is a focusable separator
 * that responds to arrow keys, Home and End.
 */
import { cn } from '@/libs/utils';
import { useCallback, useEffect, useState } from 'react';

const KEYBOARD_STEP = 24;

export const ResizeHandle = ({
  onDragTo,
  onNudge,
  onReset,
  value,
  min,
  max,
  label = 'Resize panel',
  className,
}: {
  /** Pointer moved to this viewport x position. */
  onDragTo: (clientX: number) => void;
  /** Keyboard adjustment, in pixels (negative grows the left pane). */
  onNudge: (deltaPx: number) => void;
  /** Double-click / Home key: back to the default width. */
  onReset: () => void;
  value: number;
  min: number;
  max: number;
  label?: string;
  className?: string;
}) => {
  const [isDragging, setIsDragging] = useState(false);

  useEffect(() => {
    if (!isDragging) return;

    const onMove = (event: PointerEvent) => {
      event.preventDefault();
      onDragTo(event.clientX);
    };
    const stop = () => setIsDragging(false);

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', stop);
    window.addEventListener('pointercancel', stop);

    // Keep the resize cursor while the pointer is over other elements, and
    // stop the drag from selecting text across the page.
    const previousCursor = document.body.style.cursor;
    const previousSelect = document.body.style.userSelect;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';

    return () => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
      document.body.style.cursor = previousCursor;
      document.body.style.userSelect = previousSelect;
    };
  }, [isDragging, onDragTo]);

  const onKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === 'ArrowLeft') onNudge(-KEYBOARD_STEP);
      else if (event.key === 'ArrowRight') onNudge(KEYBOARD_STEP);
      else if (event.key === 'Home' || event.key === 'Enter') onReset();
      else return;
      event.preventDefault();
    },
    [onNudge, onReset],
  );

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      tabIndex={0}
      onPointerDown={event => {
        event.preventDefault();
        setIsDragging(true);
      }}
      onDoubleClick={onReset}
      onKeyDown={onKeyDown}
      title="Drag to resize · double-click to reset"
      className={cn(
        'group relative w-1.5 shrink-0 cursor-col-resize touch-none',
        'before:absolute before:inset-y-0 before:-left-1 before:-right-1 before:content-[""]',
        className,
      )}
    >
      {/* The visible line: quiet until you approach it. */}
      <span
        className={cn(
          'pointer-events-none absolute inset-y-0 left-1/2 w-px -translate-x-1/2 transition-colors',
          isDragging ? 'bg-brand' : 'bg-border group-hover:bg-brand/60',
        )}
      />
      {/* Grip dots, so the divider reads as draggable rather than decorative. */}
      <span
        className={cn(
          'pointer-events-none absolute top-1/2 left-1/2 flex h-8 w-3 -translate-x-1/2 -translate-y-1/2',
          'flex-col items-center justify-center gap-[3px] rounded-full transition-opacity',
          isDragging ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus:opacity-100',
        )}
      >
        {[0, 1, 2].map(dot => (
          <span key={dot} className={cn('h-[3px] w-[3px] rounded-full', isDragging ? 'bg-brand' : 'bg-muted-foreground')} />
        ))}
      </span>
    </div>
  );
};
