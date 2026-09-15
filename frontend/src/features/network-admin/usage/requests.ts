import { ResourceError } from '../../../shared/readResource';
import type { Usage, UsageHistory, UsageHeatmapRow, UsageHourlyPoint, UsageStats, UsageSummary, UsageTopUser } from './types';

function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function str(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function nonNegativeInt(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) return invalid();
  return value;
}
function list<T>(value: unknown, parse: (item: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(parse) : invalid();
}

function stats(value: unknown): UsageStats {
  const v = record(value);
  return {
    current_hour_bytes: nonNegativeInt(v.current_hour_bytes),
    today_bytes: nonNegativeInt(v.today_bytes),
    yesterday_bytes: nonNegativeInt(v.yesterday_bytes),
    last_7d_bytes: nonNegativeInt(v.last_7d_bytes),
    cycle_bytes: nonNegativeInt(v.cycle_bytes),
    cycle_day: nonNegativeInt(v.cycle_day),
    cycle_total_days: nonNegativeInt(v.cycle_total_days),
    online: nonNegativeInt(v.online),
  };
}

function hourly(value: unknown): UsageHourlyPoint {
  const v = record(value);
  return { hour: str(v.hour), bytes: nonNegativeInt(v.bytes) };
}

function heatmap(value: unknown): UsageHeatmapRow {
  const v = record(value);
  const hours = list(v.hours, nonNegativeInt);
  if (hours.length !== 24) return invalid();
  return { date: str(v.date), hours };
}

function topUser(value: unknown): UsageTopUser {
  const v = record(value);
  return {
    uid: str(v.uid),
    last_24h_bytes: nonNegativeInt(v.last_24h_bytes),
    spark: list(v.spark, nonNegativeInt),
  };
}

export function parseUsageSummary(value: unknown): UsageSummary {
  const v = record(value);
  return { ts: str(v.ts), stats: stats(v.stats) };
}

export function parseUsage(value: unknown): Usage {
  const summary = parseUsageSummary(value);
  const v = record(value);
  return {
    ...summary,
    hourly_totals: list(v.hourly_totals, hourly),
    heatmap: list(v.heatmap, heatmap),
    top_n: list(v.top_n, topUser),
  };
}

export function parseUsageHistory(value: unknown): UsageHistory {
  const v = record(value);
  const retentionDays = nonNegativeInt(v.retention_days);
  if (retentionDays < 1) return invalid();
  const dates = list(v.dates, str);
  const totals = list(v.totals, nonNegativeInt);
  if (dates.length !== retentionDays || totals.length !== retentionDays) return invalid();
  const users = list(v.users, item => {
    const row = record(item);
    const values = list(row.values, nonNegativeInt);
    if (values.length !== retentionDays) return invalid();
    return { uid: str(row.uid), values };
  });
  return { ts: str(v.ts), retention_days: retentionDays, dates, users, totals };
}

export async function readUsageSummary(signal: AbortSignal): Promise<UsageSummary> {
  return read('/api/v1/admin/usage?summary=yes', signal, parseUsageSummary);
}

export async function readUsage(signal: AbortSignal): Promise<Usage> {
  return read('/api/v1/admin/usage', signal, parseUsage);
}

export async function readUsageHistory(signal: AbortSignal): Promise<UsageHistory> {
  return read('/api/v1/admin/usage-history', signal, parseUsageHistory);
}

async function read<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  const response = await fetch(path, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new ResourceError(`HTTP ${response.status}`, response.status, response.status === 401 ? 'login_required' : undefined);
  }
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) return invalid();
  return parse(await response.json());
}

