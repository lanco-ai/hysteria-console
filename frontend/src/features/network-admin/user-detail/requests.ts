import { ResourceError } from '../../../shared/readResource';
import type { UserDetail, UserDetailAlert, UserDetailHeatmap, UserDetailHourly } from './types';

export const USER_DETAIL_ENDPOINT = '/api/v1/admin/user/';

function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function stringValue(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function nonNegative(value: unknown): number {
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) return invalid();
  return value;
}
function boolValue(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid(); }
function list<T>(value: unknown, parse: (item: unknown) => T): T[] {
  return Array.isArray(value) ? value.map(parse) : invalid();
}

function alert(value: unknown): UserDetailAlert {
  const v = record(value);
  return { ts: stringValue(v.ts), kind: stringValue(v.kind), details: stringValue(v.details) };
}

function hourly(value: unknown): UserDetailHourly {
  const v = record(value);
  return { hour: stringValue(v.hour), bytes: nonNegative(v.bytes) };
}

function heatmap(value: unknown): UserDetailHeatmap {
  const v = record(value);
  const hours = list(v.hours, nonNegative);
  if (hours.length !== 24) return invalid();
  return { date: stringValue(v.date), hours };
}

export function parseUserDetail(value: unknown): UserDetail {
  const v = record(value);
  const expiresAt = v.expires_at;
  if (expiresAt !== null && typeof expiresAt !== 'string') return invalid();
  return {
    ts: stringValue(v.ts),
    uid: stringValue(v.uid),
    metered: boolValue(v.metered),
    disabled: boolValue(v.disabled),
    expired: boolValue(v.expired),
    expires_at: expiresAt,
    expiry_label: stringValue(v.expiry_label),
    note: stringValue(v.note),
    online: nonNegative(v.online),
    max_devices: nonNegative(v.max_devices),
    cycle_used_bytes: nonNegative(v.cycle_used_bytes),
    cycle_quota_bytes: nonNegative(v.cycle_quota_bytes),
    quota_extra_bytes: nonNegative(v.quota_extra_bytes),
    current_hour_bytes: nonNegative(v.current_hour_bytes),
    today_bytes: nonNegative(v.today_bytes),
    recent_alerts: list(v.recent_alerts, alert),
    hourly_bars: list(v.hourly_bars, hourly),
    heatmap: list(v.heatmap, heatmap),
  };
}

export async function readUserDetail(uid: string, signal: AbortSignal): Promise<UserDetail> {
  const response = await fetch(`${USER_DETAIL_ENDPOINT}${encodeURIComponent(uid)}`, {
    cache: 'no-store',
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
    signal,
  });
  if (!response.ok) {
    throw new ResourceError(
      `HTTP ${response.status}`,
      response.status,
      response.status === 401 ? 'login_required' : undefined,
    );
  }
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) return invalid();
  return parseUserDetail(await response.json());
}
