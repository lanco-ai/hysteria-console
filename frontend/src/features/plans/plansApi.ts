export type PlanQuadrant = 'important_urgent' | 'important' | 'urgent' | 'later';
export type PlanStatus = 'todo' | 'in_progress' | 'done';

export type PlanItem = {
  id: string;
  title: string;
  notes: string;
  quadrant: PlanQuadrant;
  plan_date: string;
  timezone: string;
  start_time: string | null;
  due_at: string | null;
  estimate_minutes: number;
  reminder_at: string | null;
  status: PlanStatus;
  created_at: string;
  updated_at: string;
};

export type PlanSnapshot = { items: PlanItem[]; revision: string };
export type PlanAssistantTaskContext = Pick<PlanItem, 'title' | 'quadrant' | 'status' | 'estimate_minutes'>;
export type PlanAssistantSuggestion = {
  title: string;
  notes: string;
  quadrant: PlanQuadrant;
  start_time: string;
  estimate_minutes: number;
  reminder_offset_minutes: number;
  reason: string;
};
export type PlanAssistantPreview = {
  summary: string;
  model: string;
  service_name: string;
  structured_output: 'gemini_native_schema' | 'json_schema' | 'json_text_fallback';
  suggestions: PlanAssistantSuggestion[];
};

async function requestPlanAt<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...init });
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const code = typeof payload.error === 'string' ? payload.error : '';
    const message = response.status === 401 ? '登录已失效，请重新登录。'
      : response.status === 403 ? '当前账号没有管理计划的权限。'
      : response.status === 409 ? '计划已在其他设备更新，请刷新后再保存。'
      : response.status === 413 ? '计划数据过多，请减少内容后重试。'
      : code === 'service_not_configured' ? '请先到服务中心配置助手服务。'
        : code === 'model_not_selected' ? '请先到服务中心为今日计划助手选择模型。'
          : code === 'model_not_available' ? '所选模型已不可用，请刷新模型列表并重新选择。'
            : code === 'authentication_failed' ? 'AI 服务认证失败，请检查服务中心中的 API Key。'
              : code === 'permission_denied' ? '所选 AI 服务没有该模型的访问权限。'
                : code === 'rate_limited' ? 'AI 服务限流，请稍后再试。'
                  : code === 'timeout' ? 'AI 服务响应超时，请稍后重试。'
      : code === 'invalid_plan' ? '计划内容不符合要求，请检查字段。'
                : code === 'invalid_model_response' ? 'AI 返回内容无法安全解析，请调整描述后重试。'
                  : '计划暂时无法读取或保存，请稍后重试。';
    throw new Error(message);
  }
  return payload as T;
}

function requestPlan<T>(init: RequestInit = {}): Promise<T> {
  return requestPlanAt<T>('/api/plans', init);
}

export function loadPlanSnapshot(signal?: AbortSignal): Promise<PlanSnapshot> {
  return requestPlan<PlanSnapshot>(signal ? { signal } : {});
}

export function savePlanSnapshot(snapshot: PlanSnapshot): Promise<PlanSnapshot> {
  return requestPlan<PlanSnapshot>({
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(snapshot),
  });
}

export function requestPlanAssistant(input: {
  date: string;
  timezone: string;
  request: string;
  existing_tasks: PlanAssistantTaskContext[];
}): Promise<PlanAssistantPreview> {
  return requestPlanAt<PlanAssistantPreview>('/api/plans/assistant', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
}
