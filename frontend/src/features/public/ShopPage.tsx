import { useState } from 'react';
import { PortalShell, ShopIcon } from './PortalShell';

// Products will be connected once their source is supplied. No reference-site
// products, prices, stock counts or sales figures are shipped as real data.
export function ShopPage() {
  const [search, setSearch] = useState('');
  return <PortalShell active="shop" pageTitle="购物">
    <div className="shop-layout">
      <aside className="shop-categories portal-card" aria-labelledby="shop-categories-title">
        <header className="shop-panel-heading"><p className="portal-eyebrow">CATEGORIES</p><h2 id="shop-categories-title">商品分类</h2></header>
        <div className="shop-category-list"><button type="button" aria-pressed="true"><span className="shop-category-icon"><ShopIcon/></span><span>全部商品</span><span className="shop-count">0</span></button></div>
      </aside>
      <section className="shop-catalog portal-card" aria-labelledby="shop-catalog-title">
        <header className="shop-catalog-heading"><div><p className="portal-eyebrow">CATALOG</p><h1 id="shop-catalog-title">商品目录</h1></div>
          <form className="shop-search" role="search" onSubmit={event => event.preventDefault()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/></svg>
            <input type="search" aria-label="搜索商品" placeholder="搜索商品关键词" value={search} onChange={event => setSearch(event.target.value)}/>
            <button type="submit">搜索</button>
          </form>
        </header>
        <div className="shop-catalog-body">
          <div className="shop-table-head" aria-hidden="true"><span>商品</span><span>价格</span><span>库存</span><span>销量</span></div>
          <div className="shop-empty" role="status"><span className="shop-empty-icon"><ShopIcon/></span><h2>{search.trim() ? '没有匹配的商品' : '暂无商品'}</h2><p>{search.trim() ? '试试其他关键词，或清除搜索。' : '商品上架后会显示在这里。'}</p>{search.trim() ? <button className="btn btn-secondary" type="button" onClick={() => setSearch('')}>清除搜索</button> : null}</div>
        </div>
      </section>
    </div>
  </PortalShell>;
}
