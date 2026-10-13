import { useEffect, useLayoutEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';
import { isNoticeProduct } from './ShopNotice';

// list_price_cents: 原价, shown struck through beside the selling price on the storefront.
type Setting = { price_cents: number | null; published: boolean; list_price_cents?: number | null };
// What the storefront shows with a product: a label before its title (学生价, 推广价…) and units sold.
type Display = Record<string, { badge: string; sold: number }>;
// buy_url, provider and category come only with the administrator's view: where each item is bought,
// from which feed, and the storefront category it belongs to.
type SourceItem = { key: string; product_id: string; title: string; public_title?: string; label: string; cost_cents: number; available: boolean; buy_url?: string; provider?: string; category?: string };
type ProductCopy = Record<string, { description: string; after_sales: string }>;
// site, supplier and covers come with the administrator's view only; this code is public and names no supplier.
type SourceStatus = { last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number; site?: string; supplier?: string; covers?: string };
type Merchant = { sources?: Record<string, SourceStatus>; products?: ProductCopy; display?: Display; stats?: { site: number; products: Record<string, number> }; revision: number; telegram: string; wechat?: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Draft = Record<string, { price: string; published: boolean; listPrice: string }>;
type DisplayDraft = Record<string, { badge: string; sold: string }>;
type Brand = 'GPT' | 'Claude' | 'Grok' | 'Gemini' | 'X' | 'Muse AI';
type Filter = 'all' | 'published' | 'unpublished' | 'unpriced' | 'soldout';

const BRANDS: Brand[] = ['GPT', 'Claude', 'Grok', 'Gemini', 'X', 'Muse AI'];
const FILTERS: [Filter, string][] = [['all', '全部'], ['published', '已上架'], ['unpublished', '未上架'], ['unpriced', '待定价'], ['soldout', '来源售罄']];
const EMPTY_COPY = { description: '', after_sales: '' };
const EMPTY_DRAFT = { price: '', published: false, listPrice: '' };
const EMPTY_DISPLAY = { badge: '', sold: '' };
const BADGES = ['学生价', '推广价', '限时价', '新人价'];
const CUSTOM_BADGE = '__custom';
const BRAND_KEY = 'hysteria.shop.admin.brand.v1';

// The feed and category come from the server, which knows its feeds; this page does not.
function providerOf(item: SourceItem | undefined) { return item?.provider ?? 'gpt'; }
function brandOf(item: SourceItem): Brand { return BRANDS.find(brand => brand === item.category) ?? 'GPT'; }
type Offer = { provider: string; productId: string; cheapest: SourceItem | undefined; variants: number; published: boolean; stale: boolean };
/**
 * One plan per public title, with each supplier's cheapest purchasable variant. A feed that is out
 * of date cannot be bought from (its products are unavailable on the storefront), so its last
 * known prices never count as the lowest.
 */
function comparePlans(items: SourceItem[], saved: Record<string, Setting>, stale: (provider: string) => boolean) {
  const plans = new Map<string, Map<string, Offer>>();
  for (const item of items) {
    const title = item.public_title ?? item.title;
    const offers = plans.get(title) ?? new Map<string, Offer>();
    const provider = providerOf(item);
    const offer = offers.get(item.product_id) ?? { provider, productId: item.product_id, cheapest: undefined, variants: 0, published: false, stale: stale(provider) };
    offer.variants += 1;
    offer.published ||= saved[item.key]?.published === true;
    if (item.available && !offer.stale && (!offer.cheapest || item.cost_cents < offer.cheapest.cost_cents)) offer.cheapest = item;
    offers.set(item.product_id, offer);
    plans.set(title, offers);
  }
  return [...plans].map(([title, offers]) => {
    const list = [...offers.values()].sort((a, b) => (a.cheapest?.cost_cents ?? Infinity) - (b.cheapest?.cost_cents ?? Infinity));
    return { title, offers: list, best: list[0]?.cheapest ? list[0].productId : null, publishedSources: new Set(list.filter(offer => offer.published).map(offer => offer.provider)).size };
  });
}
function storedBrand(): Brand | null {
  try { return BRANDS.find(brand => brand === localStorage.getItem(BRAND_KEY)) ?? null; } catch { return null; }
}
function priceText(cents: number | null | undefined) { return cents == null ? '' : money(cents).slice(1); }
function draftFor(value: Merchant): Draft {
  const draft: Draft = {};
  for (const [key, setting] of Object.entries(value.skus)) draft[key] = { price: priceText(setting.price_cents), published: setting.published, listPrice: priceText(setting.list_price_cents) };
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
  return { tone: profit < 0 ? 'loss' : 'gain', amount: `${profit < 0 ? '-' : ''}${money(Math.abs(profit))}`, rate: `毛利率 ${(profit / cents * 100).toFixed(1)}%` };
}
function hostOf(url: string) {
  try { return new URL(url).host; } catch { return url; }
}
function updatedAt(timestamp: number | null) {
  return timestamp ? new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚未更新';
}

export function ShopAdminPage() {
  const [state, setState] = useState<Merchant | null>(null);
  const [telegram, setTelegram] = useState('');
  const [wechat, setWechat] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [display, setDisplay] = useState<DisplayDraft>({});
  // Products whose label is being written by hand rather than picked from BADGES.
  const [customBadges, setCustomBadges] = useState<Set<string>>(() => new Set());
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
  // A product chosen in 同款比价: lift the filter and search that may hide it, then go to it once shown.
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
    heading.scrollIntoView({ block: 'start' });
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
      const listed = value.listPrice === '' ? null : priceCents(value.listPrice);
      if (value.listPrice !== '' && (listed === null || price === null || listed <= price)) { setError('原价需高于售价（最多两位小数）；不想显示原价可以留空。'); return; }
      skus[key] = { price_cents: price, published: value.published, ...(listed === null ? {} : { list_price_cents: listed }) };
    }
    const shown: Display = {};
    for (const [productId, value] of Object.entries(display)) {
      const badge = value.badge.trim();
      const sold = value.sold.trim() === '' ? 0 : Number(value.sold.trim());
      if (badge.length > 8) { setError('价格标签最多 8 个字，例如“学生价”。'); return; }
      if (!Number.isInteger(sold) || sold < 0 || sold > 10_000_000) { setError('已售件数请填写 0 到 10000000 之间的整数。'); return; }
      if (badge || sold) shown[productId] = { badge, sold };
    }
    if (telegram && !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(telegram)) { setError('请输入 5–32 位 Telegram 用户名，不包含 @ 或链接。'); return; }
    if (wechat && !/^[A-Za-z][-_A-Za-z0-9]{5,19}$/.test(wechat)) { setError('请输入 6–20 位微信号：字母开头，可包含字母、数字、下划线和减号。'); return; }
    // `client` tells the server this page sends every setting, so a field left out is cleared.
    const settings = { revision: state.revision, client: 2, telegram, wechat, skus, products: copy, display: shown };
    const body = JSON.stringify(settings);
    const savedBody = JSON.stringify({ ...settings, revision: state.revision + 1 });
    const encoder = new TextEncoder();
    if (encoder.encode(body).length > 65536 || encoder.encode(savedBody).length > 65536) { setError('商品设置总内容过长，请缩短商品说明或售后条款后保存。'); return; }
    // Labels are plain words; the server also refuses links and contacts in them.
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
  const present = BRANDS.filter(name => groups.has(name));
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
      || draftCents(value.listPrice) !== (saved[key]?.list_price_cents ?? null));
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
    [Object.keys({ ...display, ...state?.display }).filter(displayChanged).length, '个商品的标签或销量'],
  ].filter(([count]) => count).map(([count, unit]) => `${count} ${unit}`);
  if (state && telegram !== state.telegram) pending.push('Telegram 用户名');
  if (state && wechat !== (state.wechat ?? '')) pending.push('微信号');
  const shownCount = items.filter(visible).length;
  const brandDirty = (name: Brand) => items.some(item => brandOf(item) === name && (changed(item.key) || copyChanged(item.product_id) || displayChanged(item.product_id)));
  const providerCount = (provider: string) => items.filter(item => providerOf(item) === provider).length;
  const supplierOf = (provider: string) => state?.sources?.[provider]?.supplier ?? provider;
  const sourceName = (provider: string, source: SourceStatus) => [source.covers, source.supplier].filter(Boolean).join(' · ') || provider;
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
                <span className="shop-admin-source-name"><strong>{sourceName(provider, source)}</strong><small>{providerCount(provider)} 个规格</small></span>
                <span className="shop-admin-source-state">{source.is_stale ? '商品信息待更新，此来源商品暂不能购买' : '商品信息已更新'}{source.error ? ' · 来源暂不可用，稍后自动重试' : ''}{wait > 0 ? ` · 刷新等待 ${wait} 秒` : ''}</span>
                <small className="shop-admin-source-time">最近成功更新：{updatedAt(source.last_success)}</small>
                {source.site ? <a className="shop-admin-source-site" href={source.site} target="_blank" rel="noopener noreferrer" aria-label={`打开 ${sourceName(provider, source)} 的来源网站 ${hostOf(source.site)}`}>{hostOf(source.site)}<span aria-hidden="true"> ↗</span></a> : null}
              </p>;
            })}
          </div>
          <div className="shop-admin-card shop-admin-contact">
            <h2 className="shop-admin-card-title">商家联系方式</h2>
            <label className="shop-telegram-field">商家 Telegram 用户名<span className="shop-admin-affix"><span aria-hidden="true">@</span><input value={telegram} disabled={busy} placeholder="不含 @，例如 my_shop" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/></span></label>
            <label className="shop-telegram-field">商家微信号<span className="shop-admin-affix"><input value={wechat} disabled={busy} placeholder="例如 upc-bh" autoComplete="off" onChange={event => { setWechat(event.target.value.trim()); setDirty(true); setNotice(''); }}/></span></label>
            <small className={telegram || wechat ? undefined : 'is-warn'}>客户可以通过 Telegram 或微信联系你购买；两者都留空时，前台无法联系购买。</small>
            <p className="shop-admin-stats">前台数据：累计浏览 {(state.stats?.site ?? 0).toLocaleString('zh-CN')} 人次 · 累计售出 {Object.values(state.display ?? {}).reduce((sum, item) => sum + item.sold, 0).toLocaleString('zh-CN')} 件<small>浏览按访问 IP 每天计一次，是近似人数：同一网络下的多人会合并，换了网络的人会再计一次。已售件数由你在各商品处填写。</small></p>
          </div>
        </div>
        {present.length > 1 ? <div className="shop-admin-brands" role="group" aria-label="品牌">
          {present.map(name => {
            const active = !needle && name === activeBrand;
            return <button key={name} type="button" className={`shop-admin-brand${active ? ' active' : ''}`} aria-pressed={active} onClick={() => chooseBrand(name)}>
              {name}{' '}<span className="shop-admin-brand-count">{brandCount(name)}</span>
              {brandDirty(name) ? <span className="shop-admin-brand-dirty"><span className="sr-only">有未保存的修改</span></span> : null}
            </button>;
          })}
        </div> : null}
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
          const brandId = brand.replace(/\s+/g, '-');
          const plans = comparePlans(brandItems, saved, provider => state.sources?.[provider]?.is_stale === true);
          // Comparing only helps once a plan is offered by more than one supplier.
          const compared = plans.filter(plan => plan.offers.length > 1 && new Set(plan.offers.map(offer => offer.provider)).size > 1);
          return <section key={brand} className="shop-admin-group" aria-labelledby={`shop-admin-group-${brandId}`} hidden={!brandItems.some(visible)}>
            <header className="shop-admin-group-header">
              <h2 id={`shop-admin-group-${brandId}`}>{brand}</h2>
              <span>{products.length} 个商品 · {brandItems.length} 个规格 · 已上架 {brandItems.filter(item => saved[item.key]?.published).length}</span>
            </header>
            {compared.length ? <div className="shop-admin-compare" role="region" aria-label={`${brand} 同款比价`}>
              <h3>同款比价<small>按来源的最低可购成本排序，选便宜的那个上架。同款按套餐名归类，上架前请核对来源的商品说明。</small></h3>
              {compared.map(plan => <div key={plan.title} className="shop-admin-compare-plan">
                <strong>{plan.title}</strong>
                <ol>{plan.offers.map(offer => <li key={offer.productId} className={offer.productId === plan.best ? 'is-best' : offer.stale ? 'is-stale' : offer.cheapest ? undefined : 'is-soldout'}>
                  <a href={`#shop-admin-product-${offer.productId}`} title={offer.cheapest?.title ?? undefined} onClick={event => { event.preventDefault(); reveal(brand, offer.productId); }}>{supplierOf(offer.provider)}</a>
                  <span className="shop-admin-compare-cost">{offer.stale ? '价格待更新' : offer.cheapest ? money(offer.cheapest.cost_cents) : '售罄'}</span>
                  {offer.cheapest ? <small>{offer.cheapest.label}{offer.variants > 1 ? ` · 共 ${offer.variants} 个规格` : ''}</small> : null}
                  {offer.productId === plan.best ? <span className="badge ok">最低</span> : null}
                  {offer.published ? <span className="badge">已上架</span> : null}
                </li>)}</ol>
                {plan.publishedSources > 1 ? <p className="shop-admin-compare-warn">多个来源同时上架了 {plan.title}，前台会出现重复商品，建议只保留一个。</p> : null}
              </div>)}
            </div> : null}
            <div className="shop-admin-columns" aria-hidden="true"><span>规格</span><span>成本</span><span>售价（元）</span><span>原价（元）</span><span>毛利</span><span>上架</span></div>
            {products.map(([productId, group]) => {
              const first = group[0];
              const published = group.filter(item => saved[item.key]?.published).length;
              const unpriced = group.filter(item => saved[item.key]?.price_cents == null).length;
              const productCopy = copy[productId] ?? EMPTY_COPY;
              const filled = Boolean(productCopy.description.trim() && productCopy.after_sales.trim());
              const shown = display[productId] ?? EMPTY_DISPLAY;
              const custom = customBadges.has(productId) || Boolean(shown.badge && !BADGES.includes(shown.badge));
              return <section key={productId} className="shop-admin-product" aria-labelledby={`shop-admin-product-${productId}`} hidden={!group.some(visible)}>
                <header className="shop-admin-product-header">
                  <h3 id={`shop-admin-product-${productId}`} tabIndex={-1}>{first?.title}</h3>
                  <p className="shop-admin-product-meta">
                    <span className="shop-admin-supplier">来源：{supplierOf(providerOf(first))}</span>
                    {first?.public_title ? <span>前台名称：{first.public_title}</span> : null}
                    <span>{group.length} 个规格</span>
                    {state.stats?.products[productId] ? <span>浏览 {state.stats.products[productId].toLocaleString('zh-CN')}</span> : null}
                    <span className={`badge${published ? ' ok' : ''}`}>{published ? `已上架 ${published}/${group.length}` : '未上架'}</span>
                    {unpriced ? <span className="badge warn">待定价 {unpriced}</span> : null}
                    {first?.buy_url ? <a className="shop-admin-buy" href={first.buy_url} target="_blank" rel="noopener noreferrer" aria-label={`去来源购买 ${first.title}`}>去来源购买<span aria-hidden="true"> ↗</span></a> : null}
                  </p>
                  <div className={`shop-admin-display${displayChanged(productId) ? ' is-changed' : ''}`}>
                    <label>价格标签<select aria-label={`价格标签 ${productId}`} value={custom ? CUSTOM_BADGE : shown.badge} disabled={busy} onChange={event => chooseBadge(productId, event.target.value)}>
                      <option value="">不显示</option>{BADGES.map(name => <option key={name} value={name}>{name}</option>)}<option value={CUSTOM_BADGE}>自定义…</option>
                    </select></label>
                    {custom ? <input className="shop-admin-display-text" aria-label={`自定义标签 ${productId}`} maxLength={8} placeholder="最多 8 个字" value={shown.badge} disabled={busy} onChange={event => editDisplay(productId, { badge: event.target.value })}/> : null}
                    {shown.badge.trim() ? <span className="shop-promo-badge" aria-hidden="true">{shown.badge.trim()}</span> : null}
                    <label>已售<input className="shop-admin-display-sold" aria-label={`已售件数 ${productId}`} inputMode="numeric" placeholder="0" value={shown.sold} disabled={busy} onChange={event => editDisplay(productId, { sold: event.target.value.trim() })}/>件</label>
                  </div>
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
                  const value = draft[item.key] ?? EMPTY_DRAFT;
                  const margin = marginOf(item.cost_cents, value.price, value.published);
                  return <article key={item.key} className={`shop-admin-row${changed(item.key) ? ' is-changed' : ''}${item.available ? '' : ' is-soldout'}`} hidden={!visible(item)}>
                    <div className="shop-admin-spec">
                      <strong>{item.label}</strong>
                      {item.available && !changed(item.key) ? null : <small>{item.available ? null : <span className="badge warn">来源售罄</span>}{changed(item.key) ? <span className="badge shop-admin-changed">已修改</span> : null}</small>}
                    </div>
                    <div className="shop-admin-cost"><span className="shop-admin-mobile-label">成本</span><strong>{money(item.cost_cents)}</strong></div>
                    <label className="shop-admin-price"><span className="shop-admin-mobile-label">售价（元）</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`售价 ${item.key}`} aria-invalid={margin.tone === 'invalid' || undefined} inputMode="decimal" autoComplete="off" value={value.price} disabled={busy} placeholder="待填写" onChange={event => edit(item.key, { price: event.target.value })}/></span></label>
                    <label className="shop-admin-list-price"><span className="shop-admin-mobile-label">原价（元）</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`原价 ${item.key}`} inputMode="decimal" autoComplete="off" value={value.listPrice} disabled={busy} placeholder="可不填" onChange={event => edit(item.key, { listPrice: event.target.value })}/></span></label>
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
