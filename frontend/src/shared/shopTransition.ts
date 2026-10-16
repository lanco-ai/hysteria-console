// A product page grows out of its catalog card and shrinks back into it;
// plans of one brand slide sideways. The browser animates snapshots of the old
// and new page (View Transitions), so the DOM itself switches in one step.
// Browsers without the API, and visitors who prefer less motion, see the new
// page at once.
export type ShopTransition = 'open' | 'close' | 'next' | 'previous';

// The card and the product page's header carry these names while they morph.
const MORPH_PARTS = [[null, 'shop-card'], ['[data-shop-logo]', 'shop-card-logo'], ['[data-shop-title]', 'shop-card-title']] as const;

function nameMorph(element: Element | null, on: boolean): void {
  if (!(element instanceof HTMLElement)) return;
  for (const [selector, name] of MORPH_PARTS) {
    const part = selector ? element.querySelector<HTMLElement>(selector) : element;
    if (part) part.style.viewTransitionName = on ? name : '';
  }
}

function clearMorphNames(): void {
  document.querySelectorAll('[data-shop-card], [data-shop-hero]').forEach(element => nameMorph(element, false));
}

export function catalogCard(productId: string | null): HTMLElement | null {
  return productId ? document.querySelector<HTMLElement>(`[data-shop-card="${CSS.escape(productId)}"]`) : null;
}

export function productHero(): HTMLElement | null {
  return document.querySelector<HTMLElement>('[data-shop-hero]');
}

/** On screen at least in part, so a morph starts or lands where the eye is. */
export function onScreen(element: Element | null): element is HTMLElement {
  if (!(element instanceof HTMLElement)) return false;
  const box = element.getBoundingClientRect();
  return box.height > 0 && box.bottom > 0 && box.top < window.innerHeight;
}

/** Wholly on screen. */
export function fullyOnScreen(element: HTMLElement): boolean {
  const box = element.getBoundingClientRect();
  return box.top >= 0 && box.bottom <= window.innerHeight;
}

function animated(): boolean {
  return typeof document.startViewTransition === 'function'
    && document.visibilityState === 'visible'
    && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

let sequence = 0;

/**
 * Switch the page with `update`, morphing `from` into the element `update`
 * returns. A null `kind` (the browser already animated a back swipe) or an
 * unavailable API switches without animation.
 */
export function runShopTransition(kind: ShopTransition | null, from: Element | null, update: () => Element | null): void {
  const root = document.documentElement;
  const token = ++sequence;
  clearMorphNames();
  if (!kind || !animated()) {
    delete root.dataset.shopTransition;
    update();
    return;
  }
  nameMorph(from, true);
  root.dataset.shopTransition = kind;
  const transition = document.startViewTransition(() => {
    if (token !== sequence) return;
    clearMorphNames();
    nameMorph(update(), true);
  });
  const done = () => {
    // A newer transition owns the names and the marker now.
    if (token !== sequence) return;
    delete root.dataset.shopTransition;
    clearMorphNames();
  };
  transition.finished.then(done, done);
  transition.ready.catch(() => undefined);
  transition.updateCallbackDone.catch((error: unknown) => window.reportError(error));
}
