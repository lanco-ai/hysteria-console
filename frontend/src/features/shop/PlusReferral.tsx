import type { Ref } from 'react';
import { promoCents, type Product, type Variant } from './catalog';

export const PLUS_REFERRAL_ANCHOR = 'plus-referral';

// This campaign belongs to the merchant's ¥118 monthly Plus SKU. Do not
// advertise it for other plans, unavailable offers, or a later changed price.
export function isPlusReferralVariant(product: Product | undefined, variant: Variant | undefined): boolean {
  return product?.id === '1' && product.title === 'ChatGPT Plus' && variant?.id === '1:1'
    && variant.available && (promoCents(variant) ?? variant.price_cents) === 11800;
}

export function plusReferralVariant(product: Product | undefined): Variant | undefined {
  return product?.variants.find(variant => isPlusReferralVariant(product, variant));
}

function GiftIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 11h16v10H4zM3 7h18v4H3zM12 7v14"/><path d="M12 7H8a3 3 0 1 1 3-3l1 3Zm0 0h4a3 3 0 1 0-3-3l-1 3Z"/></svg>;
}

export function PlusReferralLink({ product, banner = false }: { product: Product; banner?: boolean }) {
  return <a className={`shop-referral-link ${banner ? 'shop-referral-banner' : 'shop-referral-teaser'}`} href={`/?product=${encodeURIComponent(product.id)}#${PLUS_REFERRAL_ANCHOR}`}>
    <span className="shop-referral-icon"><GiftIcon/></span>
    <span className="shop-referral-link-copy">
      <strong>{banner ? 'Plus 好友推荐优惠' : '推荐好友，续费最低 ¥113/月'}</strong>
      <span>{banner ? '每推荐 1 人购买，续费减 ¥1，最低 ¥113/月' : '每推荐 1 人购买，后续每月续费减 ¥1'}</span>
    </span>
    <span className="shop-referral-link-action">{banner ? '了解活动' : '查看优惠'} <span aria-hidden="true">↗</span></span>
  </a>;
}

export function PlusReferralDetails({ sectionRef }: { sectionRef: Ref<HTMLElement> }) {
  return <section id={PLUS_REFERRAL_ANCHOR} className="shop-referral-details" aria-labelledby="plus-referral-title" ref={sectionRef} tabIndex={-1}>
    <div className="shop-referral-heading"><span className="shop-referral-icon"><GiftIcon/></span><div><p>PLUS 好友推荐优惠</p><h2 id="plus-referral-title">推荐好友，续费更优惠</h2></div><span className="shop-referral-base">月订阅 ¥118</span></div>
    <ul>
      <li>每成功推荐 1 人购买，后续每月续费价格累计降低 <strong>¥1</strong>，最低 <strong>¥113/月</strong>。</li>
    </ul>
    <div className="shop-referral-examples">
      <div><span>推荐 1 人</span><strong>¥117<small>/月</small></strong></div>
      <div><span>推荐 5 人</span><strong>¥113<small>/月</small></strong></div>
    </div>
    <p className="shop-referral-note">购买时请告知商家推荐关系，续费优惠由商家确认。</p>
  </section>;
}
