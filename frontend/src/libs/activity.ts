/**
 * Activity vocabulary — the single place that decides what colour, icon and
 * wording a piece of agent work gets.
 *
 * Colour carries meaning here: a reader should be able to glance at the
 * timeline and know "that was a file write, that was a browser capture"
 * without reading a word. That only holds if every surface derives its colour
 * from this file rather than picking a Tailwind hue locally.
 */
import {
  AlertTriangleIcon,
  BrainIcon,
  FileEditIcon,
  GlobeIcon,
  SquareTerminalIcon,
  WrenchIcon,
  type LucideIcon,
} from 'lucide-react';

export type ActivityKind = 'think' | 'tool' | 'browser' | 'file' | 'terminal' | 'error';

type ActivityStyle = {
  /** Human label for the kind itself, e.g. shown in filters and legends. */
  label: string;
  icon: LucideIcon;
  /** Foreground colour — icons and text. */
  text: string;
  /** Tinted fill for chips, rails and thumbnails. */
  surface: string;
  border: string;
  /** Solid fill for dots and progress, where the hue is the whole signal. */
  solid: string;
};

export const ACTIVITY: Record<ActivityKind, ActivityStyle> = {
  think: {
    label: 'Reasoning',
    icon: BrainIcon,
    text: 'text-activity-think',
    surface: 'bg-activity-think-surface',
    border: 'border-activity-think-border',
    solid: 'bg-activity-think',
  },
  tool: {
    label: 'Tool',
    icon: WrenchIcon,
    text: 'text-activity-tool',
    surface: 'bg-activity-tool-surface',
    border: 'border-activity-tool-border',
    solid: 'bg-activity-tool',
  },
  browser: {
    label: 'Browser',
    icon: GlobeIcon,
    text: 'text-activity-browser',
    surface: 'bg-activity-browser-surface',
    border: 'border-activity-browser-border',
    solid: 'bg-activity-browser',
  },
  file: {
    label: 'File',
    icon: FileEditIcon,
    text: 'text-activity-file',
    surface: 'bg-activity-file-surface',
    border: 'border-activity-file-border',
    solid: 'bg-activity-file',
  },
  terminal: {
    label: 'Terminal',
    icon: SquareTerminalIcon,
    text: 'text-activity-terminal',
    surface: 'bg-activity-terminal-surface',
    border: 'border-activity-terminal-border',
    solid: 'bg-activity-terminal',
  },
  error: {
    label: 'Problem',
    icon: AlertTriangleIcon,
    text: 'text-activity-error',
    surface: 'bg-activity-error-surface',
    border: 'border-activity-error-border',
    solid: 'bg-activity-error',
  },
};

/** Tools whose work is really shell output, so they read as terminal work. */
const TERMINAL_TOOLS = new Set(['bash', 'python_execute']);

/**
 * Classify a lifecycle event type into an activity kind.
 *
 * `toolName` refines tool events: `bash` is terminal work, `browser_use` is
 * browser work, everything else is a generic tool call.
 */
export const activityKindFor = (type: string | undefined, toolName?: string): ActivityKind => {
  const eventType = String(type || '');

  if (eventType.includes(':error') || eventType.endsWith(':terminated')) return 'error';
  if (eventType.includes(':browser:')) return 'browser';
  if (eventType.includes(':terminal:output')) return 'terminal';
  if (eventType.includes(':file:')) return 'file';

  if (eventType.includes(':tool:')) {
    const name = String(toolName || '');
    if (TERMINAL_TOOLS.has(name)) return 'terminal';
    if (name.startsWith('browser')) return 'browser';
    if (name === 'web_search') return 'browser';
    return 'tool';
  }

  return 'think';
};
