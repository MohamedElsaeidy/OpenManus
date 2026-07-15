import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Checkbox } from '@/components/ui/checkbox';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Textarea } from '@/components/ui/textarea';
import {
  getAdminSettings,
  getCalibrationResult,
  getCalibrationStatus,
  startCalibration,
  updateAdminSettings,
  type AdminSettings,
  type CalibrationResult,
  type CalibrationStatus,
} from '@/services/admin';
import {
  Activity,
  CheckCircle2,
  Cpu,
  Gauge,
  Loader2,
  Save,
  ShieldCheck,
  SlidersHorizontal,
  Wrench,
  Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export default function AdminPage() {
  const [settings, setSettings] = useState<AdminSettings | null>(null);
  const [configOverridesText, setConfigOverridesText] = useState('{}');
  const [fallbackChainText, setFallbackChainText] = useState('[]');
  const [isSaving, setIsSaving] = useState(false);

  // Calibration state
  const [calStatus, setCalStatus] = useState<CalibrationStatus | null>(null);
  const [calResult, setCalResult] = useState<CalibrationResult | null>(null);
  const [embeddingModel, setEmbeddingModel] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    getAdminSettings()
      .then(data => {
        setSettings(data);
        setConfigOverridesText(JSON.stringify(data.config_overrides || {}, null, 2));
        setFallbackChainText(
          JSON.stringify(data.llm_connection?.fallback_chain || [], null, 2),
        );
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
      toast.success('Admin settings saved');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not save settings');
    } finally {
      setIsSaving(false);
    }
  };

  const handleStartCalibration = async () => {
    try {
      await startCalibration({
        model: settings.llm_connection.model || undefined,
        base_url: settings.llm_connection.base_url || undefined,
        embedding_model: embeddingModel || undefined,
      });
      setCalStatus({ phase: 'init', message: 'Starting...', running: true, progress: 0 });
      startPolling();
      toast.success('Calibration started');
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not start calibration');
    }
  };

  const isCalibrating = calStatus?.running === true;
  const calProgress = calStatus?.progress ?? 0;

  return (
    <div className="h-full overflow-y-auto p-6">
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

        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="size-4" />
              Model Connection
            </CardTitle>
            <CardDescription>
              Overrides here become the active runtime defaults for new agent runs.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">Model</Label>
                <Input
                  placeholder="qwen3.5-coder"
                  value={settings.llm_connection.model || ''}
                  onChange={event =>
                    setSettings({ ...settings, llm_connection: { ...settings.llm_connection, model: event.target.value } })
                  }
                />
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

        {/* Model Auto-Calibration Card */}
        <Card className="border-blue-500/30 bg-gradient-to-br from-blue-500/5 via-card to-purple-500/5">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <Gauge className="size-4 text-blue-500" />
              Model Auto-Calibration
              <span className="ml-1 rounded-full bg-blue-500/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wider text-blue-500">
                gpu optimizer
              </span>
            </CardTitle>
            <CardDescription>
              Automatically finds the maximum context window that fits entirely in GPU VRAM at full speed.
              Binary-searches across context sizes, benchmarks throughput, and saves optimal settings.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Embedding model input */}
            <div className="grid gap-3 md:grid-cols-2">
              <div className="space-y-1.5">
                <Label className="text-xs text-muted-foreground">
                  Embedding Model <span className="text-muted-foreground/60">(optional)</span>
                </Label>
                <Input
                  placeholder="text-embedding-nomic-embed-text-v1.5"
                  value={embeddingModel}
                  onChange={event => setEmbeddingModel(event.target.value)}
                  disabled={isCalibrating}
                />
              </div>
              <div className="flex items-end">
                <Button
                  onClick={handleStartCalibration}
                  disabled={isCalibrating || !settings.llm_connection.base_url}
                  className="w-full gap-2 bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                >
                  {isCalibrating ? (
                    <>
                      <Loader2 className="size-4 animate-spin" />
                      Calibrating...
                    </>
                  ) : (
                    <>
                      <Zap className="size-4" />
                      Start Calibration
                    </>
                  )}
                </Button>
              </div>
            </div>

            {/* Progress bar */}
            {calStatus && (calStatus.running || calStatus.phase === 'done' || calStatus.phase === 'error') && (
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="flex items-center gap-1.5 font-medium">
                    {calStatus.phase === 'error' ? (
                      <span className="text-red-500">✗ Error</span>
                    ) : calStatus.phase === 'done' ? (
                      <span className="flex items-center gap-1 text-green-500">
                        <CheckCircle2 className="size-3.5" /> Complete
                      </span>
                    ) : (
                      <span className="flex items-center gap-1 text-blue-500">
                        <Activity className="size-3.5 animate-pulse" />
                        {calStatus.phase === 'search' ? 'Binary Search' :
                         calStatus.phase === 'benchmark' ? 'Benchmarking' :
                         calStatus.phase === 'detect' ? 'Detecting' : 'Initializing'}
                      </span>
                    )}
                  </span>
                  <span className="tabular-nums text-muted-foreground">{calProgress}%</span>
                </div>
                {/* Progress track */}
                <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
                  <div
                    className={`absolute left-0 top-0 h-full rounded-full transition-all duration-500 ease-out ${
                      calStatus.phase === 'error'
                        ? 'bg-red-500'
                        : calStatus.phase === 'done'
                          ? 'bg-green-500'
                          : 'bg-blue-500'
                    }`}
                    style={{ width: `${calProgress}%` }}
                  />
                </div>
                <p className="text-xs text-muted-foreground">{calStatus.message}</p>
              </div>
            )}

            {/* Results card */}
            {calResult && (
              <div className="rounded-lg border border-green-500/20 bg-green-500/5 p-4">
                <div className="mb-3 flex items-center gap-2 text-sm font-medium text-green-600 dark:text-green-400">
                  <CheckCircle2 className="size-4" />
                  Calibration Results
                </div>
                <div className="grid gap-3 md:grid-cols-3">
                  <div className="rounded-md border bg-card p-3 text-center">
                    <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
                      <Cpu className="size-3" /> Optimal Context
                    </div>
                    <div className="mt-1 text-lg font-bold tabular-nums">
                      {calResult.optimal_context.toLocaleString()}
                    </div>
                    <div className="text-[10px] text-muted-foreground">tokens</div>
                  </div>
                  <div className="rounded-md border bg-card p-3 text-center">
                    <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
                      <Zap className="size-3" /> Generation Speed
                    </div>
                    <div className="mt-1 text-lg font-bold tabular-nums">
                      {calResult.generation_speed}
                    </div>
                    <div className="text-[10px] text-muted-foreground">tokens/sec</div>
                  </div>
                  <div className="rounded-md border bg-card p-3 text-center">
                    <div className="flex items-center justify-center gap-1.5 text-xs text-muted-foreground">
                      <Activity className="size-3" /> Evaluation Speed
                    </div>
                    <div className="mt-1 text-lg font-bold tabular-nums">
                      {calResult.evaluation_speed}
                    </div>
                    <div className="text-[10px] text-muted-foreground">tokens/sec</div>
                  </div>
                </div>
                <div className="mt-3 grid gap-1.5 text-xs text-muted-foreground md:grid-cols-2">
                  <div>Model: <span className="font-medium text-foreground">{calResult.model_id}</span></div>
                  <div>GPU Offload: <span className="font-medium text-foreground">{calResult.gpu_offload}</span></div>
                  {calResult.embedding_model && (
                    <div>Embedding: <span className="font-medium text-foreground">{calResult.embedding_model}</span></div>
                  )}
                  <div>Max Found: <span className="font-medium text-foreground">{calResult.max_context_found.toLocaleString()} tokens</span></div>
                </div>
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
