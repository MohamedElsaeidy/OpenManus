import { Button } from '@/components/ui/button';
import {
  getConversation,
  getIntegrationsHealth,
  listSkills,
  updateConversationSettings,
  type Conversation,
  type IntegrationsHealth,
  type SkillSummary,
} from '@/services/conversations';
import { listModels, type ModelOption } from '@/services/models';
import { listTools } from '@/services/tools';
import type { ToolOption } from '@/services/admin';
import { RefreshCcw, Save } from 'lucide-react';
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { toast } from 'sonner';

export default function ConversationSettingsPage() {
  const { conversationId } = useParams();
  const [conversation, setConversation] = useState<Conversation | null>(null);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [tools, setTools] = useState<ToolOption[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [model, setModel] = useState('');
  const [disabledTools, setDisabledTools] = useState<string[]>([]);
  const [disabledSkills, setDisabledSkills] = useState<string[]>([]);
  const [enableVendorSkills, setEnableVendorSkills] = useState(true);
  const [health, setHealth] = useState<IntegrationsHealth | null>(null);
  const [isHealthLoading, setIsHealthLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);

  const refreshHealth = async (showErrors = false) => {
    if (!conversationId) return;
    setIsHealthLoading(true);
    try {
      const data = await getIntegrationsHealth(conversationId);
      setHealth(data);
    } catch (error) {
      if (showErrors) {
        toast.error(error instanceof Error ? error.message : 'Could not load integration health');
      }
    } finally {
      setIsHealthLoading(false);
    }
  };

  useEffect(() => {
    if (!conversationId) return;
    Promise.all([getConversation(conversationId), listModels(), listTools(), listSkills(conversationId)])
      .then(([loadedConversation, loadedModels, loadedTools, loadedSkills]) => {
        setConversation(loadedConversation);
        setModels(loadedModels);
        setTools(loadedTools);
        setSkills(loadedSkills.skills || []);
        setModel(loadedConversation.model || loadedModels[0]?.id || '');
        setDisabledTools(loadedConversation.settings?.disabled_tools || []);
        setDisabledSkills(loadedConversation.settings?.disabled_skills || []);
        setEnableVendorSkills(loadedConversation.settings?.enable_vendor_skills ?? true);
      })
      .catch(error => toast.error(error instanceof Error ? error.message : 'Could not load settings'));
  }, [conversationId]);

  useEffect(() => {
    if (!conversationId) return;
    refreshHealth();
    const timer = window.setInterval(() => {
      if (!document.hidden) refreshHealth();
    }, 30000);
    return () => window.clearInterval(timer);
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

  const disabledSkillSet = new Set(disabledSkills);
  const toggleSkill = (name: string) => {
    const next = new Set(disabledSkillSet);
    if (next.has(name)) next.delete(name);
    else next.add(name);
    setDisabledSkills([...next]);
  };

  const save = async () => {
    setIsSaving(true);
    try {
      const saved = await updateConversationSettings(conversationId, {
        model,
        disabled_tools: disabledTools,
        disabled_skills: disabledSkills,
        enable_vendor_skills: enableVendorSkills,
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
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium uppercase text-muted-foreground">Integrations</h2>
            <Button variant="outline" size="sm" onClick={() => refreshHealth(true)} disabled={isHealthLoading}>
              <RefreshCcw className="size-4" />
              Refresh
            </Button>
          </div>
          <div className="grid gap-2 md:grid-cols-2">
            <div className="rounded-md border p-3 text-sm">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">AgentMemory</span>
                <span className={health?.agentmemory?.live ? 'text-emerald-500' : health?.agentmemory?.enabled ? 'text-amber-500' : 'text-muted-foreground'}>
                  {health?.agentmemory?.live ? 'Live' : health?.agentmemory?.enabled ? 'Down' : 'Disabled'}
                </span>
              </div>
              <div className="text-xs text-muted-foreground">
                {health?.agentmemory?.reason || 'Not checked yet'}
              </div>
            </div>
            <div className="rounded-md border p-3 text-sm">
              <div className="mb-1 flex items-center justify-between">
                <span className="font-medium">Obsidian</span>
                <span className={health?.obsidian?.live ? 'text-emerald-500' : 'text-amber-500'}>
                  {health?.obsidian?.live ? 'Live' : 'Waiting'}
                </span>
              </div>
              <div className="text-xs text-muted-foreground">
                {health?.obsidian?.reason || 'Not checked yet'}
                {typeof health?.obsidian?.note_count === 'number' ? ` · notes: ${health.obsidian.note_count}` : ''}
              </div>
            </div>
          </div>
        </section>

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

        <section className="space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-medium uppercase text-muted-foreground">Skills</h2>
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input type="checkbox" checked={enableVendorSkills} onChange={e => setEnableVendorSkills(e.target.checked)} />
              Enable vendor skills
            </label>
          </div>
          <div className="max-h-[420px] space-y-2 overflow-auto rounded-md border p-2">
            {skills.map(skill => {
              const enabled = !disabledSkillSet.has(skill.name);
              const fromVendor = skill.path.includes('/vendor/everything-claude-code/');
              return (
                <label key={`${skill.path}:${skill.name}`} className="flex items-center justify-between rounded-md border p-3 text-sm">
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{skill.name}</span>
                    <span className="text-xs text-muted-foreground">{fromVendor ? 'vendor' : 'local'} · {skill.type}</span>
                  </span>
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggleSkill(skill.name)}
                    disabled={fromVendor && !enableVendorSkills}
                  />
                </label>
              );
            })}
            {!skills.length && <div className="text-sm text-muted-foreground">No skills discovered.</div>}
          </div>
        </section>
      </div>
    </div>
  );
}
