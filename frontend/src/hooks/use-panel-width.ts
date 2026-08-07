/**
 * Width of a right-hand pane, persisted across sessions.
 *
 * Clamping is done on read as well as on write: the stored width comes from
 * whatever window the user last had open, and a 900px pane restored into a
 * 1024px window would leave nothing for the chat.
 */
import { useCallback, useEffect, useState } from 'react';

type Options = {
  storageKey: string;
  defaultWidth: number;
  /** Smallest useful width for the pane itself. */
  min: number;
  /** Space that must remain for everything to the left of the pane. */
  minRemaining: number;
};

const clamp = (width: number, { min, minRemaining }: Options) => {
  const available = typeof window === 'undefined' ? Infinity : window.innerWidth - minRemaining;
  return Math.round(Math.max(min, Math.min(width, Math.max(min, available))));
};

export const usePanelWidth = (options: Options) => {
  const { storageKey, defaultWidth } = options;

  const [width, setWidth] = useState(() => {
    const stored = Number(localStorage.getItem(storageKey));
    return clamp(Number.isFinite(stored) && stored > 0 ? stored : defaultWidth, options);
  });

  useEffect(() => {
    localStorage.setItem(storageKey, String(width));
  }, [storageKey, width]);

  // A window that shrinks below the stored width must not squeeze the pane
  // beside it out of existence.
  useEffect(() => {
    const onResize = () => setWidth(current => clamp(current, options));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options.min, options.minRemaining]);

  /** The pane is flush to the right edge, so its width is the gap to it. */
  const dragTo = useCallback(
    (clientX: number) => setWidth(clamp(window.innerWidth - clientX, options)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [options.min, options.minRemaining],
  );

  const nudge = useCallback(
    (deltaPx: number) => setWidth(current => clamp(current - deltaPx, options)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [options.min, options.minRemaining],
  );

  const reset = useCallback(
    () => setWidth(clamp(defaultWidth, options)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [defaultWidth, options.min, options.minRemaining],
  );

  return { width, dragTo, nudge, reset };
};
