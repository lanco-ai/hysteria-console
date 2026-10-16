import { useEffect, useRef, useState } from 'react';
import { priceCents, shopRequest } from './catalog';

type Job = { id: string; title: string; quantity: number; trigger_price_cents: number; expires_at: number; status: string; reason: string; order_id: string | null; amount_cents: number | null; delivered_count: number; checked_at: number };
type Overview = { revision: number; enabled: boolean; scheduler_enabled: boolean; balance_cents: number | null; balance_at: number | null; balance_error: string; products: { id: string; title: string }[]; jobs: Job[] };
const STATUS: Record<string, string> = { draft: '草稿', waiting: '等待到货', paused: '已暂停', submitting: '提交中', uncertain: '结果待核实', pending: '等待交付', paid: '已付款 · 等待交付', delivered: '采购完成', failed: '供应商订单失败', cancelled: '已取消', expired: '已到期' };
const REASON: Record<string, string> = { stock_low: '库存不足，等待数量齐全后一次购买。', price_high: '当前价格高于触发单价，已暂停。', balance_low: '供应商余额不足，已暂停。', source_disabled: '来源已关闭；开启来源后仍需手动启用预订。', source_changed: '来源已删除或重新添加，请重新创建预订。', supplier_unavailable: '供应商暂不可用，稍后重新检查。', credentials_unavailable: '供应商凭据不可用，请检查来源设置。', product_changed: '商品或交付方式发生变化，已暂停。', invalid_response: '供应商返回内容异常，尚未确认交付。', rate_limited: '供应商限制请求频率，稍后重新检查。', stock_changed: '供应商库存发生变化。', result_uncertain: '可能已扣款，请到供应商核对订单。系统不会再次下单，请勿重复创建相同预订。', price_changed: '实际扣款高于提交前的价格条件，请核对供应商订单。', manual: '' };
const usd = (cents: number) => `${(cents / 100).toFixed(2)} USDT`;
const when = (timestamp: number | null) => timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未查询';
function defaultDeadline() {
  const date = new Date(Date.now() + 7 * 86400000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

export function ShopProcurement() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [product, setProduct] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [price, setPrice] = useState('');
  const [deadline, setDeadline] = useState(defaultDeadline);
  const requestId = useRef(crypto.randomUUID().replaceAll('-', ''));
  const [confirming, setConfirming] = useState<Job | null>(null);
  const [accepted, setAccepted] = useState(false);
  const confirmationRef = useRef<HTMLDivElement>(null);
  const [revealed, setRevealed] = useState<Record<string, string[]>>({});
  const [notice, setNotice] = useState('');
  useEffect(() => {
    if (confirming) {
      confirmationRef.current?.scrollIntoView({ block: 'center', behavior: 'smooth' });
      confirmationRef.current?.focus({ preventScroll: true });
    }
  }, [confirming]);
  useEffect(() => {
    let active = true;
    const load = () => {
      if (locked.current || document.hidden) return;
      void shopRequest<Overview>('admin/procurement').then(value => {
        if (active && !locked.current) { setData(value); setProduct(old => old || value.products[0]?.id || ''); }
      }).catch(reason => { if (active) setError((reason as Error).message); });
    };
    load();
    const timer = window.setInterval(load, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    const hide = () => { if (document.hidden) setRevealed({}); };
    document.addEventListener('visibilitychange', hide);
    const timer = window.setTimeout(() => setRevealed({}), 60000);
    return () => { document.removeEventListener('visibilitychange', hide); window.clearTimeout(timer); };
  }, [revealed]);

  async function request(path: string, payload: object, revealId?: string) {
    if (locked.current) return false;
    locked.current = true; setBusy(true); setError(''); setNotice('');
    try {
      const result = await shopRequest<Overview | { keys: string[] }>(`admin/procurement/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if ('keys' in result && revealId) setRevealed(old => ({ ...old, [revealId]: result.keys }));
      else if ('jobs' in result) setData(result);
      return true;
    } catch (reason) { setError((reason as Error).message); return false; }
    finally { locked.current = false; setBusy(false); }
  }
  async function create() {
    if (!data) return;
    const trigger = priceCents(price);
    const count = Number(quantity);
    const expires = Math.floor(new Date(deadline).getTime() / 1000);
    const now = Math.floor(Date.now() / 1000);
    if (!product || trigger === null || trigger > 1_000_000 || !/^\d+$/.test(quantity)
      || count < 1 || count > 100 || !Number.isFinite(expires) || expires <= now + 60 || expires > now + 90 * 86400) {
      setError('请填写商品、1–100 件数量、有效单价，以及 1 分钟至 90 天内的截止时间。'); return;
    }
    if (await request('preorders', { revision: data.revision, id: requestId.current, product_id: product, quantity: count, trigger_price_cents: trigger, expires_at: expires })) {
      requestId.current = crypto.randomUUID().replaceAll('-', '');
      setNotice('草稿已保存。点击“启用预订”并确认后，才会监控和扣款采购。');
    }
  }
  async function act(job: Job, action: string) {
    if (!data) return;
    if (await request(`preorders/${job.id}/${action}`, { revision: data.revision, ...(action === 'enable' ? { accept_supplier_price: accepted } : {}) }, action === 'reveal' ? job.id : undefined)) {
      if (action === 'enable') { setConfirming(null); setAccepted(false); }
    }
  }

  return <section className="shop-procurement" aria-label="采购预订">
    <div className="shop-procurement-heading"><div><h2>采购预订</h2><p>ProdSeller 余额采购 · 一次买满后停止</p></div>
      <div className="shop-procurement-balance"><span>供应商余额</span><strong>{data?.balance_cents == null ? '—' : usd(data.balance_cents)}</strong>
        <small>{when(data?.balance_at ?? null)}{data?.balance_error ? ' · 查询未成功，显示上次余额' : ''}</small>
        <button className="btn btn-secondary" type="button" disabled={busy || !data?.enabled} onClick={() => void request('balance', {})}>查询余额</button></div></div>
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status">{notice}</p>
    {!data ? <p>正在读取采购记录…</p> : <>
      {!data.enabled || !data.scheduler_enabled ? <p className="shop-procurement-help">{!data.enabled ? '供应商来源未启用，当前不会查询库存或采购。' : '采购调度未开启，草稿可以保存，暂不能启用。'}</p> : null}
      <form className="shop-procurement-form" onSubmit={event => { event.preventDefault(); void create(); }}>
        <label>采购商品<select value={product} disabled={busy} onChange={e => setProduct(e.target.value)}>{data.products.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}</select></label>
        <label>购买数量<input inputMode="numeric" value={quantity} disabled={busy} onChange={e => setQuantity(e.target.value)} /></label>
        <label>触发单价（USDT）<input inputMode="decimal" value={price} placeholder="填写可接受单价" disabled={busy} onChange={e => setPrice(e.target.value)} /></label>
        <label>截止时间（本地）<input type="datetime-local" value={deadline} disabled={busy} onChange={e => setDeadline(e.target.value)} /></label>
        <button type="submit" className="btn btn-primary" disabled={busy}>保存预订草稿</button>
      </form>
      <p className="shop-procurement-help">约每分钟检查一次，等库存足够后整单购买。触发单价用于提交前检查；供应商不支持锁价，最终扣款以供应商为准。</p>
      {data.jobs.length === 0 ? <div className="shop-procurement-empty">暂无预订。先保存草稿，再单独启用。</div> : <div className="shop-procurement-list">{data.jobs.map(job => <article key={job.id} className="shop-procurement-job">
        <div className="shop-procurement-job-title"><strong>{job.title}</strong><span className={`shop-procurement-status is-${job.status}`}>{STATUS[job.status] ?? job.status}</span></div>
        <p>{job.quantity} 件 · 触发单价 ≤ {usd(job.trigger_price_cents)} · 条件金额 {usd(job.quantity * job.trigger_price_cents)}</p>
        <small>截止 {when(job.expires_at)}{job.checked_at ? ` · 检查于 ${when(job.checked_at)}` : ''}</small>
        {job.reason && REASON[job.reason] ? <p className="shop-procurement-help">{REASON[job.reason]}</p> : null}
        {job.order_id ? <p className="shop-procurement-order">订单 {job.order_id}{job.amount_cents !== null ? ` · 实付 ${usd(job.amount_cents)}` : ''}</p> : null}
        <div className="shop-procurement-actions">
          {['draft', 'paused'].includes(job.status) ? <button type="button" className="btn btn-primary" disabled={busy || !data.enabled || !data.scheduler_enabled} onClick={() => { setConfirming(job); setAccepted(false); }}>启用预订</button> : null}
          {job.status === 'waiting' ? <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void act(job, 'pause')}>暂停</button> : null}
          {['draft', 'waiting', 'paused'].includes(job.status) ? <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void act(job, 'cancel')}>取消预订</button> : null}
          {job.status === 'delivered' ? <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => revealed[job.id] ? setRevealed(old => { const next = { ...old }; delete next[job.id]; return next; }) : void act(job, 'reveal')}>{revealed[job.id] ? '隐藏交付内容' : `查看交付内容（${job.delivered_count} 件）`}</button> : null}
          {['uncertain', 'failed'].includes(job.status) ? <a className="btn btn-secondary" href="https://prodseller.com/" target="_blank" rel="noopener noreferrer">前往供应商核对 ↗</a> : null}
        </div>
        {revealed[job.id] ? <div className="shop-procurement-delivery"><textarea aria-label={`${job.title} 交付内容`} readOnly value={revealed[job.id]?.join('\n') ?? ''} spellCheck={false}/><button type="button" className="btn btn-secondary" onClick={() => void navigator.clipboard.writeText(revealed[job.id]?.join('\n') ?? '').then(() => setNotice('已复制交付内容。')).catch(() => setError('复制失败，请在文本框内手动复制。'))}>复制交付内容</button><small>切换标签页或 60 秒后隐藏。请自行妥善保存。</small></div> : null}
      </article>)}</div>}
      {confirming ? <div className="shop-procurement-confirm" ref={confirmationRef} tabIndex={-1} role="group" aria-label="确认启用预订">
        <h3>启用 {confirming.title} · {confirming.quantity} 件</h3>
        <p>满足条件后将自动使用供应商余额采购，买满后停止。触发单价 {usd(confirming.trigger_price_cents)}，条件金额 {usd(confirming.quantity * confirming.trigger_price_cents)}。</p>
        <label><input type="checkbox" checked={accepted} disabled={busy} onChange={e => setAccepted(e.target.checked)}/>我授权此预订自动采购，并理解供应商不支持锁价，最终扣款可能因其调价而变化。</label>
        <div className="shop-procurement-actions"><button type="button" className="btn btn-primary" disabled={busy || !accepted} onClick={() => void act(confirming, 'enable')}>确认启用并允许扣款</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirming(null)}>暂不启用</button></div>
      </div> : null}
    </>}
  </section>;
}
