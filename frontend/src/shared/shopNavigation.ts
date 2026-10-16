// The document router owns history; product pages only ask it to close.
export const SHOP_RETURN_INDEX = '__lancoaiShopReturnIndex';
export const CLOSE_PRODUCT_EVENT = 'lancoai:close-product';
// The router restores scroll positions itself and keeps the last one in the
// history entry, so a reload or a return from another site finds it again.
export const SCROLL_KEY = '__lancoaiScrollY';

export function closeShopProduct(): void {
  window.dispatchEvent(new Event(CLOSE_PRODUCT_EVENT));
}

export function readScroll(state: unknown): number | null {
  if (!state || typeof state !== 'object') return null;
  const value = (state as Record<string, unknown>)[SCROLL_KEY];
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

const initialScroll = readScroll(window.history.state);
let scrollTarget: { href: string; top: number } | null = initialScroll === null ? null : { href: window.location.href, top: initialScroll };

export function queueScrollRestore(top: number | null): void {
  scrollTarget = top === null ? null : { href: window.location.href, top };
}

export function retargetScrollRestore(): void {
  if (scrollTarget) scrollTarget.href = window.location.href;
}

export function hasPendingScrollRestore(): boolean {
  return scrollTarget?.href === window.location.href;
}

/** Called by the destination after its content has loaded, before paint. */
export function restoreScrollPosition(): void {
  if (!scrollTarget || !hasPendingScrollRestore()) return;
  const { top } = scrollTarget;
  scrollTarget = null;
  window.scrollTo({ top, left: 0, behavior: 'instant' });
}
