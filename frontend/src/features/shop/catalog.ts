// list_price_cents: the merchant's 原价, shown struck through; badge: a label such as 学生价 before the title.
export type Variant = { id: string; label: string; price_cents: number; list_price_cents?: number | null; available: boolean; quantity: null; sales: null };
export type Product = { id: string; title: string; category: string; description?: string; after_sales?: string; badge?: string; sold?: number; views?: number; variants: Variant[] };
export type Catalog = { currency: 'CNY'; telegram: string; wechat?: string; stats?: { views: number; sold: number }; products: Product[]; status: 'ready' | 'stale' | 'unavailable'; updated_at: number | null };
export function money(cents: number): string { return `¥${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`; }
/** Buyers can reach the merchant by Telegram or WeChat. */
export function hasContact(catalog: Catalog | null): boolean { return Boolean(catalog?.telegram || catalog?.wechat); }
/** The 原价 and the saving, when the merchant set an original price above the selling price. */
export function saving(variant: Variant | undefined): { listed: number; saved: number } | null {
  const listed = variant?.list_price_cents;
  return variant && listed && listed > variant.price_cents ? { listed, saved: listed - variant.price_cents } : null;
}
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
