import { ResourceError } from '../../shared/readResource';
import type { SubscriptionProfile, UserLandingNode, UserPanel } from './types';

export const USER_PANEL_ENDPOINT = '/api/v1/user/panel';
function invalid(): never { throw new Error('响应数据格式无效'); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid(); return value as Record<string, unknown>; }
function text(value: unknown): string { if (typeof value !== 'string') return invalid(); return value; }
function integer(value: unknown): number { if (typeof value !== 'number' || !Number.isInteger(value)) return invalid(); return value; }
function nonNegative(value: unknown): number { const result = integer(value); if (result < -1) return invalid(); return result; }
function profile(value: unknown): SubscriptionProfile { const item = record(value); return { key: text(item.key), label: text(item.label), description: text(item.description), url: text(item.url), qr_path: text(item.qr_path) }; }
function landing(value: unknown): UserLandingNode { const item = record(value); if (typeof item.enabled !== 'boolean' || typeof item.selected !== 'boolean') return invalid(); return { id: text(item.id), name: text(item.name), exit_ip: text(item.exit_ip), isp: text(item.isp), region: text(item.region), enabled: item.enabled, selected: item.selected, health_status: text(item.health_status) }; }
export function parseUserPanel(value: unknown): UserPanel {
  const item = record(value);
  if (typeof item.disabled !== 'boolean' || typeof item.expired !== 'boolean' || typeof item.can_change_password !== 'boolean' || typeof item.can_select_egress !== 'boolean' || typeof item.percent !== 'number' || !Number.isFinite(item.percent) || item.percent < 0 || !Array.isArray(item.subscription_profiles) || !Array.isArray(item.landing_nodes)) return invalid();
  return {
    ts: text(item.ts), username: text(item.username), revision: text(item.revision), used_bytes: nonNegative(item.used_bytes), total_bytes: nonNegative(item.total_bytes), remain_bytes: nonNegative(item.remain_bytes), tx_bytes: nonNegative(item.tx_bytes), rx_bytes: nonNegative(item.rx_bytes), online: nonNegative(item.online), max_devices: nonNegative(item.max_devices), percent: item.percent,
    cycle_reset_date: text(item.cycle_reset_date), cycle_days_left: nonNegative(item.cycle_days_left), cycle_length_days: nonNegative(item.cycle_length_days), disabled: item.disabled, expired: item.expired, expiry_label: text(item.expiry_label), can_change_password: item.can_change_password, can_select_egress: item.can_select_egress,
    subscription_profiles: item.subscription_profiles.map(profile), landing_nodes: item.landing_nodes.map(landing),
  };
}
export async function readUserPanel(signal: AbortSignal): Promise<UserPanel> {
  const response = await fetch(USER_PANEL_ENDPOINT, { credentials: 'same-origin', cache: 'no-store', signal });
  if (!response.ok) throw new ResourceError(`HTTP ${response.status}`, response.status);
  return parseUserPanel(await response.json());
}
