/**
 * Chat modes — how hard the agent should work on one message.
 *
 * Mirrors CHAT_MODES in app/agent/execution_policy.py. The backend maps these
 * onto execution budgets; the labels here are the only place the user meets
 * them, so they describe the effect rather than the budget.
 */
import { BrainIcon, GaugeIcon, NetworkIcon, ZapIcon, type LucideIcon } from 'lucide-react';

export type ChatMode = 'instant' | 'balanced' | 'thinking' | 'orchestrated';

export const DEFAULT_CHAT_MODE: ChatMode = 'balanced';

export const CHAT_MODES: {
  id: ChatMode;
  label: string;
  description: string;
  icon: LucideIcon;
  /** Activity token used for the mode's accent, so it matches the timeline. */
  accent: string;
}[] = [
  {
    id: 'instant',
    label: 'Instant',
    description: 'Answer quickly. Fewest steps, tightest budget.',
    icon: ZapIcon,
    accent: 'text-activity-tool',
  },
  {
    id: 'balanced',
    label: 'Balanced',
    description: 'The default. Enough room to use tools and check its work.',
    icon: GaugeIcon,
    accent: 'text-brand',
  },
  {
    id: 'thinking',
    label: 'Thinking',
    description: 'Plan first and dig in. Widest budget, slowest.',
    icon: BrainIcon,
    accent: 'text-activity-think',
  },
  {
    id: 'orchestrated',
    label: 'Orchestrated',
    description: 'Split independent work across parallel agents, then combine.',
    icon: NetworkIcon,
    accent: 'text-activity-browser',
  },
];

export const chatMode = (id: string | undefined) =>
  CHAT_MODES.find(mode => mode.id === id) ?? CHAT_MODES[1];

const storageKey = (conversationId: string | undefined) =>
  `openmanus.chatMode.${conversationId || 'default'}`;

/** Mode is remembered per conversation — a research thread and a quick
 *  question want different defaults, and switching back should not surprise. */
export const loadChatMode = (conversationId: string | undefined): ChatMode => {
  const stored = localStorage.getItem(storageKey(conversationId));
  return CHAT_MODES.some(mode => mode.id === stored)
    ? (stored as ChatMode)
    : DEFAULT_CHAT_MODE;
};

export const saveChatMode = (conversationId: string | undefined, mode: ChatMode) => {
  localStorage.setItem(storageKey(conversationId), mode);
};
