import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  applyCalibrationMode,
  getAdminSettings,
  getCalibrationResult,
  getCalibrationStatus,
  startCalibration,
  updateAdminSettings,
  type AdminSettings,
  type CalibrationMode,
  type CalibrationProfile,
  type CalibrationResult,
  type CalibrationStatus,
} from '@/services/admin';
import { listModels, queryModels, type ModelOption } from '@/services/models';
import {
  Activity,
  BrainCircuit,
  CheckCircle2,
  Cpu,
  Gauge,
  Loader2,
  MemoryStick,
  RefreshCcw,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  TriangleAlert,
  Wrench,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

const formatBytes = (bytes?: number) => {
  if (!bytes) return 'Unavailable';
  return `${(bytes / 1024 ** 3).toFixed(1)} GiB`;
};

const calibrationPhaseLabel = (phase: string) => {
  const labels: Record<string, string> = {
    detect: 'Reading resources',
    search_fast: 'Sizing fast mode',
    benchmark_fast: 'Benchmarking fast mode',
    search_deep: 'Sizing deep mode',
    benchmark_deep: 'Benchmarking deep mode',
    finalize: 'Applying recommendation',
  };
  return labels[phase] || 'Initializing';
};

const residencyLabel = (profile: CalibrationProfile) => {
  if (profile.residency === 'observed_full_gpu_request') return 'Full GPU request observed';
  if (profile.residency === 'confirmed_full_gpu_request') return 'Full GPU request confirmed';
  if (profile.residency === 'full_weight_residency_inferred') return 'GPU weights inferred';
  return 'LM Studio automatic offload';
};

const modelQueryStyle = (
  value?: string,
): 'lm-studio' | 'ollama' | 'openai' | 'custom' => {
  if (value === 'lmstudio') return 'lm-studio';
  if (value === 'lm-studio' || value === 'ollama' || value === 'openai') return value;
  return 'custom';
};

export default function AdminPage() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [configOverridesText, setConfigOverridesText] = useState('{}');
  const [fallbackChainText, setFallbackChainText] = useState('[]');
  const [isSaving, setIsSaving] = useState(false);

  // Calibration state
  const [calStatus, setCalStatus] = useState<CalibrationStatus | null>(null);
  const [calResult, setCalResult] = useState<CalibrationResult | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState('');
  const [availableModels, setAvailableModels] = useState<ModelOption[]>([]);
  const [selectedCalModel, setSelectedCalModel] = useState<string>('');
  const [isModelsLoading, setIsModelsLoading] = useState(false);
  const [gpuTarget, setGpuTarget] = useState('97');
  const [ramTarget, setRamTarget] = useState('85');
  const [maxContext, setMaxContext] = useState('');
  const [applyingMode, setApplyingMode] = useState<CalibrationMode | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const refreshModels = useCallback(async () => {
    setIsModelsLoading(true);
    try {
      let items: ModelOption[] = [];
      if (settings?.llm_connection.base_url) {
        items = await queryModels({
          host: settings.llm_connection.base_url,
          api_key: settings.llm_connection.api_key || '',
          style: modelQueryStyle(settings.llm_connection.api_type || 'lm-studio'),
        });
      }
      if (!items.length) {
        items = await listModels();
      }
      const seen = new Set<string>();
      const unique: ModelOption[] = [];
      for (const m of items) {
        if (!seen.has(m.id)) {
          seen.add(m.id);
          unique.push(m);
        }
      }
      setAvailableModels(unique);
      if (!selectedCalModel && unique.length > 0) {
        setSelectedCalModel(settings?.llm_connection.model || unique[0].id);
      }
    } catch {
      // ignore silently on background check
    } finally {
      setIsModelsLoading(false);
    }
  }, [settings?.llm_connection.base_url, settings?.llm_connection.api_key, settings?.llm_connection.api_type, settings?.llm_connection.model, selectedCalModel]);

  useEffect(() => {
    if (settings?.llm_connection.base_url) {
      refreshModels();
    }
  }, [settings?.llm_connection.base_url, refreshModels]);

  useEffect(() => {
    getAdminSettings()
      .then(data => {
        setSettings(data);
        setConfigOverridesText(JSON.stringify(data.config_overrides || {}, null, 2));
        setFallbackChainText(
          JSON.stringify(data.llm_connection?.fallback_chain || [], null, 2),
        );
        if (data.llm_connection?.model) {
          setSelectedCalModel(data.llm_connection.model);
          localStorage.setItem('openmanus.selectedModel', data.llm_connection.model);
        }
      })
      .catch(error => toast.error(error.message));

    // Load last calibration result
    getCalibrationResult()
      .then(data => {
        if (data.result) setCalResult(data.result);
      })
      .catch(() => {});

    // Check if calibration is running
    getCalibrationStatus()
      .then(data => {
        if (data.running) {
          setCalStatus(data);
          startPolling();
        }
      })
      .catch(() => {});

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const status = await getCalibrationStatus();
        setCalStatus(status);
        if (!status.running) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          if (status.result) {
            setCalResult(status.result);
          }
          // Reload admin settings to reflect auto-saved values
          try {
            const refreshed = await getAdminSettings();
            setSettings(refreshed);
            setConfigOverridesText(JSON.stringify(refreshed.config_overrides || {}, null, 2));
            setFallbackChainText(
              JSON.stringify(refreshed.llm_connection?.fallback_chain || [], null, 2),
            );
            if (refreshed.llm_connection?.model) {
              setSelectedCalModel(refreshed.llm_connection.model);
              localStorage.setItem('openmanus.selectedModel', refreshed.llm_connection.model);
              window.dispatchEvent(new Event('admin-settings-changed'));
            }
          } catch { /* ignore */ }
        }
      } catch {
        // ignore polling errors
      }
    }, 1500);
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
      const fallbackChain = JSON.parse(fallbackChainText || '[]');
      if (!Array.isArray(fallbackChain)) {
        throw new Error('Fallback chain must be a JSON array');
      }
      const saved = await updateAdminSettings({
        llm_connection: {
          ...settings.llm_connection,
          fallback_chain: fallbackChain,
        },
        tools: settings.tools,
        config_overrides: configOverrides,
      });
      setSettings(saved);
      setConfigOverridesText(JSON.stringify(saved.config_overrides || {}, null, 2));
      setFallbackChainText(
        JSON.stringify(saved.llm_connection?.fallback_chain || [], null, 2),
      );
      if (saved.llm_connection?.model) {
        setSelectedCalModel(saved.llm_connection.model);
        localStorage.setItem('openmanus.selectedModel', saved.llm_connection.model);
        window.dispatchEvent(new Event('admin-settings-changed'));
      }
      toast.success('Admin settings saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save settings');
    } finally {
      setIsSaving(false);
    }
  };

  const handleStartCalibration = async () => {
    try {
      const targetModel = selectedCalModel || settings.llm_connection.model || undefined;
      await startCalibration({
        model: targetModel,
        base_url: settings.llm_connection.base_url || undefined,
        embedding_model: embeddingModel || undefined,
        gpu_target_percent: Number(gpuTarget),
        ram_target_percent: Number(ramTarget),
        max_context: maxContext ? Number(maxContext) : undefined,
      });
      setCalStatus({ phase: 'init', message: 'Starting...', running: true, progress: 0 });
      startPolling();
      toast.success(`Calibration started for ${targetModel || 'default model'}`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not start calibration');
    }
  };

  const handleApplyMode = async (mode: CalibrationMode) => {
    setApplyingMode(mode);
    try {
      const { result } = await applyCalibrationMode(mode);
      setCalResult(result);
      const refreshed = await getAdminSettings();
      setSettings(refreshed);
      localStorage.setItem('openmanus.selectedModel', refreshed.llm_connection.model || '');
      window.dispatchEvent(new Event('admin-settings-changed'));
      toast.success(`${mode === 'fast' ? 'Fast' : 'Deep'} mode applied`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : `Could not apply ${mode} mode`);
    } finally {
      setApplyingMode(null);
    }
  };

  const isCalibrating = calStatus?.running === true;
  const calProgress = calStatus?.progress ?? 0;
  const hasCalibrationProfiles = Boolean(
    calResult?.profiles?.fast && calResult?.profiles?.deep,
  );

  return (
    <div className="h-full min-w-0 overflow-y-auto p-3 sm:p-6">
      <div className="mx-auto max-w-5xl space-y-6">
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card p-4">
          <div>
            <h1 className="text-xl font-semibold">Admin Console</h1>
            <p className="text-sm text-muted-foreground">Runtime connection, overrides, and global capability controls.</p>
          </div>
          <Button onClick={save} disabled={isSaving}>
            <Save className="size-4" />
            Save
          </Button>
        </div>

        <Card className="min-w-0 overflow-hidden">
          <CardHeader className="min-w-0 pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="size-4" />
              Model Connection
            </CardTitle>
            <CardDescription>
              Overrides here become the active runtime defaults for new agent runs.
            </CardDescription>
          </CardHeader>
          <CardContent className="min-w-0 space-y-4">
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label className="text-xs text-muted-foreground">Model</Label>
                  <button
                    type="button"
                    onClick={refreshModels}
                    disabled={isModelsLoading}
                    className="flex items-center gap-1 text-[11px] text-blue-500 hover:underline disabled:opacity-50"
                  >
                    <RefreshCcw className={`size-3 ${isModelsLoading ? 'animate-spin' : ''}`} />
                    Refresh list
                  </button>
                </div>
                <div className="flex gap-2">
                  <Input
                    placeholder="qwen3.5-coder"
                    value={settings.llm_connection.model || ''}
                    onChange={event => {
                      setSettings({ ...settings, llm_connection: { ...settings.llm_connection, model: event.target.value } });
                      setSelectedCalModel(event.target.value);
                    }}
                    className="flex-1"
                  />
                  {availableModels.length > 0 && (
                    <select
                      className="h-10 w-44 rounded-md border bg-background px-2 text-xs text-muted-foreground focus:outline-none focus:ring-1 focus:ring-primary truncate"
                      value=""
                      onChange={event => {
                        if (event.target.value) {
                          setSettings({ ...settings, llm_connection: { ...settings.llm_connection, model: event.target.value } });
                          setSelectedCalModel(event.target.value);
                        }
                      }}
                    >
                      <option value="">Select queued...</option>
                      {availableModels.map(item => (
                        <option key={item.id} value={item.id}>
                          {item.name && item.name !== item.id ? `${item.name} (${item.id})` : item.id} {item.state ? `(${item.state})` : ''}
                        </option>
                      ))}
                    </select>
                  )}
                </div>
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">API Type</Label>
                <Input
                  placeholder="openai | lmstudio | ollama | azure"
                  value={settings.llm_connection.api_type || 'openai'}
                  onChange={event =>
                    setSettings({ ...settings, llm_connection: { ...settings.llm_connection, api_type: event.target.value } })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Base URL</Label>
                <Input
                  placeholder="http://127.0.0.1:1234"
                  value={settings.llm_connection.base_url || ''}
                  onChange={event =>
                    setSettings({ ...settings, llm_connection: { ...settings.llm_connection, base_url: event.target.value } })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">API Key</Label>
                <Input
                  placeholder="Optional"
                  type="password"
                  value={settings.llm_connection.api_key || ''}
                  onChange={event =>
                    setSettings({ ...settings, llm_connection: { ...settings.llm_connection, api_key: event.target.value } })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Max Tokens</Label>
                <Input
                  placeholder="4096"
                  type="number"
                  value={settings.llm_connection.max_tokens || ''}
                  onChange={event =>
                    setSettings({
                      ...settings,
                      llm_connection: { ...settings.llm_connection, max_tokens: Number(event.target.value) || undefined },
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Temperature</Label>
                <Input
                  placeholder="0.7"
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
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  Thinking Budget
                  <span className="ml-1 text-amber-500">⚡ reasoning</span>
                </Label>
                <Input
                  placeholder="4096"
                  type="number"
                  value={settings.llm_connection.thinking_budget ?? ''}
                  onChange={event =>
                    setSettings({
                      ...settings,
                      llm_connection: {
                        ...settings.llm_connection,
                        thinking_budget: Number(event.target.value) || undefined,
                      },
                    })
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  Max Agent Steps
                </Label>
                <Input
                  placeholder="30"
                  type="number"
                  min="1"
                  max="200"
                  value={settings.llm_connection.max_steps ?? ''}
                  onChange={event =>
                    setSettings({
                      ...settings,
                      llm_connection: {
                        ...settings.llm_connection,
                        max_steps: Number(event.target.value) || undefined,
                      },
                    })
                  }
                />
              </div>
            </div>
            <div className="space-y-2">
              <Label className="text-xs text-muted-foreground">Fallback Chain (ordered)</Label>
              <Textarea
                className="min-h-44 font-mono text-xs"
                value={fallbackChainText}
                onChange={event => setFallbackChainText(event.target.value)}
                placeholder={`[
  { "api_type": "lmstudio", "base_url": "http://127.0.0.1:1234", "model": "qwen3.5" },
  { "api_type": "openai", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini" }
]`}
              />
            </div>
          </CardContent>
        </Card>

        <Card className="min-w-0 overflow-hidden">
          <CardHeader className="min-w-0 pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Gauge className="size-4" />
              Model Auto-Calibration
            </CardTitle>
            <CardDescription>
              Builds a GPU-first fast profile and a RAM-backed deep-context profile within explicit resource limits.
            </CardDescription>
          </CardHeader>
          <CardContent className="min-w-0 space-y-4">
            <div className="grid min-w-0 gap-3 md:grid-cols-2">
              <div className="min-w-0 space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label className="text-xs font-medium text-muted-foreground">
                    Target Model
                  </Label>
                  <button
                    type="button"
                    onClick={refreshModels}
                    disabled={isModelsLoading || isCalibrating}
                    title="Refresh queued and loaded models from LM Studio / Base URL"
                    className="flex items-center gap-1 text-[11px] text-primary hover:underline disabled:opacity-50"
                  >
                    <RefreshCcw className={`size-3 ${isModelsLoading ? 'animate-spin' : ''}`} />
                    Refresh
                  </button>
                </div>
                {availableModels.length > 0 ? (
                  <select
                    className="h-10 w-full min-w-0 max-w-full truncate rounded-md border bg-background px-3 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
                    value={selectedCalModel || settings.llm_connection.model || ''}
                    onChange={e => setSelectedCalModel(e.target.value)}
                    disabled={isCalibrating}
                  >
                    <option value="">
                      {settings.llm_connection.model ? `Default (${settings.llm_connection.model})` : 'Select model'}
                    </option>
                    {availableModels.map(item => (
                      <option key={item.id} value={item.id}>
                        {item.name && item.name !== item.id ? `${item.name} (${item.id})` : item.id}
                        {item.state ? ` [${item.state}]` : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <Input
                    placeholder="qwen3.5-coder"
                    value={selectedCalModel || settings.llm_connection.model || ''}
                    onChange={e => setSelectedCalModel(e.target.value)}
                    disabled={isCalibrating}
                  />
                )}
              </div>

              <div className="min-w-0 space-y-1.5">
                <Label className="text-xs text-muted-foreground">Co-resident Embedding Model</Label>
                <Input
                  placeholder="Optional"
                  value={embeddingModel}
                  onChange={event => setEmbeddingModel(event.target.value)}
                  disabled={isCalibrating}
                />
              </div>
            </div>

            <div className="grid min-w-0 gap-3 border-t pt-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">GPU Limit (%)</Label>
                <Input
                  type="number"
                  min="50"
                  max="99.5"
                  step="0.5"
                  value={gpuTarget}
                  onChange={event => setGpuTarget(event.target.value)}
                  disabled={isCalibrating}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">RAM Limit (%)</Label>
                <Input
                  type="number"
                  min="50"
                  max="95"
                  step="1"
                  value={ramTarget}
                  onChange={event => setRamTarget(event.target.value)}
                  disabled={isCalibrating}
                />
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Context Search Cap</Label>
                <Input
                  type="number"
                  min="8192"
                  step="1024"
                  placeholder="Model maximum"
                  value={maxContext}
                  onChange={event => setMaxContext(event.target.value)}
                  disabled={isCalibrating}
                />
              </div>
            </div>

            <div className="flex justify-end">
              <Button
                onClick={handleStartCalibration}
                disabled={
                  isCalibrating ||
                  !settings.llm_connection.base_url ||
                  !gpuTarget ||
                  !ramTarget
                }
                className="w-full gap-2 md:w-auto"
              >
                {isCalibrating ? (
                  <>
                    <Loader2 className="size-4 animate-spin" />
                    Calibrating {selectedCalModel || settings.llm_connection.model || 'model'}...
                  </>
                ) : (
                  <>
                    <Zap className="size-4" />
                    Calibrate Profiles
                  </>
                )}
              </Button>
            </div>

            {calStatus && (calStatus.running || calStatus.phase === 'done' || calStatus.phase === 'error') && (
              <div className="space-y-2 border-t pt-4">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 font-medium">
                    {calStatus.phase === 'error' ? (
                      <span className="text-destructive">Calibration failed</span>
                    ) : calStatus.phase === 'done' ? (
                      <span className="flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
                        <CheckCircle2 className="size-3.5" /> Complete
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-primary">
                        <Activity className="size-3.5 animate-pulse" />
                        {calibrationPhaseLabel(calStatus.phase)}
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums text-muted-foreground">{calProgress}%</span>
                </div>
                <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={`absolute left-0 top-0 h-full rounded-full transition-all duration-500 ease-out ${
                      calStatus.phase === 'error'
                        ? 'bg-destructive'
                        : calStatus.phase === 'done'
                          ? 'bg-emerald-500'
                          : 'bg-primary'
                    }`}
                    style={{ width: `${calProgress}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground">{calStatus.message}</p>
              </div>
            )}

            {calResult && !hasCalibrationProfiles && (
              <div className="flex items-start gap-2 border-t pt-4 text-sm text-amber-700 dark:text-amber-300">
                <TriangleAlert className="mt-0.5 size-4 shrink-0" />
                The saved result uses the retired context-only calibration. Run calibration to create resource-aware profiles.
              </div>
            )}

            {calResult && hasCalibrationProfiles && (
              <div className="space-y-4 border-t pt-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <div className="break-words text-sm font-medium">{calResult.model_id}</div>
                    <div className="text-xs text-muted-foreground">
                      Tested to {calResult.tested_max_context.toLocaleString()} of {calResult.declared_max_context.toLocaleString()} tokens
                    </div>
                  </div>
                  <div className="flex items-center gap-3 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Cpu className="size-3.5" />
                      GPU {formatBytes(calResult.resource_snapshot.gpu.total_bytes)}
                    </span>
                    <span className="flex items-center gap-1">
                      <MemoryStick className="size-3.5" />
                      RAM {formatBytes(calResult.resource_snapshot.ram.total_bytes)}
                    </span>
                  </div>
                </div>

                <div className="divide-y rounded-md border">
                  {(['fast', 'deep'] as CalibrationMode[]).map(mode => {
                    const profile = calResult.profiles[mode];
                    const isActive = calResult.active_mode === mode;
                    const isRecommended = calResult.recommended_mode === mode;
                    return (
                      <div key={mode} className="grid gap-4 p-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,2fr)_auto] lg:items-center">
                        <div className="flex items-start gap-3">
                          <div className="flex size-9 shrink-0 items-center justify-center rounded-md border bg-muted/40">
                            {mode === 'fast' ? <Zap className="size-4" /> : <BrainCircuit className="size-4" />}
                          </div>
                          <div className="min-w-0">
                            <div className="flex flex-wrap items-center gap-2">
                              <span className="text-sm font-semibold capitalize">{mode}</span>
                              {isActive && <span className="text-xs font-medium text-emerald-600 dark:text-emerald-400">Active</span>}
                              {isRecommended && <span className="text-xs text-muted-foreground">Recommended</span>}
                            </div>
                            <div className="mt-0.5 text-xs text-muted-foreground">
                              {profile.context_length.toLocaleString()} tokens, KV cache on {profile.kv_cache.toUpperCase()}
                            </div>
                          </div>
                        </div>

                        <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs sm:grid-cols-4">
                          <div>
                            <div className="text-muted-foreground">Generation</div>
                            <div className="mt-0.5 font-medium tabular-nums">{profile.generation_speed || 'N/A'} tok/s</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">Prompt eval</div>
                            <div className="mt-0.5 font-medium tabular-nums">{profile.evaluation_speed || 'N/A'} tok/s</div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">GPU / RAM</div>
                            <div className="mt-0.5 font-medium tabular-nums">
                              {profile.gpu_used_percent ?? 'N/A'}% / {profile.ram_used_percent ?? 'N/A'}%
                            </div>
                          </div>
                          <div>
                            <div className="text-muted-foreground">Residency</div>
                            <div className="mt-0.5 font-medium" title={profile.residency}>
                              {residencyLabel(profile)} ({profile.residency_confidence})
                            </div>
                          </div>
                        </div>

                        <Button
                          variant={isActive ? 'outline' : 'default'}
                          size="sm"
                          disabled={isActive || applyingMode !== null || isCalibrating}
                          onClick={() => handleApplyMode(mode)}
                        >
                          {applyingMode === mode && <Loader2 className="size-3.5 animate-spin" />}
                          {isActive ? 'Applied' : `Apply ${mode}`}
                        </Button>
                      </div>
                    );
                  })}
                </div>

                {calResult.telemetry.gpu_usage_source !== 'nvidia-smi' && (
                  <div className="flex items-start gap-2 text-xs text-amber-700 dark:text-amber-300">
                    <TriangleAlert className="mt-0.5 size-3.5 shrink-0" />
                    Exact GPU utilization is unavailable to this server. LM Studio confirms context and KV placement, but does not expose actual model-weight residency; profile confidence is reduced.
                  </div>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <SlidersHorizontal className="size-4" />
              Runtime Config Overrides
            </CardTitle>
            <CardDescription>
              Global config patch object applied by server-side runtime hooks.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Textarea
              className="min-h-64 font-mono text-xs"
              value={configOverridesText}
              onChange={event => setConfigOverridesText(event.target.value)}
            />
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Wrench className="size-4" />
              Global Tools
            </CardTitle>
            <CardDescription>
              Disable or enable capability surfaces for all users. `terminate` remains protected.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid gap-2 md:grid-cols-2">
              {settings.available_tools.map(tool => (
                <label key={tool.name} className="flex items-center justify-between rounded-md border p-3 text-sm">
                  <span>
                    <span className="block font-medium">{tool.label}</span>
                    <span className="text-xs text-muted-foreground">{tool.name}</span>
                  </span>
                  <Checkbox
                    checked={!disabled.has(tool.name)}
                    disabled={tool.locked}
                    onCheckedChange={() => toggleTool(tool.name)}
                  />
                </label>
              ))}
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Loaded Config Defaults</CardTitle>
            <CardDescription>Read-only values loaded from configuration sources.</CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="max-h-96 overflow-auto rounded-md border bg-muted p-3 text-xs">
              {JSON.stringify(settings.config_defaults || {}, null, 2)}
            </pre>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
