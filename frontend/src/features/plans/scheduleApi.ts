export type ScheduleCategory = 'rest' | 'meal' | 'work' | 'study' | 'think' | 'connect' | 'exercise' | 'life';
export type ScheduleBlockStatus = 'planned' | 'done' | 'skipped';

type BlockBase = { id: string; title: string; category: ScheduleCategory; start: string; end: string };
/** Weekdays run 0 (Monday) to 6 (Sunday). */
export type RoutineBlock = BlockBase & { weekdays: number[] };
export type DayBlock = BlockBase & { notes: string; status: ScheduleBlockStatus };
export type ScheduleRoutine = { blocks: RoutineBlock[]; targets: Partial<Record<ScheduleCategory, number>> };
export type ScheduleDay = { blocks: DayBlock[]; routine_status: Record<string, 'done' | 'skipped'>; note: string };
export type ScheduleView = { date: string; routine: ScheduleRoutine; routine_revision: string; day: ScheduleDay; day_revision: string };

async function scheduleRequest(url: string, init: RequestInit = {}): Promise<ScheduleView> {
  const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', ...init });
  const payload = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) {
    throw new Error(response.status === 401 ? '登录已失效，请重新登录。'
      : response.status === 403 ? '当前账号没有管理行程的权限。'
        : response.status === 409 ? '行程已在其他设备更新，请重新读取后再修改。'
          : response.status === 413 ? '行程内容过多，请减少后重试。'
            : response.status === 422 ? '行程内容不符合要求，请检查时间与标题。'
              : '行程暂时无法读取或保存，请稍后重试。');
  }
  return payload as ScheduleView;
}

export function loadSchedule(date: string, signal?: AbortSignal): Promise<ScheduleView> {
  return scheduleRequest(`/api/plans/schedule?date=${encodeURIComponent(date)}`, signal ? { signal } : {});
}

export function saveScheduleDay(date: string, day: ScheduleDay, revision: string): Promise<ScheduleView> {
  return scheduleRequest('/api/plans/schedule/day', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date, day, revision }),
  });
}

export function saveScheduleRoutine(date: string, routine: ScheduleRoutine, revision: string): Promise<ScheduleView> {
  return scheduleRequest('/api/plans/schedule/routine', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date, routine, revision }),
  });
}
