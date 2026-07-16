import { Clock3 } from 'lucide-react';
import { useEffect, useMemo, useState } from 'react';

type TimeValue = Date | string | number | null | undefined;

const toTimestamp = (value: TimeValue) => {
  if (value instanceof Date) return value.getTime();
  if (typeof value === 'number') return value;
  if (typeof value === 'string') return new Date(value).getTime();
  return Number.NaN;
};

const formatElapsedTime = (durationMs: number) => {
  const totalSeconds = Math.max(0, Math.floor(durationMs / 1000));
  const seconds = totalSeconds % 60;
  const totalMinutes = Math.floor(totalSeconds / 60);
  const minutes = totalMinutes % 60;
  const hours = Math.floor(totalMinutes / 60);

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
};

export function ElapsedTime({
  startedAt,
  finishedAt,
  running,
  runningLabel = 'Working',
  finishedLabel = 'Finished in',
  compactOnMobile = false,
  className = '',
}: {
  startedAt: TimeValue;
  finishedAt?: TimeValue;
  running: boolean;
  runningLabel?: string;
  finishedLabel?: string;
  compactOnMobile?: boolean;
  className?: string;
}) {
  const start = toTimestamp(startedAt);
  const finish = toTimestamp(finishedAt);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!running || Number.isFinite(finish)) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [finish, running, start]);

  const elapsed = useMemo(() => {
    if (!Number.isFinite(start)) return null;
    const endpoint = Number.isFinite(finish) ? finish : now;
    return formatElapsedTime(Math.max(0, endpoint - start));
  }, [finish, now, start]);

  if (!elapsed) return null;
  const label = running && !Number.isFinite(finish) ? runningLabel : finishedLabel;

  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap text-xs tabular-nums text-muted-foreground ${className}`}
      aria-label={`${label} ${elapsed}`}
      title={`${label} ${elapsed}`}
    >
      <Clock3 className="h-3.5 w-3.5 shrink-0" />
      <span className={compactOnMobile ? 'hidden sm:inline' : undefined}>{label}</span>
      <span className="font-mono">{elapsed}</span>
    </span>
  );
}
