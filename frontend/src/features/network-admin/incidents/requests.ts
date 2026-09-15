import { postFormJson } from '../../../shared/postForm';
import { ResourceError } from '../../../shared/readResource';
import type { IncidentAlert, IncidentPeak, IncidentPeakUser, IncidentRadar, IncidentRadarRow, IncidentStats, IncidentUser, Incidents } from './types';

export const INCIDENTS_ENDPOINT = '/api/v1/admin/incidents';
function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function str(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid(); }
function int(value: unknown): number { return typeof value === 'number' && Number.isInteger(value) && value >= 0 ? value : invalid(); }
function finite(value: unknown): number { return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : invalid(); }
function list<T>(value: unknown, parse: (item: unknown) => T): T[] { return Array.isArray(value) ? value.map(parse) : invalid(); }
function stats(value: unknown): IncidentStats { const v = record(value); return { current_hour_bytes: int(v.current_hour_bytes), today_bytes: int(v.today_bytes), yesterday_bytes: int(v.yesterday_bytes), last_7d_bytes: int(v.last_7d_bytes), cycle_bytes: int(v.cycle_bytes), cycle_day: int(v.cycle_day), cycle_total_days: int(v.cycle_total_days), online: int(v.online) }; }
function peakUser(value: unknown): IncidentPeakUser { const v = record(value); return { user: str(v.user), bytes: int(v.bytes) }; }
function peak(value: unknown): IncidentPeak { const v = record(value); return { hour: str(v.hour), bytes: int(v.bytes), users: list(v.users, peakUser) }; }
function user(value: unknown): IncidentUser { const v = record(value); return { user: str(v.user), revision: str(v.revision), last_24h_bytes: int(v.last_24h_bytes), cycle_used_bytes: int(v.cycle_used_bytes), quota_bytes: int(v.quota_bytes), quota_percent: finite(v.quota_percent), online: int(v.online), disabled: bool(v.disabled), expired: bool(v.expired), expiry_label: str(v.expiry_label), note: str(v.note) }; }
function radarRow(value: unknown): IncidentRadarRow { const v = record(value); return { key: str(v.key), label: str(v.label), status: str(v.status), ok: bool(v.ok), bytes: int(v.bytes), share: finite(v.share), active_users: int(v.active_users), online: v.online === null ? null : int(v.online), profile: str(v.profile), note: str(v.note) }; }
function radar(value: unknown): IncidentRadar { const v = record(value); return { window_hours: int(v.window_hours), total_bytes: int(v.total_bytes), recommendation: str(v.recommendation), reason: str(v.reason), rows: list(v.rows, radarRow) }; }
function alert(value: unknown): IncidentAlert { const v = record(value); return { kind: str(v.kind), label: str(v.label), user: str(v.user), key: str(v.key) }; }

export function parseIncidents(value: unknown): Incidents { const v = record(value); return { ts: str(v.ts), stats: stats(v.stats), peak_hour: peak(v.peak_hour), users: list(v.users, user), line_radar: radar(v.line_radar), cost_calibration: record(v.cost_calibration), alerts: list(v.alerts, alert) }; }

export async function writeIncidentAction(action: 'pause-user' | 'rotate-token', fields: Record<string, string>, signal: AbortSignal) {
  const { value, status } = await postFormJson(`/api/v1/admin/operations/${action}`, fields, signal);
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new ResourceError('操作结果格式无效', status);
  if (status === 401) throw new ResourceError('管理员登录已失效', 401, 'login_required');
  if (status !== 200) throw new ResourceError(`HTTP ${status}`, status);
  return value;
}

