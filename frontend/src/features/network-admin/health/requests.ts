import type { Health, HealthStatus } from './types';

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

export function parseHealth(value: unknown): Health {
  const v = record(value);
  if (typeof v.ts !== 'string' || !Array.isArray(v.kpis) || !Array.isArray(v.services)) return invalid();
  return { ts: v.ts, kpis: v.kpis.map(status), services: v.services.map(status) };
}
