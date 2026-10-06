import { useEffect, useRef, useState } from 'react';
import { ShopBrandImage } from '../shop/ShopBrandImage';
import { isClaudeNoticeProduct, ShopNotice } from '../shop/ShopNotice';
import { money, type Catalog } from '../shop/catalog';
import { findSku, type CartItem } from '../shop/cart';

function CatalogNotice({ catalog, error, onRetry }: { catalog: Catalog | null; error: boolean; onRetry: () => void }) {
  return error ? <p role="alert">商品暂时无法加载。<button type="button" className="btn btn-secondary" onClick={onRetry}>重试</button></p> : !catalog ? <p role="status">正在加载商品…</p> : catalog.status !== 'ready' ? <p role="status">商品信息正在等待更新，暂时无法购买，请稍后再试。</p> : null;
}
function Quantity({ value, onChange, label = '购买数量' }: { value: string; onChange: (value: string) => void; label?: string }) {
  const count = Number(value);
  return <div className="shop-quantity"><button type="button" aria-label={`减少${label}`} disabled={count <= 1} onClick={() => onChange(String(Math.max(1, count - 1)))}>−</button><input aria-label={label} inputMode="numeric" type="number" min="1" max="99" step="1" value={value} onChange={event => onChange(event.target.value)}/><button type="button" aria-label={`增加${label}`} disabled={count >= 99} onClick={() => onChange(String(Math.min(99, count + 1)))}>+</button></div>;
}
export function ProductDetail({ productId, catalog, error, onAdd, onBuy, onRetry }: { productId: string; catalog: Catalog | null; error: boolean; onAdd: (item: CartItem) => void; onBuy: (item: CartItem) => void; onRetry: () => void }) {
  const product = catalog?.products.find(item => item.id === productId);
  const [selection, setSelection] = useState<string | null>(null);
  const [quantity, setQuantity] = useState('1');
  const title = useRef<HTMLHeadingElement>(null);
  const variantId = selection ?? product?.variants.find(item => item.available)?.id ?? product?.variants[0]?.id;
  const variant = product?.variants.find(item => item.id === variantId);
  const count = /^\d{1,2}$/.test(quantity) ? Number(quantity) : 0;
  const validCount = count >= 1 && count <= 99;
  const valid = catalog?.status === 'ready' && !!variant?.available && validCount;
  useEffect(() => { if (product) title.current?.focus(); }, [product?.id]);
  return <>
    <nav className="shop-breadcrumb" aria-label="面包屑"><a href="/">首页</a><span>/</span><a href="/">商品目录</a><span>/</span><span>{product?.title ?? '商品详情'}</span></nav>
    <CatalogNotice catalog={catalog} error={error} onRetry={onRetry}/>
    {product ? <><section className="shop-detail portal-card">
      <div className="shop-detail-image"><ShopBrandImage category={product.category}/></div>
      <div className="shop-detail-info"><p className="portal-eyebrow">{product.category} · 人工交付</p><h1 ref={title} tabIndex={-1}>{product.title}</h1>
        <p className="shop-detail-price"><span>售价</span><strong>{variant ? money(variant.price_cents) : '—'}</strong><small>CNY / 件</small></p>
        <fieldset className="shop-specs"><legend>商品规格</legend>{product.variants.map(item => <button key={item.id} type="button" aria-pressed={variantId === item.id} onClick={() => setSelection(item.id)}>{item.label}{!item.available ? ' · 暂不可购买' : ''}</button>)}</fieldset>
        <div className="shop-detail-quantity"><span>购买数量</span><Quantity value={quantity} onChange={setQuantity}/><small>每次 1–99 件</small></div>
        {!validCount ? <p role="alert">请输入 1–99 的整数数量。</p> : null}
        <p className="shop-total">合计 <strong data-testid="purchase-total">{variant && validCount ? money(variant.price_cents * count) : '—'}</strong></p>
        <div className="shop-detail-actions"><button type="button" className="btn btn-secondary" disabled={!valid} onClick={() => variant && onAdd({ id: variant.id, quantity: count })}>加入购物车</button><button type="button" className="btn btn-primary" disabled={!valid || !catalog?.telegram} onClick={() => variant && onBuy({ id: variant.id, quantity: count })}>立即购买</button></div>
        {!catalog?.telegram ? <p className="shop-note">商家尚未配置联系方式，暂时无法联系购买。</p> : null}
        <p className="shop-note">购买信息需由商家人工确认。请在付款前确认账号条件、价格和交付方式。</p>
      </div>
    </section><section className="shop-detail-copy portal-card"><h2>商品说明</h2>{product.description && isClaudeNoticeProduct(product.id) ? <ShopNotice text={product.description} /> : <p>{product.description || '商家暂未填写商品说明，请联系商家了解详情。'}</p>}<h2>售后条款</h2>{product.after_sales && isClaudeNoticeProduct(product.id) ? <ShopNotice text={product.after_sales} /> : <p>{product.after_sales || '商家暂未填写售后条款，请在购买前与商家确认。'}</p>}</section></> : catalog ? <section className="shop-empty portal-card"><h1>商品不存在或已下架</h1><p>可以返回商品目录查看其他商品。</p><a className="btn btn-primary" href="/">返回商品目录</a></section> : null}
  </>;
}
export function CartView({ cart, catalog, error, onChange, onBuy, onRetry }: { cart: CartItem[]; catalog: Catalog | null; error: boolean; onChange: (cart: CartItem[]) => void; onBuy: () => void; onRetry: () => void }) {
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => { title.current?.focus(); }, []);
  const rows = cart.map(item => ({ item, row: findSku(catalog, item.id) }));
  const valid = catalog?.status === 'ready' && !!catalog.telegram && rows.length > 0 && rows.every(({ row }) => row?.variant.available);
  const total = rows.reduce((sum, { item, row }) => sum + (row?.variant.price_cents ?? 0) * item.quantity, 0);
  return <section className="shop-cart portal-card"><header><div><p className="portal-eyebrow">SHOPPING CART</p><h1 ref={title} tabIndex={-1}>购物车</h1></div>{cart.length ? <button type="button" className="btn btn-secondary" onClick={() => onChange([])}>清空购物车</button> : null}</header>
    <CatalogNotice catalog={catalog} error={error} onRetry={onRetry}/>
    {!cart.length ? <div className="shop-empty"><h2>购物车还是空的</h2><p>挑选商品后，点击加入购物车。</p><a className="btn btn-primary" href="/">去选购</a></div> : <>
      <div className="shop-cart-items">{rows.map(({ item, row }) => <article key={item.id} className="shop-cart-row"><ShopBrandImage category={row?.product.category ?? 'GPT'}/><div><h2>{row ? <a href={`/?product=${row.product.id}`}>{row.product.title}</a> : '商品已下架或暂不可用'}</h2><p>{row?.variant.label ?? `规格 ${item.id}`}</p><p>{row ? money(row.variant.price_cents) : '—'}{row && !row.variant.available ? ' · 暂不可购买' : ''}</p></div><Quantity label={`数量 ${item.id}`} value={String(item.quantity)} onChange={value => { if (/^\d{1,2}$/.test(value) && Number(value) >= 1) onChange(cart.map(current => current.id === item.id ? { ...current, quantity: Number(value) } : current)); }}/><strong>{row ? money(row.variant.price_cents * item.quantity) : '—'}</strong><button type="button" className="btn btn-secondary" aria-label={`移除 ${item.id}`} onClick={() => onChange(cart.filter(current => current.id !== item.id))}>移除</button></article>)}</div>
      <footer className="shop-cart-footer"><p>合计 <strong data-testid="cart-total">{catalog && rows.every(({ row }) => row) ? money(total) : '—'}</strong></p><button type="button" className="btn btn-primary" disabled={!valid} onClick={onBuy}>确认购买信息</button></footer>
      <p className="shop-note">结算前会重新核对售价和库存，最终价格及交付由商家人工确认。</p>
      {catalog && !catalog.telegram ? <p className="shop-note">商家尚未配置联系方式，暂时无法联系购买。</p> : null}
    </>}
  </section>;
}
