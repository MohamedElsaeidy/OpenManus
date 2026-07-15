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

export interface CalibrationResult {
  model_id: string;
  embedding_model: string;
  optimal_context: number;
  max_context_found: number;
  generation_speed: number;
  evaluation_speed: number;
  gpu_offload: string;
}

export async function startCalibration(params: {
  model?: string;
  embedding_model?: string;
  base_url?: string;
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
