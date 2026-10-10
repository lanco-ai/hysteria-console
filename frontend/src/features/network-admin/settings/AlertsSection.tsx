import { useEffect, useRef, useState, type FormEvent } from 'react';
import { useReadResource } from '../../../shared/readResource';
import {
  ALERTS_ENDPOINT,
  alertErrorMessage,
  draftFrom,
  parseAlertSettings,
  readAlertSettings,
  saveAlertSettings,
  sendTestAlert,
  type AlertDraft,
  type AlertSettings,
} from './alertSettings';

type Message = { tone: 'ok' | 'err' | 'info'; text: string; reload?: boolean };

const SAVED_SECRET = '已保存，留空保持不变';

/** 设置 · 告警通知: channels, write-only credentials and anomaly thresholds. */
export function AlertsSection() {
  const resource = useReadResource(ALERTS_ENDPOINT, { validate: parseAlertSettings });
  const [latest, setLatest] = useState<AlertSettings | null>(null);
  const settings = latest ?? (resource.status === 'success' ? resource.data : null);
  const [draft, setDraft] = useState<AlertDraft | null>(null);
  const [busy, setBusy] = useState<'' | 'save' | 'test' | 'load'>('');
  const [message, setMessage] = useState<Message | null>(null);
  const controller = useRef<AbortController | null>(null);

  // The form follows the server copy only when its revision changes, never mid-edit.
  const revision = settings?.revision;
  useEffect(() => {
    if (settings) setDraft(draftFrom(settings));
  }, [revision]);
  useEffect(() => () => controller.current?.abort(), []);
  const expired = resource.status === 'error' && resource.error.status === 401;
  useEffect(() => { if (expired) window.location.assign('/login'); }, [expired]);

  if (!settings || !draft) {
    if (resource.status === 'error' && !expired) {
      return <div className="settings-message is-err" role="alert">告警设置读取失败：{resource.error.message}
        <button className="btn btn-sm" type="button" onClick={resource.retry}>重试</button></div>;
    }
    return <p className="settings-muted" role="status">正在读取告警设置…</p>;
  }

  const baseline = draftFrom(settings);
  const dirty = (Object.keys(baseline) as (keyof AlertDraft)[]).some(key => baseline[key] !== draft[key]);
  const change = <K extends keyof AlertDraft>(key: K, value: AlertDraft[K]) => {
    setDraft(current => current && { ...current, [key]: value });
    setMessage(null);
  };
  const anyConfigured = settings.telegram.configured || settings.webhook.configured;

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy('save'); setMessage({ tone: 'info', text: '正在保存…' });
    try {
      const result = await saveAlertSettings(draft, settings.revision, abort.signal);
      if (result.kind === 'login') { window.location.assign('/login'); return; }
      if (result.kind === 'conflict') { setMessage({ tone: 'err', text: '告警设置刚在其他窗口修改过，本次没有保存。', reload: true }); return; }
      if (result.kind === 'invalid') { setMessage({ tone: 'err', text: alertErrorMessage(result.code) }); return; }
      setLatest(result.settings);
      setMessage({ tone: 'ok', text: result.settings.telegram.configured || result.settings.webhook.configured ? '告警设置已保存，可以发送一条测试告警确认。' : '告警设置已保存；目前没有启用任何渠道。' });
    } catch (error) {
      if (!abort.signal.aborted) setMessage({ tone: 'err', text: error instanceof Error && error.message.startsWith('HTTP ') ? `保存失败（${error.message}），请刷新后核对。` : '保存结果未确认，请刷新后核对。' });
    } finally {
      if (controller.current === abort) controller.current = null;
      setBusy('');
    }
  };
  const test = async () => {
    if (busy) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy('test'); setMessage({ tone: 'info', text: '正在发送测试告警…' });
    try {
      const result = await sendTestAlert(abort.signal);
      if (result === 'login') { window.location.assign('/login'); return; }
      setMessage(result === 'dispatched'
        ? { tone: 'ok', text: '测试告警已在后台发送，请在 Telegram / Webhook 接收端确认。' }
        : { tone: 'err', text: '还没有保存可用的告警渠道。' });
    } catch {
      if (!abort.signal.aborted) setMessage({ tone: 'err', text: '测试告警没有发出，请稍后重试。' });
    } finally {
      if (controller.current === abort) controller.current = null;
      setBusy('');
    }
  };
  const reload = async () => {
    if (busy) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy('load'); setMessage({ tone: 'info', text: '正在载入最新设置…' });
    try {
      const fresh = await readAlertSettings(abort.signal);
      if (!fresh) { window.location.assign('/login'); return; }
      setLatest(fresh);
      // Same revision means nothing changed on the server, so restore the form explicitly.
      setDraft(draftFrom(fresh));
      setMessage({ tone: 'ok', text: '已载入最新设置，请检查后再保存。' });
    } catch {
      if (!abort.signal.aborted) setMessage({ tone: 'err', text: '最新设置读取失败，请刷新页面。' });
    } finally {
      if (controller.current === abort) controller.current = null;
      setBusy('');
    }
  };

  return <form className="settings-alerts" onSubmit={save} aria-describedby="settings-alerts-desc">
    <div className="settings-channel-chips" aria-label="渠道状态">
      <span className={`settings-chip${settings.telegram.configured ? ' is-on' : ''}`}><i aria-hidden="true"/>Telegram · {settings.telegram.configured ? '已配置' : '未配置'}</span>
      <span className={`settings-chip${settings.webhook.configured ? ' is-on' : ''}`}><i aria-hidden="true"/>Webhook · {settings.webhook.configured ? `已配置${settings.webhook.host ? ` · ${settings.webhook.host}` : ''}` : '未配置'}</span>
    </div>

    <fieldset className="settings-channel">
      <legend className="sr-only">Telegram</legend>
      <div className="settings-channel-head">
        <div>
          <strong>Telegram 机器人</strong>
          <small>在 @BotFather 创建机器人并把它加入接收告警的群组或频道。</small>
        </div>
        <input className="settings-switch" type="checkbox" role="switch" aria-label="启用 Telegram 告警" checked={draft.telegramEnabled} disabled={!!busy} onChange={event => change('telegramEnabled', event.target.checked)}/>
      </div>
      {settings.telegram.configured && !draft.telegramEnabled ? <p className="settings-channel-note" role="note">保存后会删除已保存的 Bot Token 和 Chat ID；以后重新开启需要重新填写。</p> : null}
      {draft.telegramEnabled ? <div className="settings-fields">
        <div className="form-field">
          <label htmlFor="alert-telegram-token">Bot Token</label>
          <input id="alert-telegram-token" type="password" autoComplete="new-password" spellCheck={false} required={!settings.telegram.configured} placeholder={settings.telegram.configured ? SAVED_SECRET : '123456789:AA…'} value={draft.telegramToken} disabled={!!busy} onChange={event => change('telegramToken', event.target.value)}/>
        </div>
        <div className="form-field">
          <label htmlFor="alert-telegram-chat">Chat ID</label>
          <input id="alert-telegram-chat" autoComplete="off" spellCheck={false} required placeholder="-1001234567890 或 @频道名" value={draft.telegramChat} disabled={!!busy} onChange={event => change('telegramChat', event.target.value)}/>
        </div>
      </div> : null}
    </fieldset>

    <fieldset className="settings-channel">
      <legend className="sr-only">Webhook</legend>
      <div className="settings-channel-head">
        <div>
          <strong>Webhook</strong>
          <small>以 JSON POST 发送到公网上的接收端（不能是本机或内网地址）；填写签名密钥后，请求头 X-Hy2-Signature 会带上 HMAC-SHA256 签名，便于接收端验证来源。</small>
        </div>
        <input className="settings-switch" type="checkbox" role="switch" aria-label="启用 Webhook 告警" checked={draft.webhookEnabled} disabled={!!busy} onChange={event => change('webhookEnabled', event.target.checked)}/>
      </div>
      {settings.webhook.configured && !draft.webhookEnabled ? <p className="settings-channel-note" role="note">保存后会删除已保存的 Webhook 地址和签名密钥；以后重新开启需要重新填写。</p> : null}
      {draft.webhookEnabled ? <div className="settings-fields">
        <div className="form-field form-field-wide">
          <label htmlFor="alert-webhook-url">地址（https）</label>
          <input id="alert-webhook-url" type="url" inputMode="url" autoComplete="off" spellCheck={false} required={!settings.webhook.configured} placeholder={settings.webhook.configured ? `${SAVED_SECRET}${settings.webhook.host ? `（${settings.webhook.host}）` : ''}` : 'https://hooks.example.com/…'} value={draft.webhookUrl} disabled={!!busy} onChange={event => change('webhookUrl', event.target.value)}/>
        </div>
        <div className="form-field form-field-wide">
          <label htmlFor="alert-webhook-secret">签名密钥（可选）</label>
          <input id="alert-webhook-secret" type="password" autoComplete="new-password" spellCheck={false} maxLength={256} placeholder={settings.webhook.signed ? SAVED_SECRET : '不填则不签名'} value={draft.webhookSecret} disabled={!!busy || draft.webhookSecretClear} onChange={event => change('webhookSecret', event.target.value)}/>
          {settings.webhook.signed ? <label className="switch"><input type="checkbox" checked={draft.webhookSecretClear} disabled={!!busy} onChange={event => change('webhookSecretClear', event.target.checked)}/>清除已保存的签名密钥</label> : null}
        </div>
      </div> : null}
    </fieldset>

    <fieldset className="settings-channel">
      <legend className="sr-only">流量异常检测</legend>
      <div className="settings-channel-head">
        <div>
          <strong>流量异常检测</strong>
          <small>当天用量明显高于该用户近期日常水平时提醒：超过阈值倍数的标准差，且不少于最低流量。</small>
        </div>
      </div>
      <div className="settings-fields">
        <div className="form-field">
          <label htmlFor="alert-z-threshold">异常阈值（标准差倍数）</label>
          <input id="alert-z-threshold" type="number" min="1" max="10" step="0.5" inputMode="decimal" required value={draft.zThreshold} disabled={!!busy} onChange={event => change('zThreshold', event.target.value)}/>
        </div>
        <div className="form-field">
          <label htmlFor="alert-min-gib">最低流量（GiB）</label>
          <input id="alert-min-gib" type="number" min="0" max="1024" step="0.5" inputMode="decimal" required value={draft.minGib} disabled={!!busy} onChange={event => change('minGib', event.target.value)}/>
        </div>
      </div>
    </fieldset>

    <div className="settings-actions">
      {message ? <span className={`settings-message is-${message.tone}`} role={message.tone === 'err' ? 'alert' : 'status'}>{message.text}{message.reload ? <button className="btn btn-sm" type="button" onClick={() => { void reload(); }}>载入最新设置</button> : null}</span> : <span className="settings-muted">{dirty ? '有未保存的修改' : anyConfigured ? '测试告警使用已保存的设置。' : '尚未配置告警渠道。'}</span>}
      <button className="btn btn-secondary" type="button" disabled={!!busy || !anyConfigured} onClick={() => { void test(); }}>{busy === 'test' ? '正在发送…' : '发送测试告警'}</button>
      <button className="btn btn-primary" type="submit" disabled={!!busy || !dirty}>{busy === 'save' ? '正在保存…' : '保存告警设置'}</button>
    </div>
  </form>;
}
