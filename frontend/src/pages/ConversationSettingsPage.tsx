import { Button } from '@/components/ui/button';
import { getConversation, listSkills, updateConversationSettings, type Conversation, type SkillSummary } from '@/services/conversations';
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
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [model, setModel] = useState('');
  const [disabledTools, setDisabledTools] = useState<string[]>([]);
  const [disabledSkills, setDisabledSkills] = useState<string[]>([]);
  const [enableVendorSkills, setEnableVendorSkills] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

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
