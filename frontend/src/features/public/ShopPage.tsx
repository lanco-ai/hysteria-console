import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type RefObject } from 'react';
import { ShopBrandImage } from '../shop/ShopBrandImage';
import { PortalShell, ShopIcon } from './PortalShell';
import { countText, isNew, lowestOffer, money, reportVisit, shopRequest, type Catalog, type Product } from '../shop/catalog';
import { NewBadge, ProductLabels, ProductStats, PromoBadge, ShopAnnouncement, WechatContact, productHeading } from '../shop/ShopMerch';
import { CART_KEY, MAX_CART_ITEMS, readCart, type CartItem } from '../shop/cart';
import { restoreScrollPosition } from '../../shared/shopNavigation';
import { PurchaseDialog } from './ShopPurchase';
import { ProductPage, CartView } from './ShopProductDetail';
import { PlusReferralLink, plusReferralVariant } from '../shop/PlusReferral';

const CATEGORY_ORDER = ['GPT', 'Claude', 'Grok', 'Gemini', 'X', 'Muse AI', '多邻国', '短信接码', 'VPN'];
// A filter beside the categories: the products the merchant marked 新品.
const NEW_FILTER = '新品';
// These groups keep their own rows and separate the shared rows on either side.
const SEPARATE_ROW_CATEGORIES = new Set(['Grok', '短信接码', 'VPN']);

// Catalog cards are at least 300px wide and 14px apart. A row may widen into the page margins, stopping 40px
// short of the window's edges, while its cards stay at least 280px wide.
const CARD_MIN = 300, CARD_GAP = 14, WIDE_CARD_MIN = 280, WIDE_GUTTER = 40;
type Brand = { name: string; products: Product[] };
type Space = { width: number; room: number };
type Shelf = { brands: Brand[]; columns: number; width: number | null };

/** The catalog's width, and how wide a row may grow into the page margins. Measured on the storefront's parent,
 * which keeps its width while a product page hides the storefront, so the catalog comes back laid out as it was. */
function useShelfSpace(storefront: RefObject<HTMLDivElement | null>): Space | null {
  const [space, setSpace] = useState<Space | null>(null);
  useLayoutEffect(() => {
    const parent = storefront.current?.parentElement;
    if (!parent) return;
    const measure = () => {
      const style = getComputedStyle(parent);
      const start = parseFloat(style.paddingLeft);
      const width = parent.clientWidth - start - parseFloat(style.paddingRight);
      const left = parent.getBoundingClientRect().left + parent.clientLeft + start;
      const margin = Math.max(0, Math.min(left, document.documentElement.clientWidth - left - width) - WIDE_GUTTER);
      setSpace(old => old && old.width === width && old.room === width + 2 * margin ? old : { width, room: width + 2 * margin });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(parent);
    window.addEventListener('resize', measure);
    return () => { observer.disconnect(); window.removeEventListener('resize', measure); };
  }, [storefront]);
  return space;
}

/** Brands in rows ("shelves") of the catalog's columns, a column per card. A brand with a row of cards or more
 * takes whole rows. Smaller ones share a row in order; when the next one needs more columns than are left and the
 * row can widen into the page margins to take it, the row widens, its cards as
 * wide as the others where there is room, rather than splitting the brand. Otherwise a later brand that fits
 * fills the gap. Dedicated groups keep their own rows and their place in the catalog. */
function shelves(brands: Brand[], space: Space | null): Shelf[] {
  if (!space) return brands.map(brand => ({ brands: [brand], columns: 1, width: null }));
  const columns = Math.max(1, Math.floor((space.width + CARD_GAP) / (CARD_MIN + CARD_GAP)));
  const card = (space.width - (columns - 1) * CARD_GAP) / columns;
  const widest = space.room > space.width ? Math.floor((space.room + CARD_GAP) / (WIDE_CARD_MIN + CARD_GAP)) : columns;
  const rest = [...brands];
  const result: Shelf[] = [];
  while (rest.length) {
    const row = [rest.shift()!];
    let used = row[0]!.products.length;
    while (!SEPARATE_ROW_CATEGORIES.has(row[0]!.name) && used < columns && rest.length) {
      if (SEPARATE_ROW_CATEGORIES.has(rest[0]!.name)) break;
      const next = rest[0]!.products.length;
      if (next < columns && used + next <= Math.max(columns, widest)) {
        used += next;
        row.push(rest.shift()!);
        continue;
      }
      const boundary = rest.findIndex(brand => SEPARATE_ROW_CATEGORIES.has(brand.name));
      const fill = rest.findIndex((brand, index) => (boundary < 0 || index < boundary) && used + brand.products.length <= columns);
      if (fill < 0) break;
      used += rest[fill]!.products.length;
      row.push(...rest.splice(fill, 1));
    }
    result.push(used > columns && row.length > 1
      ? { brands: row, columns: used, width: Math.min(space.room, used * card + (used - 1) * CARD_GAP) }
      : { brands: row, columns, width: null });
  }
  return result;
}

/** The lowest price a buyer can pay now, with its regular price struck through when an 优惠价 lowers it. */
function CardPrice({ product }: { product: Product }) {
  const offer = lowestOffer(product);
  if (!offer) return null;
  const { cents, regular, from } = offer;
  return <div className="shop-product-price">
    <PromoBadge text={product.badge}/>
    <strong className={regular ? 'is-promo' : undefined}><small>¥</small>{Math.floor(cents / 100)}<small>.{String(cents % 100).padStart(2, '0')}</small></strong>
    {from ? <span className="shop-product-from">起</span> : null}
    {regular ? <s><span className="sr-only">原价 </span>{money(regular)}</s> : null}
  </div>;
}

function ProductRow({ product, catalogReady, onAdd }: { product: Product; catalogReady: boolean; onAdd: (item: CartItem) => void }) {
  const available = catalogReady && product.variants.some(item => item.available);
  const single = product.variants.length === 1 ? product.variants[0] : null;
  const stock = product.variants.reduce((sum, item) => sum + (item.quantity ?? 0), 0);
  const cartIcon = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M3 3h2l2.5 12h11l2-8H6"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></svg>;
  // A card: the brand, the title (up to two lines) and its labels on top; the price with its promotion
  // label, then availability, sales and the cart at the foot, so every card in a row lines up.
  // The title link covers the whole card; the card grows into the product page's header.
  return <article className={`shop-product-row${available ? '' : ' is-unavailable'}${isNew(product) ? ' is-new' : ''}`} data-shop-card={product.id}>
    <NewBadge product={product}/>
    <div className="shop-product-name"><span className="shop-product-thumbnail" data-shop-logo><ShopBrandImage category={product.category}/></span><div className="shop-product-title" data-shop-title><h3><a href={`/?product=${product.id}`} aria-label={product.title}>{productHeading(product)}</a></h3><ProductLabels product={product}>{product.variants.length > 1 ? <span className="shop-variants-badge">{product.variants.length} 种规格</span> : null}</ProductLabels></div></div>
    {catalogReady && plusReferralVariant(product) ? <PlusReferralLink product={product}/> : null}
    <div className="shop-product-buy">
      <CardPrice product={product}/>
      <div className="shop-product-foot">
        <div className="shop-product-status"><span className={`shop-product-availability${available ? stock ? ' is-available is-stock' : ' is-available' : ''}`}>{available ? stock ? `现货 ${stock} 件` : '可咨询购买' : '暂不可购买'}</span><ProductStats product={product}/></div>
        {available && !single ? <a className="shop-product-cart" href={`/?product=${product.id}`} aria-label={`选择 ${product.title} 规格`}>{cartIcon}</a> : <button className="shop-product-cart" type="button" disabled={!available} aria-label={available ? `将 ${product.title} 加入购物车` : `${product.title} 暂不可购买`} onClick={() => { if (catalogReady && single?.available) onAdd({ id: single.id, quantity: 1 }); }}>{cartIcon}</button>}
      </div>
    </div>
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
  const storefront = useRef<HTMLDivElement>(null);
  const space = useShelfSpace(storefront);
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
  const newCount = allProducts.filter(product => isNew(product)).length;
  useEffect(() => {
    if (catalog && category === NEW_FILTER && !newCount) setCategory('全部商品');
  }, [catalog, category, newCount]);
  // 新品 lead their category; the rest keep the shop's order.
  const products = allProducts.filter(product => (category === '全部商品' || (category === NEW_FILTER ? isNew(product) : product.category === category)) && `${product.title} ${product.variants.map(item => item.label).join(' ')}`.toLowerCase().includes(search.trim().toLowerCase()))
    .sort((a, b) => Number(isNew(b)) - Number(isNew(a)));
  return <PortalShell active="shop" pageTitle="购物">
    {isCart ? <>
      <div className="shop-toolbar"><a href="/">商品目录</a><div className="shop-toolbar-end"><WechatContact wechat={catalog?.wechat}/><a className="btn btn-secondary" href="/?view=cart">购物车（{cartCount}）</a></div></div>
      <p className="shop-feedback" role="status">{notice}</p>
      <CartView cart={cart} catalog={catalog} error={error} onChange={updateCart} onBuy={() => setSelected(cart)} onRetry={() => void load()}/>
    </> : null}
    {productId !== null ? <ProductPage productId={productId} catalog={catalog} error={error} notice={notice} cartCount={cartCount} onAdd={add} onBuy={item => setSelected([item])} onRetry={() => void load()}/> : null}
    {/* The catalog stays mounted under product and cart pages, so it comes back exactly as it was left. */}
    <div className="shop-storefront" ref={storefront} hidden={isCart || productId !== null}>
    <header className="shop-hero">
      <div className="shop-hero-text">
        <h1 id="shop-catalog-title" tabIndex={-1}>商品目录</h1>
        <p>AI 会员订阅 · 商家人工交付 · 确认后再付款</p>
        {catalog?.stats && (catalog.stats.views || catalog.stats.sold) ? <p className="shop-store-stats">{[catalog.stats.views ? `累计浏览 ${countText(catalog.stats.views)} 人次` : '', catalog.stats.sold ? `累计售出 ${countText(catalog.stats.sold)} 件` : ''].filter(Boolean).join(' · ')}</p> : null}
      </div>
      <div className="shop-hero-actions"><WechatContact wechat={catalog?.wechat}/><a className="btn btn-secondary" href="/?view=cart">购物车（{cartCount}）</a></div>
    </header>
    {/* The merchant's note and the current promotion side by side, above the products. */}
    {catalog?.announcement || referralProduct ? <div className={`shop-board${catalog?.announcement && referralProduct ? ' is-pair' : ''}`}>
      <ShopAnnouncement announcement={catalog?.announcement}/>
      {referralProduct ? <PlusReferralLink product={referralProduct} banner/> : null}
    </div> : null}
    <p className="shop-feedback" role="status">{notice}</p>
    <div className="shop-layout">
      <section className="shop-catalog" aria-labelledby="shop-catalog-title">
        <div className="shop-filterbar">
          <div className="shop-category-list" role="group" aria-label="商品分类">{['全部商品', ...(newCount ? [NEW_FILTER] : []), ...categories].map(name => <button key={name} type="button" className={name === NEW_FILTER ? 'is-new' : undefined} aria-pressed={category === name} onClick={() => setCategory(name)}><span>{name === '全部商品' ? '全部' : name}</span><span className="shop-count">{name === '全部商品' ? allProducts.length : name === NEW_FILTER ? newCount : allProducts.filter(product => product.category === name).length}</span></button>)}</div>
          <form className="shop-search" role="search" onSubmit={event => event.preventDefault()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
            <input type="search" aria-label="搜索商品" placeholder="搜索商品" value={search} onChange={event => setSearch(event.target.value)}/>
          </form>
        </div>
        {error ? <p className="shop-notice" role="alert">商品暂时无法加载。<button type="button" className="btn btn-secondary" onClick={() => void load()}>重试</button></p> : null}
        {catalog?.status === 'unavailable' ? <p className="shop-notice" role="status">商品信息暂不可用，请稍后再试。</p> : null}
        {catalog?.status === 'stale' ? <p className="shop-notice" role="status">商品信息正在等待更新，暂时无法购买，请稍后再试。</p> : null}
        <div className="shop-catalog-body">
          {products.length ? shelves(categories.map(name => ({ name, products: products.filter(product => product.category === name) })).filter(brand => brand.products.length), space).map(shelf => <div key={shelf.brands[0]!.name} className={shelf.width ? 'shop-shelf is-wide' : 'shop-shelf'} style={{ '--shelf-columns': shelf.columns, ...(shelf.width ? { '--shelf-width': `${shelf.width}px` } : {}) } as CSSProperties}>
            {shelf.brands.map(({ name, products: group }) => <section className="shop-category-group" data-category={name} key={name} style={{ '--group-columns': Math.min(group.length, shelf.columns) } as CSSProperties}>{category === '全部商品' || category === NEW_FILTER ? <h2 className="shop-group-heading">{name} 商品<span>（{group.length}）</span></h2> : null}<div className="shop-product-list">{group.map(product => <ProductRow key={product.id} product={product} catalogReady={catalog?.status === 'ready'} onAdd={add}/>)}</div></section>)}
          </div>) : <div className="shop-empty" role="status"><span className="shop-empty-icon"><ShopIcon/></span><h2>{!catalog && !error ? '正在加载商品' : search.trim() ? '没有匹配的商品' : '暂无商品'}</h2><p>{search.trim() ? '试试其他关键词，或清除搜索。没上架的商品也可以联系商家代购。' : '商品上架后会显示在这里。'}</p>{search.trim() ? <WechatContact wechat={catalog?.wechat}/> : null}{search.trim() ? <button className="btn btn-secondary" type="button" onClick={() => setSearch('')}>清除搜索</button> : null}</div>}
        </div>
      </section>
    </div>
    </div>
    {selected ? <PurchaseDialog catalogVersion={catalog} items={selected} onClose={() => setSelected(null)}/> : null}
  </PortalShell>;
}
