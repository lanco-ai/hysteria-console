import { useCallback, useEffect, useRef, useState } from 'react';
import brandImage from '../../assets/shop-chatgpt.png';
import { PortalShell, ShopIcon } from './PortalShell';
import { money, shopRequest, type Catalog, type Product } from '../shop/catalog';
import { CART_KEY, MAX_CART_ITEMS, readCart, type CartItem } from '../shop/cart';
import { PurchaseDialog } from './ShopPurchase';
import { ProductDetail, CartView } from './ShopProductDetail';

function ProductRow({ product, catalogReady }: { product: Product; catalogReady: boolean }) {
  const prices = product.variants.map(item => item.price_cents);
  const minimum = Math.min(...prices), maximum = Math.max(...prices);
  const available = catalogReady && product.variants.some(item => item.available);
  return <article className="shop-product-row">
    <div className="shop-product-name"><img className="shop-product-thumbnail" src={brandImage} alt="ChatGPT" width="64" height="64"/><div><h2><a href={`/?product=${product.id}`}>{product.title}</a></h2><div className="shop-product-tags"><span>人工交付</span>{product.variants.length > 1 ? <span className="shop-product-variants">{product.variants.length} 种规格</span> : null}</div></div></div>
    <div className="shop-product-price"><span className="shop-mobile-label">售价</span><strong>{money(minimum)}{maximum !== minimum ? `–${money(maximum)}` : ''}</strong></div>
    <div className="shop-product-stock"><span className="shop-mobile-label">库存</span><span className={`shop-stock-status${available ? ' is-available' : ''}`}>{available ? '可咨询购买' : '暂不可购买'}</span><small>数量未公开</small></div>
    <div className="shop-product-sales"><span className="shop-mobile-label">销量</span>未公开</div>
    <div className="shop-product-action"><a className={`btn ${available ? 'btn-primary' : 'btn-secondary'}`} href={`/?product=${product.id}`}>{available ? <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="M5 7h14l1 14H4L5 7Z"/><path d="M8 8V6a4 4 0 0 1 8 0v2"/></svg> : null}{available ? '购买' : '查看详情'}</a></div>
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
  const productId = query.get('product');
  const isCart = query.get('view') === 'cart';
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
  const products = (catalog?.products ?? []).filter(product => (category === '全部商品' || product.category === category) && `${product.title} ${product.variants.map(item => item.label).join(' ')}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <PortalShell active="shop" pageTitle="购物">
    <div className="shop-toolbar"><a href="/">商品目录</a><a className="btn btn-secondary" href="/?view=cart">购物车（{cart.reduce((sum, item) => sum + item.quantity, 0)}）</a></div>
    <p className="shop-feedback" role="status">{notice}</p>
    {isCart ? <CartView cart={cart} catalog={catalog} error={error} onChange={updateCart} onBuy={() => setSelected(cart)} onRetry={() => void load()}/> : productId ? <ProductDetail key={productId} productId={productId} catalog={catalog} error={error} onAdd={add} onBuy={item => setSelected([item])} onRetry={() => void load()}/> : <div className="shop-layout">
      <aside className="shop-categories portal-card" aria-labelledby="shop-categories-title">
        <header className="shop-panel-heading"><p className="portal-eyebrow">CATEGORIES</p><h2 id="shop-categories-title">商品分类</h2></header>
        <div className="shop-category-list">{['全部商品', ...(catalog?.products.length ? ['GPT'] : [])].map(name => <button key={name} type="button" aria-pressed={category === name} onClick={() => setCategory(name)}><span className="shop-category-icon"><ShopIcon/></span><span>{name}</span><span className="shop-count">{catalog?.products.length ?? 0}</span></button>)}</div>
      </aside>
      <section className="shop-catalog portal-card" aria-labelledby="shop-catalog-title">
        <header className="shop-catalog-heading"><div><p className="portal-eyebrow">CATALOG</p><h1 id="shop-catalog-title">商品目录</h1></div>
          <form className="shop-search" role="search" onSubmit={event => event.preventDefault()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
            <input type="search" aria-label="搜索商品" placeholder="搜索商品关键词" value={search} onChange={event => setSearch(event.target.value)}/><button type="submit">搜索</button>
          </form>
        </header>
        {error ? <p className="shop-notice" role="alert">商品暂时无法加载。<button type="button" className="btn btn-secondary" onClick={() => void load()}>重试</button></p> : null}
        {catalog?.status === 'unavailable' ? <p className="shop-notice" role="status">商品信息暂不可用，请稍后再试。</p> : null}
        {catalog?.status === 'stale' ? <p className="shop-notice" role="status">商品信息正在等待更新，暂时无法购买，请稍后再试。</p> : null}
        <div className="shop-catalog-body">
          <div className="shop-table-head" aria-hidden="true"><span>商品</span><span>价格</span><span>库存</span><span>销量</span><span></span></div>
          {products.length ? products.map(product => <ProductRow key={product.id} product={product} catalogReady={catalog?.status === 'ready'}/>) : <div className="shop-empty" role="status"><span className="shop-empty-icon"><ShopIcon/></span><h2>{!catalog && !error ? '正在加载商品' : search.trim() ? '没有匹配的商品' : '暂无商品'}</h2><p>{search.trim() ? '试试其他关键词，或清除搜索。' : '商品上架后会显示在这里。'}</p>{search.trim() ? <button className="btn btn-secondary" type="button" onClick={() => setSearch('')}>清除搜索</button> : null}</div>}
        </div>
      </section>
    </div>}
    {selected ? <PurchaseDialog catalogVersion={catalog} items={selected} onClose={() => setSelected(null)}/> : null}
  </PortalShell>;
}
