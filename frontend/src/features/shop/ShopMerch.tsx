import { useState, type ReactNode } from 'react';
import { countText, money, promoCents, promoName, type Product, type Variant } from './catalog';

const WARRANTY_PRODUCTS = new Set(['ChatGPT Plus', 'ChatGPT Go', 'ChatGPT Pro 100', 'ChatGPT Pro 200', 'ChatGPT Pro 500']);
const REGION_SUFFIX = /｜(菲律宾区|埃及区|智利区|卡付充值)$/;

/** Show the source region as a separate label while retaining it in purchase information. */
export function productHeading(product: Product): string {
  return product.category === 'GPT' ? product.title.replace(REGION_SUFFIX, '') : product.title;
}

/** Region, warranty and end date; a card adds its variant count after them. */
export function ProductLabels({ product, children }: { product: Product; children?: ReactNode }) {
  const region = product.category === 'GPT' ? product.title.match(REGION_SUFFIX)?.[1] : undefined;
  const warranty = product.category === 'GPT' && WARRANTY_PRODUCTS.has(productHeading(product));
  if (!region && !warranty && !product.ends_at && !children) return null;
  return <div className="shop-product-labels">{region ? <span className="shop-region-badge">{region}</span> : null}<WarrantyBadge product={product}/><EndsBadge product={product}/>{children}</div>;
}

/** The merchant's 30-day warranty applies to these five GPT subscription plans. */
export function WarrantyBadge({ product }: { product: Product }) {
  return product.category === 'GPT' && WARRANTY_PRODUCTS.has(product.title.replace(/｜.*$/, ''))
    ? <span className="shop-warranty-badge">质保30天</span> : null;
}

/** A limited-time service's last day, in the shop's (Beijing) time: "10月31日截止". */
export function EndsBadge({ product }: { product: Product }) {
  if (!product.ends_at) return null;
  const day = new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', month: 'long', day: 'numeric' }).format(new Date(product.ends_at * 1000));
  return <span className="shop-ends-badge">{day}截止</span>;
}

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
  const views = product.views ? `${countText(product.views)} 人看过` : '';
  const sold = product.sold ? `已售 ${countText(product.sold)} 件` : '';
  return views || sold ? <span className="shop-product-stats">{views ? <span>{views}</span> : null}{views && sold ? <span className="shop-product-stats-separator"> · </span> : null}{sold ? <span className="shop-product-sold">{sold}</span> : null}</span> : null;
}

/** The merchant's WeChat ID with a copy button. */
export function WechatContact({ wechat }: { wechat?: string | undefined }) {
  const [copied, setCopied] = useState(false);
  if (!wechat) return null;
  return <span className="shop-contact"><span>商家微信</span><strong>{wechat}</strong><button type="button" className="btn btn-secondary btn-sm" onClick={() => void navigator.clipboard?.writeText(wechat).then(() => setCopied(true), () => setCopied(false))}>{copied ? '已复制' : '复制'}</button></span>;
}
