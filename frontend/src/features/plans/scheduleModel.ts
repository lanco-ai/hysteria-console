import type { RoutineBlock, ScheduleCategory, ScheduleDay, ScheduleRoutine } from './scheduleApi';

export const categories: Array<{ id: ScheduleCategory; label: string; hint: string }> = [
  { id: 'rest', label: '作息', hint: '睡眠、起床、午休' },
  { id: 'meal', label: '吃饭', hint: '三餐与补给' },
  { id: 'work', label: '工作', hint: '专注产出' },
  { id: 'study', label: '学习', hint: '输入与练习' },
  { id: 'think', label: '思考', hint: '复盘、规划、独处' },
  { id: 'connect', label: '联系', hint: '家人、朋友、合作' },
  { id: 'exercise', label: '运动', hint: '身体与精力' },
  { id: 'life', label: '生活', hint: '通勤、家务、放松' },
];
export const categoryLabel = Object.fromEntries(categories.map(item => [item.id, item.label])) as Record<ScheduleCategory, string>;
export const weekdayLabels = ['一', '二', '三', '四', '五', '六', '日'];

export type RepeatMode = 'once' | 'daily' | 'weekdays' | 'weekend' | 'custom';
export const repeatWeekdays: Record<Exclude<RepeatMode, 'once' | 'custom'>, number[]> = {
  daily: [0, 1, 2, 3, 4, 5, 6], weekdays: [0, 1, 2, 3, 4], weekend: [5, 6],
};

export function repeatModeFor(weekdays: number[]): RepeatMode {
  const key = [...weekdays].sort().join(',');
  const match = (Object.entries(repeatWeekdays) as Array<[RepeatMode, number[]]>).find(([, days]) => days.join(',') === key);
  return match ? match[0] : 'custom';
}

export function repeatLabel(weekdays: number[]): string {
  const mode = repeatModeFor(weekdays);
  if (mode === 'daily') return '每天';
  if (mode === 'weekdays') return '工作日';
  if (mode === 'weekend') return '周末';
  return `每周${weekdays.map(day => weekdayLabels[day]).join('、')}`;
}

/** Monday is 0, Sunday is 6. */
export function weekdayOf(date: string): number {
  const [year, month, day] = date.split('-').map(Number);
  return (new Date(Date.UTC(year || 2000, (month || 1) - 1, day || 1, 12)).getUTCDay() + 6) % 7;
}

export function toMinutes(clock: string): number {
  const [hours, minutes] = clock.split(':').map(Number);
  return (hours || 0) * 60 + (minutes || 0);
}

export function toClock(minutes: number): string {
  const value = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

/** An end at or before the start wraps past midnight, e.g. sleep 23:00–07:00. */
export function spanMinutes(start: string, end: string): number {
  const from = toMinutes(start);
  const to = toMinutes(end);
  return to > from ? to - from : 1440 - from + to;
}

export function formatDuration(minutes: number): string {
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (!hours) return `${rest} 分钟`;
  return rest ? `${hours} 小时 ${rest} 分` : `${hours} 小时`;
}

export function formatHours(minutes: number): string {
  const hours = Math.round(minutes / 6) / 10;
  return `${Number.isInteger(hours) ? hours : hours.toFixed(1)}h`;
}

export type ScheduleEntry = {
  key: string;
  kind: 'routine' | 'day' | 'task';
  id: string;
  title: string;
  category: ScheduleCategory | null;
  start: string;
  end: string;
  status: 'planned' | 'done' | 'skipped';
  notes: string;
  weekdays: number[];
};

export type TaskMarker = { id: string; title: string; start: string; minutes: number; done: boolean };

export function routineEntry(block: RoutineBlock, status: ScheduleEntry['status'] = 'planned'): ScheduleEntry {
  return {
    key: `routine:${block.id}`, kind: 'routine', id: block.id, title: block.title, category: block.category,
    start: block.start, end: block.end, status, notes: '', weekdays: block.weekdays,
  };
}

export function dayEntries(date: string, routine: ScheduleRoutine, day: ScheduleDay, tasks: TaskMarker[] = []): ScheduleEntry[] {
  const weekday = weekdayOf(date);
  const fromRoutine = routine.blocks.filter(block => block.weekdays.includes(weekday))
    .map(block => routineEntry(block, day.routine_status[block.id] || 'planned'));
  const fromDay = day.blocks.map((block): ScheduleEntry => ({
    key: `day:${block.id}`, kind: 'day', id: block.id, title: block.title, category: block.category,
    start: block.start, end: block.end, status: block.status, notes: block.notes, weekdays: [],
  }));
  const fromTasks = tasks.map((task): ScheduleEntry => ({
    key: `task:${task.id}`, kind: 'task', id: task.id, title: task.title, category: null,
    start: task.start, end: toClock(toMinutes(task.start) + task.minutes), status: task.done ? 'done' : 'planned', notes: '', weekdays: [],
  }));
  return [...fromRoutine, ...fromDay, ...fromTasks].sort((a, b) => toMinutes(a.start) - toMinutes(b.start) || a.title.localeCompare(b.title));
}

export function containsMinute(entry: Pick<ScheduleEntry, 'start' | 'end'>, minute: number): boolean {
  const from = toMinutes(entry.start);
  const to = toMinutes(entry.end);
  return to > from ? minute >= from && minute < to : minute >= from || minute < to;
}

/** Visible pieces of an entry on a 0–24h axis; overnight entries show as an evening and a morning piece. */
export function segmentsOf(entry: Pick<ScheduleEntry, 'start' | 'end'>): Array<{ from: number; to: number; continued: boolean }> {
  const from = toMinutes(entry.start);
  const to = toMinutes(entry.end);
  if (to > from) return [{ from, to, continued: false }];
  return [{ from, to: 1440, continued: false }, ...(to > 0 ? [{ from: 0, to, continued: true }] : [])];
}

export type PlacedSegment = { entry: ScheduleEntry; from: number; to: number; continued: boolean; lane: number; lanes: number };

/** Side-by-side columns for overlapping segments, the way calendar apps lay out a day. */
export function placeSegments(entries: ScheduleEntry[]): PlacedSegment[] {
  const pieces = entries.filter(entry => entry.status !== 'skipped')
    .flatMap(entry => segmentsOf(entry).map(segment => ({ entry, ...segment, lane: 0, lanes: 1 })))
    .sort((a, b) => a.from - b.from || b.to - a.to);
  const placed: PlacedSegment[] = [];
  let group: PlacedSegment[] = [];
  let groupEnd = -1;
  const closeGroup = () => {
    const lanes = Math.max(1, ...group.map(piece => piece.lane + 1));
    group.forEach(piece => { piece.lanes = lanes; });
    placed.push(...group);
    group = [];
  };
  for (const piece of pieces) {
    if (group.length && piece.from >= groupEnd) closeGroup();
    const laneEnds: number[] = [];
    group.forEach(other => { laneEnds[other.lane] = Math.max(laneEnds[other.lane] ?? -1, other.to); });
    let lane = laneEnds.findIndex(end => end <= piece.from);
    if (lane < 0) lane = laneEnds.length;
    piece.lane = lane;
    group.push(piece);
    groupEnd = Math.max(groupEnd, piece.to);
  }
  if (group.length) closeGroup();
  return placed;
}

export function allocation(entries: ScheduleEntry[]): Record<ScheduleCategory, number> {
  const totals = Object.fromEntries(categories.map(item => [item.id, 0])) as Record<ScheduleCategory, number>;
  entries.forEach(entry => {
    if (entry.category && entry.status !== 'skipped') totals[entry.category] += spanMinutes(entry.start, entry.end);
  });
  return totals;
}

export function overlapsWith(candidate: Pick<ScheduleEntry, 'start' | 'end'>, entries: ScheduleEntry[], ignoreKey = ''): ScheduleEntry[] {
  const mine = segmentsOf(candidate);
  return entries.filter(entry => entry.key !== ignoreKey && entry.kind !== 'task' && entry.status !== 'skipped'
    && segmentsOf(entry).some(other => mine.some(segment => segment.from < other.to && other.from < segment.to)));
}

export function newBlockId(): string {
  return (globalThis.crypto?.randomUUID?.() || `block-${Date.now()}-${Math.random().toString(16).slice(2)}`).replace(/[^A-Za-z0-9_-]/g, '').slice(0, 64);
}

const all = [0, 1, 2, 3, 4, 5, 6];
const workdays = [0, 1, 2, 3, 4];
const weekend = [5, 6];

/** A balanced starting routine; the owner reviews it before it is saved. */
export function suggestedRoutine(): ScheduleRoutine {
  const blocks: Array<Omit<RoutineBlock, 'id'>> = [
    { title: '起床 · 洗漱 · 喝水', category: 'rest', start: '07:00', end: '07:30', weekdays: all },
    { title: '早餐', category: 'meal', start: '07:30', end: '08:00', weekdays: all },
    { title: '规划今天的三件要事', category: 'think', start: '08:30', end: '08:45', weekdays: workdays },
    { title: '深度工作', category: 'work', start: '09:00', end: '12:00', weekdays: workdays },
    { title: '午餐', category: 'meal', start: '12:00', end: '12:45', weekdays: all },
    { title: '午休', category: 'rest', start: '12:45', end: '13:15', weekdays: all },
    { title: '工作 · 沟通与协作', category: 'work', start: '13:30', end: '18:00', weekdays: workdays },
    { title: '晚餐', category: 'meal', start: '18:30', end: '19:15', weekdays: all },
    { title: '运动', category: 'exercise', start: '19:30', end: '20:15', weekdays: all },
    { title: '学习', category: 'study', start: '20:30', end: '21:30', weekdays: all },
    { title: '联系家人或朋友', category: 'connect', start: '21:30', end: '21:50', weekdays: all },
    { title: '复盘与思考', category: 'think', start: '21:50', end: '22:15', weekdays: all },
    { title: '放松 · 洗漱 · 远离屏幕', category: 'life', start: '22:30', end: '23:00', weekdays: all },
    { title: '睡觉', category: 'rest', start: '23:00', end: '07:00', weekdays: all },
    { title: '长时段学习', category: 'study', start: '09:30', end: '11:30', weekdays: weekend },
    { title: '户外 · 生活 · 见朋友', category: 'life', start: '14:00', end: '17:00', weekdays: weekend },
  ];
  return {
    blocks: blocks.map(block => ({ ...block, id: newBlockId() })),
    targets: { rest: 480, meal: 120, work: 420, study: 90, think: 40, connect: 30, exercise: 45, life: 60 },
  };
}

export type BlockPreset = { title: string; category: ScheduleCategory; start: string; end: string };
export const presets: BlockPreset[] = [
  { title: '起床洗漱', category: 'rest', start: '07:00', end: '07:30' },
  { title: '早餐', category: 'meal', start: '07:30', end: '08:00' },
  { title: '午餐', category: 'meal', start: '12:00', end: '12:45' },
  { title: '晚餐', category: 'meal', start: '18:30', end: '19:15' },
  { title: '深度工作', category: 'work', start: '09:00', end: '11:00' },
  { title: '阅读学习', category: 'study', start: '20:30', end: '21:30' },
  { title: '复盘思考', category: 'think', start: '21:45', end: '22:15' },
  { title: '联系家人', category: 'connect', start: '21:15', end: '21:45' },
  { title: '运动', category: 'exercise', start: '19:00', end: '19:45' },
  { title: '睡觉', category: 'rest', start: '23:00', end: '07:00' },
];

export function withoutStaleMarks(day: ScheduleDay, routine: ScheduleRoutine): ScheduleDay {
  const ids = new Set(routine.blocks.map(block => block.id));
  return { ...day, routine_status: Object.fromEntries(Object.entries(day.routine_status).filter(([id]) => ids.has(id))) };
}
