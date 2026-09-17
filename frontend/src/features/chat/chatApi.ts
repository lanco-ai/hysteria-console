export type ChatMessageData = {
  role: 'system' | 'user' | 'assistant';
  content: string;
};

export type ChatSettings = {
  base_url: string;
  temperature: number;
  api_key_configured: boolean;
  api_key_masked: string;
};

export type ReasoningEffort = 'auto' | 'low' | 'medium' | 'high';

export type SettingsUpdate = {
  base_url?: string;
  temperature?: number;
  api_key?: string;
};

export type ChatModel = { id: string; name: string; context_window?: number };
export type ChatConnectionResult = { ok: true; message: string; models_count: number };
export type ChatStreamEvent =
  | { type: 'delta'; text: string }
  | { type: 'usage'; usage: Record<string, unknown> }
  | { type: 'done' }
  | { type: 'notice'; notice: string }
  | { type: 'error'; error: string; upstream_status?: number; retry_after?: string };

export class ChatApiError extends Error {
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = 'ChatApiError';
  }
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    return undefined;
  }
}
function errorMessage(status: number, payload: unknown): string {
  if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
    const code = (payload as Record<string, unknown>).error;
    if (code === 'api_key_not_configured') return '请先在 API 设置中填写 API Key。';
    if (code === 'settings_incomplete') return '请先完成 API Base URL 设置。';
    if (code === 'upstream_error') return '第三方 API 暂时不可用，请稍后重试。';
    if (code === 'authentication_failed') return '第三方 API 认证失败，请检查 API Key。';
    if (code === 'models_endpoint_unavailable') return '模型列表接口不可用，可手动填写模型标识。';
    if (code === 'rate_limited') return '第三方 API 请求过于频繁，请稍后重试。';
    if (code === 'upstream_unavailable') return '模型服务当前没有可用容量，请稍后重试或切换模型。';
    if (code === 'timeout') return '连接第三方 API 超时，请稍后重试。';
    if (code === 'login_required') return '登录已失效，请重新登录。';
  }
  return `请求失败（HTTP ${status}）`;
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
    headers: { Accept: 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  const payload = await readJson(response);
  if (!response.ok) throw new ChatApiError(response.status, errorMessage(response.status, payload));
  return payload as T;
}

export function loadChatSettings(): Promise<ChatSettings> {
  return requestJson<ChatSettings>('/api/chat/settings');
}

export function saveChatSettings(update: SettingsUpdate): Promise<ChatSettings> {
  return requestJson<ChatSettings>('/api/chat/settings', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(update),
  });
}

export function loadChatModels(): Promise<ChatModel[]> {
  return requestJson<ChatModel[]>('/api/chat/models');
}

export function testChatConnection(): Promise<ChatConnectionResult> {
  return requestJson<ChatConnectionResult>('/api/chat/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
}

export function completeChat(
  messages: ChatMessageData[],
  model: string,
  reasoning_effort: ReasoningEffort = 'auto',
): Promise<Record<string, unknown>> {
  const body: { messages: ChatMessageData[]; model: string; reasoning_effort?: ReasoningEffort } = { messages, model };
  if (reasoning_effort !== 'auto') body.reasoning_effort = reasoning_effort;
  return requestJson<Record<string, unknown>>('/api/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function decodeStreamEvent(rawEvent: string): ChatStreamEvent | null {
  const data = rawEvent.split('\n')
    .filter(line => line.startsWith('data:'))
    .map(line => line.slice(5).replace(/^ /, ''))
    .join('\n')
    .trim();
  if (!data) return null;
  if (data === '[DONE]') return { type: 'done' };
  try {
    const value: unknown = JSON.parse(data);
    if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
    const event = value as Record<string, unknown>;
    if (event.type === 'delta' && typeof event.text === 'string') return { type: 'delta', text: event.text };
    if (event.type === 'usage' && event.usage && typeof event.usage === 'object' && !Array.isArray(event.usage)) {
      return { type: 'usage', usage: event.usage as Record<string, unknown> };
    }
    if (event.type === 'done') return { type: 'done' };
    if (event.type === 'notice' && typeof event.notice === 'string') return { type: 'notice', notice: event.notice };
    if (event.type === 'error' && typeof event.error === 'string') {
      return {
        type: 'error',
        error: event.error,
        ...(typeof event.upstream_status === 'number' ? { upstream_status: event.upstream_status } : {}),
        ...(typeof event.retry_after === 'string' ? { retry_after: event.retry_after } : {}),
      };
    }
  } catch {
    // Ignore comments and malformed partial records; the next chunk may complete them.
  }
  return null;
}

export async function streamChat(
  messages: ChatMessageData[],
  model: string,
  reasoning_effort: ReasoningEffort = 'auto',
  signal?: AbortSignal,
  onEvent?: (event: ChatStreamEvent) => void,
): Promise<void> {
  const body: { messages: ChatMessageData[]; model: string; stream: true; reasoning_effort?: ReasoningEffort } = { messages, model, stream: true };
  if (reasoning_effort !== 'auto') body.reasoning_effort = reasoning_effort;
  const response = await fetch('/api/chat/completions', {
    method: 'POST',
    credentials: 'same-origin',
    cache: 'no-store',
    ...(signal ? { signal } : {}),
    headers: { Accept: 'text/event-stream', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const payload = await readJson(response);
    throw new ChatApiError(response.status, errorMessage(response.status, payload));
  }
  if (!response.body) throw new ChatApiError(502, '第三方 API 没有返回流式响应。');
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  const emitRecords = () => {
    const events: ChatStreamEvent[] = [];
    while (buffer.includes('\n\n')) {
      const index = buffer.indexOf('\n\n');
      const event = decodeStreamEvent(buffer.slice(0, index));
      buffer = buffer.slice(index + 2);
      if (event) events.push(event);
    }
    events.forEach(event => onEvent?.(event));
  };
  while (true) {
    const result = await reader.read();
    buffer += decoder.decode(result.value || new Uint8Array(), { stream: !result.done });
    buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
    emitRecords();
    if (result.done) break;
  }
  if (buffer.trim()) {
    const event = decodeStreamEvent(buffer);
    if (event) onEvent?.(event);
  }
}
