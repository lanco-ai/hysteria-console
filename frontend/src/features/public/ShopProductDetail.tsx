import { useEffect, useRef, useState, type MouseEvent } from 'react';
import { closeShopProduct } from '../../shared/shopNavigation';
import { ShopBrandImage } from '../shop/ShopBrandImage';
import { isNoticeProduct, ShopCopyText, ShopNotice } from '../shop/ShopNotice';
import { hasContact, money, promoCents, promoName, type Catalog, type Product } from '../shop/catalog';
import { ProductLabels, ProductStats, PromoBadge, PromoPrice, productHeading } from '../shop/ShopMerch';
import { cartPromo, findSku, type CartItem } from '../shop/cart';
import { PLUS_REFERRAL_ANCHOR, PlusReferralDetails, isPlusReferralVariant, plusReferralVariant } from '../shop/PlusReferral';

function CatalogNotice({ catalog, error, onRetry }: { catalog: Catalog | null; error: boolean; onRetry: () => void }) {
  return error ? <p role="alert">商品暂时无法加载。<button type="button" className="btn btn-secondary" onClick={onRetry}>重试</button></p> : !catalog ? <p role="status">正在加载商品…</p> : catalog.status !== 'ready' ? <p role="status">商品信息正在等待更新，暂时无法购买，请稍后再试。</p> : null;
}
function Quantity({ value, onChange, label = '购买数量' }: { value: string; onChange: (value: string) => void; label?: string }) {
  const count = Number(value);
  return <div className="shop-quantity"><button type="button" aria-label={`减少${label}`} disabled={count <= 1} onClick={() => onChange(String(Math.max(1, count - 1)))}>−</button><input aria-label={label} inputMode="numeric" type="number" min="1" max="99" step="1" value={value} onChange={event => onChange(event.target.value)}/><button type="button" aria-label={`增加${label}`} disabled={count >= 99} onClick={() => onChange(String(Math.min(99, count + 1)))}>+</button></div>;
}
/** Plan names within one brand: "Plus" and "Pro 200" rather than "ChatGPT Plus". */
function planNames(products: Product[]): Map<string, string> {
  const words = products.map(item => item.title.split(/\s+/));
  const first = words[0] ?? [];
  let shared = 0;
  // Only a shared leading word that leaves every plan a name of its own.
  while (words.length > 1 && words.every(parts => parts.length > shared + 1 && parts[shared] === first[shared])) shared += 1;
  return new Map(products.map((item, index) => [item.id, (words[index] ?? []).slice(shared).join(' ') || item.title]));
}

function backToCatalog(event: MouseEvent<HTMLAnchorElement>): void {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
  event.preventDefault();
  closeShopProduct();
}

// A product's own page. The catalog card grows into its header (see
// shopTransition.ts); the other plans of the brand are one tap away.
export function ProductPage({ productId, catalog, error, notice, cartCount, onAdd, onBuy, onRetry }: { productId: string; catalog: Catalog | null; error: boolean; notice: string; cartCount: number; onAdd: (item: CartItem) => void; onBuy: (item: CartItem) => void; onRetry: () => void }) {
  const product = catalog?.products.find(item => item.id === productId);
  const [shown, setShown] = useState(productId);
  const [selection, setSelection] = useState<string | null>(null);
  const [quantity, setQuantity] = useState('1');
  // Another plan starts from its own default variant and one item.
  if (shown !== productId) { setShown(productId); setSelection(null); setQuantity('1'); }
  const page = useRef<HTMLDivElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const referral = useRef<HTMLElement>(null);
  const fromReferral = window.location.hash === `#${PLUS_REFERRAL_ANCHOR}`;
  const variantId = selection ?? (fromReferral ? plusReferralVariant(product)?.id : undefined) ?? product?.variants.find(item => item.available)?.id ?? product?.variants[0]?.id;
  const variant = product?.variants.find(item => item.id === variantId);
  const count = /^\d{1,2}$/.test(quantity) ? Number(quantity) : 0;
  const validCount = count >= 1 && count <= 99;
  const valid = catalog?.status === 'ready' && !!variant?.available && validCount;
  const promo = promoCents(variant);
  const plans = product ? catalog?.products.filter(item => item.category === product.category) ?? [] : [];
  const names = planNames(plans);
  const position = plans.findIndex(item => item.id === product?.id);
  useEffect(() => {
    if (!product) return;
    // Keep focus on a plan the visitor just picked; otherwise start at the product.
    const active = document.activeElement;
    if (active instanceof HTMLElement && active !== document.body && page.current?.contains(active)) return;
    const target = fromReferral && referral.current ? referral.current : title.current;
    target?.focus({ preventScroll: true });
    if (target === referral.current) target?.scrollIntoView({ block: 'start', behavior: 'instant' });
  }, [product?.id, fromReferral]);
  return <div className="shop-product-page" ref={page}>
    <nav className="shop-detail-topbar" aria-label="商品页">
      <a className="shop-detail-back" href="/" onClick={backToCatalog}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>商品目录</a>
      <a className="shop-detail-cart" href="/?view=cart">购物车（{cartCount}）</a>
    </nav>
    <CatalogNotice catalog={catalog} error={error} onRetry={onRetry}/>
    {product ? <article className="shop-detail" aria-labelledby="shop-detail-title">
      <header className="shop-detail-hero" data-shop-hero>
        <div className="shop-detail-image" data-shop-logo><ShopBrandImage category={product.category}/></div>
        <div className="shop-detail-intro">
          <p className="portal-eyebrow">{product.category} · 人工交付</p>
          <div className="shop-detail-heading" data-shop-title><h1 id="shop-detail-title" ref={title} tabIndex={-1} aria-label={product.title}><PromoBadge text={product.badge}/>{productHeading(product)}</h1><ProductLabels product={product}/></div>
          <ProductStats product={product}/>
          {product.category === '短信接码' ? <p className="shop-note">已有激活码？<a href="/?view=sms">进入短信接码</a></p> : null}
        </div>
      </header>
      {plans.length > 1 ? <nav className="shop-plans" aria-label={`${product.category} 其他商品`}>{plans.map((item, index) => <a key={item.id} href={`/?product=${encodeURIComponent(item.id)}`} aria-current={item.id === product.id ? 'page' : undefined} data-shop-step={index < position ? 'previous' : 'next'}>{names.get(item.id)}</a>)}</nav> : null}
      <section className="shop-detail-buy" aria-label="购买选项">
        <div className="shop-detail-price"><p><span>售价</span><strong>{variant ? money(variant.price_cents) : '—'}</strong><small>CNY / 件</small></p>{variant ? <PromoPrice product={product} variant={variant}/> : null}{variant?.available && variant.quantity ? <span className="shop-detail-stock">现货 {variant.quantity} 件</span> : null}</div>
        <fieldset className="shop-specs"><legend>商品规格</legend>{product.variants.map(item => <button key={item.id} type="button" aria-pressed={variantId === item.id} onClick={() => setSelection(item.id)}>{item.label}{!item.available ? ' · 暂不可购买' : ''}</button>)}</fieldset>
        <div className="shop-detail-quantity"><span>购买数量</span><Quantity value={quantity} onChange={setQuantity}/><small>每次 1–99 件</small></div>
        {!validCount ? <p role="alert">请输入 1–99 的整数数量。</p> : null}
        <div className="shop-detail-buybar">
          <p className="shop-detail-feedback" role="status">{notice}</p>
          <p className="shop-total">合计 <strong data-testid="purchase-total">{variant && validCount ? money(variant.price_cents * count) : '—'}</strong>{promo && validCount ? <span className="shop-promo-total">{promoName(product)}合计 {money(promo * count)}</span> : null}</p>
          <div className="shop-detail-actions"><button type="button" className="btn btn-secondary" disabled={!valid} onClick={() => variant && onAdd({ id: variant.id, quantity: count })}>加入购物车</button><button type="button" className="btn btn-primary" disabled={!valid || !hasContact(catalog)} onClick={() => variant && onBuy({ id: variant.id, quantity: count })}>立即购买</button></div>
          {!hasContact(catalog) ? <p className="shop-note">商家尚未配置联系方式，暂时无法联系购买。</p> : null}
        </div>
        <p className="shop-note">购买信息需由商家人工确认。请在付款前确认账号条件、价格和交付方式。</p>
      </section>
      {catalog?.status === 'ready' && isPlusReferralVariant(product, variant) ? <PlusReferralDetails sectionRef={referral}/> : null}
      <section className="shop-detail-copy" data-product={product.id}><h2>商品说明</h2>{product.description && isNoticeProduct(product.id) ? <ShopNotice text={product.description} /> : (product.description ? <ShopCopyText text={product.description} /> : <p>商家暂未填写商品说明，请联系商家了解详情。</p>)}<h2>售后条款</h2>{product.after_sales && isNoticeProduct(product.id) ? <ShopNotice text={product.after_sales} /> : (product.after_sales ? <ShopCopyText text={product.after_sales} /> : <p>商家暂未填写售后条款，请在购买前与商家确认。</p>)}</section>
    </article> : catalog ? <section className="shop-empty portal-card"><h1>商品不存在或已下架</h1><p>可以返回商品目录查看其他商品。</p><button className="btn btn-primary" type="button" onClick={closeShopProduct}>返回商品目录</button></section> : null}
  </div>;
}
export function CartView({ cart, catalog, error, onChange, onBuy, onRetry }: { cart: CartItem[]; catalog: Catalog | null; error: boolean; onChange: (cart: CartItem[]) => void; onBuy: () => void; onRetry: () => void }) {
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => { title.current?.focus(); }, []);
  const rows = cart.map(item => ({ item, row: findSku(catalog, item.id) }));
  const valid = catalog?.status === 'ready' && hasContact(catalog) && rows.length > 0 && rows.every(({ row }) => row?.variant.available);
  const total = rows.reduce((sum, { item, row }) => sum + (row?.variant.price_cents ?? 0) * item.quantity, 0);
  const promo = catalog && rows.every(({ row }) => row) ? cartPromo(catalog, cart) : null;
  return <section className="shop-cart portal-card"><header><div><p className="portal-eyebrow">SHOPPING CART</p><h1 ref={title} tabIndex={-1}>购物车</h1></div>{cart.length ? <button type="button" className="btn btn-secondary" onClick={() => onChange([])}>清空购物车</button> : null}</header>
    <CatalogNotice catalog={catalog} error={error} onRetry={onRetry}/>
    {!cart.length ? <div className="shop-empty"><h2>购物车还是空的</h2><p>挑选商品后，点击加入购物车。</p><a className="btn btn-primary" href="/">去选购</a></div> : <>
      <div className="shop-cart-items">{rows.map(({ item, row }) => <article key={item.id} className="shop-cart-row"><ShopBrandImage category={row?.product.category ?? 'GPT'}/><div><h2>{row ? <a href={`/?product=${row.product.id}`}>{row.product.title}</a> : '商品已下架或暂不可用'}</h2><p>{row?.variant.label ?? `规格 ${item.id}`}</p><p>{row ? money(row.variant.price_cents) : '—'}{row && !row.variant.available ? ' · 暂不可购买' : ''}</p>{row ? <PromoPrice product={row.product} variant={row.variant}/> : null}</div><Quantity label={`数量 ${item.id}`} value={String(item.quantity)} onChange={value => { if (/^\d{1,2}$/.test(value) && Number(value) >= 1) onChange(cart.map(current => current.id === item.id ? { ...current, quantity: Number(value) } : current)); }}/><strong>{row ? money(row.variant.price_cents * item.quantity) : '—'}</strong><button type="button" className="btn btn-secondary" aria-label={`移除 ${item.id}`} onClick={() => onChange(cart.filter(current => current.id !== item.id))}>移除</button></article>)}</div>
      <footer className="shop-cart-footer"><p>合计 <strong data-testid="cart-total">{catalog && rows.every(({ row }) => row) ? money(total) : '—'}</strong>{promo ? <span className="shop-promo-total">{promo.name}合计 {money(promo.total)}</span> : null}</p><button type="button" className="btn btn-primary" disabled={!valid} onClick={onBuy}>确认购买信息</button></footer>
      <p className="shop-note">{promo ? `${promo.name}需符合条件，以商家确认为准。` : ''}结算前会重新核对售价和库存，最终价格及交付由商家人工确认。</p>
      {catalog && !hasContact(catalog) ? <p className="shop-note">商家尚未配置联系方式，暂时无法联系购买。</p> : null}
    </>}
  </section>;
}
