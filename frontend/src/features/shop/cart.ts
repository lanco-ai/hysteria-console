import { money, promoCents, promoName, type Catalog } from './catalog';

export type CartItem = { id: string; quantity: number };
export const CART_KEY = 'hysteria.shop.cart.v1';
export const MAX_CART_ITEMS = 50;
export function cleanCart(value: unknown): CartItem[] {
  if (!Array.isArray(value)) return [];
  const rows = new Map<string, number>();
  for (const item of value.slice(0, 500)) {
    if (!item || typeof item !== 'object' || typeof item.id !== 'string' || !/^[1-9]\d{0,15}:[1-9]\d{0,15}$/.test(item.id) || !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 99) continue;
    if (!rows.has(item.id) && rows.size >= MAX_CART_ITEMS) continue;
    rows.set(item.id, Math.min(99, (rows.get(item.id) ?? 0) + item.quantity));
  }
  return Array.from(rows, ([id, quantity]) => ({ id, quantity }));
}
export function readCart(): CartItem[] {
  try { const raw = localStorage.getItem(CART_KEY); return raw && raw.length <= 50000 ? cleanCart(JSON.parse(raw)) : []; } catch { return []; }
}
export function findSku(catalog: Catalog | null, id: string) {
  for (const product of catalog?.products ?? []) {
    const variant = product.variants.find(item => item.id === id);
    if (variant) return { product, variant };
  }
  return null;
}
/** The total at 优惠价 (学生价…), where items have one; null when none has. */
export function cartPromo(catalog: Catalog, items: CartItem[]): { name: string; total: number } | null {
  const names = new Set<string>();
  let total = 0;
  for (const item of items) {
    const row = findSku(catalog, item.id);
    if (!row) return null;
    const promo = promoCents(row.variant);
    if (promo) names.add(promoName(row.product));
    total += (promo ?? row.variant.price_cents) * item.quantity;
  }
  return names.size ? { name: names.size === 1 ? [...names][0]! : '优惠价', total } : null;
}
export function purchaseSummary(catalog: Catalog, items: CartItem[]): string {
  if (catalog.status !== 'ready') throw new Error('商品信息正在等待更新，暂时无法购买。');
  if (!catalog.telegram && !catalog.wechat) throw new Error('商家尚未配置联系方式，暂时无法联系购买。');
  if (!items.length || items.length > MAX_CART_ITEMS) throw new Error('购物车为空，请先选择商品。');
  let total = 0;
  const lines = items.map(item => {
    const row = findSku(catalog, item.id);
    if (!row?.variant.available || !Number.isInteger(item.quantity) || item.quantity < 1 || item.quantity > 99) throw new Error('商品已下架、售罄或数量无效，请返回检查商品。');
    const subtotal = row.variant.price_cents * item.quantity;
    const promo = promoCents(row.variant);
    total += subtotal;
    return `${row.product.title} · ${row.variant.label}\n数量：${item.quantity} · 单价：${money(row.variant.price_cents)}${promo ? `（${promoName(row.product)} ${money(promo)}）` : ''} · 小计：${money(subtotal)}`;
  });
  const promo = cartPromo(catalog, items);
  return `购买信息（待商家人工确认）\n${lines.join('\n\n')}\n\n合计：${money(total)} CNY${promo ? `\n${promo.name}合计：${money(promo.total)} CNY（需符合条件，以商家确认为准）` : ''}\n请确认账号适用条件、最终价格、付款和交付方式。`;
}
