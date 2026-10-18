import { useEffect, useRef, useState } from 'react';
import { priceCents, shopRequest } from './catalog';

// price_cents is the supplier's unit price in USDT cents. stock is a count only after an
// explicit refresh; the catalog refresh knows only whether a product is in stock.
type Quote = { price_cents: number; stock: number | null; in_stock: boolean; quoted_at: number };
type Product = { id: string; title: string; quote?: Quote | null };
type Job = { id: string; title: string; quantity: number; trigger_price_cents: number; expires_at: number; status: string; reason: string; order_id: string | null; amount_cents: number | null; delivered_count: number; checked_at: number };
type Overview = { supplier?: { name: string; site: string }; revision: number; enabled: boolean; scheduler_enabled: boolean; balance_cents: number | null; balance_at: number | null; balance_error: string; products: Product[]; jobs: Job[] };
const STATUS: Record<string, string> = { draft: '草稿', waiting: '等待到货', paused: '已暂停', submitting: '提交中', uncertain: '结果待核实', pending: '等待交付', paid: '已付款 · 等待交付', delivered: '采购完成', failed: '供应商订单失败', cancelled: '已取消', expired: '已到期' };
const REASON: Record<string, string> = { stock_low: '库存不足，等待数量齐全后一次购买。', price_high: '当前价格高于触发单价，已暂停。', balance_low: '供应商余额不足，已暂停。', source_disabled: '来源已关闭；开启来源后仍需手动启用预订。', source_changed: '来源已删除或重新添加，请重新创建预订。', supplier_unavailable: '供应商暂不可用，稍后重新检查。', credentials_unavailable: '供应商凭据不可用，请检查来源设置。', product_changed: '商品或交付方式发生变化，已暂停。', invalid_response: '供应商返回内容异常，尚未确认交付。', rate_limited: '供应商限制请求频率，稍后重新检查。', stock_changed: '供应商库存发生变化。', result_uncertain: '可能已扣款，请到供应商核对订单。系统不会再次下单，请勿重复创建相同预订。', price_changed: '实际扣款高于提交前的价格条件，请核对供应商订单。', manual: '' };
// Still owned by the scheduler or awaiting the merchant; the rest is history.
const OPEN = new Set(['draft', 'waiting', 'paused', 'submitting', 'uncertain', 'pending', 'paid']);
const amount = (cents: number) => (cents / 100).toFixed(2);
const usd = (cents: number) => `${amount(cents)} USDT`;
const when = (timestamp: number | null) => timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未查询';
const short = (timestamp: number) => new Date(timestamp * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
const inStock = (quote: Quote) => quote.stock !== null ? quote.stock > 0 : quote.in_stock;
const stockText = (quote: Quote) => !inStock(quote) ? '缺货' : quote.stock !== null ? `库存 ${quote.stock}` : '有货';
function localInput(time: number) {
  const date = new Date(time);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}
const inDays = (days: number) => localInput(Date.now() + days * 86400000);

const BALANCE_REASON: Record<string, string> = { invalid_response: '供应商余额格式异常', credentials_unavailable: '供应商凭据不可用', rate_limited: '供应商限制请求频率', source_disabled: '来源已关闭', supplier_unavailable: '供应商暂不可用' };

// 商品管理 → 采购预订: the balance and a new preorder beside the preorders so far.
export function ShopProcurement({ unsavedProducts = false }: { unsavedProducts?: boolean }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [product, setProduct] = useState('');
  const [quantity, setQuantity] = useState('1');
  const [price, setPrice] = useState('');
  const [deadline, setDeadline] = useState(() => inDays(7));
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
      setNotice('草稿已保存。在预订记录里点“启用预订”并确认后，才会监控和扣款采购。');
    }
  }
  async function act(job: Job, action: string) {
    if (!data) return;
    if (await request(`preorders/${job.id}/${action}`, { revision: data.revision, ...(action === 'enable' ? { accept_supplier_price: accepted } : {}) }, action === 'reveal' ? job.id : undefined)) {
      if (action === 'enable') { setConfirming(null); setAccepted(false); }
    }
  }

  const selected = data?.products.find(item => item.id === product);
  const quote = selected?.quote ?? null;
  const quotedAt = Math.max(0, ...(data?.products ?? []).map(item => item.quote?.quoted_at ?? 0));
  const trigger = priceCents(price);
  const count = /^\d{1,3}$/.test(quantity) ? Number(quantity) : 0;
  const estimate = trigger !== null && count >= 1 && count <= 100 ? trigger * count : null;
  const open = data?.jobs.filter(job => OPEN.has(job.status)).length ?? 0;
  const done = data?.jobs.filter(job => job.status === 'delivered').length ?? 0;
  const job = (item: Job) => <article key={item.id} className={`shop-procurement-job is-${item.status}`}>
    <header className="shop-procurement-job-title"><strong>{item.title}</strong><span className={`shop-procurement-status is-${item.status}`}>{STATUS[item.status] ?? item.status}</span>{item.checked_at ? <small className="shop-procurement-checked">检查于 {when(item.checked_at)}</small> : null}</header>
    <dl className="shop-procurement-facts">
      <div><dt>数量</dt><dd>{item.quantity} 件</dd></div>
      <div><dt>触发单价</dt><dd>≤ {usd(item.trigger_price_cents)}</dd></div>
      <div><dt>条件金额</dt><dd>{usd(item.quantity * item.trigger_price_cents)}</dd></div>
      <div><dt>截止</dt><dd>{short(item.expires_at)}</dd></div>
    </dl>
    {item.reason && REASON[item.reason] ? <p className="shop-procurement-reason">{REASON[item.reason]}</p> : null}
    {item.order_id ? <p className="shop-procurement-order">订单 {item.order_id}{item.amount_cents !== null ? ` · 实付 ${usd(item.amount_cents)}` : ''}</p> : null}
    <div className="shop-procurement-actions">
      {['draft', 'paused'].includes(item.status) ? <button type="button" className="btn btn-primary btn-sm" disabled={busy || !data?.enabled || !data?.scheduler_enabled} onClick={() => { setConfirming(item); setAccepted(false); }}>启用预订</button> : null}
      {item.status === 'waiting' ? <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void act(item, 'pause')}>暂停</button> : null}
      {['draft', 'waiting', 'paused'].includes(item.status) ? <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void act(item, 'cancel')}>取消预订</button> : null}
      {item.status === 'delivered' ? <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => revealed[item.id] ? setRevealed(old => { const next = { ...old }; delete next[item.id]; return next; }) : void act(item, 'reveal')}>{revealed[item.id] ? '隐藏交付内容' : `查看交付内容（${item.delivered_count} 件）`}</button> : null}
      {['uncertain', 'failed'].includes(item.status) && data?.supplier?.site ? <a className="btn btn-secondary btn-sm" href={data.supplier.site} target="_blank" rel="noopener noreferrer">前往供应商核对 ↗</a> : null}
    </div>
    {confirming?.id === item.id ? <div className="shop-procurement-confirm" ref={confirmationRef} tabIndex={-1} role="group" aria-label="确认启用预订">
      <h3>启用 {confirming.title} · {confirming.quantity} 件</h3>
      <p>满足条件后将自动使用供应商余额采购，买满后停止。触发单价 {usd(confirming.trigger_price_cents)}，条件金额 {usd(confirming.quantity * confirming.trigger_price_cents)}。</p>
      <label><input type="checkbox" checked={accepted} disabled={busy} onChange={e => setAccepted(e.target.checked)}/>我授权此预订自动采购，并理解供应商不支持锁价，最终扣款可能因其调价而变化。</label>
      <div className="shop-procurement-actions"><button type="button" className="btn btn-primary" disabled={busy || !accepted} onClick={() => void act(confirming, 'enable')}>确认启用并允许扣款</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirming(null)}>暂不启用</button></div>
    </div> : null}
    {revealed[item.id] ? <div className="shop-procurement-delivery"><textarea aria-label={`${item.title} 交付内容`} readOnly value={revealed[item.id]?.join('\n') ?? ''} spellCheck={false}/><button type="button" className="btn btn-secondary" onClick={() => void navigator.clipboard.writeText(revealed[item.id]?.join('\n') ?? '').then(() => setNotice('已复制交付内容。')).catch(() => setError('复制失败，请在文本框内手动复制。'))}>复制交付内容</button><small>切换标签页或 60 秒后隐藏。请自行妥善保存。</small></div> : null}
  </article>;
  const openJobs = data?.jobs.filter(item => OPEN.has(item.status)) ?? [];
  const pastJobs = data?.jobs.filter(item => !OPEN.has(item.status)) ?? [];
  // The balance and counts in one bar; below it, a new preorder beside the preorders so far.
  return <section className="shop-procurement" aria-label="采购预订">
    {unsavedProducts ? <p className="shop-procurement-banner">商品管理里还有未保存的修改，<a href="/admin/shop">返回商品管理</a>保存后再离开。</p> : null}
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status" className="shop-procurement-notice">{notice}</p>
    {!data ? <p className="shop-procurement-banner">正在读取采购记录…</p> : <>
      {!data.enabled || !data.scheduler_enabled ? <p className="shop-procurement-banner is-warn">{!data.enabled ? '供应商来源未启用，当前不会查询库存或采购。' : '采购调度未开启，草稿可以保存，暂不能启用。'}</p> : null}
      <section className="shop-procurement-summary" aria-label="采购概览">
        <div className="shop-procurement-balance" role="group" aria-label="供应商余额">
          <span>{data.supplier?.name ?? '供应商'} 余额</span><strong>{data.balance_cents == null ? '—' : usd(data.balance_cents)}</strong>
          <small>{data.balance_at ? `${data.balance_error ? '上次成功查询于' : '查询于'} ${when(data.balance_at)}` : '尚未成功查询'}{data.balance_error ? ` · ${BALANCE_REASON[data.balance_error] ?? '余额查询未成功'}${data.balance_cents == null ? '' : '，显示上次余额'}` : ''}</small>
        </div>
        <dl className="shop-procurement-counts">
          <div className={open ? 'is-active' : undefined}><dt>进行中</dt><dd>{open}</dd></div>
          <div><dt>已完成</dt><dd>{done}</dd></div>
          <div><dt>报价更新</dt><dd>{quotedAt ? short(quotedAt) : '—'}</dd></div>
        </dl>
        <button className="btn btn-secondary" type="button" disabled={busy || !data.enabled} onClick={() => void request('balance', {})}>刷新余额和报价</button>
      </section>
      <div className="shop-procurement-layout">
        <form className="shop-procurement-card shop-procurement-form" aria-labelledby="shop-procurement-new" onSubmit={event => { event.preventDefault(); void create(); }}>
          <h2 id="shop-procurement-new">新建预订</h2>
          <fieldset className="shop-procurement-products"><legend>采购商品</legend>
            {data.products.map(item => <label key={item.id} className={`shop-procurement-product${item.id === product ? ' is-selected' : ''}`}>
              <input type="radio" name="shop-procurement-product" value={item.id} checked={item.id === product} disabled={busy} onChange={() => setProduct(item.id)}/>
              <span className="shop-procurement-product-name">{item.title}</span>
              <span className="shop-procurement-product-quote">{item.quote ? <><strong>{usd(item.quote.price_cents)}</strong><span className={inStock(item.quote) ? 'is-in' : 'is-out'}>{stockText(item.quote)}</span></> : <span>暂无报价</span>}</span>
            </label>)}
            <small>{quotedAt ? `报价更新于 ${short(quotedAt)}，缺货时同样显示供应商价格。` : '暂无报价，可点“刷新余额和报价”查询。'}</small>
          </fieldset>
          <div className="shop-procurement-fields">
            <label>购买数量<input inputMode="numeric" value={quantity} disabled={busy} onChange={e => setQuantity(e.target.value)} /></label>
            <label>触发单价（USDT）<input inputMode="decimal" value={price} placeholder={quote ? amount(quote.price_cents) : '可接受单价'} disabled={busy} onChange={e => setPrice(e.target.value)} /></label>
          </div>
          {quote ? <button type="button" className="shop-procurement-link" disabled={busy} onClick={() => setPrice(amount(quote.price_cents))}>按现价 {amount(quote.price_cents)} 填入触发单价</button> : null}
          <label>截止时间（本地）<input type="datetime-local" value={deadline} disabled={busy} onChange={e => setDeadline(e.target.value)} /></label>
          <div className="shop-procurement-quick" role="group" aria-label="截止时间快捷设置">{[1, 3, 7, 30].map(days => <button key={days} type="button" disabled={busy} onClick={() => setDeadline(inDays(days))}>{days} 天后</button>)}</div>
          <p className="shop-procurement-estimate">{estimate === null ? '填写数量和触发单价后显示条件金额。' : <>条件金额 <strong>{usd(estimate)}</strong>{count > 1 && trigger !== null ? `（${count} × ${amount(trigger)}）` : ''}
            {data.balance_cents != null && estimate > data.balance_cents ? <span className="is-warn">高于当前余额，启用后会因余额不足暂停。</span> : null}</>}</p>
          <button type="submit" className="btn btn-primary" disabled={busy}>保存预订草稿</button>
          <small className="shop-procurement-help">草稿不会扣款；在预订记录里启用并确认后，才会监控价格和库存并自动采购。</small>
        </form>
        <section className="shop-procurement-records" aria-labelledby="shop-procurement-records">
          <header><h2 id="shop-procurement-records">预订记录</h2><span>{data.jobs.length ? `${open ? `未完成 ${open} · ` : ''}共 ${data.jobs.length} 条` : ''}</span></header>
          {data.jobs.length === 0 ? <div className="shop-procurement-empty"><strong>暂无预订</strong><span>先保存草稿，再在这里单独启用。</span></div> : <>
            {openJobs.length ? <div className="shop-procurement-group"><h3>进行中 · {openJobs.length}</h3><div className="shop-procurement-list">{openJobs.map(job)}</div></div> : null}
            {pastJobs.length ? <div className="shop-procurement-group"><h3>已结束 · {pastJobs.length}</h3><div className="shop-procurement-list">{pastJobs.map(job)}</div></div> : null}
          </>}
        </section>
      </div>
    </>}
  </section>;
}
