import { useEffect, useRef, useState } from 'react';
import { money, shopRequest } from './catalog';

// A Gemini or Muse AI SKU the merchant can stock: its storefront name and sale state.
export type StockedProduct = { key: string; productId: string; title: string; label: string; category: string; priceCents: number | null; published: boolean; supplierAvailable: boolean };
type Meta = { id: string; added_at: number; delivered_at: number; note: string };
type Overview = { revision: number; skus: Record<string, { stock: Meta[]; delivered: Meta[] } | undefined> };
type Delivered = { id: string; line: string; delivered_at: number; note: string };
type AddResult = Overview & { added: number; skipped: number };
type DeliverResult = Overview & { delivered: Delivered };

const when = (timestamp: number) => new Date(timestamp * 1000).toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false });
const token = () => crypto.randomUUID().replaceAll('-', '');
const linesOf = (text: string) => text.split('\n').map(line => line.trim()).filter(Boolean);

// What a buyer receives with a unit: the line itself and how to take the account over.
const MUSE_TAKEOVER = [
  '到账后请立即修改：',
  'A）更改邮箱：「联络资料」→ 增加你的电邮；验证新电邮后，删除原旧电邮。',
  'B）更改密码：输入新密码，并勾选「如有其他人使用了你的帐户，请选择此选项」，登出其他装置。',
  'C）换绑 2FA：进入「双重验证」，重新绑定为你自己的手机号或验证器 App（如 Google Authenticator）。',
];
export function deliveryText(product: StockedProduct, line: string): string {
  if (product.category === 'Muse AI') return [`Muse AI 账号：${line}`, '登录：打开 https://muse.ai/ ，用以上账号和密码登录，先核对 Token 余额。', '', ...MUSE_TAKEOVER].join('\n');
  if (product.category === 'Gemini') return [`${product.title} 领取链接：${line}`, '请先登录你自己的 Google 账号，再用 Chrome 浏览器打开链接，按页面提示领取。'].join('\n');
  return `${product.title}：${line}`;
}

// 商品管理 → 本店库存: the merchant's own accounts and links for Gemini and Muse AI.
export function ShopInventory({ products, loading = false, unsavedProducts = false, onCounts }: { products: StockedProduct[]; loading?: boolean; unsavedProducts?: boolean; onCounts?: (stock: Record<string, number>) => void }) {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});
  // One token per pending delivery: a retried request returns the same unit.
  const pending = useRef<Record<string, string>>({});
  const [delivered, setDelivered] = useState<{ key: string; item: Delivered } | null>(null);
  const [revealed, setRevealed] = useState<Record<string, string>>({});
  useEffect(() => {
    let active = true;
    const load = () => {
      if (locked.current || document.hidden) return;
      void shopRequest<Overview>('admin/inventory').then(value => { if (active && !locked.current) setData(value); })
        .catch(reason => { if (active) setError((reason as Error).message); });
    };
    load();
    const timer = window.setInterval(load, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  // 商品管理 shows the same counts beside each SKU without reading everything again.
  useEffect(() => {
    if (data && onCounts) onCounts(Object.fromEntries(Object.entries(data.skus).flatMap(([key, entry]) => entry?.stock.length ? [[key, entry.stock.length]] : [])));
  }, [data, onCounts]);
  // Accounts on screen are hidden again after a minute or when the tab is left.
  useEffect(() => {
    const hide = () => { if (document.hidden) { setRevealed({}); setDelivered(null); } };
    document.addEventListener('visibilitychange', hide);
    const timer = window.setTimeout(() => { setRevealed({}); setDelivered(null); }, 60000);
    return () => { document.removeEventListener('visibilitychange', hide); window.clearTimeout(timer); };
  }, [revealed, delivered]);

  async function post<T>(path: string, payload: object): Promise<T | null> {
    if (locked.current) return null;
    locked.current = true; setBusy(true); setError(''); setNotice('');
    try {
      return await shopRequest<T>(`admin/inventory/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
    } catch (reason) { setError((reason as Error).message); return null; }
    finally { locked.current = false; setBusy(false); }
  }
  async function add(product: StockedProduct) {
    if (!data) return;
    const lines = linesOf(drafts[product.key] ?? '');
    if (!lines.length) { setError('请先粘贴要添加的账号或链接，每行一件。'); return; }
    if (lines.length > 500) { setError('每次最多添加 500 件，请分批添加。'); return; }
    const result = await post<AddResult>('add', { revision: data.revision, sku: product.key, lines });
    if (result) {
      setData(result);
      setDrafts(old => ({ ...old, [product.key]: '' }));
      setNotice(`${product.title}：已添加 ${result.added} 件${result.skipped ? `，跳过 ${result.skipped} 件重复` : ''}。`);
    }
  }
  async function deliver(product: StockedProduct) {
    const request = pending.current[product.key] ?? token();
    pending.current[product.key] = request;
    const result = await post<DeliverResult>('deliver', { sku: product.key, request, note: (notes[product.key] ?? '').trim() });
    if (result) {
      delete pending.current[product.key];
      setData(result);
      setNotes(old => ({ ...old, [product.key]: '' }));
      setDelivered({ key: product.key, item: result.delivered });
      setNotice(`${product.title}：已发货 1 件，已从现货扣除。`);
    }
  }
  async function act(item: Meta, action: 'reveal' | 'remove' | 'restock') {
    if (!data) return;
    if (action === 'remove' && !window.confirm('删除这件库存？删除后无法恢复。')) return;
    const result = await post<Overview | { line: string }>(`items/${item.id}/${action}`, { revision: data.revision });
    if (!result) return;
    if ('line' in result) setRevealed(old => ({ ...old, [item.id]: result.line }));
    else { setData(result); setNotice(action === 'remove' ? '已删除这件库存。' : '已退回现货。'); }
  }
  function copy(text: string) {
    void navigator.clipboard.writeText(text).then(() => setNotice('已复制，可直接发给买家。')).catch(() => setError('复制失败，请在文本框内手动复制。'));
  }

  return <section className="shop-inventory" aria-label="本店库存">
    {unsavedProducts ? <p className="shop-procurement-banner">商品管理里还有未保存的修改，<a href="/admin/shop">返回商品管理</a>保存后再离开。</p> : null}
    <p className="shop-inventory-intro">账号和链接只保存在本服务器：列表和前台只显示数量，点“查看”或发货时才显示内容。有现货时，即使供应商缺货，前台也可下单；发货后自动计入已售。</p>
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status" className="shop-procurement-notice">{notice}</p>
    {loading || !data ? <p className="shop-procurement-banner">正在读取库存…</p> : !products.length ? <p className="shop-procurement-banner">暂无可管理库存的商品：Gemini 和 Muse AI 的来源启用后会显示在这里。</p>
      : <div className="shop-inventory-list">{products.map(product => {
        const entry = data.skus[product.key] ?? { stock: [], delivered: [] };
        const draft = linesOf(drafts[product.key] ?? '').length;
        const shown = delivered?.key === product.key ? delivered.item : null;
        return <article key={product.key} className="shop-inventory-product" aria-label={`${product.title} 库存`}>
          <header className="shop-inventory-head">
            <div><h2>{product.title}</h2><small>{product.label}</small></div>
            <div className="shop-inventory-tags">
              <span className={product.published ? 'is-on' : undefined}>{product.published ? `已上架${product.priceCents !== null ? ` · ${money(product.priceCents)}` : ''}` : '未上架'}</span>
              <span>{product.supplierAvailable ? '供应商有货' : '供应商缺货'}</span>
            </div>
          </header>
          <dl className="shop-inventory-counts">
            <div className={entry.stock.length ? 'is-stocked' : undefined}><dt>现货</dt><dd>{entry.stock.length}<small>件</small></dd></div>
            <div><dt>已发货</dt><dd>{entry.delivered.length}<small>件</small></dd></div>
          </dl>
          {!product.published && entry.stock.length ? <p className="shop-inventory-hint">未上架：在商品管理上架后，顾客才能看到现货并下单。</p> : null}
          <div className="shop-inventory-deliver">
            <label>发货备注<input value={notes[product.key] ?? ''} maxLength={60} placeholder="可选，例如买家微信" disabled={busy} onChange={event => setNotes(old => ({ ...old, [product.key]: event.target.value }))}/></label>
            <button type="button" className="btn btn-primary" disabled={busy || !entry.stock.length} onClick={() => void deliver(product)}>发货一件</button>
          </div>
          {shown ? <div className="shop-inventory-delivered" role="group" aria-label="发货内容">
            <textarea aria-label={`${product.title} 发货内容`} readOnly rows={product.category === 'Muse AI' ? 7 : 3} value={deliveryText(product, shown.line)} spellCheck={false}/>
            <div><button type="button" className="btn btn-secondary" onClick={() => copy(deliveryText(product, shown.line))}>复制发货内容</button><button type="button" className="btn btn-secondary" onClick={() => setDelivered(null)}>隐藏</button></div>
            <small>已从现货扣除并计入已售。切换标签页或 60 秒后隐藏；之后可在发货记录里查看。</small>
          </div> : null}
          <label className="shop-inventory-add">添加库存<textarea value={drafts[product.key] ?? ''} rows={4} disabled={busy} spellCheck={false}
            placeholder={product.category === 'Muse AI' ? '每行一件，例如：\n账号 | 密码' : '每行一件，例如：\n领取链接'}
            onChange={event => setDrafts(old => ({ ...old, [product.key]: event.target.value }))}/></label>
          <div className="shop-inventory-add-actions"><button type="button" className="btn btn-secondary" disabled={busy || !draft} onClick={() => void add(product)}>{draft ? `添加 ${draft} 件到库存` : '添加到库存'}</button><small>与已有库存完全相同的行会跳过。</small></div>
          <details className="shop-inventory-details"><summary>现货明细（{entry.stock.length}）</summary>
            {entry.stock.length ? <ol>{entry.stock.map((item, index) => <li key={item.id}>
              <span>第 {index + 1} 件 · 添加于 {when(item.added_at)}</span>
              {revealed[item.id] ? <code>{revealed[item.id]}</code> : null}
              <span className="shop-inventory-row-actions"><button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => revealed[item.id] ? setRevealed(old => { const next = { ...old }; delete next[item.id]; return next; }) : void act(item, 'reveal')}>{revealed[item.id] ? '隐藏' : '查看'}</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void act(item, 'remove')}>删除</button></span>
            </li>)}</ol> : <p>暂无现货。</p>}
          </details>
          <details className="shop-inventory-details"><summary>发货记录（{entry.delivered.length}）</summary>
            {entry.delivered.length ? <ol>{entry.delivered.map(item => <li key={item.id}>
              <span>{when(item.delivered_at)} 发货{item.note ? ` · ${item.note}` : ''}</span>
              {revealed[item.id] ? <code>{revealed[item.id]}</code> : null}
              <span className="shop-inventory-row-actions"><button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => revealed[item.id] ? setRevealed(old => { const next = { ...old }; delete next[item.id]; return next; }) : void act(item, 'reveal')}>{revealed[item.id] ? '隐藏' : '查看'}</button>
                <button type="button" className="btn btn-secondary btn-sm" disabled={busy} onClick={() => void act(item, 'restock')}>退回现货</button></span>
            </li>)}</ol> : <p>暂无发货记录。</p>}
          </details>
        </article>;
      })}</div>}
  </section>;
}
