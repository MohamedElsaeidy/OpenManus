import { confirm } from '@/components/block/confirm';
import { Button } from '@/components/ui/button';
import { DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { Plus, Send, Square } from 'lucide-react';
import { useState } from 'react';

interface ChatInputProps {
  taskId?: string;
  status?: 'idle' | 'thinking' | 'terminating' | 'completed';
  defaultValue?: string;
  onSubmit?: (value: { taskId?: string; prompt: string }) => Promise<void>;
  onTerminate?: () => Promise<void>;
}

export const ChatInput = ({ taskId, status = 'idle', defaultValue = '', onSubmit, onTerminate }: ChatInputProps) => {
  const [value, setValue] = useState(defaultValue);

  const handleKeyDown = async (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (status === 'terminating' || !value.trim()) {
        return;
      }
      await onSubmit?.({ taskId, prompt: value.trim() });
      setValue('');
    }
  };

  const handleSendClick = async () => {
    const v = value.trim();
    if ((status === 'thinking' && !v) || status === 'terminating') {
      confirm({
        content: (
          <DialogHeader>
            <DialogTitle>Terminate Task</DialogTitle>
            <DialogDescription>Are you sure you want to terminate this task?</DialogDescription>
          </DialogHeader>
        ),
        onConfirm: async () => {
          await onTerminate?.();
        },
        buttonText: {
          cancel: 'Cancel',
          confirm: 'Terminate',
          loading: 'Terminating...',
        },
      });
      return;
    }
    if (v) {
      await onSubmit?.({ prompt: v });
      setValue('');
    }
  };

  return (
    <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-background/95 px-3 pb-3 pt-3 backdrop-blur-sm sm:px-4 sm:pb-4">
      <div className="pointer-events-auto mx-auto flex w-full max-w-3xl flex-col gap-2">
        {status === 'completed' && (
          <div className="flex justify-center">
            <Button
              variant="outline"
              size="sm"
              className="bg-background"
              type="button"
              onClick={() => (window.location.href = '/')}
            >
              <Plus className="h-4 w-4" />
              New task
            </Button>
          </div>
        )}
        <div className="flex w-full flex-col rounded-xl border bg-background shadow-lg shadow-black/5">
          <Textarea
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            disabled={status === 'terminating'}
            placeholder={
              status === 'thinking'
                ? 'Send a message to the agent'
                : status === 'terminating'
                  ? 'Terminating...'
                  : status === 'completed'
                    ? 'Send a follow-up in this conversation'
                    : 'Send message to Manus'
            }
            className="min-h-[58px] max-h-40 flex-1 resize-none border-none bg-transparent px-4 py-3 shadow-none outline-none focus-visible:ring-0 focus-visible:ring-offset-0 dark:bg-transparent"
          />
          <div className="border-border flex items-center justify-between border-t px-4 py-2">
            <div />
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="icon"
                variant="ghost"
                className="h-8 w-8 cursor-pointer rounded-md"
                onClick={handleSendClick}
                disabled={status === 'terminating'}
                aria-label={status === 'thinking' && !value.trim() ? 'Terminate task' : 'Send message'}
              >
                {status === 'thinking' && !value.trim() ? <Square className="h-3.5 w-3.5" /> : <Send className="h-4 w-4" />}
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
