import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { ShopBrandImage } from '../shop/ShopBrandImage';
import { PortalShell, ShopIcon } from './PortalShell';
import { countText, money, reportVisit, shopRequest, type Catalog, type Product } from '../shop/catalog';
import { ProductStats, PromoBadge, PromoPrice, WarrantyBadge, WechatContact } from '../shop/ShopMerch';
import { CART_KEY, MAX_CART_ITEMS, readCart, type CartItem } from '../shop/cart';
import { restoreScrollPosition } from '../../shared/shopNavigation';
import { PurchaseDialog } from './ShopPurchase';
import { ProductPage, CartView } from './ShopProductDetail';
import { PlusReferralLink, plusReferralVariant } from '../shop/PlusReferral';

const CATEGORY_ORDER = ['GPT', 'Claude', 'Grok', 'Gemini', 'X', 'Muse AI', '短信接码'];

function ProductRow({ product, catalogReady, onAdd }: { product: Product; catalogReady: boolean; onAdd: (item: CartItem) => void }) {
  const prices = product.variants.map(item => item.price_cents);
  const minimum = Math.min(...prices), maximum = Math.max(...prices);
  const available = catalogReady && product.variants.some(item => item.available);
  const single = product.variants.length === 1 ? product.variants[0] : null;
  const cartIcon = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 3h2l2.5 12h11l2-8H6"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></svg>;
  // A card: the brand, the title with its promotion label, then the price and its 优惠价 below it.
  // The title link covers the whole card; the card grows into the product page's header.
  return <article className="shop-product-row" data-shop-card={product.id}>
    <div className="shop-product-name"><span className="shop-product-thumbnail" data-shop-logo><ShopBrandImage category={product.category}/></span><div><div className="shop-product-title" data-shop-title><PromoBadge text={product.badge}/><h3><a href={`/?product=${product.id}`}>{product.title}</a></h3><WarrantyBadge product={product}/></div><div className="shop-product-tags">{product.variants.length > 1 ? <span className="shop-product-variants">{product.variants.length} 种规格</span> : null}<span className={`shop-product-availability${available ? ' is-available' : ''}`}>{available ? '可咨询购买' : '暂不可购买'}</span><ProductStats product={product}/></div></div></div>
    <div className="shop-product-price"><strong>{money(minimum).slice(1)}{maximum !== minimum ? `–${money(maximum).slice(1)}` : ''} <span>CNY</span></strong><PromoPrice product={product}/></div>
    <div className="shop-product-action">{available && !single ? <a className="shop-product-cart" href={`/?product=${product.id}`} aria-label={`选择 ${product.title} 规格`}>{cartIcon}</a> : <button className="shop-product-cart" type="button" disabled={!available} aria-label={available ? `将 ${product.title} 加入购物车` : `${product.title} 暂不可购买`} onClick={() => { if (catalogReady && single?.available) onAdd({ id: single.id, quantity: 1 }); }}>{cartIcon}</button>}<a className="shop-product-detail-link" href={`/?product=${product.id}`} aria-label={`${product.title} 商品详情`}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m9 5 7 7-7 7"/></svg></a></div>
    {catalogReady && plusReferralVariant(product) ? <PlusReferralLink product={product}/> : null}
  </article>;
}

export function ShopPage() {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部商品');
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<CartItem[] | null>(null);
  const [cart, setCart] = useState<CartItem[]>(readCart);
  const [notice, setNotice] = useState('');
  const query = new URLSearchParams(window.location.search);
  const isCart = query.get('view') === 'cart';
  const productId = isCart ? null : query.get('product') || null;
  const routeKey = window.location.search;
  useEffect(() => { setSelected(null); setNotice(''); }, [routeKey]);
  const requestId = useRef(0);
  const load = useCallback(async () => {
    const id = ++requestId.current;
    try {
      const value = await shopRequest<Catalog>('catalog');
      if (id === requestId.current) { setCatalog(value); setError(false); }
      return value;
    } catch {
      if (id === requestId.current) { setError(true); setCatalog(null); setSelected(null); }
      return null;
    }
  }, []);
  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => { clearInterval(timer); requestId.current += 1; };
  }, [load]);
  // One page view for the shop, and for the product when one is open.
  useEffect(() => { reportVisit(productId); }, [productId]);
  // Reloads and history returns from SMS wait until products give the page height.
  useLayoutEffect(() => {
    if (catalog || error) restoreScrollPosition();
  }, [catalog, error, routeKey]);
  function updateCart(next: CartItem[], success = '购物车已更新。') {
    setCart(next); setSelected(null);
    try { localStorage.setItem(CART_KEY, JSON.stringify(next)); setNotice(success); }
    catch { setNotice('浏览器无法保存购物车，切换页面后可能丢失。'); }
  }
  function add(item: CartItem) {
    const existing = cart.find(row => row.id === item.id);
    if ((!existing && cart.length >= MAX_CART_ITEMS) || (existing && existing.quantity + item.quantity > 99)) { setNotice('购物车最多 50 个规格，每个规格最多 99 件。'); return; }
    updateCart(existing ? cart.map(row => row.id === item.id ? { ...row, quantity: row.quantity + item.quantity } : row) : [...cart, item], '已加入购物车。');
  }
  const allProducts = catalog?.products ?? [];
  const referralProduct = catalog?.status === 'ready' ? allProducts.find(product => plusReferralVariant(product)) : undefined;
  const cartCount = cart.reduce((sum, item) => sum + item.quantity, 0);
  const categories = [...new Set(allProducts.map(product => product.category))].sort((a, b) => CATEGORY_ORDER.indexOf(a) - CATEGORY_ORDER.indexOf(b));
  const products = allProducts.filter(product => (category === '全部商品' || product.category === category) && `${product.title} ${product.variants.map(item => item.label).join(' ')}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <PortalShell active="shop" pageTitle="购物">
    {isCart ? <>
      <div className="shop-toolbar"><a href="/">商品目录</a><div className="shop-toolbar-end"><WechatContact wechat={catalog?.wechat}/><a className="btn btn-secondary" href="/?view=cart">购物车（{cartCount}）</a></div></div>
      <p className="shop-feedback" role="status">{notice}</p>
      <CartView cart={cart} catalog={catalog} error={error} onChange={updateCart} onBuy={() => setSelected(cart)} onRetry={() => void load()}/>
    </> : null}
    {productId !== null ? <ProductPage productId={productId} catalog={catalog} error={error} notice={notice} cartCount={cartCount} onAdd={add} onBuy={item => setSelected([item])} onRetry={() => void load()}/> : null}
    {/* The catalog stays mounted under product and cart pages, so it comes back exactly as it was left. */}
    <div className="shop-storefront" hidden={isCart || productId !== null}>
    <header className="shop-hero">
      <div className="shop-hero-text">
        <h1 id="shop-catalog-title" tabIndex={-1}>商品目录</h1>
        <p>AI 会员订阅 · 商家人工交付 · 确认后再付款</p>
        {catalog?.stats && (catalog.stats.views || catalog.stats.sold) ? <p className="shop-store-stats">{[catalog.stats.views ? `累计浏览 ${countText(catalog.stats.views)} 人次` : '', catalog.stats.sold ? `累计售出 ${countText(catalog.stats.sold)} 件` : ''].filter(Boolean).join(' · ')}</p> : null}
      </div>
      <div className="shop-hero-actions"><WechatContact wechat={catalog?.wechat}/><a className="btn btn-secondary" href="/?view=cart">购物车（{cartCount}）</a></div>
      {referralProduct ? <PlusReferralLink product={referralProduct} banner/> : null}
    </header>
    <p className="shop-feedback" role="status">{notice}</p>
    <div className="shop-layout">
      <section className="shop-catalog" aria-labelledby="shop-catalog-title">
        <div className="shop-filterbar">
          <div className="shop-category-list" role="group" aria-label="商品分类">{['全部商品', ...categories].map(name => <button key={name} type="button" aria-pressed={category === name} onClick={() => setCategory(name)}><span>{name === '全部商品' ? '全部' : name}</span><span className="shop-count">{name === '全部商品' ? allProducts.length : allProducts.filter(product => product.category === name).length}</span></button>)}</div>
          <form className="shop-search" role="search" onSubmit={event => event.preventDefault()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
            <input type="search" aria-label="搜索商品" placeholder="搜索商品" value={search} onChange={event => setSearch(event.target.value)}/>
          </form>
        </div>
        {error ? <p className="shop-notice" role="alert">商品暂时无法加载。<button type="button" className="btn btn-secondary" onClick={() => void load()}>重试</button></p> : null}
        {catalog?.status === 'unavailable' ? <p className="shop-notice" role="status">商品信息暂不可用，请稍后再试。</p> : null}
        {catalog?.status === 'stale' ? <p className="shop-notice" role="status">商品信息正在等待更新，暂时无法购买，请稍后再试。</p> : null}
        <div className="shop-catalog-body">
          {products.length ? <>{categories.map(name => { const group = products.filter(product => product.category === name); return group.length ? <section className="shop-category-group" key={name}>{category === '全部商品' ? <h2 className="shop-group-heading">{name} 商品<span>（{group.length}）</span></h2> : null}<div className="shop-product-list">{group.map(product => <ProductRow key={product.id} product={product} catalogReady={catalog?.status === 'ready'} onAdd={add}/>)}</div></section> : null; })}</> : <div className="shop-empty" role="status"><span className="shop-empty-icon"><ShopIcon/></span><h2>{!catalog && !error ? '正在加载商品' : search.trim() ? '没有匹配的商品' : '暂无商品'}</h2><p>{search.trim() ? '试试其他关键词，或清除搜索。' : '商品上架后会显示在这里。'}</p>{search.trim() ? <button className="btn btn-secondary" type="button" onClick={() => setSearch('')}>清除搜索</button> : null}</div>}
        </div>
      </section>
    </div>
    </div>
    {selected ? <PurchaseDialog catalogVersion={catalog} items={selected} onClose={() => setSelected(null)}/> : null}
  </PortalShell>;
}
