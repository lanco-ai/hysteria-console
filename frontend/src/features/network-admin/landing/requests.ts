import { ResourceError } from '../../../shared/readResource';
import { postFormJson } from '../../../shared/postForm';
import type { AdminLanding, LandingHealth, LandingOperation, LandingOperationAction } from './types';

export const LANDING_ENDPOINT = '/api/v1/admin/landing-egresses';
function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== 'string') return invalid(); return value; }
function revision(value: unknown): string { const result = text(value); if (!/^[0-9a-f]{64}$/.test(result)) return invalid(); return result; }
function health(value: unknown): LandingHealth | undefined {
  if (value === undefined || value === null) return undefined;
  const source = record(value);
  const result: LandingHealth = {};
  for (const key of ['status', 'observed_ip', 'checked_at', 'error_code'] as const) {
    if (source[key] !== undefined) result[key] = text(source[key]);
  }
  return result;
}
export function parseLanding(value: unknown): AdminLanding {
  const root = record(value);
  if (!Array.isArray(root.nodes) || !Array.isArray(root.users)) return invalid();
  const nodes = root.nodes.map(item => {
    const node = record(item);
    if (typeof node.enabled !== 'boolean') return invalid();
    const result = { id: text(node.id), name: text(node.name), exit_ip: text(node.exit_ip), isp: text(node.isp), region: text(node.region), enabled: node.enabled };
    const nodeHealth = health(node.health);
    return nodeHealth ? { ...result, health: nodeHealth } : result;
  });
  const users = root.users.map(item => {
    const user = record(item);
    if (!Array.isArray(user.allowed_ids) || user.allowed_ids.some(id => typeof id !== 'string')) return invalid();
    return { user: text(user.user), revision: revision(user.revision), allowed_ids: user.allowed_ids as string[] };
  });
  return { ts: text(root.ts), revision: revision(root.revision), nodes, users };
}
export async function readLanding(signal: AbortSignal): Promise<AdminLanding> {
  const response = await fetch(LANDING_ENDPOINT, { credentials: 'same-origin', cache: 'no-store', signal });
  if (!response.ok) throw new ResourceError(`HTTP ${response.status}`, response.status);
  return parseLanding(await response.json());
}

export function parseLandingOperation(value: unknown, action: LandingOperationAction): LandingOperation {
  const root = record(value);
  if (root.ok === true) {
    if (root.action !== action || (root.revision !== undefined && typeof root.revision !== 'string')) return invalid();
    return { ok: true, action, ...(root.revision === undefined ? {} : { revision: revision(root.revision) }) };
  }
  if (root.ok === false && root.action === action && typeof root.error === 'string') {
    if (root.retry_after !== undefined && (!Number.isInteger(root.retry_after) || (root.retry_after as number) <= 0)) return invalid();
    return { ok: false, action, error: root.error, ...(typeof root.code === 'string' ? { code: root.code } : {}), ...(root.retry_after === undefined ? {} : { retry_after: root.retry_after as number }) };
  }
  return invalid();
}

export type LandingFormFields = Record<string, string | readonly string[]>;

export async function mutateLanding(action: LandingOperationAction, fields: LandingFormFields, signal: AbortSignal): Promise<LandingOperation> {
  const paths: Record<LandingOperationAction, string> = {
    save: '/api/v1/admin/landing-egresses/save',
    delete: '/api/v1/admin/landing-egresses/delete',
    check: '/api/v1/admin/landing-egresses/check',
    access: '/api/v1/admin/landing-egresses/access',
    select: '/api/v1/user/landing-egress/select',
  };
  const { value, status } = await postFormJson(paths[action], fields, signal);
  if (status !== 200 && status !== 401 && status !== 403 && status !== 404 && status !== 409 && status !== 422 && status !== 429) throw new ResourceError(`HTTP ${status}`, status);
  if (status === 401) throw new ResourceError('管理员或用户登录已失效', status);
  return parseLandingOperation(value, action);
}
