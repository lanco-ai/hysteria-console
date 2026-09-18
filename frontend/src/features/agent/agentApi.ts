export type AgentUserRules = {
  username: string;
  revision: string;
  rules: string[];
  fake_ip_filter: string[];
  tun_route_exclude_address: string[];
};

export type AgentPlan = {
  change_id: string;
  target_user: string;
  pack: string;
  label: string;
  description: string;
  before_revision: string;
  after_revision: string;
  additions: string[];
  requires_confirmation: boolean;
};

export type AgentPlanResponse = {
  ok: true;
  action: 'inspect' | 'apply_pack';
  target_user: string;
  explanation: string;
  snapshot?: AgentUserRules;
  plan?: AgentPlan;
};

export class AgentApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(message: string, status: number, code: string) {
    super(message);
    this.name = 'AgentApiError';
    this.status = status;
    this.code = code;
  }
}

async function parse<T>(response: Response): Promise<T> {
  let value: unknown;
  try { value = await response.json(); } catch { throw new AgentApiError('服务器响应无效', response.status, 'invalid_response'); }
  if (!response.ok || !value || typeof value !== 'object') {
    const data = value as Record<string, unknown> | null;
    const code = typeof data?.error === 'string' ? data.error : 'request_failed';
    const message = code === 'login_required'
      ? '管理员登录已失效'
      : code === 'revision_conflict'
        ? '目标用户规则已变化，请重新读取并生成预览'
        : `请求失败（${response.status}）`;
    throw new AgentApiError(message, response.status, code);
  }
  return value as T;
}

export async function loadAgentUsers(signal?: AbortSignal): Promise<string[]> {
  const response = await fetch('/api/v1/admin/rules', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, ...(signal ? { signal } : {}) });
  const data = await parse<{ users?: unknown }>(response);
  return Array.isArray(data.users) ? data.users.filter((value): value is string => typeof value === 'string' && value.trim().length > 0) : [];
}

export async function planAgent(message: string, targetUser: string, signal?: AbortSignal): Promise<AgentPlanResponse> {
  const response = await fetch('/api/v1/admin/agent/plan', {
    method: 'POST', credentials: 'same-origin', ...(signal ? { signal } : {}),
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ message, target_user: targetUser }),
  });
  return parse<AgentPlanResponse>(response);
}

export async function applyAgentChange(changeId: string, signal?: AbortSignal): Promise<{ ok: true; result: { username: string; revision: string } }> {
  const response = await fetch('/api/v1/admin/agent/apply', {
    method: 'POST', credentials: 'same-origin', ...(signal ? { signal } : {}),
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ change_id: changeId }),
  });
  return parse(response);
}

export async function undoAgentChange(changeId: string, signal?: AbortSignal): Promise<{ ok: true; result: { username: string; revision: string } }> {
  const response = await fetch('/api/v1/admin/agent/undo', {
    method: 'POST', credentials: 'same-origin', ...(signal ? { signal } : {}),
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify({ change_id: changeId }),
  });
  return parse(response);
}
