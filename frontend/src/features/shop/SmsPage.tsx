import { useEffect, useRef, useState, type ReactElement } from 'react';
import { PortalShell } from '../public/PortalShell';

type Action = 'verify' | 'acquire' | 'check' | 'cancel';
type State = 'PENDING' | 'CANCEL_PENDING' | 'RECEIVED' | 'CANCELED' | 'TIMEOUT' | 'BANNED' | 'FINISHED';
type Country = 'US' | 'CA' | 'BR' | 'GB';
type Order = { order_id: number; phone: string; country?: Country | null; status: State; sms_code: string | null; wait_seconds: number; requested_phone?: string };
type Verified = { remaining: number; max_uses: number; has_pending_order: boolean };
const ACTIVE = new Set<State>(['PENDING', 'CANCEL_PENDING']);
const COUNTRIES: Record<Country, string> = { US: '美国 · USA', CA: '加拿大 · Canada', BR: '巴西 · Brazil', GB: '英国 · UK' };
// The calling code a platform asks for beside the number, and the country's short name in its picker.
const DIAL: Record<Country, string> = { US: '1', CA: '1', BR: '55', GB: '44' };
const PICKER: Record<Country, string> = { US: '美国', CA: '加拿大', BR: '巴西', GB: '英国' };
const STATUS: Record<State, string> = { PENDING: '等待短信', CANCEL_PENDING: '正在确认取消', RECEIVED: '验证码已收到', CANCELED: '本次接码已取消', TIMEOUT: '本次接码已超时', BANNED: '当前号码不可用', FINISHED: '本次接码已结束' };
const UNKNOWN_RESULT = '操作结果尚未确认，请保留当前激活码并重试。不要立即更换激活码。';
const SPECIFIC_ERRORS: Record<string, string> = {
  specific_phone_invalid: '指定号码格式不正确，请输入 10 位美国手机号。',
  specific_phone_unavailable: '指定号码暂不可用，本次未扣次数。可稍后重试，但不保证能再次取得原号码。',
  specific_phone_unsupported: '当前线路暂不支持指定号码，请联系商家确认。',
};
class SmsRequestError extends Error {}

function usPhone(value: string) {
  const compact = value.replace(/[\s().-]/g, '');
  const number = compact.startsWith('+1') ? compact.slice(2) : /^1[0-9]{10}$/.test(compact) ? compact.slice(1) : compact;
  return /^[0-9]{10}$/.test(number) ? number : '';
}

// +1 (202) 555-0123, +44 7400 123456, +55 11 91234-5678; anything else as given.
function displayPhone(phone: string) {
  if (/^\+1[0-9]{10}$/.test(phone)) { const number = phone.slice(2); return `+1 (${number.slice(0, 3)}) ${number.slice(3, 6)}-${number.slice(6)}`; }
  if (/^\+44[0-9]{10}$/.test(phone)) { const number = phone.slice(3); return `+44 ${number.slice(0, 4)} ${number.slice(4)}`; }
  if (/^\+55[0-9]{10,11}$/.test(phone)) { const number = phone.slice(3); return `+55 ${number.slice(0, 2)} ${number.slice(2, -4)}-${number.slice(-4)}`; }
  return phone;
}

function countryOf(order: Order): Country | null {
  return order.country && Object.hasOwn(COUNTRIES, order.country) ? order.country : null;
}

// The number without its calling code, for platforms that pick the country separately.
function nationalNumber(order: Order) {
  const country = countryOf(order);
  return country && order.phone.startsWith(`+${DIAL[country]}`) ? order.phone.slice(DIAL[country].length + 1) : '';
}

const waited = (seconds: number) => `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;

// Small flat flags for the four countries numbers come from; an unknown country shows a globe.
function Flag({ country }: { country: Country | null }) {
  const shapes: Record<Country, ReactElement> = {
    US: <><rect width="24" height="16" fill="#fff"/>{[0, 1, 2, 3, 4, 5, 6].map(stripe => <rect key={stripe} y={stripe * 32 / 13} width="24" height={16 / 13} fill="#b22234"/>)}<rect width="10.5" height={16 / 13 * 7} fill="#3c3b6e"/></>,
    CA: <><rect width="24" height="16" fill="#fff"/><rect width="6" height="16" fill="#d52b1e"/><rect x="18" width="6" height="16" fill="#d52b1e"/><path d="M12 3.5 13 6l1.8-.8-.6 2.6 1.6-.6-.4 1.6L17 9l-3 .9.3 1.6H12.5V13h-1v-1.5H9.7l.3-1.6L7 9l1.6-.4-.4-1.6 1.6.6-.6-2.6L11 6Z" fill="#d52b1e"/></>,
    BR: <><rect width="24" height="16" fill="#009b3a"/><path d="M12 2.2 21.4 8 12 13.8 2.6 8Z" fill="#fedf00"/><circle cx="12" cy="8" r="3.5" fill="#002776"/></>,
    GB: <><rect width="24" height="16" fill="#012169"/><path d="M0 0 24 16M24 0 0 16" stroke="#fff" strokeWidth="3.2"/><path d="M0 0 24 16M24 0 0 16" stroke="#c8102e" strokeWidth="1.2"/><path d="M12 0v16M0 8h24" stroke="#fff" strokeWidth="5"/><path d="M12 0v16M0 8h24" stroke="#c8102e" strokeWidth="3"/></>,
  };
  return country ? <svg className="sms-flag" viewBox="0 0 24 16" aria-hidden="true">{shapes[country]}</svg>
    : <svg className="sms-flag is-unknown" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c2.5 2.6 3.8 5.6 3.8 9s-1.3 6.4-3.8 9c-2.5-2.6-3.8-5.6-3.8-9S9.5 5.6 12 3Z"/></svg>;
}

function reusablePhone(order: Order) {
  return order.country === 'US' || (order.country === undefined && /^\+1[0-9]{10}$/.test(order.phone)) ? usPhone(order.phone) : '';
}

function SmsMark() {
  return <svg viewBox="0 0 32 32" fill="none" aria-hidden="true"><rect x="8" y="3" width="16" height="26" rx="4" stroke="currentColor" strokeWidth="1.5"/><path d="M13 7h6M14 25h4M12 13h8M12 17h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/></svg>;
}

export function SmsPage() {
  const [code, setCode] = useState('');
  const [verified, setVerified] = useState<Verified | null>(null);
  const [useSpecific, setUseSpecific] = useState(false);
  const [specificPhone, setSpecificPhone] = useState('');
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
  const requestedPhone = useSpecific && !verified?.has_pending_order ? usPhone(specificPhone) : '';
  const invalidPhone = useSpecific && !verified?.has_pending_order && !requestedPhone;
  const phoneMismatch = order?.requested_phone && usPhone(order.phone) !== order.requested_phone;

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
    if (lock.current || Date.now() < cooldown || ((action === 'verify' || action === 'acquire') && invalidPhone)) return;
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
        body: JSON.stringify({ activation_code: code.trim(),
          ...(action === 'acquire' && requestedPhone ? { specific_phone: requestedPhone } : {}),
          ...((action === 'check' || action === 'cancel') && order ? { order_id: order.order_id } : {}) }),
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
          if (action === 'acquire' && requestedPhone && Object.hasOwn(SPECIFIC_ERRORS, value.error)) throw new SmsRequestError(SPECIFIC_ERRORS[value.error]);
          throw new SmsRequestError(action === 'verify' ? '激活码暂不可用，请检查输入或联系商家。' : action === 'cancel' ? '暂未确认取消，请继续查看当前号码状态，稍后再试。' : '当前操作未完成，请稍后重试或联系商家。');
        }
        throw new SmsRequestError(mutating ? UNKNOWN_RESULT : '接码服务暂时不可用，请稍后重试。');
      }
      failures.current = 0; setPaused(false);
      if (action !== 'check') setConfirmCancel(false);
      if (action === 'verify') {
        setVerified(value as Verified);
      } else if (action === 'acquire') {
        setOrder({ ...value, requested_phone: requestedPhone || undefined } as Order);
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

  function reset(keepPhone = false) {
    generation.current += 1; controller.current?.abort(); lock.current = false; failures.current = 0;
    setBusy(null); setOrder(null); setVerified(null); setCode(''); setError(''); setNotice(''); setPaused(false); setConfirmCancel(false);
    if (!keepPhone) { setUseSpecific(false); setSpecificPhone(''); }
  }
  function reusePhone() {
    const phone = order ? reusablePhone(order) : '';
    if (!phone || active) return;
    reset(); setCode(code); setUseSpecific(true); setSpecificPhone(phone);
  }
  async function copy(value: string, label: string) {
    const epoch = generation.current;
    try { await navigator.clipboard.writeText(value); if (mounted.current && epoch === generation.current) setNotice(`${label}已复制。`); }
    catch { if (mounted.current && epoch === generation.current) setNotice('复制失败，请选中文字手动复制。'); }
  }
  const step = order?.status === 'RECEIVED' ? 3 : order ? 2 : verified ? 1 : 0;
  const blocked = busy !== null || remainingCooldown > 0;
  const country = order ? countryOf(order) : null;
  const national = order ? nationalNumber(order) : '';
  const shownPhone = order ? displayPhone(order.phone) : '';
  const dialEnd = shownPhone.startsWith('+') ? shownPhone.indexOf(' ') : -1;

  // The page: a short header, then the steps in one card beside the guide (below it on a phone).
  // After a number is issued the card leads with it: country, calling code and two ways to copy it.
  return <PortalShell active="sms" pageTitle="短信接码">
    <div className="sms-page">
      <header className="sms-heading">
        <div className="sms-heading-row">
          <span className="sms-symbol"><SmsMark/></span>
          <div><h1>短信接码</h1><p>用已购买的激活码取一个号码，在这里接收验证码。</p></div>
          <span className="sms-private"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>私密使用 · 不保存激活码</span>
        </div>
      </header>
      <div className="sms-layout">
        <section className="sms-workspace" aria-label="短信接码">
          <ol className="sms-steps" aria-label="接码步骤">{['验证激活码', '获取号码', '接收验证码'].map((label, index) => <li key={label} className={step > index ? 'is-done' : step === index ? 'is-current' : ''} aria-current={step === index ? 'step' : undefined}><span>{step > index ? '✓' : index + 1}</span><b>{label}</b></li>)}</ol>
          {!order ? <div className="sms-content">
            <form onSubmit={event => { event.preventDefault(); if (!verified && !blocked && !invalidPhone && code.trim().length >= 8) void perform('verify'); }}>
              <div className="sms-field"><label htmlFor="sms-code">激活码</label><input id="sms-code" type="text" autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={128} placeholder="输入商家交付的激活码" value={code} disabled={busy !== null || verified !== null} onChange={event => { setCode(event.target.value); setError(''); }}/></div>
              <p className="sms-field-hint">激活码仅用于本次接码，请勿分享给他人。</p>
              {!verified?.has_pending_order ? <div className={`sms-specific${useSpecific ? ' is-enabled' : ''}`}>
                <label className="sms-specific-toggle"><input type="checkbox" role="switch" aria-label="指定手机号" checked={useSpecific} disabled={busy !== null || verified !== null} onChange={event => { setUseSpecific(event.target.checked); setError(''); }}/><span className="sms-toggle-track" aria-hidden="true"/><span><strong>指定手机号</strong><small>再次使用原号码时开启（仅美国号码）</small></span></label>
                {useSpecific ? <><label className="sms-phone-field"><span aria-hidden="true"><Flag country="US"/>+1</span><input type="tel" aria-label="美国手机号" aria-describedby="sms-specific-hint" autoComplete="off" inputMode="tel" placeholder="输入 10 位美国手机号" value={specificPhone} disabled={busy !== null || verified !== null} onChange={event => { setSpecificPhone(event.target.value); setError(''); }} onBlur={event => { const phone = usPhone(event.target.value); if (phone) setSpecificPhone(phone); }}/></label><p id="sms-specific-hint">每次需有可用激活次数，原号码能否再次取得以当前状态为准。{specificPhone && invalidPhone ? '请输入完整的 10 位美国手机号。' : ''}</p></> : null}
              </div> : null}
              {!verified ? <button className="btn btn-primary sms-primary" type="submit" disabled={blocked || invalidPhone || code.trim().length < 8}>{busy === 'verify' ? '正在验证…' : '验证激活码'}<span aria-hidden="true">→</span></button> : null}
            </form>
            {verified ? <div className={`sms-verified${verified.remaining || verified.has_pending_order ? '' : ' is-empty'}`}>
              <div className="sms-verified-head"><span className="sms-verified-mark" aria-hidden="true">✓</span><strong>激活码已验证</strong><span className="sms-uses"><span>剩余 {verified.remaining} 次</span><small>共 {verified.max_uses} 次</small></span></div>
              <p>{verified.has_pending_order ? '此激活码有未结束的接码，将恢复原订单，不重新申请指定号码。' : !verified.remaining ? '此激活码已无可用次数，请更换有效激活码。' : '确认已准备好接收短信，再获取号码。号码有效时间有限。'}</p>
              <button className="btn btn-primary sms-primary" type="button" disabled={blocked || invalidPhone || (!verified.remaining && !verified.has_pending_order)} onClick={() => void perform('acquire')}>{busy === 'acquire' ? '正在获取号码…' : verified.has_pending_order ? '恢复当前号码' : '获取接收号码'}<span aria-hidden="true">→</span></button>
              <button className="sms-text-button" type="button" disabled={busy !== null} onClick={() => reset(true)}>使用其他激活码</button>
            </div> : null}
          </div> : <div className="sms-content">
            <div className="sms-number">
              <div className="sms-number-meta">
                <span className="sms-country"><Flag country={country}/><span>{country ? COUNTRIES[country] : '国家/地区未确认'}</span>{country ? <b>+{DIAL[country]}</b> : null}</span>
                <span className={`sms-live-status${order.status === 'RECEIVED' ? ' is-received' : active ? ' is-active' : ''}`} role="status"><span className="sms-status-dot"/>{STATUS[order.status]}</span>
              </div>
              <span className="sms-number-label">接收号码</span>
              <strong>{dialEnd > 0 ? <><span className="sms-dial">{shownPhone.slice(0, dialEnd)}</span>{shownPhone.slice(dialEnd)}</> : shownPhone}</strong>
              <div className="sms-number-actions"><button type="button" className="sms-copy" onClick={() => void copy(order.phone, '号码')}>复制号码</button>{national && country ? <button type="button" className="sms-copy" onClick={() => void copy(national, '不含区号的号码')}>复制不含 +{DIAL[country]}</button> : null}</div>
            </div>
            {order.sms_code ? <div className="sms-code-result"><span>短信验证码</span><strong>{order.sms_code}</strong><button type="button" className="sms-copy is-primary" onClick={() => void copy(order.sms_code!, '验证码')}>复制验证码</button></div> : <div className={`sms-waiting${active ? ' is-active' : ''}`}><span className="sms-code-placeholder" aria-hidden="true">— — — — — —</span><p>{active ? order.status === 'CANCEL_PENDING' ? '正在等待取消结果。确认前请保留当前页面。' : country ? `在需要验证的平台选择「${PICKER[country]} +${DIAL[country]}」，填入上方号码并发送短信。` : '请在需要验证的平台填写上方号码并发送短信。' : '当前接码已结束，未收到验证码。'}</p>{active && order.wait_seconds ? <small className="sms-waited">已等待 {waited(order.wait_seconds)}</small> : null}</div>}
            {active ? <><div className="sms-poll-status"><span>{paused ? '自动查询已暂停' : !visible ? '返回页面后继续查询' : busy === 'check' ? '正在查询验证码…' : `约 ${Math.max(0, Math.ceil((Math.max(due, cooldown) - now) / 1000))} 秒后自动查询`}</span>{paused ? <button className="sms-text-button" type="button" disabled={blocked} onClick={() => void perform('check')}>重试查询</button> : <span>收到后自动停止</span>}</div>
              {order.status === 'PENDING' ? <div className="sms-cancel">{confirmCancel ? <div className="sms-cancel-confirm"><p>确认取消当前号码？只有确认取消并退回次数后，才能再次取号。</p><div><button className="btn btn-secondary" type="button" disabled={blocked} onClick={() => void perform('cancel')}>{busy === 'cancel' ? '正在提交…' : '确认取消当前号码'}</button><button className="sms-text-button" type="button" disabled={busy !== null} onClick={() => setConfirmCancel(false)}>继续等待</button></div></div> : <button className="sms-text-button" type="button" disabled={blocked} onClick={() => setConfirmCancel(true)}>号码无法使用？申请取消</button>}</div> : null}
            </> : <div className="sms-finish"><p>{order.status === 'RECEIVED' ? '请及时使用验证码。再次接码需重新验证可用次数，并申请新的接码订单。' : '如需再次取号，请重新验证激活码的剩余次数。'}</p><div className="sms-finish-actions">{reusablePhone(order) ? <button className="btn btn-secondary" type="button" onClick={reusePhone}>再次使用此号码</button> : null}<button className="sms-text-button" type="button" onClick={() => reset()}>返回激活码输入</button></div></div>}
          </div>}
          <div className="sms-feedback">{phoneMismatch ? <p role="alert">返回的号码与指定号码不同，已保留本次订单。请先核对处理，不要重复取号。</p> : null}{error ? <p role="alert">{error}{remainingCooldown ? `（${remainingCooldown} 秒后可重试）` : ''}</p> : null}<p role="status">{notice}</p></div>
        </section>
        <aside className="sms-guide" aria-label="使用提示"><h2>使用须知</h2><ol>
          <li><h3>先确认适用平台</h3><p>当前默认服务为 ChatGPT Codex。其他平台及账号限制，请在购买前向商家确认。</p></li>
          <li><h3>按号码的国家填写</h3><p>号码旁会标出国家和区号。平台要求先选国家时，选对国家后填「不含区号」的号码。</p></li>
          <li><h3>取号后及时使用</h3><p>号码有时效。请在需要验证的平台发起短信，本页会自动查看接收结果。</p></li>
          <li><h3>妥善保管激活码</h3><p>本页不保存激活码。刷新或离开后需重新输入；离开页面不会替你取消号码。</p></li>
        </ol><footer>还没有激活码？<a href="/">返回商店咨询购买 <span aria-hidden="true">↗</span></a></footer></aside>
      </div>
    </div>
  </PortalShell>;
}
