import type { ModelOption } from './models';

export interface ToolOption {
  name: string;
  label: string;
  scope: string;
  enabled?: boolean;
  locked?: boolean;
}

export interface AdminSettings {
  llm_connection: {
    model?: string;
    base_url?: string;
    api_key?: string;
    api_type?: string;
    max_tokens?: number;
    temperature?: number;
    thinking_budget?: number;
    max_steps?: number;
    context_window?: number;
    calibration_mode?: CalibrationMode;
    fallback_chain?: Array<{
      model?: string;
      base_url?: string;
      api_key?: string;
      api_type?: string;
      max_tokens?: number;
      temperature?: number;
      thinking_budget?: number;
    }>;
  };
  llm_connection_override?: AdminSettings['llm_connection'];
  tools: { disabled: string[] };
  config_defaults?: Record<string, unknown>;
  config_overrides?: Record<string, unknown>;
  available_tools: ToolOption[];
  models?: ModelOption[];
}

export async function getAdminSettings(): Promise<AdminSettings> {
  const response = await fetch('/api/admin/settings', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Admin access required');
  return response.json();
}

export async function updateAdminSettings(settings: Partial<AdminSettings>): Promise<AdminSettings> {
  const response = await fetch('/api/admin/settings', {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error('Could not save admin settings');
  return response.json();
}

export interface CalibrationStatus {
  phase: string;
  message: string;
  running: boolean;
  progress: number;
  model_id?: string;
  embedding_model?: string;
  result?: CalibrationResult;
}

export type CalibrationMode = 'fast' | 'deep';

export interface ResourceSummary {
  source: string;
  total_bytes: number;
  used_bytes: number;
  free_bytes?: number;
  available_bytes?: number;
  used_percent: number | null;
  devices?: Array<{
    index: number;
    name: string;
    total_bytes: number;
    used_bytes: number;
    free_bytes: number;
  }>;
}

export interface CalibrationProfile {
  mode: CalibrationMode;
  context_length: number;
  kv_cache: 'gpu' | 'ram';
  flash_attention: boolean;
  generation_speed: number;
  evaluation_speed: number;
  residency: string;
  residency_confidence: 'high' | 'medium' | 'low';
  full_gpu_requested: boolean;
  gpu_used_percent: number | null;
  ram_used_percent: number | null;
  gpu_load_delta_bytes: number | null;
  estimate: {
    available?: boolean;
    source?: string;
    estimated_gpu_bytes?: number;
    estimated_total_bytes?: number;
    confidence?: string;
    guardrails_allow?: boolean;
  };
  load_config: Record<string, unknown>;
}

export interface CalibrationResult {
  model_id: string;
  embedding_model: string;
  declared_max_context: number;
  tested_max_context: number;
  gpu_target_percent: number;
  ram_target_percent: number;
  resource_snapshot: {
    gpu: ResourceSummary;
    ram: ResourceSummary;
  };
  telemetry: {
    gpu_usage_source: string;
    ram_usage_source: string;
    lms_cli_available: boolean;
    actual_weight_residency_exposed_by_lmstudio: boolean;
  };
  profiles: Record<CalibrationMode, CalibrationProfile>;
  recommended_mode: CalibrationMode;
  active_mode: CalibrationMode;
  probes: Array<{
    mode: CalibrationMode;
    context_length: number;
    fits: boolean;
    reason: string;
  }>;
  // Kept optional so an older saved result can be identified and replaced.
  optimal_context?: number;
}

export async function startCalibration(params: {
  model?: string;
  embedding_model?: string;
  base_url?: string;
  gpu_target_percent?: number;
  ram_target_percent?: number;
  max_context?: number;
}): Promise<{ status: string; message: string }> {
  const response = await fetch('/api/admin/calibrate', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(params),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || 'Could not start calibration');
  }
  return response.json();
}

export async function getCalibrationStatus(): Promise<CalibrationStatus> {
  const response = await fetch('/api/admin/calibrate/status', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Could not fetch calibration status');
  return response.json();
}

export async function getCalibrationResult(): Promise<{ result: CalibrationResult | null }> {
  const response = await fetch('/api/admin/calibration-result', { credentials: 'same-origin' });
  if (!response.ok) throw new Error('Could not fetch calibration result');
  return response.json();
}

export async function applyCalibrationMode(
  mode: CalibrationMode,
): Promise<{ result: CalibrationResult }> {
  const response = await fetch('/api/admin/calibration/apply', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || `Could not apply ${mode} calibration mode`);
  }
  return response.json();
}
