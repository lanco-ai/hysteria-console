import { postFormJson } from '../../../shared/postForm';

export const ALERTS_ENDPOINT = '/api/v1/admin/alerts';

/** What the console may know about alert channels: never a token, URL or secret. */
export type AlertSettings = {
  telegram: { configured: boolean; chat_id: string };
  webhook: { configured: boolean; host: string; signed: boolean };
  anomaly_z_threshold: number;
  anomaly_min_gib: number;
  revision: string;
};

export type AlertDraft = {
  telegramEnabled: boolean;
  telegramToken: string;
  telegramChat: string;
  webhookEnabled: boolean;
  webhookUrl: string;
  webhookSecret: string;
  webhookSecretClear: boolean;
  zThreshold: string;
  minGib: string;
};

export type AlertSaveResult =
  | { kind: 'saved'; settings: AlertSettings }
  | { kind: 'invalid'; code: string }
  | { kind: 'conflict' }
  | { kind: 'login' };

const FIELD_ERRORS: Record<string, string> = {
  telegram_token_invalid: 'Bot Token 格式不正确，应形如 123456789:AA…（在 @BotFather 中获取）。',
  telegram_chat_invalid: 'Chat ID 应为数字 ID（群组通常以 -100 开头）或 @频道用户名。',
  webhook_url_invalid: 'Webhook 地址必须以 https:// 开头，且不能包含账号密码或空格。',
  webhook_url_private: 'Webhook 不能指向本机或内网地址：告警由服务器发出，只能发往公网上的接收端。',
  webhook_secret_invalid: '签名密钥不能超过 256 个字符。',
  z_threshold_invalid: '异常阈值需在 1 到 10 之间。',
  min_gib_invalid: '最低流量需在 0 到 1024 GiB 之间。',
  config_unreadable: '服务器上的告警配置无法解析，本次没有修改；请先检查 /root/hysteria/alerts.json。',
};

export function alertErrorMessage(code: string): string {
  return FIELD_ERRORS[code] ?? '告警设置未通过校验，请检查后重试。';
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function parseAlertSettings(value: unknown): AlertSettings {
  const data = record(value);
  const telegram = record(data.telegram);
  const webhook = record(data.webhook);
  if (
    typeof telegram.configured !== 'boolean' || typeof telegram.chat_id !== 'string'
    || typeof webhook.configured !== 'boolean' || typeof webhook.host !== 'string' || typeof webhook.signed !== 'boolean'
    || typeof data.anomaly_z_threshold !== 'number' || !Number.isFinite(data.anomaly_z_threshold)
    || typeof data.anomaly_min_gib !== 'number' || !Number.isFinite(data.anomaly_min_gib)
    || typeof data.revision !== 'string'
  ) {
    throw new Error('Invalid alert settings');
  }
  return {
    telegram: { configured: telegram.configured, chat_id: telegram.chat_id },
    webhook: { configured: webhook.configured, host: webhook.host, signed: webhook.signed },
    anomaly_z_threshold: data.anomaly_z_threshold,
    anomaly_min_gib: data.anomaly_min_gib,
    revision: data.revision,
  };
}

/** A fresh read after a conflict; null means the session has expired. */
export async function readAlertSettings(signal: AbortSignal): Promise<AlertSettings | null> {
  const response = await fetch(ALERTS_ENDPOINT, { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' }, signal });
  if (response.status === 401) return null;
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return parseAlertSettings(await response.json());
}

export function draftFrom(settings: AlertSettings): AlertDraft {
  return {
    telegramEnabled: settings.telegram.configured,
    telegramToken: '',
    telegramChat: settings.telegram.chat_id,
    webhookEnabled: settings.webhook.configured,
    webhookUrl: '',
    webhookSecret: '',
    webhookSecretClear: false,
    zThreshold: String(settings.anomaly_z_threshold),
    minGib: String(settings.anomaly_min_gib),
  };
}

export async function saveAlertSettings(draft: AlertDraft, revision: string, signal: AbortSignal): Promise<AlertSaveResult> {
  const { value, status } = await postFormJson('/api/v1/admin/alerts/save', {
    telegram_enabled: draft.telegramEnabled ? '1' : '',
    telegram_bot_token: draft.telegramEnabled ? draft.telegramToken : '',
    telegram_chat_id: draft.telegramEnabled ? draft.telegramChat : '',
    webhook_enabled: draft.webhookEnabled ? '1' : '',
    webhook_url: draft.webhookEnabled ? draft.webhookUrl : '',
    webhook_secret: draft.webhookEnabled && !draft.webhookSecretClear ? draft.webhookSecret : '',
    webhook_secret_clear: draft.webhookEnabled && draft.webhookSecretClear ? '1' : '',
    anomaly_z_threshold: draft.zThreshold,
    anomaly_min_gib: draft.minGib,
    revision,
  }, signal);
  const data = record(value);
  if (status === 401) return { kind: 'login' };
  if (status === 409 || data.error === 'revision_conflict') return { kind: 'conflict' };
  if (status === 422 && data.error === 'validation_error' && typeof data.code === 'string') return { kind: 'invalid', code: data.code };
  if (status !== 200 || data.ok !== true) throw new Error(`HTTP ${status}`);
  return { kind: 'saved', settings: parseAlertSettings(data) };
}

/** Sends one message through every saved channel; delivery happens in the background. */
export async function sendTestAlert(signal: AbortSignal): Promise<'dispatched' | 'no_channels' | 'login'> {
  const { value, status } = await postFormJson('/api/v1/admin/health/test-alert', {}, signal);
  const data = record(value);
  if (status === 401) return 'login';
  if (data.reason === 'alert_no_channels') return 'no_channels';
  if (status !== 200 || data.ok !== true) throw new Error(`HTTP ${status}`);
  return 'dispatched';
}
