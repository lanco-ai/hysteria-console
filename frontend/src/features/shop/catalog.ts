// promo_price_cents: an 优惠价 below the price, for buyers it applies to; badge: its name (学生价…), also shown before the title.
export type Variant = { id: string; label: string; price_cents: number; promo_price_cents?: number | null; available: boolean; quantity: null; sales: null };
export type Product = { id: string; title: string; category: string; description?: string; after_sales?: string; badge?: string; sold?: number; views?: number; variants: Variant[] };
export type Catalog = { currency: 'CNY'; telegram: string; wechat?: string; stats?: { views: number; sold: number }; products: Product[]; status: 'ready' | 'stale' | 'unavailable'; updated_at: number | null };
export function money(cents: number): string { return `¥${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`; }
/** Buyers can reach the merchant by Telegram or WeChat. */
export function hasContact(catalog: Catalog | null): boolean { return Boolean(catalog?.telegram || catalog?.wechat); }
/** The variant's 优惠价, when the merchant set one below the price. */
export function promoCents(variant: Variant | undefined): number | null {
  const promo = variant?.promo_price_cents;
  return variant && promo && promo < variant.price_cents ? promo : null;
}
/** What the 优惠价 is called: the product's label, such as 学生价. */
export function promoName(product: Product): string { return product.badge || '优惠价'; }
/** 1,234 or 1.2万, for view and sales counts. */
export function countText(value: number): string {
  return value >= 10_000 ? `${(Math.floor(value / 1000) / 10).toFixed(1).replace(/\.0$/, '')}万` : value.toLocaleString('zh-CN');
}
/** Tell the shop someone opened it (and which product); it counts each visitor once a day. */
export function reportVisit(product: string | null): void {
  void fetch('/api/v1/shop/visit', {
    method: 'POST', credentials: 'same-origin', keepalive: true, headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(product ? { product } : {}),
  }).catch(() => undefined);
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
