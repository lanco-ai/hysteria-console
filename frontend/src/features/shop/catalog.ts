export type Variant = { id: string; label: string; price_cents: number; available: boolean; quantity: null; sales: null };
export type Product = { id: string; title: string; category: string; description?: string; after_sales?: string; variants: Variant[] };
export type Catalog = { currency: 'CNY'; telegram: string; products: Product[]; status: 'ready' | 'stale' | 'unavailable'; updated_at: number | null };
export function money(cents: number): string { return `¥${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`; }
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
