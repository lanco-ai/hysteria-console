import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import { AdminShell } from '../../shared/AdminShell';
import { money, priceCents, shopRequest } from './catalog';
import { isNoticeProduct } from './ShopNotice';
import { ShopProcurement } from './ShopProcurement';
import { ShopInventory, useInventorySession, type StockedProduct } from './ShopInventory';

// promo_price_cents: the 优惠价 (学生价, 限时价…) under the selling price, for the buyers it applies to.
type Setting = { price_cents: number | null; published: boolean; promo_price_cents?: number | null; pricing?: { mode: 'cost_plus'; profit_cents: number } | null };
// What the storefront shows with a product: the name of its 优惠价, units sold, and how long it is marked 新品.
type Display = Record<string, { badge: string; sold: number; new_until?: number }>;
// The storefront announcement: the merchant's note and the unlisted products buyers can ask for.
type Announcement = { text: string; items: string[] };
type LocalNames = { products: Record<string, string>; variants: Record<string, string> };
// buy_url, provider and category come only with the administrator's view: where each item is bought,
// from which feed, and the storefront category it belongs to.
type SourceItem = { key: string; product_id: string; title: string; public_title?: string; label: string; public_label?: string; cost_cents: number; available: boolean; archived?: boolean; buy_url?: string; provider?: string; category?: string };
type ProductCopy = Record<string, { description: string; after_sales: string }>;
// site, supplier and covers come with the administrator's view only; this code is public and names no supplier.
type SourceStatus = { last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number; enabled?: boolean; can_read?: boolean; site?: string; supplier?: string; covers?: string };
type RemovedSource = Pick<SourceStatus, 'supplier' | 'covers' | 'site' | 'can_read'> & { can_add: boolean };
type Merchant = { inventory_items?: SourceItem[]; inventory?: Record<string, number>; removed_sources?: Record<string, RemovedSource>; source_enabled?: Record<string, boolean>; sources?: Record<string, SourceStatus>; products?: ProductCopy; display?: Display; announcement?: Announcement; automatic_sold?: Record<string, number>; local_names?: Partial<LocalNames>; stats?: { site: number; products: Record<string, number> }; revision: number; telegram: string; wechat?: string; skus: Record<string, Setting>; items: SourceItem[]; last_success: number | null; error: string | null; is_stale: boolean; cooldown_seconds: number; retry_after_seconds: number };
type Draft = Record<string, { price: string; published: boolean; promo: string; automatic: boolean; profit: string }>;
type DisplayDraft = Record<string, { badge: string; sold: string; newUntil: number }>;
type Brand = 'GPT' | 'Claude' | 'Grok' | 'Gemini' | 'X' | 'Muse AI' | '多邻国' | '短信接码' | 'VPN';
type Filter = 'all' | 'published' | 'unpublished' | 'unpriced' | 'soldout';
// One supplier's product within a storefront plan, and the plan with every supplier offering it.
type SupplierProduct = { productId: string; provider: string; title: string; rows: SourceItem[]; cheapest: SourceItem | undefined; stale: boolean; enabled: boolean };
type Plan = { key: string; title: string; products: SupplierProduct[]; best: SupplierProduct | undefined };

const BRANDS: Brand[] = ['GPT', 'Claude', 'Grok', 'Gemini', 'X', 'Muse AI', '多邻国', '短信接码', 'VPN'];
const FILTERS: [Filter, string][] = [['all', '全部'], ['published', '已上架'], ['unpublished', '未上架'], ['unpriced', '待定价'], ['soldout', '来源售罄']];
const EMPTY_COPY = { description: '', after_sales: '' };
const EMPTY_DRAFT = { price: '', published: false, promo: '', automatic: false, profit: '5.00' };
const DYNAMIC_PRICING_MINIMUMS: Record<string, number> = { '2000000000001:1': 500, '2000000000003:1': 1000 };
const EMPTY_DISPLAY = { badge: '', sold: '', newUntil: 0 };
const EMPTY_ANNOUNCEMENT: Announcement = { text: '', items: [] };
// How long a product stays 新品 when marked.
const NEW_DAYS = [7, 14, 30];
// A product name in the announcement: plain words, never a link (the server checks the same).
const ANNOUNCEMENT_ITEM = /^[0-9A-Za-z\u4e00-\u9fff][0-9A-Za-z\u4e00-\u9fff .+\-/&·]{0,23}$/;
const BADGES = ['学生价', '限时价', '推广价', '新人价'];
const CUSTOM_BADGE = '__custom';
const BRAND_KEY = 'hysteria.shop.admin.brand.v1';
// Brands the merchant also stocks with own accounts, links and SMS codes (本店库存).
const STOCKED = new Set<Brand>(['Gemini', 'Muse AI', '短信接码']);

// The feed and category come from the server, which knows its feeds; this page does not.
function providerOf(item: SourceItem | undefined) { return item?.provider ?? 'gpt'; }
function brandOf(item: SourceItem): Brand { return BRANDS.find(brand => brand === item.category) ?? 'GPT'; }
function sourceEnabled(value: Merchant | null, provider: string) {
  return provider === 'local' || (value?.sources?.[provider]?.can_read !== false && (value?.source_enabled?.[provider] ?? value?.sources?.[provider]?.enabled ?? true));
}
function sourcesFor(value: Merchant) {
  return { ...value.source_enabled, ...Object.fromEntries(Object.keys(value.sources ?? { gpt: value })
    .filter(provider => provider !== 'local').map(provider => [provider, sourceEnabled(value, provider)])) };
}
/**
 * Storefront plans (one per public title), each with the supplier products that sell it, cheapest
 * first. Store-owned products remain separate even when their names match a supplier plan.
 * A feed that is out of date cannot be bought from (its products are unavailable on the
 * storefront), so its last known prices never count as the lowest.
 */
function plansOf(items: SourceItem[], stale: (provider: string) => boolean, enabled: (provider: string) => boolean): Plan[] {
  const plans = new Map<string, { title: string; products: Map<string, SupplierProduct> }>();
  for (const item of items) {
    const title = item.public_title ?? item.title;
    const provider = providerOf(item);
    const key = provider === 'local' ? `local:${item.product_id}` : `source:${title}`;
    const plan = plans.get(key) ?? { title, products: new Map<string, SupplierProduct>() };
    const products = plan.products;
    const product = products.get(item.product_id) ?? { productId: item.product_id, provider, title: item.title, rows: [], cheapest: undefined, stale: stale(provider), enabled: enabled(provider) };
    product.rows.push(item);
    if (product.enabled && item.available && !product.stale && (!product.cheapest || item.cost_cents < product.cheapest.cost_cents)) product.cheapest = item;
    products.set(item.product_id, product);
    plans.set(key, plan);
  }
  return [...plans].map(([key, { title, products }]) => {
    const list = [...products.values()].sort((a, b) => (a.cheapest?.cost_cents ?? Infinity) - (b.cheapest?.cost_cents ?? Infinity));
    return { key, title, products: list, best: list[0]?.cheapest ? list[0] : undefined };
  }).sort((a, b) => a.title.localeCompare(b.title, 'zh-CN', { numeric: true }));
}
function storedBrand(): Brand | null {
  try { return BRANDS.find(brand => brand === localStorage.getItem(BRAND_KEY)) ?? null; } catch { return null; }
}
function priceText(cents: number | null | undefined) { return cents == null ? '' : money(cents).slice(1); }
// Preview only; the server independently calculates the authoritative selling price.
function costPlusPrice(cost: number, profit: number | null) {
  if (!Number.isInteger(cost) || cost <= 0 || profit === null || profit < 500) return null;
  const floor = cost + profit;
  const remainder = floor % 100;
  const rounded = floor - remainder + (remainder === 0 ? 0 : remainder <= 88 ? 88 : 99);
  return rounded <= 100_000_000 ? rounded : null;
}
function draftFor(value: Merchant): Draft {
  const draft: Draft = {};
  for (const [key, setting] of Object.entries(value.skus)) draft[key] = { price: priceText(setting.price_cents), published: setting.published, promo: priceText(setting.promo_price_cents), automatic: Boolean(setting.pricing), profit: priceText(setting.pricing?.profit_cents ?? DYNAMIC_PRICING_MINIMUMS[key] ?? 500) };
  for (const item of value.items) draft[item.key] ??= { ...EMPTY_DRAFT, profit: priceText(DYNAMIC_PRICING_MINIMUMS[item.key] ?? 500) };
  return draft;
}
function displayFor(value: Merchant): DisplayDraft {
  return Object.fromEntries(Object.entries(value.display ?? {}).map(([id, item]) => [id, { badge: item.badge, sold: item.sold ? String(item.sold) : '', newUntil: item.new_until ?? 0 }]));
}
// Names separated by 、 , ， or new lines; repeats once.
function itemsOf(text: string) { return [...new Set(text.split(/[、,，\n]/).map(item => item.trim()).filter(Boolean))]; }
const nowSeconds = () => Math.floor(Date.now() / 1000);
const dayText = (seconds: number) => new Date(seconds * 1000).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' });
function namesFor(value: Merchant): LocalNames {
  const names = { products: { ...value.local_names?.products }, variants: { ...value.local_names?.variants } };
  for (const item of value.items) {
    if (providerOf(item) !== 'local') continue;
    names.products[item.product_id] = item.public_title ?? item.title;
    names.variants[item.key] = item.label;
  }
  return names;
}
function validName(text: string, limit: number) {
  const length = [...text.trim()].length;
  return length > 0 && length <= limit && !/[\p{Cc}\p{Cf}\p{Cs}\p{Zl}\p{Zp}<>]/u.test(text);
}
// NaN marks unparsable input so it never compares equal to a saved price.
function draftCents(price: string) { return price === '' ? null : priceCents(price) ?? Number.NaN; }
// Filters and counts describe the saved storefront, so rows never jump while being edited.
function matchesFilter(filter: Filter, item: SourceItem, saved: Setting | undefined, enabled: boolean) {
  if (filter === 'published') return enabled && saved?.published === true;
  if (filter === 'unpublished') return !enabled || saved?.published !== true;
  if (filter === 'unpriced') return enabled && saved?.price_cents == null;
  if (filter === 'soldout') return enabled && !item.available;
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
  const inventorySession = useInventorySession();
  const [state, setState] = useState<Merchant | null>(null);
  const [telegram, setTelegram] = useState('');
  const [wechat, setWechat] = useState('');
  const [draft, setDraft] = useState<Draft>({});
  const [display, setDisplay] = useState<DisplayDraft>({});
  const [names, setNames] = useState<LocalNames>({ products: {}, variants: {} });
  const [sourceDraft, setSourceDraft] = useState<Record<string, boolean>>({});
  const [openSources, setOpenSources] = useState(false);
  const [announcement, setAnnouncement] = useState({ text: '', items: '' });
  const [openAnnouncement, setOpenAnnouncement] = useState(false);
  // Products whose label is being written by hand rather than picked from BADGES.
  const [customBadges, setCustomBadges] = useState<Set<string>>(() => new Set());
  // Products whose settings (label, names, description) are open.
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
  // 本店库存 reports its counts; only the per-SKU numbers change, unsaved inputs stay.
  const updateStock = useCallback((stock: Record<string, number>) => { setState(old => old && { ...old, inventory: stock }); }, []);
  function accept(value: Merchant) { setState(value); setTelegram(value.telegram); setWechat(value.wechat ?? ''); setDraft(draftFor(value)); setDisplay(displayFor(value)); setNames(namesFor(value)); setSourceDraft(sourcesFor(value)); setAnnouncement({ text: value.announcement?.text ?? '', items: (value.announcement?.items ?? []).join('、') }); setCustomBadges(new Set()); setCopy(value.products ?? {}); setOpenProducts(old => new Set([...old].filter(id => value.items.some(item => item.product_id === id)))); setDirty(false); }
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
  function editAnnouncement(change: Partial<typeof announcement>) {
    setAnnouncement(value => ({ ...value, ...change }));
    setDirty(true); setNotice('');
  }
  function editName(field: keyof LocalNames, id: string, name: string) {
    setNames(value => ({ ...value, [field]: { ...value[field], [id]: name } }));
    setDirty(true); setNotice('');
  }
  function editSource(provider: string, enabled: boolean) {
    setSourceDraft(value => ({ ...value, [provider]: enabled }));
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
    if (busy) return;
    if (dirty && !window.confirm('重新读取会丢弃尚未保存的输入，确定继续吗？')) return;
    setBusy(true); setError(''); setNotice('');
    try { accept(await shopRequest<Merchant>('admin')); } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function refresh() {
    if (busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await shopRequest<Merchant>('refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      // Keep the merchant revision and all unsaved values: source refresh is not a save.
      // Default copy for newly discovered products joins the saved baseline, not the edits.
      setState(old => {
        if (!old) return value;
        const local = new Map(old.items.filter(item => providerOf(item) === 'local').map(item => [item.key, item]));
        const refreshedItems = value.items.map(item => {
          const previous = providerOf(item) === 'local' ? local.get(item.key) : undefined;
          // Local names belong to the retained merchant revision, not the source refresh.
          return previous ? { ...item, title: previous.title, public_title: previous.public_title ?? previous.title, label: previous.label } : item;
        });
        const sources = Object.fromEntries(Object.entries(value.sources ?? { gpt: value })
          .map(([provider, source]) => [provider, { ...source, enabled: sourceEnabled(old, provider) }]));
        return { ...old, products: { ...value.products, ...old.products }, automatic_sold: value.automatic_sold ?? {}, removed_sources: value.removed_sources ?? {}, sources, items: refreshedItems, last_success: value.last_success, error: value.error, is_stale: value.is_stale, cooldown_seconds: value.cooldown_seconds, retry_after_seconds: value.retry_after_seconds };
      });
      setDraft(old => { const next = { ...old }; for (const item of value.items) next[item.key] ??= { ...EMPTY_DRAFT }; return next; });
      // Newly discovered products receive effective defaults; existing dirty or
      // explicitly cleared input continues to win over the refreshed response.
      setCopy(old => ({ ...value.products, ...old }));
      setNames(old => { const added = namesFor(value); return { products: { ...added.products, ...old.products }, variants: { ...added.variants, ...old.variants } }; });
      setNotice(value.error ? '部分来源暂不可用；售价和未保存输入已保留。' : '来源检查完成；售价和未保存输入已保留。');
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function changeSource(provider: string, deleted: boolean) {
    if (!state || busy || dirty) return;
    const source = deleted ? state.sources?.[provider] : state.removed_sources?.[provider];
    const name = source?.supplier ?? provider;
    const bookmarked = source?.can_read === false;
    const confirmation = bookmarked
      ? `永久删除来源「${name}」？\n\n将移除已保存的来源链接。以后可以重新添加，仍暂不读取商品。`
      : `永久删除来源「${name}」？\n\n将停止读取，移除前后台商品，并永久清除该来源的售价、优惠价、文案、标签、补录销量和上架设置，无法撤销。\n\n以后重新添加需要重新抓取并重新定价。浏览和实际订单历史会保留。`;
    if (deleted && !window.confirm(confirmation)) return;
    setBusy(true); setError(''); setNotice('');
    try {
      const value = await shopRequest<Merchant>(`admin/sources/${encodeURIComponent(provider)}`, {
        method: deleted ? 'DELETE' : 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: state.revision, ...(deleted ? { confirm: 'delete-source-and-products' } : {}) }),
      });
      accept(value);
      setNotice(deleted ? (bookmarked ? `来源「${name}」已永久删除。` : `来源「${name}」及其商品设置已永久删除。`)
        : (bookmarked ? `来源「${name}」已重新添加，暂不读取商品。` : `来源「${name}」已重新添加，请刷新来源并重新定价后上架。`));
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function save() {
    if (!state || busy) return;
    setError(''); setNotice('');
    const skus: Record<string, Setting> = {};
    for (const [key, value] of Object.entries(draft)) {
      const price = value.price === '' ? null : priceCents(value.price);
      if ((value.price !== '' && price === null) || (value.published && price === null)) { setError('请逐项填写有效售价（最多两位小数）；没有售价的规格不能上架。'); return; }
      const promo = value.promo === '' ? null : priceCents(value.promo);
      if (value.promo !== '' && (promo === null || price === null || promo >= price)) { setError('优惠价需低于售价（最多两位小数）；不做优惠可以留空。'); return; }
      skus[key] = { price_cents: price, published: value.published, ...(promo === null ? {} : { promo_price_cents: promo }) };
      const minimum = DYNAMIC_PRICING_MINIMUMS[key];
      if (minimum !== undefined) {
        const profit = priceCents(value.profit);
        if (value.automatic && (profit === null || profit < minimum)) { setError(`动态定价的最低毛利不能低于 ¥${minimum / 100}，请填写有效金额。`); return; }
        skus[key].pricing = value.automatic ? { mode: 'cost_plus', profit_cents: profit! } : null;
      }
    }
    const shown: Display = {};
    for (const [productId, value] of Object.entries(display)) {
      const badge = value.badge.trim();
      const sold = value.sold.trim() === '' ? 0 : Number(value.sold.trim());
      if (badge.length > 8) { setError('优惠标签最多 8 个字，例如“学生价”。'); return; }
      if (!Number.isInteger(sold) || sold < 0 || sold > 10_000_000) { setError('已售件数请填写 0 到 10000000 之间的整数。'); return; }
      // An expired 新品 mark is dropped on the next save.
      const newUntil = value.newUntil > nowSeconds() ? value.newUntil : 0;
      if (badge || sold || newUntil || state.display?.[productId]?.new_until) shown[productId] = { badge, sold, new_until: newUntil };
    }
    const shownNote = { text: announcement.text.trim(), items: itemsOf(announcement.items) };
    if (shownNote.text.length > 200 || /[<>]/.test(shownNote.text)) { setError('店铺公告最多 200 个字，不能包含 < 或 >。'); return; }
    if (shownNote.items.length > 40 || shownNote.items.some(item => !ANNOUNCEMENT_ITEM.test(item))) { setError('代购商品最多 40 个，每个最多 24 个字，只用文字、数字、空格和 . + - / & ·。'); return; }
    if (telegram && !/^[A-Za-z][A-Za-z0-9_]{4,31}$/.test(telegram)) { setError('请输入 5–32 位 Telegram 用户名，不包含 @ 或链接。'); return; }
    if (wechat && !/^[A-Za-z][-_A-Za-z0-9]{5,19}$/.test(wechat)) { setError('请输入 6–20 位微信号：字母开头，可包含字母、数字、下划线和减号。'); return; }
    const localNames: LocalNames = { products: {}, variants: {} };
    for (const field of ['products', 'variants'] as const) {
      const limit = field === 'products' ? 120 : 80;
      for (const [id, name] of Object.entries(names[field])) {
        if (!validName(name, limit)) { setError(`${field === 'products' ? '商品名称' : '规格名称'}请填写 1–${limit} 个字的单行纯文本。`); return; }
        localNames[field][id] = name.trim();
      }
    }
    // Keep client 3 for existing price semantics; local_names is an explicit optional field.
    const controls = Object.fromEntries(Object.entries(sourceDraft).filter(([, enabled]) => !enabled));
    const settings = { revision: state.revision, client: 3, telegram, wechat, skus, products: copy, display: shown, announcement: shownNote, local_names: localNames, source_enabled: controls };
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

  const allItems = state?.items ?? [];
  const saved = state?.skus ?? {};
  const isEnabled = (provider: string) => sourceEnabled(state, provider);
  // Hide disabled feeds from browsing, while retaining their complete save drafts.
  const items = allItems.filter(item => isEnabled(providerOf(item)));
  const sourceChanged = (provider: string) => (sourceDraft[provider] ?? true) !== isEnabled(provider);
  const sourceChanges = Object.keys(sourceDraft).filter(sourceChanged).length;
  const savedNames = state ? namesFor(state) : { products: {}, variants: {} };
  const nameChanged = (field: keyof LocalNames, id: string) => names[field][id] !== undefined && names[field][id]!.trim() !== savedNames[field][id];
  const productNamesChanged = (productId: string) => nameChanged('products', productId) || items.some(item => item.product_id === productId && nameChanged('variants', item.key));
  const byBrand = new Map<Brand, SourceItem[]>();
  for (const item of items) byBrand.set(brandOf(item), [...(byBrand.get(brandOf(item)) ?? []), item]);
  const present = BRANDS.filter(name => byBrand.has(name));
  const activeBrand = present.find(name => name === brand) ?? present[0];
  const needle = query.trim().toLowerCase();
  const found = (item: SourceItem) => !needle || [item.title, item.public_title, item.label, item.public_label, item.key].some(text => text?.toLowerCase().includes(needle));
  // Tabs show one brand at a time; a search looks across every brand.
  const inScope = (item: SourceItem) => (needle !== '' || brandOf(item) === activeBrand) && found(item);
  const counts = Object.fromEntries(FILTERS.map(([value]) => [value, items.filter(item => inScope(item) && matchesFilter(value, item, saved[item.key], isEnabled(providerOf(item)))).length])) as Record<Filter, number>;
  const visible = (item: SourceItem) => inScope(item) && matchesFilter(filter, item, saved[item.key], isEnabled(providerOf(item)));
  const brandCount = (name: Brand) => items.filter(item => brandOf(item) === name && found(item) && matchesFilter(filter, item, saved[item.key], isEnabled(providerOf(item)))).length;
  const priceChanged = (key: string) => {
    const value = draft[key];
    return value !== undefined && (value.published !== (saved[key]?.published ?? false) || draftCents(value.price) !== (saved[key]?.price_cents ?? null)
      || draftCents(value.promo) !== (saved[key]?.promo_price_cents ?? null)
      || value.automatic !== Boolean(saved[key]?.pricing)
      || (value.automatic && draftCents(value.profit) !== saved[key]?.pricing?.profit_cents));
  };
  const changed = (key: string) => priceChanged(key) || nameChanged('variants', key);
  const displayChanged = (productId: string) => {
    const current = display[productId] ?? EMPTY_DISPLAY;
    const base = state?.display?.[productId];
    return current.badge.trim() !== (base?.badge ?? '') || (current.sold.trim() === '' ? 0 : Number(current.sold.trim())) !== (base?.sold ?? 0)
      || current.newUntil !== (base?.new_until ?? 0);
  };
  const savedAnnouncement = state?.announcement ?? EMPTY_ANNOUNCEMENT;
  const announcementItems = itemsOf(announcement.items);
  const announcementChanged = Boolean(state) && (announcement.text.trim() !== savedAnnouncement.text || announcementItems.join('\n') !== savedAnnouncement.items.join('\n'));
  const copyChanged = (productId: string) => {
    const current = copy[productId] ?? EMPTY_COPY;
    const base = state?.products?.[productId] ?? EMPTY_COPY;
    return current.description !== base.description || current.after_sales !== base.after_sales;
  };
  const pending = [
    [Object.keys(draft).filter(priceChanged).length, '个规格'],
    [Object.keys(names.products).filter(productNamesChanged).length, '个商品的名称或规格名称'],
    [Object.keys({ ...copy, ...state?.products }).filter(copyChanged).length, '个商品的说明'],
    [Object.keys({ ...display, ...state?.display }).filter(displayChanged).length, '个商品的前台展示'],
    [sourceChanges, '个来源读取设置'],
  ].filter(([count]) => count).map(([count, unit]) => `${count} ${unit}`);
  if (state && telegram !== state.telegram) pending.push('Telegram 用户名');
  if (state && wechat !== (state.wechat ?? '')) pending.push('微信号');
  if (announcementChanged) pending.push('店铺公告');
  const shownCount = items.filter(visible).length;
  const brandDirty = (name: Brand) => items.some(item => brandOf(item) === name && (changed(item.key) || productNamesChanged(item.product_id) || copyChanged(item.product_id) || displayChanged(item.product_id)));
  const providerCount = (provider: string) => allItems.filter(item => providerOf(item) === provider).length;
  const supplierOf = (provider: string) => state?.sources?.[provider]?.supplier ?? provider;
  const sourceName = (provider: string, source: SourceStatus) => [source.covers, source.supplier].filter(Boolean).join(' · ') || provider;
  const isStale = (provider: string) => state?.sources?.[provider]?.is_stale === true;
  const onSale = new Set(items.filter(item => isEnabled(providerOf(item)) && saved[item.key]?.published).map(item => item.product_id)).size;
  const unpriced = items.filter(item => isEnabled(providerOf(item)) && saved[item.key]?.price_cents == null).length;
  const soldTotal = Object.values(state?.display ?? {}).reduce((sum, item) => sum + item.sold, 0)
    + Object.values(state?.automatic_sold ?? {}).reduce((sum, count) => sum + count, 0);
  const feedback = <>
    {error ? <p role="alert" className="shop-admin-feedback is-error">{error}</p> : null}
    <p role="status" className="shop-admin-feedback">{notice}</p>
  </>;

  // 本店库存 and 采购预订 are their own pages; product edits stay in this component while they show.
  const page = new URLSearchParams(window.location.search).get('view');
  if (page === 'inventory') {
    const inventoryItems = new Map(items.filter(item => providerOf(item) !== 'local' && STOCKED.has(brandOf(item))).map(item => [item.key, item]));
    for (const item of state?.inventory_items ?? []) inventoryItems.set(item.key, item);
    const stocked: StockedProduct[] = [...inventoryItems.values()].map(item => ({
      key: item.key, productId: item.product_id, title: item.public_title ?? item.title, label: item.label, category: item.category ?? brandOf(item),
      priceCents: saved[item.key]?.price_cents ?? null, published: Boolean(saved[item.key]?.published) && Boolean(state?.sources?.[providerOf(item)]?.enabled ?? !state?.removed_sources?.[providerOf(item)]), supplierAvailable: item.available,
    }));
    return <AdminShell active="inventory" pageTitle="本店库存" subtitle="自有账号、链接与激活码 · 有现货即可下单"><ShopInventory session={inventorySession} products={stocked} loading={!state} unsavedProducts={dirty} onCounts={updateStock}/></AdminShell>;
  }
  if (page === 'procurement') {
    return <AdminShell active="procurement" pageTitle="采购预订" subtitle="供应商余额采购 · 买满即停"><ShopProcurement unsavedProducts={dirty}/></AdminShell>;
  }
  return <AdminShell active="shop" pageTitle="商品管理" topbarExtra={<div className="shop-admin-actions"><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void refresh()}>刷新来源</button><button type="button" className="btn btn-secondary" disabled={busy} onClick={() => void reload()}>重新读取</button></div>}>
    <section className="shop-admin-panel">
      {state ? <>
        <section className="shop-admin-overview" aria-label="店铺概览">
          <dl className="shop-admin-kpis">
            <div><dt>在售商品</dt><dd>{onSale}</dd></div>
            <div className={unpriced ? 'is-attention' : undefined}><dt>待定价规格</dt><dd>{unpriced}</dd></div>
            <div><dt>累计浏览</dt><dd title="按访问 IP 每天计一次，是近似人数：同一网络下的多人会合并，换了网络的人会再计一次。">{(state.stats?.site ?? 0).toLocaleString('zh-CN')}<small>人次</small></dd></div>
            <div><dt>累计售出</dt><dd title="已保存的补录销量与自动记录合计">{soldTotal.toLocaleString('zh-CN')}<small>件</small></dd></div>
          </dl>
          <div className="shop-admin-contact" role="group" aria-label="商家联系方式">
            <label>微信<input aria-label="商家微信号" value={wechat} disabled={busy} placeholder="例如 upc-bh" autoComplete="off" onChange={event => { setWechat(event.target.value.trim()); setDirty(true); setNotice(''); }}/></label>
            <label>Telegram<span className="shop-admin-affix"><span aria-hidden="true">@</span><input aria-label="商家 Telegram 用户名" value={telegram} disabled={busy} placeholder="用户名" autoComplete="off" onChange={event => { setTelegram(event.target.value.trim()); setDirty(true); setNotice(''); }}/></span></label>
            <small className={telegram || wechat ? undefined : 'is-warn'}>{telegram || wechat ? '顾客通过这里的联系方式找你下单。' : '微信和 Telegram 都没填时，前台无法联系购买。'}</small>
          </div>
          <div className="shop-admin-sources" role="group" aria-label="来源状态">
            {Object.entries(state.sources ?? { gpt: state }).map(([provider, source]) => {
              const enabled = isEnabled(provider);
              const bookmarked = source.can_read === false;
              const count = providerCount(provider);
              const warn = enabled && (source.is_stale || Boolean(source.error));
              const wait = Math.max(source.retry_after_seconds, source.cooldown_seconds);
              const status = bookmarked ? '已保存来源链接 · 暂不读取商品' : enabled ? `${source.is_stale ? '商品信息待更新，此来源商品暂不能购买' : '商品信息已更新'}${source.error ? ' · 来源暂不可用，稍后自动重试' : ''}${wait > 0 ? ` · 刷新等待 ${wait} 秒` : ''} · 最近成功更新 ${updatedAt(source.last_success)}` : '读取已停用 · 前台隐藏 · 商品设置已保留';
              const body = <><span className="shop-source-dot" aria-hidden="true"/>{sourceName(provider, source)}<b>{count}</b>{bookmarked ? <small>暂不读取</small> : !enabled ? <small>已停用</small> : warn ? <small>{source.error ? '暂不可用' : '待更新'}</small> : !count ? <small>暂无商品</small> : null}</>;
              const chipClass = `shop-source-chip${!enabled ? ' is-disabled' : warn ? ' is-warn' : ''}`;
              return source.site
                ? <a key={provider} className={chipClass} href={source.site} target="_blank" rel="noopener noreferrer" title={status} aria-label={`${sourceName(provider, source)}：${status}，打开来源网站 ${hostOf(source.site)}`}>{body}</a>
                : <span key={provider} className={chipClass} title={status}>{body}</span>;
            })}
            <button type="button" className="shop-admin-source-toggle" aria-expanded={openSources} aria-controls="shop-admin-source-settings" onClick={() => setOpenSources(value => !value)}>来源设置{sourceChanges ? <span aria-hidden="true" className="shop-admin-source-dirty"/> : null}</button>
          </div>
          {openSources ? <div className="shop-admin-source-settings" id="shop-admin-source-settings" role="group" aria-label="来源读取设置">
            <p>停用并保存后停止读取，该来源商品会从前台和下方管理列表隐藏，已保存的价格、文案和上架设置会保留。本店商品独立管理。</p>
            <div className="shop-admin-source-options">{Object.entries(state.sources ?? { gpt: state }).filter(([provider]) => provider !== 'local').map(([provider, source]) => <div key={provider} className={`shop-admin-source-option${sourceChanged(provider) ? ' is-changed' : ''}`}>
              <span><strong>{source.supplier ?? provider}</strong><small>{source.covers}</small></span>
              {source.can_read === false ? <small>暂不读取</small> : null}
              {sourceChanged(provider) ? <small className="shop-admin-source-pending">未保存</small> : null}
              <input type="checkbox" role="switch" className="shop-admin-switch" aria-label={`读取 ${source.supplier ?? provider}`} checked={source.can_read === false ? false : sourceDraft[provider] ?? true} disabled={busy || source.can_read === false} onChange={event => editSource(provider, event.target.checked)}/>
              <button type="button" className="shop-admin-source-delete" aria-label={`删除来源 ${source.supplier ?? provider}`} disabled={busy || dirty} onClick={() => void changeSource(provider, true)}>删除来源</button>
            </div>)}</div>
            <small>开关修改后点击“保存商品设置”生效。删除来源会永久清除其商品设置。</small>
            {dirty ? <p className="shop-admin-source-draft-help">有未保存的输入，请先保存或重新读取，再删除或重新添加来源。</p> : null}
            {Object.keys(state.removed_sources ?? {}).length ? <div className="shop-admin-source-removed" role="group" aria-label="已删除的来源">
              <p>已删除的来源 · 重新添加后，可读取的商品需要重新定价并上架；待接入的来源仍暂不读取。</p>
              <div className="shop-admin-source-options">{Object.entries(state.removed_sources ?? {}).map(([provider, source]) => <div key={provider} className="shop-admin-source-option">
                <span><strong>{source.supplier ?? provider}</strong><small>{source.covers}</small></span>
                {source.can_read === false ? <small>暂不读取</small> : null}
                <button type="button" className="btn btn-secondary btn-sm" aria-label={`重新添加 ${source.supplier ?? provider}`} disabled={busy || dirty || !source.can_add} onClick={() => void changeSource(provider, false)}>{source.can_add ? '重新添加' : '暂不可添加'}</button>
              </div>)}</div>
            </div> : null}
          </div> : null}
          <div className="shop-admin-announcement" role="group" aria-label="店铺公告">
            <div className="shop-admin-announcement-summary">
              <strong>店铺公告</strong>
              <span>{announcement.text.trim() || announcementItems.length ? <>{announcement.text.trim() || '（无公告文字）'}{announcementItems.length ? <b> · 还可代购 {announcementItems.length} 项</b> : null}</> : '未填写，前台不显示公告'}</span>
              <button type="button" className="shop-admin-source-toggle" aria-expanded={openAnnouncement} aria-controls="shop-admin-announcement-fields" onClick={() => setOpenAnnouncement(value => !value)}>编辑公告{announcementChanged ? <span aria-hidden="true" className="shop-admin-source-dirty"/> : null}</button>
            </div>
            {openAnnouncement ? <div className="shop-admin-announcement-fields" id="shop-admin-announcement-fields">
              <label><span>公告内容<small>{announcement.text.trim().length}/200</small></span><textarea aria-label="公告内容" rows={3} maxLength={220} value={announcement.text} disabled={busy} placeholder="例如：主营 AI 会员，其他商品没上架的直接问我。" onChange={event => editAnnouncement({ text: event.target.value })}/></label>
              <label><span>还可代购<small>{announcementItems.length}/40 项</small></span><textarea aria-label="还可代购的商品" rows={3} value={announcement.items} disabled={busy} placeholder="用顿号、逗号或换行分隔，例如：YouTube Premium、Gmail 账号" onChange={event => editAnnouncement({ items: event.target.value })}/></label>
              <small>显示在前台商品目录上方。两项都清空并保存后，前台不显示公告。</small>
            </div> : null}
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
        {!items.length ? <p className="shop-admin-empty">{allItems.length ? '暂无已启用来源的商品，可在来源设置中重新启用。' : '尚无来源商品，请刷新来源。获取到的商品会先保存为草稿。'}</p> : null}
        {items.length && !shownCount ? <div className="shop-admin-empty"><p>没有符合条件的规格。</p><button type="button" className="btn btn-secondary btn-sm" onClick={() => { setFilter('all'); setQuery(''); }}>清除筛选</button></div> : null}
        <div className="shop-admin-products">{BRANDS.filter(name => byBrand.has(name)).map(brandName => {
          const brandItems = byBrand.get(brandName) ?? [];
          const brandId = brandName.replace(/\s+/g, '-');
          const plans = plansOf(brandItems, isStale, isEnabled);
          return <section key={brandName} className="shop-admin-group" aria-labelledby={`shop-admin-group-${brandId}`} hidden={!brandItems.some(visible)}>
            <header className="shop-admin-group-header">
              <h2 id={`shop-admin-group-${brandId}`}>{brandName}</h2>
              <span>{plans.length} 个商品方案 · {brandItems.length} 个规格 · 已上架 {brandItems.filter(item => isEnabled(providerOf(item)) && saved[item.key]?.published).length}</span>
            </header>
            {plans.map(plan => {
              const planItems = plan.products.flatMap(product => product.rows);
              const publishedRows = planItems.filter(item => isEnabled(providerOf(item)) && saved[item.key]?.published && saved[item.key]?.price_cents != null);
              const prices = publishedRows.map(item => {
                const setting = saved[item.key]!;
                return setting.pricing ? costPlusPrice(item.cost_cents, setting.pricing.profit_cents) : setting.promo_price_cents ?? setting.price_cents;
              }).filter((price): price is number => price !== null);
              const sellers = new Set(publishedRows.map(item => providerOf(item))).size;
              const comparable = new Set(plan.products.filter(product => product.enabled).map(product => product.provider)).size > 1;
              return <article key={plan.key} className="shop-plan" aria-label={plan.title} hidden={!planItems.some(visible)}>
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
                  const automaticSold = state.automatic_sold?.[productId] ?? 0;
                  const sold = (Number(shown.sold) || 0) + automaticSold;
                  const custom = customBadges.has(productId) || Boolean(shown.badge && !BADGES.includes(shown.badge));
                  const open = openProducts.has(productId);
                  const published = product.enabled && product.rows.some(item => saved[item.key]?.published);
                  const views = state.stats?.products[productId] ?? 0;
                  const settingsChanged = displayChanged(productId) || copyChanged(productId) || productNamesChanged(productId);
                  return <section key={productId} className={`shop-admin-product${open ? ' is-open' : ''}${!product.enabled ? ' is-disabled' : ''}`} aria-labelledby={`shop-admin-product-${productId}`} hidden={!product.rows.some(visible)}>
                    <header className="shop-admin-product-header">
                      <div className="shop-admin-source-info">
                        <div className="shop-admin-source-heading">
                          <span className={`shop-admin-supplier${product.enabled && product.stale ? ' is-stale' : ''}`}>{supplierOf(product.provider)}</span>
                          {comparable && plan.best?.productId === productId ? <span className="badge ok">最低</span> : null}
                          {!product.enabled ? <span className="badge shop-admin-source-off">来源已停用 · 前台隐藏</span> : product.stale ? <span className="badge warn">价格待更新</span> : null}
                          <span className="shop-admin-product-flags">
                            {shown.newUntil > nowSeconds() ? <span className="shop-new-badge">新品</span> : null}
                            {shown.badge.trim() && !product.rows.some(item => draft[item.key]?.automatic) ? <span className="shop-promo-badge">{shown.badge.trim()}</span> : null}
                            {sold ? <span>已售 {sold.toLocaleString('zh-CN')}</span> : null}
                            {views ? <span>浏览 {views.toLocaleString('zh-CN')}</span> : null}
                            {published && !filled ? <span className="is-warn">缺说明</span> : null}
                          </span>
                        </div>
                        <h4 id={`shop-admin-product-${productId}`} tabIndex={-1} title={product.title}>{product.title}</h4>
                      </div>
                      <div className="shop-admin-product-actions">
                        <button type="button" className={`shop-admin-product-toggle${settingsChanged ? ' is-changed' : ''}`} aria-expanded={open} aria-controls={`shop-admin-settings-${productId}`} aria-label={`商品设置 ${productId}`} onClick={() => toggleProduct(productId)}>设置{settingsChanged ? ' · 已修改' : ''}<svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none"><path d="m4 6 4 4 4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg></button>
                        {first?.buy_url ? <a className="shop-admin-buy" href={first.buy_url} target="_blank" rel="noopener noreferrer" aria-label={`去来源购买 ${product.title}`}>去进货<span aria-hidden="true"> ↗</span></a> : null}
                      </div>
                    </header>
                    {product.rows.filter(item => DYNAMIC_PRICING_MINIMUMS[item.key] !== undefined).map(item => {
                      const value = draft[item.key] ?? EMPTY_DRAFT;
                      return <div className="shop-admin-pricing" key={item.key}>
                        <label className="shop-admin-pricing-toggle"><input type="checkbox" checked={value.automatic} disabled={busy} aria-label={`动态定价 ${item.key}`} onChange={event => edit(item.key, { automatic: event.target.checked })}/>动态定价</label>
                        {value.automatic ? <><label className="shop-admin-profit-floor">每份最低毛利 ¥<input aria-label={`最低毛利 ${item.key}`} inputMode="decimal" value={value.profit} disabled={busy} onChange={event => edit(item.key, { profit: event.target.value })}/></label><span>按同步成本加毛利，向上取整至整数、.88 或 .99。</span><small>成本约每 15 分钟同步；固定售价和优惠价暂不生效，切回手动时恢复。成本失效时暂停购买。</small></> : <span>开启后随同步成本调价，每份至少赚 ¥{DYNAMIC_PRICING_MINIMUMS[item.key]! / 100}。</span>}
                      </div>;
                    })}
                    <div className="shop-admin-variants">
                      <div className="shop-admin-columns" aria-hidden="true"><span/><span>成本</span><span>售价（元）</span><span>{shown.badge.trim() || '优惠价'}（元）</span><span>成交毛利 / 毛利率</span><span>上架</span></div>
                      {[...product.rows].sort((a, b) => a.cost_cents - b.cost_cents).map(item => {
                        const value = draft[item.key] ?? EMPTY_DRAFT;
                        const quote = value.automatic ? costPlusPrice(item.cost_cents, priceCents(value.profit)) : null;
                        const shownPrice = value.automatic ? priceText(quote) : value.price;
                        const promo = !value.automatic && value.promo && priceCents(value.promo) !== null && (priceCents(value.promo) ?? 0) < (priceCents(value.price) ?? 0) ? value.promo : '';
                        const unavailableQuote = value.automatic && (quote === null || product.stale || item.archived);
                        const margin = unavailableQuote ? { tone: 'empty', amount: '待同步成本', rate: '' } : marginOf(item.cost_cents, promo || shownPrice, value.published);
                        const regularMargin = promo ? marginOf(item.cost_cents, value.price, value.published) : null;
                        return <article key={item.key} className={`shop-admin-row${changed(item.key) ? ' is-changed' : ''}${item.available ? '' : ' is-soldout'}`} hidden={!visible(item)}>
                          <div className="shop-admin-spec">
                            <strong>{item.public_label ?? item.label}</strong>
                            {state.inventory?.[item.key] ? <a className="badge shop-admin-stock" href="/admin/shop?view=inventory" title="本店库存的现货件数">现货 {state.inventory[item.key]}</a> : null}
                            {item.public_label ? <small className="shop-admin-source-label">来源规格：{item.label}</small> : null}
                            {(item.available || !product.enabled) && !changed(item.key) ? null : <small>{product.enabled && !item.available ? <span className="badge warn">来源售罄</span> : null}{changed(item.key) ? <span className="badge shop-admin-changed">已修改</span> : null}</small>}
                          </div>
                          <div className="shop-admin-cost"><span className="shop-admin-mobile-label">成本</span><strong>{money(item.cost_cents)}</strong></div>
                          <label className="shop-admin-price"><span className="shop-admin-mobile-label">售价</span><span className={`shop-admin-affix${value.automatic ? ' is-automatic' : ''}`}><span aria-hidden="true">¥</span><input aria-label={`售价 ${item.key}`} aria-invalid={margin.tone === 'invalid' || undefined} inputMode="decimal" autoComplete="off" value={shownPrice} disabled={busy || value.automatic} placeholder={value.automatic ? '待同步' : '待填写'} onChange={event => edit(item.key, { price: event.target.value })}/></span>{value.automatic ? <small>{unavailableQuote ? '成本待更新 · 暂停购买' : '自动成交价'}</small> : null}</label>
                          <label className="shop-admin-promo"><span className="shop-admin-mobile-label">{shown.badge.trim() || '优惠价'}</span><span className="shop-admin-affix"><span aria-hidden="true">¥</span><input aria-label={`优惠价 ${item.key}`} inputMode="decimal" autoComplete="off" value={value.automatic ? '' : value.promo} disabled={busy || value.automatic} placeholder={value.automatic ? '动态定价中' : '不优惠'} onChange={event => edit(item.key, { promo: event.target.value })}/></span></label>
                          <div className={`shop-admin-margin is-${margin.tone}`}>
                            <span className="shop-admin-mobile-label">毛利</span>
                            <div className="shop-admin-margin-main"><strong>{margin.amount}</strong>{margin.rate ? <span>{margin.rate}</span> : null}</div>
                            {regularMargin?.rate ? <small>原价毛利 {regularMargin.amount}</small> : null}
                          </div>
                          <label className="shop-publish"><input aria-label={`上架 ${item.key}`} className="shop-admin-switch" type="checkbox" checked={value.published} disabled={busy} onChange={event => edit(item.key, { published: event.target.checked })}/><span>上架</span></label>
                        </article>;
                      })}
                    </div>
                    {open ? <div className="shop-admin-product-settings" id={`shop-admin-settings-${productId}`}>
                      {product.provider === 'local' ? <div className="shop-admin-names">
                        <label className="shop-admin-product-name">商品名称<input aria-label={`商品名称 ${productId}`} aria-invalid={!validName(names.products[productId] ?? '', 120) || undefined} maxLength={120} value={names.products[productId] ?? ''} disabled={busy} onChange={event => editName('products', productId, event.target.value)}/></label>
                        {product.rows.map((item, index) => <label key={item.key}>规格 {index + 1} 名称<input aria-label={`规格名称 ${item.key}`} aria-invalid={!validName(names.variants[item.key] ?? '', 80) || undefined} maxLength={80} value={names.variants[item.key] ?? ''} disabled={busy} onChange={event => editName('variants', item.key, event.target.value)}/></label>)}
                      </div> : null}
                      <div className="shop-admin-display" role="group" aria-label={`前台展示 ${productId}`}>
                        <div className="shop-admin-display-field">
                          <label>优惠标签<select aria-label={`优惠标签 ${productId}`} value={custom ? CUSTOM_BADGE : shown.badge} disabled={busy} onChange={event => chooseBadge(productId, event.target.value)}>
                            <option value="">不显示</option>{BADGES.map(name => <option key={name} value={name}>{name}</option>)}<option value={CUSTOM_BADGE}>自定义…</option>
                          </select></label>
                          {custom ? <input className="shop-admin-display-text" aria-label={`自定义标签 ${productId}`} maxLength={8} placeholder="最多 8 个字" value={shown.badge} disabled={busy} onChange={event => editDisplay(productId, { badge: event.target.value })}/> : null}
                          <small>显示在前台价格和商品页标题前，也是优惠价的名称，如「学生价 ¥118.00」。优惠价在规格行里填写。</small>
                        </div>
                        <div className="shop-admin-display-field">
                          <label>新品标记<select aria-label={`新品标记 ${productId}`} value={shown.newUntil > nowSeconds() ? 'keep' : ''} disabled={busy} onChange={event => editDisplay(productId, { newUntil: event.target.value === '' ? 0 : event.target.value === 'keep' ? shown.newUntil : nowSeconds() + Number(event.target.value) * 86400 })}>
                            <option value="">不标记</option>{shown.newUntil > nowSeconds() ? <option value="keep">新品至 {dayText(shown.newUntil)}</option> : null}{NEW_DAYS.map(days => <option key={days} value={days}>标记 {days} 天</option>)}
                          </select></label>
                          <small>前台卡片显示「新品」角标、排在同类商品前面，并出现在「新品」筛选里；到期自动取消。</small>
                        </div>
                        <div className="shop-admin-display-field shop-admin-sales" role="group" aria-label={`商品销量 ${productId}`}>
                          <label>已售件数<input className="shop-admin-display-sold" aria-label={`已售件数 ${productId}`} aria-describedby={`shop-admin-sales-help-${productId}`} inputMode="numeric" autoComplete="off" placeholder="0" value={shown.sold} disabled={busy} onChange={event => editDisplay(productId, { sold: event.target.value.trim() })}/>件</label>
                          <small id={`shop-admin-sales-help-${productId}`}>补录历史销量，自动记录的无需重复填写。<span>自动记录 {automaticSold.toLocaleString('zh-CN')} 件 · 合计 {sold.toLocaleString('zh-CN')} 件</span></small>
                        </div>
                      </div>
                      {isNoticeProduct(productId) && <p className="shop-note">商品说明支持 Markdown 标题、列表和强调。{product.rows.length > 1 ? '以「### 规格：规格名称」开头的小节，只在买家选中该规格时显示。' : ''}清空并保存后，客户将看到未填写提示。</p>}
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
