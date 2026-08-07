/**
 * Mode picker for the composer.
 *
 * A dropdown rather than a segmented control: each mode changes cost and
 * latency in ways the label alone does not convey, so the descriptions need
 * somewhere to live.
 */
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { CHAT_MODES, chatMode, type ChatMode } from '@/libs/chat-modes';
import { cn } from '@/libs/utils';
import { CheckIcon, ChevronDownIcon } from 'lucide-react';

export const ChatModeSelector = ({
  value,
  onChange,
  disabled,
}: {
  value: ChatMode;
  onChange: (mode: ChatMode) => void;
  disabled?: boolean;
}) => {
  const active = chatMode(value);
  const ActiveIcon = active.icon;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild disabled={disabled}>
        <button
          type="button"
          title="How hard should the agent work on this message?"
          className={cn(
            'hover:bg-accent hover:text-accent-foreground flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors disabled:opacity-50',
            'text-muted-foreground',
          )}
        >
          <ActiveIcon className={cn('h-3.5 w-3.5', active.accent)} />
          {active.label}
          <ChevronDownIcon className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-72">
        {CHAT_MODES.map(mode => {
          const Icon = mode.icon;
          const isActive = mode.id === value;
          return (
            <DropdownMenuItem
              key={mode.id}
              onSelect={() => onChange(mode.id)}
              className="flex items-start gap-2.5 py-2"
            >
              <Icon className={cn('mt-0.5 h-4 w-4 flex-none', mode.accent)} />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  {mode.label}
                  {isActive && <CheckIcon className="text-brand h-3.5 w-3.5" />}
                </div>
                <p className="text-muted-foreground text-xs leading-snug">
                  {mode.description}
                </p>
              </div>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
};
