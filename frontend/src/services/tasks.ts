import { getActiveConnectionPayload } from './conversations';

export interface CreateTaskParams {
  taskId?: string;
  conversationId?: string;
  model?: string;
  prompt: string;
  llm_connection?: Record<string, unknown>;
}

export interface Task {
  id: string;
  created_at: string;
  updated_at?: string | null;
  request: string;
  status?: string;
  result?: unknown;
  conversation_id?: string;
}

export interface PageTasksParams {
  page: number;
  pageSize: number;
}

export interface PageTasksResult {
  tasks: Task[];
  total: number;
}

export interface GetTaskParams {
  taskId: string;
}

export interface GetTaskEventsParams {
  taskId: string;
}

export interface SendTaskMessageParams {
  taskId: string;
  message: string;
}

export interface TerminateTaskParams {
  taskId: string;
}

export interface ShareTaskParams {
  taskId: string;
}

export async function createTask(
  params: CreateTaskParams,
): Promise<{ data?: { task_id: string; message: string; conversation_id?: string }; error?: string; status?: number }> {
  try {
    const formData = new FormData();

    if (params.taskId) {
      formData.append('task_id', params.taskId);
    }
    if (params.conversationId) {
      formData.append('conversation_id', params.conversationId);
    }
    if (params.model) {
      formData.append('model', params.model);
    }
    const connectionPayload = params.llm_connection || getActiveConnectionPayload(params.model);
    if (connectionPayload) {
      formData.append('llm_connection', JSON.stringify(connectionPayload));
    }
    formData.append('prompt', params.prompt);

    const response = await fetch('/api/tasks', {
      method: 'POST',
      credentials: 'same-origin',
      body: formData,
    });

    if (!response.ok) {
      let detail = 'Failed to create task';
      try {
        const payload = await response.json();
        detail = payload.detail || payload.error || detail;
      } catch {
        // keep default detail
      }
      return { error: detail, status: response.status };
    }

    const data = await response.json();
    return { data, status: response.status };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' };
  }
}

export function getTaskEvents(params: GetTaskEventsParams): EventSource {
  return new EventSource(`/api/tasks/${params.taskId}/events`, { withCredentials: true });
}

export async function getTask(params: GetTaskParams): Promise<{ data?: Task; error?: string }> {
  try {
    const response = await fetch(`/api/tasks/${params.taskId}`, {
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error('Failed to fetch task');
    }

    const data = await response.json();
    return { data };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' };
  }
}

export async function sendTaskMessage(params: SendTaskMessageParams): Promise<{ data?: { id: string; queued: boolean }; error?: string }> {
  try {
    const response = await fetch(`/api/tasks/${params.taskId}/message`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ message: params.message }),
    });

    if (!response.ok) {
      throw new Error('Failed to send task message');
    }

    const data = await response.json();
    return { data };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' };
  }
}

export async function terminateTask(params: TerminateTaskParams): Promise<{ data?: Task; error?: string }> {
  try {
    const response = await fetch(`/api/tasks/${params.taskId}/terminate`, {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error('Failed to terminate task');
    }

    const data = await response.json();
    return { data };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' };
  }
}

export async function pageTasks(params: PageTasksParams): Promise<{ data?: PageTasksResult; error?: string }> {
  try {
    // 这里应该调用实际的API
    const response = await fetch(`/api/tasks?page=${params.page}&pageSize=${params.pageSize}`, {
      method: 'GET',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
      },
    });

    if (!response.ok) {
      throw new Error('Failed to fetch tasks');
    }

    const data = await response.json();
    return { data };
  } catch (error) {
    return { error: error instanceof Error ? error.message : 'Unknown error' };
  }
}
