import { useState } from 'react';
import { countText, money, promoCents, promoName, type Product, type Variant } from './catalog';

/** A promotion label such as 学生价, shown before the title. */
export function PromoBadge({ text }: { text?: string | undefined }) {
  return text ? <span className="shop-promo-badge">{text}</span> : null;
}

/** A selected variant's offer, or the lowest available offer on a product card ("起" when they differ). */
export function PromoPrice({ product, variant }: { product: Product; variant?: Variant | undefined }) {
  const prices = (variant ? [variant] : product.variants.filter(item => item.available)).map(promoCents).filter((value): value is number => value !== null);
  if (!prices.length) return null;
  return <span className="shop-promo-price"><span>{promoName(product)}：</span><strong>{money(Math.min(...prices))}</strong>{new Set(prices).size > 1 ? <small>起</small> : null}</span>;
}

/** "128 人看过 · 已售 36 件", leaving out what is still zero. */
export function ProductStats({ product }: { product: Product }) {
  const parts = [product.views ? `${countText(product.views)} 人看过` : '', product.sold ? `已售 ${countText(product.sold)} 件` : ''].filter(Boolean);
  return parts.length ? <span className="shop-product-stats">{parts.join(' · ')}</span> : null;
}

/** The merchant's WeChat ID with a copy button. */
export function WechatContact({ wechat }: { wechat?: string | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!wechat) return null;
  return <span className="shop-contact"><span>商家微信</span><strong>{wechat}</strong><button type="button" className="btn btn-secondary btn-sm" onClick={() => void navigator.clipboard?.writeText(wechat).then(() => setCopied(true), () => setCopied(false))}>{copied ? '已复制' : '复制'}</button></span>;
}
