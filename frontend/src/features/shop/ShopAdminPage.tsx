import { useEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';
import { isNoticeProduct } from './ShopNotice';

type Setting = { price_cents: number | null; published: boolean };
type SourceItem = { key: string; product_id: string; title: string; public_title?: string; label: string; cost_cents: number; available: boolean };
type ProductCopy = Record<string, { description: string; after_sales: string }>;
type SourceStatus = { last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Merchant = { sources?: Record<string, SourceStatus>; products?: ProductCopy; revision: number; telegram: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Draft = Record<string, { price: string; published: boolean }>;
type Brand = 'GPT' | 'Claude' | 'Grok';
type Filter = 'all' | 'published' | 'unpublished' | 'unpriced' | 'soldout';

const BRANDS: Brand[] = ['GPT', 'Claude', 'Grok'];
const FILTERS: [Filter, string][] = [['all', '全部'], ['published', '已上架'], ['unpublished', '未上架'], ['unpriced', '待定价'], ['soldout', '来源售罄']];
// Same split as the storefront categories: secondary-source product ids start at 10^12.
const SECONDARY_PRODUCT_BASE = 1_000_000_000_000;
const EMPTY_COPY = { description: '', after_sales: '' };

function isSecondary(productId: string) { return Number(productId) >= SECONDARY_PRODUCT_BASE; }
function brandOf(item: SourceItem): Brand {
  if (!isSecondary(item.product_id)) return 'GPT';
  return /^claude/i.test(item.public_title ?? item.title) ? 'Claude' : 'Grok';
}
function priceText(setting: Setting | undefined) { return setting?.price_cents == null ? '' : money(setting.price_cents).slice(1); }
function draftFor(value: Merchant): Draft {
  const draft: Draft = {};
  for (const [key, setting] of Object.entries(value.skus)) draft[key] = { price: priceText(setting), published: setting.published };
  for (const item of value.items) draft[item.key] ??= { price: '', published: false };
  return draft;
}
// NaN marks unparsable input so it never compares equal to a saved price.
function draftCents(price: string) { return price === '' ? null : priceCents(price) ?? Number.NaN; }
// Filters and counts describe the saved storefront, so rows never jump while being edited.
function matchesFilter(filter: Filter, item: SourceItem, saved: Setting | undefined) {
  if (filter === 'published') return saved?.published === true;
  if (filter === 'unpublished') return saved?.published !== true;
  if (filter === 'unpriced') return saved?.price_cents == null;
  if (filter === 'soldout') return !item.available;
  return true;
}
function marginOf(cost: number, price: string, published: boolean) {
  if (price === '') return published ? { tone: 'invalid', amount: '需填写售价', rate: '' } : { tone: 'empty', amount: '—', rate: '' };
  const cents = priceCents(price);
  if (cents === null) return { tone: 'invalid', amount: '售价格式有误', rate: '' };
  const profit = cents - cost;
  return { tone: profit < 0 ? 'loss' : 'gain', amount: `${profit < 0 ? '-' : ''}${money(Math.abs(profit))}`, rate: `毛利率 ${(profit / cents * 100).toFixed(1)}%` };
}
function updatedAt(timestamp: number | null) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚未更新';
}

export function ShopAdminPage() {
  const [state, setState] = useState<Merchant | null>(null);
  const [telegram, setTelegram] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [copy, setCopy] = useState<ProductCopy>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  function accept(value: Merchant) { setState(value); setTelegram(value.telegram); setDraft(draftFor(value)); setCopy(value.products ?? {}); setDirty(false); }
  useEffect(() => {
    let active = true;
    void shopRequest<Merchant>('admin').then(value => { if (active) accept(value); }).catch(reason => { if (active) setError(String(reason.message)); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    if (!dirty) return;
    function unload(event: BeforeUnloadEvent) { event.preventDefault(); }
    window.addEventListener('beforeunload', unload);
    return () => window.removeEventListener('beforeunload', unload);
  }, [dirty]);
  function edit(key: string, change: Partial<Draft[string]>) {
    setDraft(value => ({ ...value, [key]: { price: '', published: false, ...value[key], ...change } }));
    setDirty(true); setNotice('');
  }
  function editCopy(productId: string, field: keyof typeof EMPTY_COPY, value: string) {
    setCopy(old => ({ ...old, [productId]: { ...EMPTY_COPY, ...old[productId], [field]: value } }));
    setDirty(true); setNotice('');
  }
  async function reload() {
    if (dirty && !window.confirm('重新读取会丢弃尚未保存的输入，确定继续吗？')) return;
    setBusy(true); setError(''); setNotice('');
    try { accept(await shopRequest<Merchant>('admin')); } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function refresh() {
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await shopRequest<Merchant>('refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      // Keep the merchant revision and all unsaved values: source refresh is not a save.
      // Default copy for newly discovered products joins the saved baseline, not the edits.
      setState(old => old ? { ...old, products: { ...value.products, ...old.products }, sources: value.sources ?? { gpt: value }, items: value.items, last_success: value.last_success, error: value.error, is_stale: value.is_stale, cooldown_seconds: value.cooldown_seconds, retry_after_seconds: value.retry_after_seconds } : value);
      setDraft(old => { const next = { ...old }; for (const item of value.items) next[item.key] ??= { price: '', published: false }; return next; });
      // Newly discovered products receive effective defaults; existing dirty or
      // explicitly cleared input continues to win over the refreshed response.
      setCopy(old => ({ ...value.products, ...old }));
      setNotice(value.error ? '部分来源暂不可用；售价和未保存输入已保留。' : '来源检查完成；售价和未保存输入已保留。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!state) return;
    setError(''); setNotice('');
    const skus: Record<string, Setting> = {};
    for (const [key, value] of Object.entries(draft)) {
      const price = value.price === '' ? null : priceCents(value.price);
      if ((value.price !== '' && price === null) || (value.published && price === null)) { setError('请逐项填写有效售价（最多两位小数）；没有售价的规格不能上架。'); return; }
      skus[key] = { price_cents: price, published: value.published };
    }
    if (telegram && !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(telegram)) { setError('请输入 5–32 位 Telegram 用户名，不包含 @ 或链接。'); return; }
    const settings = { revision: state.revision, telegram, skus, products: copy };
    const body = JSON.stringify(settings);
    const savedBody = JSON.stringify({ ...settings, revision: state.revision + 1 });
    const encoder = new TextEncoder();
    if (encoder.encode(body).length > 65536 || encoder.encode(savedBody).length > 65536) { setError('商品设置总内容过长，请缩短商品说明或售后条款后保存。'); return; }
    setBusy(true);
    try {
      accept(await shopRequest<Merchant>('admin', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body }));
      setNotice('商品设置已保存。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }

  const items = state?.items ?? [];
  const saved = state?.skus ?? {};
  const groups = new Map<Brand, Map<string, SourceItem[]>>();
  for (const item of items) {
    const products = groups.get(brandOf(item)) ?? new Map<string, SourceItem[]>();
    products.set(item.product_id, [...(products.get(item.product_id) ?? []), item]);
    groups.set(brandOf(item), products);
  }
  const counts = Object.fromEntries(FILTERS.map(([value]) => [value, items.filter(item => matchesFilter(value, item, saved[item.key])).length])) as Record<Filter, number>;
  const needle = query.trim().toLowerCase();
  const found = (...texts: (string | undefined)[]) => !needle || texts.some(text => text?.toLowerCase().includes(needle));
  const visible = (item: SourceItem) => matchesFilter(filter, item, saved[item.key]) && found(item.title, item.public_title, item.label, item.key);
  const changed = (key: string) => {
    const value = draft[key];
    return value !== undefined && (value.published !== (saved[key]?.published ?? false) || draftCents(value.price) !== (saved[key]?.price_cents ?? null));
  };
  const copyChanged = (productId: string) => {
    const current = copy[productId] ?? EMPTY_COPY;
    const base = state?.products?.[productId] ?? EMPTY_COPY;
    return current.description !== base.description || current.after_sales !== base.after_sales;
  };
  const pending = [
    [Object.keys(draft).filter(changed).length, '个规格'],
    [Object.keys({ ...copy, ...state?.products }).filter(copyChanged).length, '个商品的说明'],
  ].filter(([count]) => count).map(([count, unit]) => `${count} ${unit}`);
  if (state && telegram !== state.telegram) pending.push('Telegram 用户名');
  const shownCount = items.filter(visible).length;
  const providerCount = (provider: string) => items.filter(item => (isSecondary(item.product_id) ? 'anli' : 'gpt') === provider).length;
  const feedback = <>
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status" className="shop-admin-feedback">{notice}</p>
  </>;

  return <AdminShell active="shop" pageTitle="商品管理" topbarExtra={<div className="shop-admin-actions"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>刷新来源</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void reload()}>重新读取</button></div>}>
    <section className="shop-admin-panel">
      {state ? <>
        <div className="shop-admin-settings">
          <div className="shop-admin-card shop-admin-source-status">
            <h2 className="shop-admin-card-title">来源状态</h2>
            {Object.entries(state.sources ?? { gpt: state }).map(([provider, source]) => {
              const wait = Math.max(source.retry_after_seconds, source.cooldown_seconds);
              return <p key={provider} className={`shop-admin-source${source.is_stale || source.error ? ' is-warn' : ''}`}>
                <span className="shop-admin-source-name"><strong>{provider === 'anli' ? 'Claude / Grok' : 'GPT'}</strong><small>{providerCount(provider)} 个规格</small></span>
                <span className="shop-admin-source-state">{source.is_stale ? '商品信息待更新，此来源商品暂不能购买' : '商品信息已更新'}{source.error ? ' · 来源暂不可用，稍后自动重试' : ''}{wait > 0 ? ` · 刷新等待 ${wait} 秒` : ''}</span>
                <small className="shop-admin-source-time">最近成功更新：{updatedAt(source.last_success)}</small>
              </p>;
            })}
          </div>
          <div className="shop-admin-card shop-admin-contact">
            <h2 className="shop-admin-card-title">商家联系方式</h2>
            <label className="shop-telegram-field">商家 Telegram 用户名<span className="shop-admin-affix"><span aria-hidden="true">@</span><input value={telegram} disabled={busy} placeholder="不含 @，例如 my_shop" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/></span></label>
            <small className={telegram ? undefined : 'is-warn'}>客户将通过此用户名联系商家；留空时无法联系购买。</small>
          </div>
        </div>
        {items.length ? <div className="shop-admin-toolbar">
          <div className="filter-chips shop-admin-filters" role="group" aria-label="规格筛选">
            {FILTERS.map(([value, label]) => <button key={value} type="button" className={`chip${filter === value ? ' active' : ''}${value === 'unpriced' && counts.unpriced ? ' is-attention' : ''}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>
              {label}{' '}<span className="shop-admin-chip-count">{counts[value]}</span>
            </button>)}
          </div>
          <input type="search" className="user-filter-input shop-admin-search" aria-label="搜索商品或规格" placeholder="搜索商品或规格" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)}/>
        </div> : <p className="shop-admin-empty">尚无来源商品，请刷新来源。获取到的商品会先保存为草稿。</p>}
        {items.length && !shownCount ? <div className="shop-admin-empty"><p>没有符合条件的规格。</p><button type="button" className="btn btn-secondary btn-sm" onClick={() => { setFilter('all'); setQuery(''); }}>清除筛选</button></div> : null}
        <div className="shop-admin-products">{BRANDS.filter(brand => groups.has(brand)).map(brand => {
          const products = [...(groups.get(brand) ?? [])];
          const brandItems = products.flatMap(([, group]) => group);
          return <section key={brand} className="shop-admin-group" aria-labelledby={`shop-admin-group-${brand}`} hidden={!brandItems.some(visible)}>
            <header className="shop-admin-group-header">
              <h2 id={`shop-admin-group-${brand}`}>{brand}</h2>
              <span>{products.length} 个商品 · {brandItems.length} 个规格 · 已上架 {brandItems.filter(item => saved[item.key]?.published).length}</span>
            </header>
            <div className="shop-admin-columns" aria-hidden="true"><span>规格</span><span>成本</span><span>售价（元）</span><span>毛利</span><span>上架</span></div>
            {products.map(([productId, group]) => {
              const first = group[0];
              const published = group.filter(item => saved[item.key]?.published).length;
              const unpriced = group.filter(item => saved[item.key]?.price_cents == null).length;
              const productCopy = copy[productId] ?? EMPTY_COPY;
              const filled = Boolean(productCopy.description.trim() && productCopy.after_sales.trim());
              return <section key={productId} className="shop-admin-product" aria-labelledby={`shop-admin-product-${productId}`} hidden={!group.some(visible)}>
                <header className="shop-admin-product-header">
                  <h3 id={`shop-admin-product-${productId}`}>{first?.title}</h3>
                  <p className="shop-admin-product-meta">
                    {first?.public_title ? <span>前台名称：{first.public_title}</span> : null}
                    <span>{group.length} 个规格</span>
                    <span className={`badge${published ? ' ok' : ''}`}>{published ? `已上架 ${published}/${group.length}` : '未上架'}</span>
                    {unpriced ? <span className="badge warn">待定价 {unpriced}</span> : null}
                  </p>
                </header>
                <details className="shop-copy-editor">
                  <summary>说明与售后<small className={filled ? undefined : 'is-empty'}>{copyChanged(productId) ? '已修改' : filled ? '已填写' : '未填写'}</small></summary>
                  <div className="shop-copy-body">
                    <p className="shop-note">上架后展示给客户，每项最多 4000 字。请填写本店实际提供的说明与条款。</p>
                    {isNoticeProduct(productId) && <p className="shop-note">支持 Markdown 标题、列表和强调。清空并保存后，客户将看到未填写提示。</p>}
                    <div className="shop-copy-fields">{(['description', 'after_sales'] as const).map(field => {
                      const name = field === 'description' ? '商品说明' : '售后条款';
                      return <label key={field}><span className="shop-copy-label">{name}<small>{productCopy[field].length}/4000</small></span><textarea aria-label={`${name} ${productId}`} rows={6} maxLength={4000} disabled={busy} value={productCopy[field]} onChange={event => editCopy(productId, field, event.target.value)}/></label>;
                    })}</div>
                  </div>
                </details>
                {group.map(item => {
                  const value = draft[item.key] ?? { price: '', published: false };
                  const margin = marginOf(item.cost_cents, value.price, value.published);
                  return <article key={item.key} className={`shop-admin-row${changed(item.key) ? ' is-changed' : ''}${item.available ? '' : ' is-soldout'}`} hidden={!visible(item)}>
                    <div className="shop-admin-spec">
                      <strong>{item.label}</strong>
                      {item.available && !changed(item.key) ? null : <small>{item.available ? null : <span className="badge warn">来源售罄</span>}{changed(item.key) ? <span className="badge shop-admin-changed">已修改</span> : null}</small>}
                    </div>
                    <div className="shop-admin-cost"><span className="shop-admin-mobile-label">成本</span><strong>{money(item.cost_cents)}</strong></div>
                    <label className="shop-admin-price"><span className="shop-admin-mobile-label">售价（元）</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`售价 ${item.key}`} aria-invalid={margin.tone === 'invalid' || undefined} inputMode="decimal" autoComplete="off" value={value.price} disabled={busy} placeholder="待填写" onChange={event => edit(item.key, { price: event.target.value })}/></span></label>
                    <div className={`shop-admin-margin is-${margin.tone}`}><span className="shop-admin-mobile-label">毛利</span><strong>{margin.amount}</strong>{margin.rate ? <small>{margin.rate}</small> : null}</div>
                    <label className="shop-publish"><input aria-label={`上架 ${item.key}`} className="shop-admin-switch" type="checkbox" checked={value.published} disabled={busy} onChange={event => edit(item.key, { published: event.target.checked })}/><span>上架</span></label>
                  </article>;
                })}
              </section>;
            })}
          </section>;
        })}</div>
        <div className={`shop-admin-savebar${error ? ' is-error' : dirty ? ' is-dirty' : ''}`}>
          <div className="shop-admin-savebar-text">
            {feedback}
            {error || notice ? null : <span className="shop-note">{dirty ? `有尚未保存的修改${pending.length ? `：${pending.join('、')}` : ''}。` : '设置已与服务器同步。'}</span>}
          </div>
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>{busy ? '处理中…' : '保存商品设置'}</button>
        </div>
      </> : <>{feedback}{error ? null : <p className="shop-admin-empty">正在读取商品管理数据…</p>}</>}
    </section>
  </AdminShell>;
}
