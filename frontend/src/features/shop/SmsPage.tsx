import { useEffect, useRef, useState } from 'react';
import { PortalShell } from '../public/PortalShell';

type Action = 'verify' | 'acquire' | 'check' | 'cancel';
type State = 'PENDING' | 'CANCEL_PENDING' | 'RECEIVED' | 'CANCELED' | 'TIMEOUT' | 'BANNED' | 'FINISHED';
type Order = { order_id: number; phone: string; status: State; sms_code: string | null; wait_seconds: number };
type Verified = { remaining: number; max_uses: number; has_pending_order: boolean };
const ACTIVE = new Set<State>(['PENDING', 'CANCEL_PENDING']);
const STATUS: Record<State, string> = { PENDING: '等待短信', CANCEL_PENDING: '正在确认取消', RECEIVED: '验证码已收到', CANCELED: '本次接码已取消', TIMEOUT: '本次接码已超时', BANNED: '当前号码不可用', FINISHED: '本次接码已结束' };
const UNKNOWN_RESULT = '操作结果尚未确认，请保留当前激活码并重试。不要立即更换激活码。';
class SmsRequestError extends Error {}

function SmsMark() {
  return <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect x="8" y="3" width="16" height="26" rx="4" stroke="currentColor" strokeWidth="1.5"/><path d="M13 7h6M14 25h4M12 13h8M12 17h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>;
}

export function SmsPage() {
  const [code, setCode] = useState('');
  const [verified, setVerified] = useState<Verified | null>(null);
  const [order, setOrder] = useState<Order | null>(null);
  const [busy, setBusy] = useState<Action | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [due, setDue] = useState(0);
  const [cooldown, setCooldown] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [paused, setPaused] = useState(false);
  const [visible, setVisible] = useState(() => !document.hidden);
  const generation = useRef(0);
  const lock = useRef(false);
  const failures = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const active = order !== null && ACTIVE.has(order.status);
  const remainingCooldown = Math.max(0, Math.ceil((cooldown - now) / 1000));

  useEffect(() => {
    mounted.current = true;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    const visibility = () => setVisible(!document.hidden);
    const hide = () => { setVisible(false); generation.current += 1; controller.current?.abort(); lock.current = false; setBusy(null); };
    const show = () => { setVisible(!document.hidden); setDue(Date.now() + 10_000); };
    document.addEventListener('visibilitychange', visibility);
    window.addEventListener('pagehide', hide);
    window.addEventListener('pageshow', show);
    return () => {
      mounted.current = false; generation.current += 1; controller.current?.abort();
      clearInterval(timer); document.removeEventListener('visibilitychange', visibility);
      window.removeEventListener('pagehide', hide); window.removeEventListener('pageshow', show);
    };
  }, []);

  async function perform(action: Action) {
    if (lock.current || Date.now() < cooldown) return;
    lock.current = true;
    const epoch = generation.current;
    const abort = new AbortController(); controller.current = abort;
    const timeout = window.setTimeout(() => abort.abort(), 30_000);
    setBusy(action); setError(''); setNotice('');
    let delay = 10_000;
    const mutating = action === 'acquire' || action === 'cancel';
    const current = () => mounted.current && epoch === generation.current;
    try {
      const response = await fetch(`/api/v1/shop/sms/${action}`, {
        method: 'POST', credentials: 'same-origin', cache: 'no-store', referrerPolicy: 'no-referrer', signal: abort.signal,
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ activation_code: code.trim(), ...((action === 'check' || action === 'cancel') && order ? { order_id: order.order_id } : {}) }),
      });
      const value = await response.json().catch(() => { throw new SmsRequestError(mutating ? UNKNOWN_RESULT : '接码服务返回异常，请稍后重试。'); });
      if (!current()) return;
      if (!response.ok || value.ok !== true) {
        if (response.status === 429) {
          const seconds = Number(response.headers.get('Retry-After') ?? value.retry_after);
          delay = Math.max(10, Math.min(300, Number.isFinite(seconds) ? seconds : 10)) * 1000;
          setCooldown(Date.now() + delay);
          throw new SmsRequestError('请求较频繁，稍后可继续。');
        }
        if (response.status === 400 || response.status === 409) {
          if (action === 'check') setPaused(true);
          throw new SmsRequestError(action === 'verify' ? '激活码暂不可用，请检查输入或联系商家。' : action === 'cancel' ? '暂未确认取消，请继续查看当前号码状态，稍后再试。' : '当前操作未完成，请稍后重试或联系商家。');
        }
        throw new SmsRequestError(mutating ? UNKNOWN_RESULT : '接码服务暂时不可用，请稍后重试。');
      }
      failures.current = 0; setPaused(false);
      if (action !== 'check') setConfirmCancel(false);
      if (action === 'verify') {
        setVerified(value as Verified);
      } else if (action === 'acquire') {
        setOrder(value as Order);
      } else {
        setOrder(old => old ? { ...old, status: value.status, sms_code: value.sms_code, wait_seconds: value.wait_seconds } : old);
      }
    } catch (failure) {
      if (!current()) return;
      setError(failure instanceof SmsRequestError ? failure.message : mutating ? UNKNOWN_RESULT : '暂时无法连接接码服务，请稍后重试。');
      if (action === 'check') {
        failures.current += 1;
        delay = Math.max(delay, Math.min(60, 10 * 2 ** Math.min(failures.current - 1, 3)) * 1000);
        if (failures.current >= 6) setPaused(true);
      }
    } finally {
      clearTimeout(timeout);
      if (current()) { lock.current = false; controller.current = null; setBusy(null); setDue(Date.now() + delay); }
    }
  }

  useEffect(() => {
    if (!active || paused || !visible || busy) return;
    const timer = window.setTimeout(() => void perform('check'), Math.max(0, due - Date.now(), cooldown - Date.now()));
    return () => clearTimeout(timer);
  // The timer always captures the current credential/order; it is canceled on every state change.
  }, [active, paused, visible, busy, due, cooldown, code, order]);

  function reset() {
    generation.current += 1; controller.current?.abort(); lock.current = false; failures.current = 0;
    setBusy(null); setOrder(null); setVerified(null); setCode(''); setError(''); setNotice(''); setPaused(false); setConfirmCancel(false);
  }
  async function copy(value: string, label: string) {
    const epoch = generation.current;
    try { await navigator.clipboard.writeText(value); if (mounted.current && epoch === generation.current) setNotice(`${label}已复制。`); }
    catch { if (mounted.current && epoch === generation.current) setNotice('复制失败，请选中文字手动复制。'); }
  }
  const step = order?.status === 'RECEIVED' ? 3 : order ? 2 : verified ? 1 : 0;
  const blocked = busy !== null || remainingCooldown > 0;

  return <PortalShell active="sms" pageTitle="短信接码">
    <div className="sms-page">
      <header className="sms-heading"><a href="/" className="sms-back">← 返回商店</a><span className="sms-eyebrow">SMS · 接码中心</span><h1>一个号码，接收你的验证码。</h1><p>使用已购买的激活码，在这里完成取号与收码。</p></header>
      <div className="sms-layout">
        <section className="sms-workspace" aria-labelledby="sms-workspace-title">
          <header className="sms-card-header"><span className="sms-symbol"><SmsMark/></span><div><h2 id="sms-workspace-title">短信接码</h2><p>按激活码对应的服务分配号码</p></div><span className="sms-private">私密使用</span></header>
          <ol className="sms-steps" aria-label="接码步骤">{['验证激活码', '获取号码', '接收验证码'].map((label, index) => <li key={label} className={step >= index ? 'is-current' : ''} aria-current={step === index ? 'step' : undefined}><span>{step > index ? '✓' : `0${index + 1}`}</span>{label}</li>)}</ol>
          {!order ? <div className="sms-content">
            <form onSubmit={event => { event.preventDefault(); if (!verified && !blocked && code.trim().length >= 8) void perform('verify'); }}>
              <label className="sms-field" htmlFor="sms-code">激活码<input id="sms-code" type="password" autoComplete="off" spellCheck={false} maxLength={128} placeholder="输入商家交付的激活码" value={code} disabled={busy !== null || verified !== null} onChange={event => { setCode(event.target.value); setError(''); }}/></label>
              <p className="sms-field-hint">激活码仅用于本次接码，请勿分享给他人。</p>
              {!verified ? <button className="btn btn-primary sms-primary" type="submit" disabled={blocked || code.trim().length < 8}>{busy === 'verify' ? '正在验证…' : '验证激活码'}<span aria-hidden="true">→</span></button> : null}
            </form>
            {verified ? <div className="sms-verified"><div><span className="sms-status-dot"/><strong>激活码已验证</strong><span>剩余 {verified.remaining} 次</span></div><p>{verified.has_pending_order ? '此激活码有未结束的接码，点击下方恢复当前号码。' : '确认已准备好接收短信，再获取号码。号码有效时间有限。'}</p><button className="btn btn-primary sms-primary" type="button" disabled={blocked || (!verified.remaining && !verified.has_pending_order)} onClick={() => void perform('acquire')}>{busy === 'acquire' ? '正在获取号码…' : verified.has_pending_order ? '恢复当前号码' : '获取接收号码'}<span aria-hidden="true">→</span></button><button className="sms-text-button" type="button" disabled={busy !== null} onClick={reset}>使用其他激活码</button></div> : null}
          </div> : <div className="sms-content">
            <div className={`sms-live-status${order.status === 'RECEIVED' ? ' is-received' : ''}`} role="status"><span className="sms-status-dot"/>{STATUS[order.status]}</div>
            <div className="sms-number"><span>接收号码</span><strong>{order.phone}</strong><button type="button" className="sms-copy" onClick={() => void copy(order.phone, '号码')}>复制号码</button></div>
            {order.sms_code ? <div className="sms-code-result"><span>短信验证码</span><strong>{order.sms_code}</strong><button type="button" className="sms-copy" onClick={() => void copy(order.sms_code!, '验证码')}>复制验证码</button></div> : <div className="sms-waiting"><span className="sms-code-placeholder" aria-hidden="true">— — — — — —</span><p>{active ? order.status === 'CANCEL_PENDING' ? '正在等待取消结果。确认前请保留当前页面。' : '请在需要验证的平台填写上方号码并发送短信。' : '当前接码已结束，未收到验证码。'}</p></div>}
            {active ? <><div className="sms-poll-status"><span>{paused ? '自动查询已暂停' : !visible ? '返回页面后继续查询' : busy === 'check' ? '正在查询验证码…' : `约 ${Math.max(0, Math.ceil((Math.max(due, cooldown) - now) / 1000))} 秒后自动查询`}</span>{paused ? <button className="sms-text-button" type="button" disabled={blocked} onClick={() => void perform('check')}>重试查询</button> : <span>收到后自动停止</span>}</div>
              {order.status === 'PENDING' ? <div className="sms-cancel">{confirmCancel ? <div className="sms-cancel-confirm"><p>确认取消当前号码？只有确认取消并退回次数后，才能再次取号。</p><div><button className="btn btn-secondary" type="button" disabled={blocked} onClick={() => void perform('cancel')}>{busy === 'cancel' ? '正在提交…' : '确认取消当前号码'}</button><button className="sms-text-button" type="button" disabled={busy !== null} onClick={() => setConfirmCancel(false)}>继续等待</button></div></div> : <button className="sms-text-button" type="button" disabled={blocked} onClick={() => setConfirmCancel(true)}>号码无法使用？申请取消</button>}</div> : null}
            </> : <div className="sms-finish"><p>{order.status === 'RECEIVED' ? '请及时使用验证码。此次查询已停止。' : '如需再次取号，请重新验证激活码的剩余次数。'}</p><button className="btn btn-secondary" type="button" onClick={reset}>返回激活码输入</button></div>}
          </div>}
          <div className="sms-feedback">{error ? <p role="alert">{error}{remainingCooldown ? `（${remainingCooldown} 秒后可重试）` : ''}</p> : null}<p role="status">{notice}</p></div>
        </section>
        <aside className="sms-guide" aria-label="使用提示"><span className="sms-eyebrow">开始前，了解这些</span><h2>准备好，再取号。</h2><div><span>01</span><h3>先确认适用平台</h3><p>当前默认服务为 ChatGPT。其他平台及账号限制，请在购买前向商家确认。</p></div><div><span>02</span><h3>取号后及时使用</h3><p>号码有时效。请在需要验证的平台发起短信，本页会自动查看接收结果。</p></div><div><span>03</span><h3>妥善保管激活码</h3><p>本页不保存激活码。刷新或离开后需重新输入；离开页面不会替你取消号码。</p></div><footer>还没有激活码？<a href="/">返回商店咨询购买 <span aria-hidden="true">↗</span></a></footer></aside>
      </div>
    </div>
  </PortalShell>;
}
