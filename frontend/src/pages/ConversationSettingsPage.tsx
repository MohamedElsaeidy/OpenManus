import { Button } from '@/components/ui/button';
import { getConversation, updateConversationSettings, type Conversation } from '@/services/conversations';
import { listModels, type ModelOption } from '@/services/models';
import { listTools } from '@/services/tools';
import type { ToolOption } from '@/services/admin';
import { Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { toast } from 'sonner';

export default function ConversationSettingsPage() {
  const { conversationId } = useParams();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [tools, setTools] = useState<ToolOption[]>([]);
  const [model, setModel] = useState('');
  const [disabledTools, setDisabledTools] = useState<string[]>([]);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!conversationId) return;
    Promise.all([getConversation(conversationId), listModels(), listTools()])
      .then(([loadedConversation, loadedModels, loadedTools]) => {
        setConversation(loadedConversation);
        setModels(loadedModels);
        setTools(loadedTools);
        setModel(loadedConversation.model || loadedModels[0]?.id || '');
        setDisabledTools(loadedConversation.settings?.disabled_tools || []);
      })
      .catch(error => toast.error(error instanceof Error ? error.message : 'Could not load settings'));
  }, [conversationId]);

  if (!conversation || !conversationId) {
    return <div className="p-6 text-sm text-muted-foreground">Loading conversation settings...</div>;
  }

  const disabled = new Set(disabledTools);
  const toggleTool = (name: string) => {
    const next = new Set(disabled);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setDisabledTools([...next]);
  };

  const save = async () => {
    setIsSaving(true);
    try {
      const saved = await updateConversationSettings(conversationId, {
        model,
        disabled_tools: disabledTools,
      });
      setConversation(saved);
      toast.success('Conversation settings saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save settings');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-4xl space-y-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">{conversation.title}</h1>
            <p className="text-sm text-muted-foreground">Conversation model and tool access.</p>
          </div>
          <Button onClick={save} disabled={isSaving}>
            <Save className="size-4" />
            Save
          </Button>
        </div>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Model</h2>
          <select
            className="h-10 w-full rounded-md border bg-background px-3 text-sm"
            value={model}
            onChange={event => setModel(event.target.value)}
          >
            {models.map(item => (
              <option key={item.id} value={item.id}>
                {item.id}
              </option>
            ))}
          </select>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Tools</h2>
          <div className="grid gap-2 md:grid-cols-2">
            {tools.map(tool => {
              const globallyDisabled = tool.enabled === false;
              const enabled = !globallyDisabled && !disabled.has(tool.name);
              return (
                <label key={tool.name} className="flex items-center justify-between rounded-md border p-3 text-sm">
                  <span>
                    <span className="block font-medium">{tool.label}</span>
                    <span className="text-xs text-muted-foreground">
                      {globallyDisabled ? 'Disabled by admin' : tool.name}
                    </span>
                  </span>
                  <input
                    type="checkbox"
                    checked={enabled}
                    disabled={tool.locked || globallyDisabled}
                    onChange={() => toggleTool(tool.name)}
                  />
                </label>
              );
            })}
          </div>
        </section>
      </div>
    </div>
  );
}
