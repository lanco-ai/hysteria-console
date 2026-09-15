import { hasExactKeys, postFormJson } from '../../../shared/postForm';
import { ResourceError } from '../../../shared/readResource';
import type { Action, Counters, MutationResult, Overview, OverviewUser, Poll } from './types';

function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.getPrototypeOf(value) !== Object.prototype) return invalid();
  return value as Record<string, unknown>;
}
function str(value: unknown): string { return typeof value === 'string' ? value : invalid(); }
function num(value: unknown): number { return typeof value === 'number' && Number.isFinite(value) ? value : invalid(); }
function int(value: unknown): number { const n = num(value); return Number.isInteger(n) && n >= 0 ? n : invalid(); }
function bool(value: unknown): boolean { return typeof value === 'boolean' ? value : invalid(); }
function list<T>(value: unknown, parse: (item: unknown) => T): T[] { return Array.isArray(value) ? value.map(parse) : invalid(); }
function link(value: unknown): string {
  const text = str(value);
  const url = new URL(text);
  return ['http:', 'https:'].includes(url.protocol) ? text : invalid();
}
function counters(value: unknown): Counters {
  const v = record(value);
  return { user: str(v.user), tx: int(v.tx), rx: int(v.rx), used: int(v.used), total: int(v.total), percent: num(v.percent), online: int(v.online), revision: str(v.revision), disabled: bool(v.disabled) };
}
function user(value: unknown): OverviewUser {
  const v = record(value);
  return { ...counters(v), max_devices: int(v.max_devices), base_quota_gb: int(v.base_quota_gb), quota_extra_gb: int(v.quota_extra_gb), metered: bool(v.metered), tuic_enabled: bool(v.tuic_enabled), expires_at: str(v.expires_at), expired: bool(v.expired), expiry_label: str(v.expiry_label), note: str(v.note), landing_isp: str(v.landing_isp), landing_region: str(v.landing_region), landing_note: str(v.landing_note), landing_ip: str(v.landing_ip), panel_url: link(v.panel_url), subscription_url: link(v.subscription_url), spark: v.spark === null ? null : list(v.spark, item => {
    if (!Array.isArray(item) || item.length !== 2) return invalid();
    return [str(item[0]), int(item[1])];
  }) };
}
function unique<T extends Counters>(users: T[]): T[] { return new Set(users.map(row => row.user)).size === users.length ? users : invalid(); }
export function parseOverview(value: unknown): Overview {
  const v = record(value), c = record(v.cycle);
  const cycle = { key: str(c.key), total_used: int(c.total_used), range: str(c.range), settlement_day: int(c.settlement_day), length_days: int(c.length_days), length_min: int(c.length_min), length_max: int(c.length_max) };
  if (cycle.settlement_day < 1 || cycle.settlement_day > 28 || cycle.length_min < 1 || cycle.length_days < cycle.length_min || cycle.length_days > cycle.length_max) return invalid();
  return { cycle, users: unique(list(v.users, user)), landing_options: list(v.landing_options, item => { const choice = record(item); return { id: str(choice.id), name: str(choice.name) }; }) };
}
export function parsePoll(value: unknown): Poll { const v = record(value); return { ts: str(v.ts), total_used: int(v.total_used), users: unique(list(v.users, counters)) }; }
export function parseReload(value: unknown): boolean { const v = record(value); bool(v.xray); bool(v.tuic); return bool(v.pending); }

export async function readJson<T>(path: string, signal: AbortSignal, parse: (value: unknown) => T): Promise<T> {
  const response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal });
  if (!response.ok) throw new ResourceError(`HTTP ${response.status}`, response.status, response.status === 401 ? 'login_required' : undefined);
  if (!/^application\/json(?:\s*;|$)/i.test(response.headers.get('content-type') || '')) return invalid();
  const value: unknown = await response.json();
  return parse(value);
}

const accountCodes = new Set(['user empty', 'err:username_invalid', 'err:max_devices_invalid', 'err:quota_invalid', 'err:quota_extra_invalid', 'err:expiry_invalid', 'err:note_too_long', 'err:landing_invalid', 'err:landing_too_long', 'err:landing_ip_invalid', 'err:panel_password_short', 'err:panel_password_long', 'err:proxy_password_long', 'user_exists_use_reset_token', '家宽出口已不可用，请重新选择']);
const fieldIds = new Set(['', 'create-user', 'create-quota-gb', 'create-quota-extra-gb', 'create-expires-at', 'create-note', 'create-panel-password', 'create-proxy-password', 'create-landing-initial-egress']);
const rotationCodes = new Set(['rotated', 'err:rotated_retry', 'err:rotated_pending', 'err:rotated_static_pending']);
export async function write(action: Action, fields: Record<string, string>, signal: AbortSignal): Promise<MutationResult> {
  const account = action === 'create' || action === 'update';
  const { value, status } = await postFormJson(`/api/v1/admin/${account ? 'users' : 'operations'}/${action}`, fields, signal);
  const v = record(value);
  if (status === 401 && v.error === 'login_required') return { kind: 'auth', code: '请重新登录管理员账号' };
  if (status === 409 && v.ok === false && v.error === 'revision_conflict' && hasExactKeys(v, ['ok', 'error'])) return { kind: 'conflict', code: '用户配置已更改，请刷新核对；草稿保留，旧版本不能覆盖新配置' };
  if (status === 404 && v.ok === false && v.error === 'user_not_found' && hasExactKeys(v, ['ok', 'error'])) return { kind: 'missing', code: '用户不存在，请刷新核对' };
  if (status === 422 && v.ok === false && v.error === 'validation_error') {
    const code = str(v.code);
    if (account && accountCodes.has(code) && fieldIds.has(str(v.field_id)) && hasExactKeys(v, ['ok', 'error', 'code', 'field_id'])) return { kind: 'invalid', code, field: str(v.field_id) };
    if (!account && hasExactKeys(v, ['ok', 'error', 'code']) && ((action === 'cycle' && ['err:settlement_invalid', 'err:cycle_length_invalid'].includes(code)) || (action === 'toggle-user' && code === 'invalid_desired'))) return { kind: 'invalid', code, field: code === 'err:settlement_invalid' ? 'cycle-day' : 'cycle-length' };
    return invalid();
  }
  if (status !== 200 || v.ok !== true || v.user !== (fields.user || '').trim()) return invalid();
  if (account) {
    if (!hasExactKeys(v, ['ok', 'outcome', 'user']) || v.outcome !== (action === 'create' ? 'created' : 'updated')) return invalid();
    return { kind: 'success', code: `${str(v.outcome)} ${str(v.user)}` };
  }
  if (v.action !== action) return invalid();
  if (action === 'rotate-token' || action === 'delete') {
    if (!hasExactKeys(v, ['ok', 'action', 'user', 'code']) || !(action === 'rotate-token' ? rotationCodes : new Set(['deleted', 'err:deleted_retry'])).has(str(v.code))) return invalid();
    return { kind: 'success', code: `${str(v.code)} ${str(v.user)}` };
  }
  if (!hasExactKeys(v, ['ok', 'action', 'user', 'day', 'disabled_until'])) return invalid();
  if (action === 'cycle' ? int(v.day) !== Number(fields.day) : v.day !== null) return invalid();
  if (action === 'pause-user' ? !str(v.disabled_until) : v.disabled_until !== '') return invalid();
  const code = action === 'cycle' ? `settlement ${num(v.day)}` : action === 'toggle-user' ? `${fields.desired === 'enabled' ? 'enabled' : 'disabled'} ${str(v.user)}` : action === 'reset-usage-all' ? 'reset usage all' : `${action === 'refresh-usage' ? 'refresh usage' : action === 'pause-user' ? 'paused' : 'reset usage'} ${str(v.user)}`;
  return { kind: 'success', code };
}
