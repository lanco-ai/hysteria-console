import type { JournalKind } from './journalApi';

export const journalKindLabels: Record<JournalKind, string> = {
  life: '生活', ai_storage: 'AI / 存储学习', paper: '论文阅读', ielts: '雅思练习',
  thought: '思考变化', daily_review: '每日复盘', weekly_review: '每周回顾',
};

/**
 * A record's time in the zone it was written in. Python accepts a few zone names browsers do
 * not (for example `localtime`); such a time is shown in UTC, marked, rather than failing the page.
 */
export function zonedTime(value: string, timezone: string, options: Intl.DateTimeFormatOptions): string {
  try {
    return new Intl.DateTimeFormat('zh-CN', { ...options, timeZone: timezone }).format(new Date(value));
  } catch {
    return `${new Intl.DateTimeFormat('zh-CN', { ...options, timeZone: 'UTC' }).format(new Date(value))} UTC`;
  }
}

/** The calendar day of an instant in a zone, as YYYY-MM-DD (UTC when the zone is unknown). */
export function zonedDay(value: string, timezone: string): string {
  const parts = (zone: string) => new Intl.DateTimeFormat('en-CA', { timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
  try { return parts(timezone); } catch { return parts('UTC'); }
}
