export type JournalKind = 'life' | 'ai_storage' | 'paper' | 'ielts' | 'thought' | 'daily_review' | 'weekly_review';
export type JournalSkill = 'listening' | 'speaking' | 'reading' | 'writing';

export type JournalDraft = {
  kind: JournalKind;
  occurred_at: string;
  ended_at: string | null;
  timezone: string;
  title: string;
  body: string;
  question: string;
  explanation: string;
  source: string;
  uncertainty: string;
  next_check: string;
  skill: JournalSkill | null;
  material: string;
  raw_result: string;
  correction: string;
  previous_view: string;
  trigger_evidence: string;
  current_view: string;
  learning_state: string;
  evidence: string;
  takeaway: string;
};

export type JournalRecord = JournalDraft & {
  chat_source?: { conversation_id: string; message_id: string; title: string; sha256: string } | null;
  id: string;
  local_date: string;
  created_at: string;
  updated_at: string;
  revision: number;
};

export type JournalList = { items: JournalRecord[] };
export type JournalSummary = { week_start: string; week_end: string; total: number; counts: Partial<Record<JournalKind, number>>; items: JournalRecord[] };

async function journalRequest<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...init });
  const data = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    const message = response.status === 409 ? '记录已在其他设备修改。当前草稿已保留，请刷新记录后再处理。'
      : response.status === 401 ? '登录已失效，请重新登录。'
        : response.status === 403 ? '当前账号没有管理记录的权限。'
          : response.status === 413 ? '记录内容过长，请缩短后重试。'
            : response.status === 422 ? '记录格式不符合要求，请检查时间与内容。'
              : '记录暂时无法读取或保存，请稍后重试。';
    throw new Error(message);
  }
  return data as T;
}

export function listJournal(filters: { date?: string | undefined; kind?: string | undefined; q?: string | undefined; start?: string | undefined; end?: string | undefined }, signal?: AbortSignal): Promise<JournalList> {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => { if (value) query.set(key, value); });
  return journalRequest<JournalList>(`/api/journal${query.size ? `?${query}` : ''}`, signal ? { signal } : {});
}

export function createJournal(draft: JournalDraft): Promise<{ item: JournalRecord }> {
  return journalRequest('/api/journal', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(draft) });
}

export function updateJournal(id: string, draft: JournalDraft, revision: number): Promise<{ item: JournalRecord }> {
  return journalRequest(`/api/journal/${encodeURIComponent(id)}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...draft, revision }) });
}

export function deleteJournal(id: string, revision: number): Promise<void> {
  return journalRequest(`/api/journal/${encodeURIComponent(id)}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision }) });
}

export function loadJournalSummary(weekStart: string, signal?: AbortSignal): Promise<JournalSummary> {
  return journalRequest(`/api/journal/summary?week_start=${encodeURIComponent(weekStart)}`, signal ? { signal } : {});
}

export async function downloadJournal(): Promise<void> {
  const response = await fetch('/api/journal/export', { credentials: 'same-origin', cache: 'no-store' });
  if (!response.ok) throw new Error('导出失败，请稍后重试。');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = 'life-learning-journal.json';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
