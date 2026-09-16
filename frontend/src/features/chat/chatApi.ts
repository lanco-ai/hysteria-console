export type ChatMessageData = {
  role: 'system' | 'user' | 'assistant';
  content: string;
};

export type ChatSettings = {
  base_url: string;
  model: string;
  temperature: number;
  api_key_configured: boolean;
  api_key_masked: string;
  reasoning_enabled: boolean;
  reasoning_effort: ReasoningEffort;
};

export type ReasoningEffort = 'auto' | 'low' | 'medium' | 'high';

export type SettingsUpdate = {
  base_url?: string;
  model?: string;
  temperature?: number;
  api_key?: string;
  reasoning_enabled?: boolean;
  reasoning_effort?: ReasoningEffort;
};

export type ChatModel = { id: string; name: string };
export type ChatConnectionResult = { ok: true; message: string; models_count: number };

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
    if (code === 'settings_incomplete') return '请先完成 API Base URL 和模型设置。';
    if (code === 'upstream_error') return '第三方 API 暂时不可用，请稍后重试。';
    if (code === 'authentication_failed') return '第三方 API 认证失败，请检查 API Key。';
    if (code === 'models_endpoint_unavailable') return '模型列表接口不可用，可手动填写 Model ID。';
    if (code === 'rate_limited') return '第三方 API 请求过于频繁，请稍后重试。';
    if (code === 'upstream_unavailable') return '第三方 API 当前不可用，请稍后重试。';
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
  if (!response.ok) throw new Error(errorMessage(response.status, payload));
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

export function completeChat(messages: ChatMessageData[]): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>('/api/chat/completions', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages }),
  });
}
