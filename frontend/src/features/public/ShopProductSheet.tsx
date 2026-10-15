import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { closeShopProduct } from '../../shared/shopNavigation';

/** Native modality handles Tab containment and makes the catalog inert. */
export function ProductSheet({ productId, cartCount, onVisibilityChange, children }: { productId: string | null; cartCount: number; onVisibilityChange: (visible: boolean) => void; children: (id: string) => ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [lastProduct, setLastProduct] = useState(productId);
  const [closing, setClosing] = useState(false);
  const restore = useRef<(() => void) | null>(null);
  const backdropPress = useRef(false);
  useLayoutEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (productId) {
      setLastProduct(productId);
      setClosing(false);
      if (!node.open) {
        const active = document.activeElement;
        const scrollY = window.scrollY;
        const body = document.body;
        const { overflow, paddingRight } = body.style;
        const gutter = window.innerWidth - document.documentElement.clientWidth;
        body.style.overflow = 'hidden';
        if (gutter) body.style.paddingRight = `${parseFloat(getComputedStyle(body).paddingRight) + gutter}px`;
        node.showModal();
        onVisibilityChange(true);
        restore.current = () => {
          body.style.overflow = overflow;
          body.style.paddingRight = paddingRight;
          window.scrollTo({ top: scrollY, behavior: 'instant' });
          if (active instanceof HTMLElement && active !== body && active.isConnected) active.focus({ preventScroll: true });
          else document.getElementById('main-content')?.focus({ preventScroll: true });
        };
      }
      return;
    }
    if (!node.open) return;
    setClosing(true);
    const timer = window.setTimeout(() => {
      node.close();
      onVisibilityChange(false);
      restore.current?.();
      restore.current = null;
      setLastProduct(null);
      setClosing(false);
    }, window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 180);
    return () => window.clearTimeout(timer);
  }, [productId, onVisibilityChange]);
  useEffect(() => () => { restore.current?.(); }, []);
  const id = productId ?? lastProduct;
  return <dialog ref={dialog} className={`shop-product-sheet${closing ? ' is-closing' : ''}`} aria-label="商品详情"
    onCancel={event => { event.preventDefault(); closeShopProduct(); }}
    onPointerDown={event => {
      const rect = event.currentTarget.getBoundingClientRect();
      backdropPress.current = event.target === event.currentTarget && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom);
    }}
    onClick={event => {
      if (backdropPress.current && event.target === event.currentTarget) {
        const rect = event.currentTarget.getBoundingClientRect();
        if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) closeShopProduct();
      }
      backdropPress.current = false;
    }}>
    <header className="shop-sheet-header"><button className="shop-sheet-back" type="button" onClick={closeShopProduct}><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m14 6-6 6 6 6"/></svg>继续选购</button><div><a href="/?view=cart" className="shop-sheet-cart">购物车（{cartCount}）</a><button className="shop-sheet-close" type="button" onClick={closeShopProduct} aria-label="关闭商品详情"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div></header>
    {id ? children(id) : null}
  </dialog>;
}
