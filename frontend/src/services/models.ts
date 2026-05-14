export interface ModelOption {
  id: string;
  name: string;
  api_type: string;
}

export async function listModels(): Promise<ModelOption[]> {
  const response = await fetch('/api/models', { credentials: 'same-origin' });
  if (!response.ok) return [];
  const data = await response.json();
  return data.models || [];
}

export async function ejectModel(model?: string): Promise<{ ok: boolean; instance_id?: string; detail?: string }> {
  const response = await fetch('/api/models/eject', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(model ? { model } : {}),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    return { ok: false, detail: data?.detail || 'Could not eject model' };
  }
  return { ok: true, instance_id: data?.instance_id };
}
