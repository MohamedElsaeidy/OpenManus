import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { getAdminSettings, updateAdminSettings, type AdminSettings } from '@/services/admin';
import { Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { toast } from 'sonner';

export default function AdminPage() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [configOverridesText, setConfigOverridesText] = useState('{}');
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    getAdminSettings()
      .then(data => {
        setSettings(data);
        setConfigOverridesText(JSON.stringify(data.config_overrides || {}, null, 2));
      })
      .catch(error => toast.error(error.message));
  }, []);

  if (!settings) {
    return <div className="p-6 text-sm text-muted-foreground">Loading admin settings...</div>;
  }

  const disabled = new Set(settings.tools.disabled || []);
  const toggleTool = (name: string) => {
    const next = new Set(disabled);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setSettings({ ...settings, tools: { disabled: [...next] } });
  };

  const save = async () => {
    setIsSaving(true);
    try {
      const configOverrides = JSON.parse(configOverridesText || '{}');
      const saved = await updateAdminSettings({
        llm_connection: settings.llm_connection,
        tools: settings.tools,
        config_overrides: configOverrides,
      });
      setSettings(saved);
      setConfigOverridesText(JSON.stringify(saved.config_overrides || {}, null, 2));
      toast.success('Admin settings saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save settings');
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="mx-auto max-w-5xl space-y-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-semibold">Admin</h1>
            <p className="text-sm text-muted-foreground">Connection settings and global tool availability.</p>
          </div>
          <Button onClick={save} disabled={isSaving}>
            <Save className="size-4" />
            Save
          </Button>
        </div>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Model Connection</h2>
          <p className="text-sm text-muted-foreground">
            Defaults are loaded from config.toml. Saving here creates a runtime override used by new agent runs.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            <Input
              placeholder="Model"
              value={settings.llm_connection.model || ''}
              onChange={event =>
                setSettings({ ...settings, llm_connection: { ...settings.llm_connection, model: event.target.value } })
              }
            />
            <Input
              placeholder="API type: openai, azure, aws"
              value={settings.llm_connection.api_type || 'openai'}
              onChange={event =>
                setSettings({ ...settings, llm_connection: { ...settings.llm_connection, api_type: event.target.value } })
              }
            />
            <Input
              placeholder="Base URL"
              value={settings.llm_connection.base_url || ''}
              onChange={event =>
                setSettings({ ...settings, llm_connection: { ...settings.llm_connection, base_url: event.target.value } })
              }
            />
            <Input
              placeholder="API key"
              type="password"
              value={settings.llm_connection.api_key || ''}
              onChange={event =>
                setSettings({ ...settings, llm_connection: { ...settings.llm_connection, api_key: event.target.value } })
              }
            />
            <Input
              placeholder="Max tokens"
              type="number"
              value={settings.llm_connection.max_tokens || ''}
              onChange={event =>
                setSettings({
                  ...settings,
                  llm_connection: { ...settings.llm_connection, max_tokens: Number(event.target.value) || undefined },
                })
              }
            />
            <Input
              placeholder="Temperature"
              type="number"
              step="0.1"
              value={settings.llm_connection.temperature ?? ''}
              onChange={event =>
                setSettings({
                  ...settings,
                  llm_connection: { ...settings.llm_connection, temperature: Number(event.target.value) },
                })
              }
            />
          </div>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Loaded Config Defaults</h2>
          <pre className="max-h-96 overflow-auto rounded-md border bg-muted p-3 text-xs">
            {JSON.stringify(settings.config_defaults || {}, null, 2)}
          </pre>
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Runtime Config Overrides</h2>
          <p className="text-sm text-muted-foreground">
            Store overrides for config paths here. Hooks are available server-side through runtime settings.
          </p>
          <Textarea
            className="min-h-64 font-mono text-xs"
            value={configOverridesText}
            onChange={event => setConfigOverridesText(event.target.value)}
          />
        </section>

        <section className="space-y-3">
          <h2 className="text-sm font-medium uppercase text-muted-foreground">Global Tools</h2>
          <div className="grid gap-2 md:grid-cols-2">
            {settings.available_tools.map(tool => (
              <label key={tool.name} className="flex items-center justify-between rounded-md border p-3 text-sm">
                <span>
                  <span className="block font-medium">{tool.label}</span>
                  <span className="text-xs text-muted-foreground">{tool.name}</span>
                </span>
                <input
                  type="checkbox"
                  checked={!disabled.has(tool.name)}
                  disabled={tool.locked}
                  onChange={() => toggleTool(tool.name)}
                />
              </label>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
