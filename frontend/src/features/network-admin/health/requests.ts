import { postFormJson } from '../../../shared/postForm';
import { ResourceError } from '../../../shared/readResource';
import type { Health, HealthCalibration, HealthCalibrationPolicy, HealthCalibrationWindow, HealthLineRadar, HealthLineRadarRow, HealthOperation, HealthStatus, HealthUpdate } from './types';

export const HEALTH_ENDPOINT = '/api/v1/admin/health';

function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function status(value: unknown): HealthStatus {
  const v = record(value);
  if (typeof v.title !== 'string' || typeof v.ok !== 'boolean' || typeof v.label !== 'string') return invalid();
  return { title: v.title, ok: v.ok, label: v.label };
}

function nonNegative(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return invalid();
  return value;
}

function optionalNonNegative(value: unknown): number | null {
  if (value === null) return null;
  return nonNegative(value);
}

function lineRadar(value: unknown): HealthLineRadar {
  const v = record(value);
  if (typeof v.window_hours !== 'number' || !Number.isInteger(v.window_hours) || v.window_hours <= 0 || typeof v.total_bytes !== 'number' || !Number.isInteger(v.total_bytes) || v.total_bytes < 0 || typeof v.recommendation !== 'string' || typeof v.reason !== 'string' || !Array.isArray(v.rows)) return invalid();
  const rows: HealthLineRadarRow[] = v.rows.map(item => {
    const row = record(item);
    if (typeof row.key !== 'string' || typeof row.label !== 'string' || typeof row.status !== 'string' || typeof row.ok !== 'boolean' || typeof row.share !== 'number' || !Number.isFinite(row.share) || row.share < 0 || row.share > 100 || typeof row.active_users !== 'number' || !Number.isInteger(row.active_users) || row.active_users < 0 || (row.online !== null && (typeof row.online !== 'number' || !Number.isInteger(row.online) || row.online < 0)) || typeof row.profile !== 'string' || typeof row.note !== 'string') return invalid();
    return { key: row.key, label: row.label, status: row.status, ok: row.ok, bytes: nonNegative(row.bytes), share: row.share, active_users: row.active_users, online: row.online as number | null, profile: row.profile, note: row.note };
  });
  return { window_hours: v.window_hours, total_bytes: v.total_bytes, recommendation: v.recommendation, reason: v.reason, rows };
}

function calibration(value: unknown): HealthCalibration {
  const v = record(value);
  const requiredInt = ['window_hours', 'sample_count', 'included_sample_count', 'app_raw_bytes', 'net_total_bytes', 'net_tx_bytes', 'egress_sample_count'];
  if (requiredInt.some(key => typeof v[key] !== 'number' || !Number.isInteger(v[key]) || v[key] < 0) || typeof v.current_multiplier !== 'number' || !Number.isFinite(v.current_multiplier) || v.current_multiplier <= 0 || typeof v.confidence !== 'string' || !Array.isArray(v.ifaces) || v.ifaces.some(item => typeof item !== 'string') || typeof v.last_ts !== 'string' || typeof v.method !== 'string' || !Array.isArray(v.windows)) return invalid();
  const parseWindow = (item: unknown): HealthCalibrationWindow => {
    const w = record(item);
    if (typeof w.window_hours !== 'number' || !Number.isInteger(w.window_hours) || w.window_hours <= 0 || typeof w.included_sample_count !== 'number' || !Number.isInteger(w.included_sample_count) || w.included_sample_count < 0 || typeof w.sample_count !== 'number' || !Number.isInteger(w.sample_count) || w.sample_count < 0 || typeof w.confidence !== 'string') return invalid();
    return { window_hours: w.window_hours, suggested_multiplier: w.suggested_multiplier === null ? null : nonNegative(w.suggested_multiplier), egress_multiplier: w.egress_multiplier === null ? null : nonNegative(w.egress_multiplier), app_raw_bytes: nonNegative(w.app_raw_bytes), included_sample_count: w.included_sample_count, sample_count: w.sample_count, confidence: w.confidence };
  };
  const policy = record(v.policy);
  if (typeof policy.enabled !== 'boolean' || typeof policy.mode !== 'string' || typeof policy.min_confidence !== 'string' || typeof policy.max_delta_percent !== 'number' || !Number.isFinite(policy.max_delta_percent) || policy.max_delta_percent < 0 || typeof policy.min_delta_percent !== 'number' || !Number.isFinite(policy.min_delta_percent) || policy.min_delta_percent < 0 || typeof policy.cooldown_hours !== 'number' || !Number.isFinite(policy.cooldown_hours) || policy.cooldown_hours <= 0) return invalid();
  return { window_hours: v.window_hours as number, sample_count: v.sample_count as number, included_sample_count: v.included_sample_count as number, app_raw_bytes: v.app_raw_bytes as number, net_total_bytes: v.net_total_bytes as number, net_tx_bytes: v.net_tx_bytes as number, current_multiplier: v.current_multiplier, suggested_multiplier: optionalNonNegative(v.suggested_multiplier), egress_multiplier: optionalNonNegative(v.egress_multiplier), delta_percent: v.delta_percent === null ? null : (typeof v.delta_percent === 'number' && Number.isFinite(v.delta_percent) ? v.delta_percent : invalid()), confidence: v.confidence, ifaces: v.ifaces as string[], last_ts: v.last_ts, method: v.method, egress_sample_count: v.egress_sample_count as number, windows: v.windows.map(parseWindow), policy: policy as unknown as HealthCalibrationPolicy };
}

function update(value: unknown): HealthUpdate {
  const v = record(value);
  if (typeof v.status !== 'string' || typeof v.reason !== 'string' || typeof v.ts !== 'string' || typeof v.pending !== 'boolean' || typeof v.version !== 'string' || typeof v.previous_version !== 'string') return invalid();
  return { status: v.status, reason: v.reason, ts: v.ts, pending: v.pending, version: v.version, previous_version: v.previous_version };
}

export function parseHealth(value: unknown): Health {
  const v = record(value);
  if (typeof v.ts !== 'string' || !Array.isArray(v.kpis) || !Array.isArray(v.services)) return invalid();
  return { ts: v.ts, kpis: v.kpis.map(status), services: v.services.map(status), line_radar: lineRadar(v.line_radar), calibration: calibration(v.calibration), update: update(v.update) };
}

function operation(value: unknown): HealthOperation {
  const v = record(value);
  if (typeof v.ok !== 'boolean' || typeof v.status !== 'string' || typeof v.reason !== 'string' || typeof v.ts !== 'string' || typeof v.pending !== 'boolean') return invalid();
  if (v.current !== undefined && typeof v.current !== 'string') return invalid();
  if (v.latest !== undefined && typeof v.latest !== 'string') return invalid();
  if (v.update_available !== undefined && typeof v.update_available !== 'boolean') return invalid();
  return { ok: v.ok, status: v.status, reason: v.reason, ts: v.ts, pending: v.pending, current: typeof v.current === 'string' ? v.current : '', latest: typeof v.latest === 'string' ? v.latest : '', update_available: v.update_available === true };
}

export async function runHealthAction(action: string, fields: Record<string, string>, signal: AbortSignal): Promise<HealthOperation> {
  const { value, status: responseStatus } = await postFormJson(`/api/v1/admin/health/${action}`, fields, signal);
  if (responseStatus === 401 && record(value).error === 'login_required') throw new ResourceError('管理员登录已失效', 401, 'login_required');
  const result = operation(value);
  if (responseStatus !== 200) throw new ResourceError(result.reason || `HTTP ${responseStatus}`, responseStatus);
  return result;
}
