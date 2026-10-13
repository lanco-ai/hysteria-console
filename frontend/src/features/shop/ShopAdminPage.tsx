import { useEffect, useLayoutEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';
import { isNoticeProduct } from './ShopNotice';

// promo_price_cents: the 优惠价 (学生价, 限时价…) under the selling price, for the buyers it applies to.
type Setting = { price_cents: number | null; published: boolean; promo_price_cents?: number | null };
// What the storefront shows with a product: the name of its 优惠价 (a label before the title) and units sold.
type Display = Record<string, { badge: string; sold: number }>;
// buy_url, provider and category come only with the administrator's view: where each item is bought,
// from which feed, and the storefront category it belongs to.
type SourceItem = { key: string; product_id: string; title: string; public_title?: string; label: string; cost_cents: number; available: boolean; buy_url?: string; provider?: string; category?: string };
type ProductCopy = Record<string, { description: string; after_sales: string }>;
// site, supplier and covers come with the administrator's view only; this code is public and names no supplier.
type SourceStatus = { last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number; site?: string; supplier?: string; covers?: string };
type Merchant = { sources?: Record<string, SourceStatus>; products?: ProductCopy; display?: Display; stats?: { site: number; products: Record<string, number> }; revision: number; telegram: string; wechat?: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Draft = Record<string, { price: string; published: boolean; promo: string }>;
type DisplayDraft = Record<string, { badge: string; sold: string }>;
type Brand = 'GPT' | 'Claude' | 'Grok' | 'Gemini' | 'X' | 'Muse AI';
type Filter = 'all' | 'published' | 'unpublished' | 'unpriced' | 'soldout';
// One supplier's product within a storefront plan, and the plan with every supplier offering it.
type SupplierProduct = { productId: string; provider: string; title: string; rows: SourceItem[]; cheapest: SourceItem | undefined; stale: boolean };
type Plan = { title: string; products: SupplierProduct[]; best: SupplierProduct | undefined };

const BRANDS: Brand[] = ['GPT', 'Claude', 'Grok', 'Gemini', 'X', 'Muse AI'];
const FILTERS: [Filter, string][] = [['all', '全部'], ['published', '已上架'], ['unpublished', '未上架'], ['unpriced', '待定价'], ['soldout', '来源售罄']];
const EMPTY_COPY = { description: '', after_sales: '' };
const EMPTY_DRAFT = { price: '', published: false, promo: '' };
const EMPTY_DISPLAY = { badge: '', sold: '' };
const BADGES = ['学生价', '限时价', '推广价', '新人价'];
const CUSTOM_BADGE = '__custom';
const BRAND_KEY = 'hysteria.shop.admin.brand.v1';

// The feed and category come from the server, which knows its feeds; this page does not.
function providerOf(item: SourceItem | undefined) { return item?.provider ?? 'gpt'; }
function brandOf(item: SourceItem): Brand { return BRANDS.find(brand => brand === item.category) ?? 'GPT'; }
/**
 * Storefront plans (one per public title), each with the supplier products that sell it, cheapest
 * first. A feed that is out of date cannot be bought from (its products are unavailable on the
 * storefront), so its last known prices never count as the lowest.
 */
function plansOf(items: SourceItem[], stale: (provider: string) => boolean): Plan[] {
  const plans = new Map<string, Map<string, SupplierProduct>>();
  for (const item of items) {
    const title = item.public_title ?? item.title;
    const products = plans.get(title) ?? new Map<string, SupplierProduct>();
    const provider = providerOf(item);
    const product = products.get(item.product_id) ?? { productId: item.product_id, provider, title: item.title, rows: [], cheapest: undefined, stale: stale(provider) };
    product.rows.push(item);
    if (item.available && !product.stale && (!product.cheapest || item.cost_cents < product.cheapest.cost_cents)) product.cheapest = item;
    products.set(item.product_id, product);
    plans.set(title, products);
  }
  return [...plans].map(([title, products]) => {
    const list = [...products.values()].sort((a, b) => (a.cheapest?.cost_cents ?? Infinity) - (b.cheapest?.cost_cents ?? Infinity));
    return { title, products: list, best: list[0]?.cheapest ? list[0] : undefined };
  }).sort((a, b) => a.title.localeCompare(b.title, 'zh-CN', { numeric: true }));
}
function storedBrand(): Brand | null {
  try { return BRANDS.find(brand => brand === localStorage.getItem(BRAND_KEY)) ?? null; } catch { return null; }
}
function priceText(cents: number | null | undefined) { return cents == null ? '' : money(cents).slice(1); }
function draftFor(value: Merchant): Draft {
  const draft: Draft = {};
  for (const [key, setting] of Object.entries(value.skus)) draft[key] = { price: priceText(setting.price_cents), published: setting.published, promo: priceText(setting.promo_price_cents) };
  for (const item of value.items) draft[item.key] ??= { ...EMPTY_DRAFT };
  return draft;
}
function displayFor(value: Merchant): DisplayDraft {
  return Object.fromEntries(Object.entries(value.display ?? {}).map(([id, item]) => [id, { badge: item.badge, sold: item.sold ? String(item.sold) : '' }]));
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
  return { tone: profit < 0 ? 'loss' : 'gain', amount: `${profit < 0 ? '-' : ''}${money(Math.abs(profit))}`, rate: `${(profit / cents * 100).toFixed(1)}%` };
}
function hostOf(url: string) {
  try { return new URL(url).host; } catch { return url; }
}
function updatedAt(timestamp: number | null) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚未更新';
}

export function ShopAdminPage() {
  const [state, setState] = useState<Merchant | null>(null);
  const [telegram, setTelegram] = useState('');
  const [wechat, setWechat] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [display, setDisplay] = useState<DisplayDraft>({});
  // Products whose label is being written by hand rather than picked from BADGES.
  const [customBadges, setCustomBadges] = useState<Set<string>>(() => new Set());
  // Products whose settings (label, sold count, description) are open.
  const [openProducts, setOpenProducts] = useState<Set<string>>(() => new Set());
  const [copy, setCopy] = useState<ProductCopy>({});
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [brand, setBrand] = useState<Brand | null>(storedBrand);
  function chooseBrand(value: Brand) {
    setBrand(value); setQuery('');
    try { localStorage.setItem(BRAND_KEY, value); } catch { /* Storage unavailable: keep the choice for this visit. */ }
  }
  function accept(value: Merchant) { setState(value); setTelegram(value.telegram); setWechat(value.wechat ?? ''); setDraft(draftFor(value)); setDisplay(displayFor(value)); setCustomBadges(new Set()); setCopy(value.products ?? {}); setDirty(false); }
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
    setDraft(value => ({ ...value, [key]: { ...EMPTY_DRAFT, ...value[key], ...change } }));
    setDirty(true); setNotice('');
  }
  function editDisplay(productId: string, change: Partial<DisplayDraft[string]>) {
    setDisplay(value => ({ ...value, [productId]: { ...EMPTY_DISPLAY, ...value[productId], ...change } }));
    setDirty(true); setNotice('');
  }
  function toggleProduct(productId: string) {
    setOpenProducts(old => { const next = new Set(old); if (next.has(productId)) next.delete(productId); else next.add(productId); return next; });
  }
  // The cheapest supplier of a plan: lift the filter and search that may hide it, then go to it once shown.
  const [revealing, setRevealing] = useState<string | null>(null);
  function reveal(target: Brand, productId: string) {
    setFilter('all'); setQuery(''); chooseBrand(target); setRevealing(productId);
  }
  useLayoutEffect(() => {
    if (!revealing) return;
    const heading = document.getElementById(`shop-admin-product-${revealing}`);
    setRevealing(null);
    if (!heading) return;
    history.replaceState(null, '', `#shop-admin-product-${revealing}`);
    heading.scrollIntoView({ block: 'center' });
    heading.focus({ preventScroll: true });
  }, [revealing]);
  function chooseBadge(productId: string, choice: string) {
    setCustomBadges(old => { const next = new Set(old); if (choice === CUSTOM_BADGE) next.add(productId); else next.delete(productId); return next; });
    editDisplay(productId, { badge: choice === CUSTOM_BADGE ? '' : choice });
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
      setDraft(old => { const next = { ...old }; for (const item of value.items) next[item.key] ??= { ...EMPTY_DRAFT }; return next; });
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
      const promo = value.promo === '' ? null : priceCents(value.promo);
      if (value.promo !== '' && (promo === null || price === null || promo >= price)) { setError('优惠价需低于售价（最多两位小数）；不做优惠可以留空。'); return; }
      skus[key] = { price_cents: price, published: value.published, ...(promo === null ? {} : { promo_price_cents: promo }) };
    }
    const shown: Display = {};
    for (const [productId, value] of Object.entries(display)) {
      const badge = value.badge.trim();
      const sold = value.sold.trim() === '' ? 0 : Number(value.sold.trim());
      if (badge.length > 8) { setError('优惠标签最多 8 个字，例如“学生价”。'); return; }
      if (!Number.isInteger(sold) || sold < 0 || sold > 10_000_000) { setError('已售件数请填写 0 到 10000000 之间的整数。'); return; }
      if (badge || sold) shown[productId] = { badge, sold };
    }
    if (telegram && !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(telegram)) { setError('请输入 5–32 位 Telegram 用户名，不包含 @ 或链接。'); return; }
    if (wechat && !/^[A-Za-z][-_A-Za-z0-9]{5,19}$/.test(wechat)) { setError('请输入 6–20 位微信号：字母开头，可包含字母、数字、下划线和减号。'); return; }
    // `client` tells the server this page sends every setting, so a field left out is cleared.
    const settings = { revision: state.revision, client: 3, telegram, wechat, skus, products: copy, display: shown };
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
  const byBrand = new Map<Brand, SourceItem[]>();
  for (const item of items) byBrand.set(brandOf(item), [...(byBrand.get(brandOf(item)) ?? []), item]);
  const present = BRANDS.filter(name => byBrand.has(name));
  const activeBrand = present.find(name => name === brand) ?? present[0];
  const needle = query.trim().toLowerCase();
  const found = (item: SourceItem) => !needle || [item.title, item.public_title, item.label, item.key].some(text => text?.toLowerCase().includes(needle));
  // Tabs show one brand at a time; a search looks across every brand.
  const inScope = (item: SourceItem) => (needle !== '' || brandOf(item) === activeBrand) && found(item);
  const counts = Object.fromEntries(FILTERS.map(([value]) => [value, items.filter(item => inScope(item) && matchesFilter(value, item, saved[item.key])).length])) as Record<Filter, number>;
  const visible = (item: SourceItem) => inScope(item) && matchesFilter(filter, item, saved[item.key]);
  const brandCount = (name: Brand) => items.filter(item => brandOf(item) === name && found(item) && matchesFilter(filter, item, saved[item.key])).length;
  const changed = (key: string) => {
    const value = draft[key];
    return value !== undefined && (value.published !== (saved[key]?.published ?? false) || draftCents(value.price) !== (saved[key]?.price_cents ?? null)
      || draftCents(value.promo) !== (saved[key]?.promo_price_cents ?? null));
  };
  const displayChanged = (productId: string) => {
    const current = display[productId] ?? EMPTY_DISPLAY;
    const base = state?.display?.[productId];
    return current.badge.trim() !== (base?.badge ?? '') || (current.sold.trim() === '' ? 0 : Number(current.sold.trim())) !== (base?.sold ?? 0);
  };
  const copyChanged = (productId: string) => {
    const current = copy[productId] ?? EMPTY_COPY;
    const base = state?.products?.[productId] ?? EMPTY_COPY;
    return current.description !== base.description || current.after_sales !== base.after_sales;
  };
  const pending = [
    [Object.keys(draft).filter(changed).length, '个规格'],
    [Object.keys({ ...copy, ...state?.products }).filter(copyChanged).length, '个商品的说明'],
    [Object.keys({ ...display, ...state?.display }).filter(displayChanged).length, '个商品的优惠标签或销量'],
  ].filter(([count]) => count).map(([count, unit]) => `${count} ${unit}`);
  if (state && telegram !== state.telegram) pending.push('Telegram 用户名');
  if (state && wechat !== (state.wechat ?? '')) pending.push('微信号');
  const shownCount = items.filter(visible).length;
  const brandDirty = (name: Brand) => items.some(item => brandOf(item) === name && (changed(item.key) || copyChanged(item.product_id) || displayChanged(item.product_id)));
  const providerCount = (provider: string) => items.filter(item => providerOf(item) === provider).length;
  const supplierOf = (provider: string) => state?.sources?.[provider]?.supplier ?? provider;
  const sourceName = (provider: string, source: SourceStatus) => [source.covers, source.supplier].filter(Boolean).join(' · ') || provider;
  const isStale = (provider: string) => state?.sources?.[provider]?.is_stale === true;
  const onSale = new Set(items.filter(item => saved[item.key]?.published).map(item => item.product_id)).size;
  const unpriced = items.filter(item => saved[item.key]?.price_cents == null).length;
  const soldTotal = Object.values(state?.display ?? {}).reduce((sum, item) => sum + item.sold, 0);
  const feedback = <>
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status" className="shop-admin-feedback">{notice}</p>
  </>;

  return <AdminShell active="shop" pageTitle="商品管理" topbarExtra={<div className="shop-admin-actions"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>刷新来源</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void reload()}>重新读取</button></div>}>
    <section className="shop-admin-panel">
      {state ? <>
        <section className="shop-admin-overview" aria-label="店铺概览">
          <dl className="shop-admin-kpis">
            <div><dt>在售商品</dt><dd>{onSale}</dd></div>
            <div className={unpriced ? 'is-attention' : undefined}><dt>待定价规格</dt><dd>{unpriced}</dd></div>
            <div><dt>累计浏览</dt><dd title="按访问 IP 每天计一次，是近似人数：同一网络下的多人会合并，换了网络的人会再计一次。">{(state.stats?.site ?? 0).toLocaleString('zh-CN')}<small>人次</small></dd></div>
            <div><dt>累计售出</dt><dd title="由你在各商品的设置里填写">{soldTotal.toLocaleString('zh-CN')}<small>件</small></dd></div>
          </dl>
          <div className="shop-admin-contact" role="group" aria-label="商家联系方式">
            <label>微信<input aria-label="商家微信号" value={wechat} disabled={busy} placeholder="例如 upc-bh" autoComplete="off" onChange={event => { setWechat(event.target.value.trim()); setDirty(true); setNotice(''); }}/></label>
            <label>Telegram<span className="shop-admin-affix"><span aria-hidden="true">@</span><input aria-label="商家 Telegram 用户名" value={telegram} disabled={busy} placeholder="用户名" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/></span></label>
            <small className={telegram || wechat ? undefined : 'is-warn'}>{telegram || wechat ? '顾客通过这里的联系方式找你下单。' : '微信和 Telegram 都没填时，前台无法联系购买。'}</small>
          </div>
          <div className="shop-admin-sources" role="group" aria-label="来源状态">
            {Object.entries(state.sources ?? { gpt: state }).map(([provider, source]) => {
              const warn = source.is_stale || Boolean(source.error);
              const wait = Math.max(source.retry_after_seconds, source.cooldown_seconds);
              const status = `${source.is_stale ? '商品信息待更新，此来源商品暂不能购买' : '商品信息已更新'}${source.error ? ' · 来源暂不可用，稍后自动重试' : ''}${wait > 0 ? ` · 刷新等待 ${wait} 秒` : ''} · 最近成功更新 ${updatedAt(source.last_success)}`;
              const body = <><span className="shop-source-dot" aria-hidden="true"/>{sourceName(provider, source)}<b>{providerCount(provider)}</b>{warn ? <small>{source.error ? '暂不可用' : '待更新'}</small> : null}</>;
              return source.site
                ? <a key={provider} className={`shop-source-chip${warn ? ' is-warn' : ''}`} href={source.site} target="_blank" rel="noopener noreferrer" title={status} aria-label={`${sourceName(provider, source)}：${status}，打开来源网站 ${hostOf(source.site)}`}>{body}</a>
                : <span key={provider} className={`shop-source-chip${warn ? ' is-warn' : ''}`} title={status}>{body}</span>;
            })}
          </div>
        </section>
        <div className="shop-admin-toolbar">
          {present.length > 1 ? <div className="shop-admin-brands" role="group" aria-label="品牌">
            {present.map(name => {
              const active = !needle && name === activeBrand;
              return <button key={name} type="button" className={`shop-admin-brand${active ? ' active' : ''}`} aria-pressed={active} onClick={() => chooseBrand(name)}>
                {name}{' '}<span className="shop-admin-brand-count">{brandCount(name)}</span>
                {brandDirty(name) ? <span className="shop-admin-brand-dirty"><span className="sr-only">有未保存的修改</span></span> : null}
              </button>;
            })}
          </div> : null}
          {items.length ? <div className="shop-admin-toolbar-row">
            <div className="filter-chips shop-admin-filters" role="group" aria-label="规格筛选">
              {FILTERS.map(([value, label]) => <button key={value} type="button" className={`chip${filter === value ? ' active' : ''}${value === 'unpriced' && counts.unpriced ? ' is-attention' : ''}`} aria-pressed={filter === value} onClick={() => setFilter(value)}>
                {label}{' '}<span className="shop-admin-chip-count">{counts[value]}</span>
              </button>)}
            </div>
            <input type="search" className="user-filter-input shop-admin-search" aria-label="搜索商品或规格" placeholder="搜索商品或规格" autoComplete="off" value={query} onChange={event => setQuery(event.target.value)}/>
          </div> : null}
        </div>
        {!items.length ? <p className="shop-admin-empty">尚无来源商品，请刷新来源。获取到的商品会先保存为草稿。</p> : null}
        {items.length && !shownCount ? <div className="shop-admin-empty"><p>没有符合条件的规格。</p><button type="button" className="btn btn-secondary btn-sm" onClick={() => { setFilter('all'); setQuery(''); }}>清除筛选</button></div> : null}
        <div className="shop-admin-products">{BRANDS.filter(name => byBrand.has(name)).map(brandName => {
          const brandItems = byBrand.get(brandName) ?? [];
          const brandId = brandName.replace(/\s+/g, '-');
          const plans = plansOf(brandItems, isStale);
          return <section key={brandName} className="shop-admin-group" aria-labelledby={`shop-admin-group-${brandId}`} hidden={!brandItems.some(visible)}>
            <header className="shop-admin-group-header">
              <h2 id={`shop-admin-group-${brandId}`}>{brandName}</h2>
              <span>{plans.length} 个前台商品 · {brandItems.length} 个规格 · 已上架 {brandItems.filter(item => saved[item.key]?.published).length}</span>
            </header>
            {plans.map(plan => {
              const planItems = plan.products.flatMap(product => product.rows);
              const publishedRows = planItems.filter(item => saved[item.key]?.published && saved[item.key]?.price_cents != null);
              const prices = publishedRows.map(item => saved[item.key]!.price_cents!);
              const sellers = new Set(publishedRows.map(item => providerOf(item))).size;
              const comparable = new Set(plan.products.map(product => product.provider)).size > 1;
              return <article key={plan.title} className="shop-plan" aria-label={plan.title} hidden={!planItems.some(visible)}>
                <header className="shop-plan-head">
                  <div className="shop-plan-title"><h3>{plan.title}</h3>
                    {prices.length ? <span className="badge ok">前台 {money(Math.min(...prices))}{Math.max(...prices) !== Math.min(...prices) ? `–${money(Math.max(...prices)).slice(1)}` : ''}</span> : <span className="badge">未上架</span>}
                  </div>
                  {comparable ? plan.best ? <button type="button" className="shop-plan-best" title="同款按套餐名归类，上架前请核对来源的商品说明" onClick={() => reveal(brandName, plan.best!.productId)}>最低成本 {money(plan.best.cheapest!.cost_cents)} · {supplierOf(plan.best.provider)}</button> : <span className="shop-plan-note">暂无可购来源</span> : null}
                  {sellers > 1 ? <span className="shop-plan-warn">{sellers} 个来源同时上架，前台会出现重复商品</span> : null}
                </header>
                {plan.products.map(product => {
                  const productId = product.productId;
                  const first = product.rows[0];
                  const productCopy = copy[productId] ?? EMPTY_COPY;
                  const filled = Boolean(productCopy.description.trim() && productCopy.after_sales.trim());
                  const shown = display[productId] ?? EMPTY_DISPLAY;
                  const custom = customBadges.has(productId) || Boolean(shown.badge && !BADGES.includes(shown.badge));
                  const open = openProducts.has(productId);
                  const published = product.rows.some(item => saved[item.key]?.published);
                  const views = state.stats?.products[productId] ?? 0;
                  const settingsChanged = displayChanged(productId) || copyChanged(productId);
                  return <section key={productId} className={`shop-admin-product${open ? ' is-open' : ''}`} aria-labelledby={`shop-admin-product-${productId}`} hidden={!product.rows.some(visible)}>
                    <header className="shop-admin-product-header">
                      <div className="shop-admin-source-heading">
                        <span className={`shop-admin-supplier${product.stale ? ' is-stale' : ''}`}>{supplierOf(product.provider)}</span>
                        {comparable && plan.best?.productId === productId ? <span className="badge ok">最低</span> : null}
                        {product.stale ? <span className="badge warn">价格待更新</span> : null}
                      </div>
                      <h4 id={`shop-admin-product-${productId}`} tabIndex={-1} title={product.title}>{product.title}</h4>
                      <span className="shop-admin-product-flags">
                        {shown.badge.trim() ? <span className="shop-promo-badge">{shown.badge.trim()}</span> : null}
                        {Number(shown.sold) ? <span>已售 {Number(shown.sold).toLocaleString('zh-CN')}</span> : null}
                        {views ? <span>浏览 {views.toLocaleString('zh-CN')}</span> : null}
                        {published && !filled ? <span className="is-warn">缺说明</span> : null}
                      </span>
                      <div className="shop-admin-product-actions">
                        <button type="button" className={`shop-admin-product-toggle${settingsChanged ? ' is-changed' : ''}`} aria-expanded={open} aria-controls={`shop-admin-settings-${productId}`} aria-label={`商品设置 ${productId}`} onClick={() => toggleProduct(productId)}>设置{settingsChanged ? ' · 已修改' : ''}<span aria-hidden="true">{open ? ' −' : ' +'}</span></button>
                        {first?.buy_url ? <a className="shop-admin-buy" href={first.buy_url} target="_blank" rel="noopener noreferrer" aria-label={`去来源购买 ${product.title}`}>去进货<span aria-hidden="true"> ↗</span></a> : null}
                      </div>
                    </header>
                    <div className="shop-admin-variants">
                      <div className="shop-admin-columns" aria-hidden="true"><span>规格</span><span>成本</span><span>售价（元）</span><span>{shown.badge.trim() || '优惠价'}（元）</span><span>毛利 / 毛利率</span><span>上架</span></div>
                      {[...product.rows].sort((a, b) => a.cost_cents - b.cost_cents).map(item => {
                        const value = draft[item.key] ?? EMPTY_DRAFT;
                        const margin = marginOf(item.cost_cents, value.price, value.published);
                        const promoMargin = value.promo ? marginOf(item.cost_cents, value.promo, false) : null;
                        return <article key={item.key} className={`shop-admin-row${changed(item.key) ? ' is-changed' : ''}${item.available ? '' : ' is-soldout'}`} hidden={!visible(item)}>
                          <div className="shop-admin-spec">
                            <strong>{item.label}</strong>
                            {item.available && !changed(item.key) ? null : <small>{item.available ? null : <span className="badge warn">来源售罄</span>}{changed(item.key) ? <span className="badge shop-admin-changed">已修改</span> : null}</small>}
                          </div>
                          <div className="shop-admin-cost"><span className="shop-admin-mobile-label">成本</span><strong>{money(item.cost_cents)}</strong></div>
                          <label className="shop-admin-price"><span className="shop-admin-mobile-label">售价</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`售价 ${item.key}`} aria-invalid={margin.tone === 'invalid' || undefined} inputMode="decimal" autoComplete="off" value={value.price} disabled={busy} placeholder="待填写" onChange={event => edit(item.key, { price: event.target.value })}/></span></label>
                          <label className="shop-admin-promo"><span className="shop-admin-mobile-label">{shown.badge.trim() || '优惠价'}</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`优惠价 ${item.key}`} inputMode="decimal" autoComplete="off" value={value.promo} disabled={busy} placeholder="不优惠" onChange={event => edit(item.key, { promo: event.target.value })}/></span></label>
                          <div className={`shop-admin-margin is-${margin.tone}`}>
                            <span className="shop-admin-mobile-label">毛利</span>
                            <div className="shop-admin-margin-main"><strong>{margin.amount}</strong>{margin.rate ? <span>{margin.rate}</span> : null}</div>
                            {margin.rate && promoMargin && promoMargin.tone !== 'invalid' && promoMargin.tone !== 'empty' ? <small className={promoMargin.tone === 'loss' ? 'is-loss' : undefined}>优惠 {promoMargin.amount}</small> : null}
                          </div>
                          <label className="shop-publish"><input aria-label={`上架 ${item.key}`} className="shop-admin-switch" type="checkbox" checked={value.published} disabled={busy} onChange={event => edit(item.key, { published: event.target.checked })}/><span>上架</span></label>
                        </article>;
                      })}
                    </div>
                    {open ? <div className="shop-admin-product-settings" id={`shop-admin-settings-${productId}`}>
                      <div className="shop-admin-display">
                        <label>优惠标签<select aria-label={`优惠标签 ${productId}`} value={custom ? CUSTOM_BADGE : shown.badge} disabled={busy} onChange={event => chooseBadge(productId, event.target.value)}>
                          <option value="">不显示</option>{BADGES.map(name => <option key={name} value={name}>{name}</option>)}<option value={CUSTOM_BADGE}>自定义…</option>
                        </select></label>
                        {custom ? <input className="shop-admin-display-text" aria-label={`自定义标签 ${productId}`} maxLength={8} placeholder="最多 8 个字" value={shown.badge} disabled={busy} onChange={event => editDisplay(productId, { badge: event.target.value })}/> : null}
                        <label>已售<input className="shop-admin-display-sold" aria-label={`已售件数 ${productId}`} inputMode="numeric" placeholder="0" value={shown.sold} disabled={busy} onChange={event => editDisplay(productId, { sold: event.target.value.trim() })}/>件</label>
                        <p className="shop-note">标签显示在前台标题前，并作为优惠价的名称，例如「学生价 ¥118.00」。优惠价在各规格行里填写。</p>
                      </div>
                      {isNoticeProduct(productId) && <p className="shop-note">商品说明支持 Markdown 标题、列表和强调。清空并保存后，客户将看到未填写提示。</p>}
                      <div className="shop-copy-fields">{(['description', 'after_sales'] as const).map(field => {
                        const name = field === 'description' ? '商品说明' : '售后条款';
                        return <label key={field}><span className="shop-copy-label">{name}<small>{productCopy[field].length}/4000</small></span><textarea aria-label={`${name} ${productId}`} rows={5} maxLength={4000} disabled={busy} value={productCopy[field]} onChange={event => editCopy(productId, field, event.target.value)}/></label>;
                      })}</div>
                    </div> : null}
                  </section>;
                })}
              </article>;
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
