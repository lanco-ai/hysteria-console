import { useCallback, useEffect, useRef, useState } from 'react';
import { PortalShell, ShopIcon } from './PortalShell';
import { money, shopRequest, type Catalog, type Product } from '../shop/catalog';

function PurchaseDialog({ catalog, productId, onClose }: { catalog: Catalog; productId: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const product = catalog.products.find(item => item.id === productId);
  const [variantId, setVariantId] = useState(product?.variants.find(item => item.available)?.id ?? '');
  const [quantity, setQuantity] = useState('1');
  const [notice, setNotice] = useState('');
  const variant = product?.variants.find(item => item.id === variantId);
  const count = /^\d{1,2}$/.test(quantity) ? Number(quantity) : 0;
  const valid = !!variant?.available && count >= 1 && count <= 99 && catalog.status === 'ready';
  const total = variant ? variant.price_cents * count : 0;
  const summary = product && variant ? `购买信息（待商家人工确认）\n商品：${product.title} 充值卡密\n规格：${variant.label}\n数量：${count}\n单价：${money(variant.price_cents)}\n合计：${money(total)} CNY\n请确认账号适用条件、最终价格、付款和交付方式。` : '';
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function copy() {
    try { await navigator.clipboard.writeText(summary); setNotice('购买信息已复制，请发送给商家确认。'); }
    catch { setNotice('无法自动复制，请选中下方购买信息手动复制。'); }
  }
  return <dialog ref={dialog} className="shop-purchase" aria-labelledby="purchase-title" onClose={onClose}>
    <header><div><p className="portal-eyebrow">PURCHASE DETAILS</p><h2 id="purchase-title">购买信息</h2></div><button type="button" className="btn btn-secondary" onClick={() => dialog.current?.close()} aria-label="关闭购买信息">关闭</button></header>
    <p className="shop-note">待商家人工确认 · 请联系商家确认商品及金额，人工付款后交付。</p>
    {product ? <><h3>{product.title}</h3><p>充值卡密 · 购买前请与商家确认账号条件及套餐是否适用。</p>
      <label>商品规格<select value={variantId} onChange={event => { setVariantId(event.target.value); setNotice(''); }}>{product.variants.map(item => <option key={item.id} value={item.id} disabled={!item.available}>{item.label}{!item.available ? '（暂不可购买）' : ''} · {money(item.price_cents)}</option>)}</select></label>
      <label>购买数量<input inputMode="numeric" type="number" min="1" max="99" step="1" value={quantity} onChange={event => { setQuantity(event.target.value); setNotice(''); }}/></label>
      <p className="shop-total">合计 <strong data-testid="purchase-total">{valid ? money(total) : '—'}</strong></p>
    </> : null}
    {!valid ? <p role="alert">请填写 1–99 的整数数量；商品停售、售罄或信息过期时无法购买。</p> : null}
    <div className="shop-purchase-actions"><button type="button" className="btn btn-secondary" disabled={!valid} onClick={() => void copy()}>复制购买信息</button>
      {valid && catalog.telegram ? <a className="btn btn-primary" href={`https://t.me/${catalog.telegram}`} target="_blank" rel="noopener noreferrer">联系商家 Telegram</a> : null}</div>
    {!catalog.telegram ? <p className="shop-note">商家尚未配置联系方式，暂时无法联系购买。</p> : null}
    <p role="status">{notice}</p>
    {valid ? <details><summary>查看可复制的购买信息</summary><textarea aria-label="可复制的购买信息" readOnly value={summary} rows={7}/></details> : null}
  </dialog>;
}

function ProductRow({ product, stale, onPurchase }: { product: Product; stale: boolean; onPurchase: () => void }) {
  const prices = product.variants.map(item => item.price_cents);
  const minimum = Math.min(...prices), maximum = Math.max(...prices);
  const available = product.variants.some(item => item.available);
  return <article className="shop-product-row">
    <div className="shop-product-name"><span className="shop-gpt-badge" aria-hidden="true">GPT</span><div><h2>{product.title}</h2><p>充值卡密 · {product.variants.length} 种规格</p><p className="shop-spec-preview">{product.variants.map(item => item.label).join(' / ')}</p></div></div>
    <div className="shop-product-price"><span className="shop-mobile-label">售价</span><strong>{money(minimum)}{maximum !== minimum ? `–${money(maximum)}` : ''}</strong></div>
    <div className="shop-product-stock"><span className="shop-mobile-label">库存</span>{available ? '可咨询购买' : '暂不可购买'}<small>数量未公开</small></div>
    <div className="shop-product-sales"><span className="shop-mobile-label">销量</span>—</div>
    <div className="shop-product-action"><button type="button" className="btn btn-primary" disabled={!available} onClick={onPurchase}>{available ? '购买' : stale ? '暂不可购买' : '已售罄'}</button></div>
  </article>;
}

export function ShopPage() {
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('全部商品');
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [opening, setOpening] = useState(false);
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
  async function open(product: Product) {
    setOpening(true);
    const current = await load();
    setOpening(false);
    if (current?.products.find(item => item.id === product.id)?.variants.some(item => item.available)) setSelected(product.id);
  }
  const products = (catalog?.products ?? []).filter(product => (category === '全部商品' || product.category === category) && `${product.title} ${product.variants.map(item => item.label).join(' ')}`.toLowerCase().includes(search.trim().toLowerCase()));
  return <PortalShell active="shop" pageTitle="购物">
    <div className="shop-layout" aria-busy={opening}>
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
          {products.length ? products.map(product => <ProductRow key={product.id} product={product} stale={catalog?.status !== 'ready'} onPurchase={() => { if (!opening) void open(product); }}/>) : <div className="shop-empty" role="status"><span className="shop-empty-icon"><ShopIcon/></span><h2>{!catalog && !error ? '正在加载商品' : search.trim() ? '没有匹配的商品' : '暂无商品'}</h2><p>{search.trim() ? '试试其他关键词，或清除搜索。' : '商品上架后会显示在这里。'}</p>{search.trim() ? <button className="btn btn-secondary" type="button" onClick={() => setSearch('')}>清除搜索</button> : null}</div>}
        </div>
      </section>
    </div>
    {catalog && selected ? <PurchaseDialog catalog={catalog} productId={selected} onClose={() => setSelected(null)}/> : null}
  </PortalShell>;
}
