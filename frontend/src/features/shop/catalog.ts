// promo_price_cents: an 优惠价 below the price, for buyers it applies to; badge: its name (学生价…), also shown before the title.
// quantity: units in the merchant's own stock (本店库存), when there are any.
// contact_only: purchase must be arranged with the merchant; it does not imply stock.
export type Variant = { id: string; label: string; price_cents: number; promo_price_cents?: number | null; available: boolean; contact_only?: boolean; quantity: number | null; sales: null };
// ends_at: when a limited-time service leaves the shop (seconds); new_until: while the merchant's 新品 mark lasts.
// Keep tags as text for open storefront tabs; hot_tags names the labels to highlight in red.
export type Product = { id: string; title: string; category: string; description?: string; after_sales?: string; badge?: string; sold?: number; views?: number; ends_at?: number; new_until?: number; tags?: string[]; hot_tags?: string[]; variants: Variant[] };
// announcement: the merchant's note to buyers and the unlisted products they can ask for.
export type Announcement = { text: string; items: string[] };
export type Catalog = { currency: 'CNY'; telegram: string; wechat?: string; announcement?: Announcement; stats?: { views: number; sold: number }; products: Product[]; status: 'ready' | 'stale' | 'unavailable'; updated_at: number | null };
export function money(cents: number): string { return `¥${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`; }
/** Buyers can reach the merchant by Telegram or WeChat. */
export function hasContact(catalog: Catalog | null): boolean { return Boolean(catalog?.telegram || catalog?.wechat); }
/** The variant's 优惠价, when the merchant set one below the price. */
export function promoCents(variant: Variant | undefined): number | null {
  const promo = variant?.promo_price_cents;
  return variant && promo && promo < variant.price_cents ? promo : null;
}
/** The lowest price a buyer can pay now, the regular price an 优惠价 lowers, and whether specs pay different prices ("起"). */
export function lowestOffer(product: Product): { cents: number; regular: number | null; from: boolean } | null {
  const offered = product.variants.filter(item => item.available);
  const pool = offered.length ? offered : product.variants;
  const pay = (item: Variant) => (offered.length ? promoCents(item) : null) ?? item.price_cents;
  if (!pool.length) return null;
  const cheapest = pool.reduce((best, item) => pay(item) < pay(best) ? item : best);
  const cents = pay(cheapest);
  return { cents, regular: cents < cheapest.price_cents ? cheapest.price_cents : null, from: new Set(pool.map(pay)).size > 1 };
}
/** A product the merchant marked 新品, until the mark ends. */
export function isNew(product: Product, now = Date.now()): boolean { return Boolean(product.new_until && product.new_until * 1000 > now); }
/** What the 优惠价 is called: the product's label, such as 学生价. */
export function promoName(product: Product): string { return product.badge || '优惠价'; }
/** 1,234 or 1.2万, for view and sales counts. */
export function countText(value: number): string {
  return value >= 10_000 ? `${(Math.floor(value / 1000) / 10).toFixed(1).replace(/\.0$/, '')}万` : value.toLocaleString('zh-CN');
}
/** Tell the shop someone opened it (and which product); it counts each visitor once a day. */
export async function reportVisit(product: string | null): Promise<boolean> {
  return fetch('/api/v1/shop/visit', {
    method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(product ? { product } : {}),
  }).then(response => response.ok).catch(() => false);
}
export function priceCents(value: string): number | null {
  if (!/^\d{1,7}(?:\.\d{1,2})?$/.test(value)) return null;
  const [whole = '0', fraction = ''] = value.split('.');
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, '0'));
  return result > 0 && result <= 100_000_000 ? result : null;
}
export async function shopRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1/shop/${path}`, { credentials: 'same-origin', ...init });
  if (!response.ok) throw new Error(response.status === 409 ? '设置已被其他页面修改。你的输入已保留，请重新读取后再保存。' : response.status === 401 || response.status === 403 ? '请使用管理员身份重新登录。' : '请求失败，请稍后重试。');
  return response.json() as Promise<T>;
}
