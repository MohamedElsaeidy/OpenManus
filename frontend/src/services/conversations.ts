export interface Conversation {
  id: string;
  conversation_id: string;
  title: string;
  model?: string | null;
  settings?: {
    disabled_tools?: string[];
  };
  latest_task_id?: string | null;
  latest_status?: string | null;
  state?: 'idle' | 'running' | 'paused' | 'waiting' | 'finished' | 'error' | 'stuck';
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConversationEvent {
  id?: string;
  type: 'progress';
  name: string;
  content: Record<string, unknown>;
  task_id?: string;
  created_at?: string | null;
}

export interface ConversationHistory {
  conversation: Conversation;
  tasks: Array<{
    id: string;
    task_id: string;
    status?: string;
    request?: string;
    conversation_id?: string;
    created_at?: string | null;
  }>;
  events: ConversationEvent[];
}

export interface ConversationRuntime {
  conversation_id: string;
  status: 'running' | 'idle';
  running_count: number;
  hidden_system_containers?: number;
  sandbox?: {
    exists: boolean;
    status: string;
    container?: {
      id: string;
      name: string;
      image: string;
    } | null;
  };
  urls?: Array<{
    port: string;
    url: string;
    pid: number;
    command: string;
    args: string;
  }>;
  processes: Array<{
    pid: number;
    ppid: number;
    stat: string;
    elapsed: string;
    command: string;
    args: string;
    ports?: string[];
    zombie?: boolean;
    protected?: boolean;
  }>;
  containers: Array<{
    id: string;
    name: string;
    image: string;
    status: string;
    command: string;
    protected?: boolean;
  }>;
}

export interface SkillSummary {
  name: string;
  path: string;
  type: string;
  version: string;
  agent: string;
  triggers: string[];
}

export async function listConversations(): Promise<{ conversations: Conversation[] }> {
  const response = await fetch('/api/conversations', {
    credentials: 'same-origin',
  });
  if (!response.ok) return { conversations: [] };
  return response.json();
}

export async function createConversation(title = 'New conversation', model?: string): Promise<Conversation> {
  const response = await fetch('/api/conversations', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title, model }),
  });
  if (!response.ok) throw new Error('Could not create conversation');
  return response.json();
}

export async function getConversation(conversationId: string): Promise<Conversation> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not load conversation');
  return response.json();
}

export async function getConversationHistory(conversationId: string): Promise<ConversationHistory> {
  const response = await fetch(`/api/conversations/${conversationId}/events/history`, {
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not load conversation history');
  return response.json();
}

export async function getConversationRuntime(conversationId: string): Promise<ConversationRuntime> {
  const response = await fetch(`/api/conversations/${conversationId}/runtime`, {
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not load conversation runtime');
  return response.json();
}

export async function killConversationProcess(conversationId: string, pid: number): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/runtime/processes/${pid}/kill`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ signal: 'TERM' }),
  });
  if (!response.ok) throw new Error('Could not kill process');
}

export async function stopConversationContainer(conversationId: string, containerId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/runtime/containers/${containerId}/stop`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not stop container');
}

export async function pauseConversationSandbox(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/sandbox/pause`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not pause sandbox');
}

export async function resumeConversationSandbox(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}/sandbox/resume`, {
    method: 'POST',
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not resume sandbox');
}

export async function listSkills(conversationId?: string): Promise<{ skills: SkillSummary[] }> {
  const suffix = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : '';
  const response = await fetch(`/api/skills${suffix}`, {
    credentials: 'same-origin',
  });
  if (!response.ok) return { skills: [] };
  return response.json();
}

export async function sendConversationMessage(
  conversationId: string,
  message: string,
  model?: string,
): Promise<{ conversation_id: string; task_id: string; queued: boolean; created_task: boolean }> {
  const response = await fetch(`/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, model }),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new Error(data.detail || 'Could not send message');
  }
  return response.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`/api/conversations/${conversationId}`, {
    method: 'DELETE',
    credentials: 'same-origin',
  });
  if (!response.ok) throw new Error('Could not delete conversation');
}

export async function updateConversationSettings(
  conversationId: string,
  settings: { model?: string; disabled_tools?: string[] },
): Promise<Conversation> {
  const response = await fetch(`/api/conversations/${conversationId}/settings`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(settings),
  });
  if (!response.ok) throw new Error('Could not update conversation settings');
  return response.json();
}
