import { serviceHref } from './shared/serviceHref';
import { flushSync } from 'react-dom';
import { createRoot } from 'react-dom/client';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useState } from 'react';
import type { ComponentType } from 'react';
import './styles/index.css';
import { LoginModal, resolveSameOriginReturnTo } from './features/auth/LoginModal';
import { PortalSessionContext } from './features/public/PortalShell';
import { ShopPage } from './features/public/ShopPage';
import { CLOSE_PRODUCT_EVENT, hasPendingScrollRestore, queueScrollRestore, readScroll, restoreScrollPosition, retargetScrollRestore, SCROLL_KEY, SHOP_RETURN_INDEX } from './shared/shopNavigation';
import { catalogCard, fullyOnScreen, onScreen, productHero, runShopTransition, type ShopTransition } from './shared/shopTransition';
import { applyInitialShellPreferences, CodexShell } from './shared/CodexShell';
import { useSession } from './shared/session';

// The shop service serves two documents: the public portal at / (shopping and
// SMS codes) and the merchant console at /admin/shop. Every other page belongs
// to the network or workbench + AI service, so links to it are ordinary full
// page loads. The public shop page stays in the entry chunk; the SMS view and
// the merchant console load on demand.
type LazyPage<P> = ComponentType<P> & { preload: () => Promise<void> };

function lazyPage<P extends object>(load: () => Promise<ComponentType<P>>): LazyPage<P> {
  let Loaded: ComponentType<P> | undefined;
  let pending: Promise<void> | undefined;
  const preload = () => {
    pending ??= load().then(component => { Loaded = component; }, (error: unknown) => { pending = undefined; throw error; });
    return pending;
  };
  const Deferred = lazy(() => preload().then(() => ({ default: Loaded as ComponentType<P> })));
  const Page = (props: P) => (Loaded ? <Loaded {...props}/> : <Deferred {...props}/>);
  return Object.assign(Page, { preload });
}

const SmsPage = lazyPage(() => import('./features/shop/SmsPage').then(module => module.SmsPage));
const ShopAdminPage = lazyPage(() => import('./features/shop/ShopAdminPage').then(module => module.ShopAdminPage));

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');
const appRoot = root;
const reactRoot = createRoot(appRoot);
const REACT_PREVIEW_PREFIX = '/__react';

const PORTAL_ROUTE = '/';
const ADMIN_ROUTE = '/admin/shop';
const REACT_DOCUMENT_ROUTES = new Set([PORTAL_ROUTE, ADMIN_ROUTE]);
// Portal views that moved to the workbench + AI service. The server redirects
// them, so the client must not render them in place.
const MOVED_PORTAL_VIEWS = new Set(['trending', 'chat', 'video']);
const SAFE_LOGIN_QUERY_KEYS = new Set(['msg', 'tab', 'range', 'window', 'page', 'filter', 'view', 'conversation']);

type RouteMetadata = { title: string; bodyClass: string; shell?: boolean };
const ROUTE_METADATA: Record<string, RouteMetadata> = {
  [PORTAL_ROUTE]: { title: '购物 · LancoAI', bodyClass: 'page-portal page-workbench' },
  [ADMIN_ROUTE]: { title: '商品管理', bodyClass: 'has-shell', shell: true },
};

function normalizeRoute(pathname: string): string {
  return pathname.startsWith(REACT_PREVIEW_PREFIX) ? pathname.slice(REACT_PREVIEW_PREFIX.length) || '/' : pathname;
}

function currentLocationKey(): string { return window.location.href; }

const REACT_HISTORY_INDEX = '__hysteriaReactHistoryIndex';

function readReactHistoryIndex(state: unknown): number | null {
  if (!state || typeof state !== 'object') return null;
  const value = (state as Record<string, unknown>)[REACT_HISTORY_INDEX];
  return typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
}

function withReactHistoryIndex(state: unknown, index: number): Record<string, unknown> {
  const value: Record<string, unknown> = state && typeof state === 'object' && !Array.isArray(state)
    ? { ...(state as Record<string, unknown>) }
    : { __hysteriaPreviousHistoryState: state };
  value[REACT_HISTORY_INDEX] = index;
  return value;
}

let reactHistoryIndex = readReactHistoryIndex(window.history.state) ?? Math.max(0, window.history.length - 1);
let reactHistoryLocation = window.location.href;
if (readReactHistoryIndex(window.history.state) === null) {
  window.history.replaceState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', window.location.href);
}

// This router restores scroll positions, not the browser: going back from a
// product page animates out of a snapshot of that page, which the browser
// would first have scrolled to the catalog's old position.
window.history.scrollRestoration = 'manual';
const scrollPositions = new Map<number, number>();
let scrollSaveTimer = 0;
// The entry itself keeps the position too, once scrolling settles, for a
// reload (browsers ignore history changes made while a page unloads).
function persistScroll(): void {
  if (hasPendingScrollRestore()) return;
  if (readScroll(window.history.state) === window.scrollY) return;
  const state = withReactHistoryIndex(window.history.state, reactHistoryIndex);
  state[SCROLL_KEY] = window.scrollY;
  window.history.replaceState(state, '');
}
function rememberScroll(): void {
  if (hasPendingScrollRestore()) return;
  scrollPositions.set(reactHistoryIndex, window.scrollY);
  window.clearTimeout(scrollSaveTimer);
  scrollSaveTimer = window.setTimeout(persistScroll, 250);
}
window.addEventListener('scroll', rememberScroll, { passive: true });
function savedScroll(index: number, state: unknown): number {
  return scrollPositions.get(index) ?? readScroll(state) ?? 0;
}

function pushReactHistory(path: string): void {
  const previous = new URL(window.location.href);
  const next = new URL(path, window.location.href);
  const state = withReactHistoryIndex(window.history.state, reactHistoryIndex + 1);
  delete state[SCROLL_KEY];
  // Leaving a product page for the catalog it came from goes back to it.
  if (shopProductOf(next) && shopProductOf(previous) === null) state[SHOP_RETURN_INDEX] = reactHistoryIndex;
  else if (!shopProductOf(previous) || shopProductOf(previous) !== shopProductOf(next)) delete state[SHOP_RETURN_INDEX];
  scrollPositions.set(reactHistoryIndex, window.scrollY);
  persistScroll();
  // A new branch of history discards all old forward positions.
  for (const index of scrollPositions.keys()) if (index > reactHistoryIndex) scrollPositions.delete(index);
  reactHistoryIndex += 1;
  window.history.pushState(state, '', path);
  reactHistoryLocation = window.location.href;
  queueScrollRestore(window.location.hash ? null : 0);
}

function replaceReactHistory(path: string): void {
  window.history.replaceState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', path);
  reactHistoryLocation = window.location.href;
  retargetScrollRestore();
}

// Match PortalRoute and ShopPage: unknown view values still render the shop.
// null for the catalog, the product ID for a product page, undefined otherwise.
function shopProductOf(url: URL): string | null | undefined {
  const view = url.searchParams.get('view') || '';
  if (normalizeRoute(url.pathname) !== PORTAL_ROUTE || view === 'sms' || view === 'cart' || MOVED_PORTAL_VIEWS.has(view)) return undefined;
  return url.searchParams.get('product') || null;
}

function shopTransitionBetween(from: URL, to: URL, backward: boolean): ShopTransition | null {
  const leaving = shopProductOf(from), entering = shopProductOf(to);
  if (leaving === undefined || entering === undefined || leaving === entering) return null;
  if (leaving === null) return 'open';
  if (entering === null) return 'close';
  return backward ? 'previous' : 'next';
}

// Render the shop view the URL now names and return the element the morph
// lands on. A product page starts at the top (or where its entry was left);
// the catalog returns to its position and brings the product's card into view.
function enterShopView(kind: ShopTransition, leaving: string | null, top: number | null, commit: () => void): Element | null {
  const previousY = window.scrollY;
  commit();
  if (kind === 'next' || kind === 'previous') {
    const page = document.querySelector('.shop-product-page');
    const pageTop = page ? page.getBoundingClientRect().top + window.scrollY : 0;
    window.scrollTo({ top: top ?? Math.min(previousY, pageTop), left: 0, behavior: 'instant' });
    return null;
  }
  // A #fragment (the referral rules) is scrolled to by the page itself.
  if (!window.location.hash) window.scrollTo({ top: top ?? 0, left: 0, behavior: 'instant' });
  if (kind === 'open') {
    const hero = productHero();
    return onScreen(hero) ? hero : null;
  }
  const card = catalogCard(leaving);
  if (!card) {
    document.getElementById('shop-catalog-title')?.focus({ preventScroll: true });
    return null;
  }
  if (!fullyOnScreen(card)) card.scrollIntoView({ block: 'center', inline: 'nearest', behavior: 'instant' });
  card.querySelector<HTMLElement>('.shop-product-title a')?.focus({ preventScroll: true });
  return card;
}

// True only for locations this service renders itself.
function isShopDocument(url: URL): boolean {
  const route = normalizeRoute(url.pathname);
  if (!REACT_DOCUMENT_ROUTES.has(route)) return false;
  return route !== PORTAL_ROUTE || !MOVED_PORTAL_VIEWS.has(url.searchParams.get('view') || '');
}

function previewPath(path: string): string {
  if (!window.location.pathname.startsWith(REACT_PREVIEW_PREFIX) || path.startsWith(REACT_PREVIEW_PREFIX)) return path;
  return `${REACT_PREVIEW_PREFIX}${path}`;
}

function sanitizeReturnTo(value?: string): string | undefined {
  const sameOrigin = resolveSameOriginReturnTo(value);
  if (!sameOrigin) return undefined;
  const destination = new URL(sameOrigin, window.location.origin);
  const query = new URLSearchParams();
  for (const [key, item] of destination.searchParams) {
    if (SAFE_LOGIN_QUERY_KEYS.has(key) || (key === 'product' && /^[1-9]\d{0,15}$/.test(item))) query.append(key, item);
  }
  const suffix = query.toString();
  return `${destination.pathname}${suffix ? `?${suffix}` : ''}`;
}

let navigationSequence = 0;

// onNavigate renders the current URL once its page code has loaded; commit
// renders it synchronously, for shop views that animate between snapshots.
function installClientNavigation(onNavigate: () => void, commit: () => void): () => void {
  let closingProduct = false;
  const onCloseProduct = () => {
    const leaving = shopProductOf(new URL(window.location.href));
    if (closingProduct || !leaving) return;
    const token = ++navigationSequence;
    const returnIndex: unknown = window.history.state?.[SHOP_RETURN_INDEX];
    if (typeof returnIndex === 'number' && Number.isSafeInteger(returnIndex) && returnIndex >= 0 && returnIndex < reactHistoryIndex) {
      closingProduct = true;
      window.history.go(returnIndex - reactHistoryIndex);
      return;
    }
    // A shared product link has no catalog entry behind it. Stay on this site.
    const hero = productHero();
    runShopTransition('close', onScreen(hero) ? hero : null, () => {
      if (token !== navigationSequence) return null;
      replaceReactHistory(previewPath('/'));
      return enterShopView('close', leaving, 0, commit);
    });
  };
  const onPopState = (event: PopStateEvent) => {
    closingProduct = false;
    const previous = new URL(reactHistoryLocation);
    const nextLocation = window.location.href;
    const next = new URL(nextLocation);
    const sameRoute = previous.pathname === next.pathname && previous.search === next.search;
    const guardEvent = new CustomEvent<{ state: unknown; sameRoute: boolean; fromIndex: number }>('hysteria:before-client-popstate', {
      detail: { state: event.state, sameRoute, fromIndex: reactHistoryIndex }, cancelable: true,
    });
    window.dispatchEvent(guardEvent);
    if (guardEvent.defaultPrevented) return;
    const nextIndex = readReactHistoryIndex(event.state);
    const backward = nextIndex !== null && nextIndex < reactHistoryIndex;
    if (nextIndex !== null) reactHistoryIndex = nextIndex;
    reactHistoryLocation = nextLocation;
    window.clearTimeout(scrollSaveTimer);
    queueScrollRestore(null);
    const token = ++navigationSequence;
    const top = savedScroll(reactHistoryIndex, event.state);
    const kind = shopTransitionBetween(previous, next, backward);
    if (kind) {
      const from = kind === 'open' ? catalogCard(shopProductOf(next) ?? null) : kind === 'close' ? productHero() : null;
      // After a back swipe the browser has already shown its own animation.
      runShopTransition(event.hasUAVisualTransition ? null : kind, onScreen(from) ? from : null, () => (
        token === navigationSequence ? enterShopView(kind, shopProductOf(previous) ?? null, top, commit) : null
      ));
      return;
    }
    if (nextLocation === previous.href) window.scrollTo({ top, left: 0, behavior: 'instant' });
    else queueScrollRestore(top);
    onNavigate();
  };
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(serviceHref(anchor.href), window.location.href);
    if (url.origin !== window.location.origin) {
      if (url.href !== anchor.href) { event.preventDefault(); window.location.assign(url.href); }
      return;
    }
    if (url.origin !== window.location.origin || !isShopDocument(url)) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) {
      event.preventDefault();
      const next = `${window.location.pathname}${window.location.search}${url.hash}`;
      if (next !== `${window.location.pathname}${window.location.search}${window.location.hash}`) {
        navigationSequence += 1;
        pushReactHistory(next);
        onNavigate();
      }
      const targetId = (() => {
        try { return decodeURIComponent(url.hash.slice(1)); } catch { return url.hash.slice(1); }
      })();
      const target = document.getElementById(targetId);
      if (target instanceof HTMLElement) {
        target.scrollIntoView();
        target.focus({ preventScroll: true });
      }
      return;
    }
    event.preventDefault();
    const next = `${previewPath(normalizeRoute(url.pathname))}${url.search}${url.hash}`;
    if (next === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
    const current = new URL(window.location.href);
    // Clicking the selected plan only removes its optional referral fragment.
    // It must not add history entries or lose the original catalog return point.
    if (anchor.hasAttribute('data-shop-step') && shopProductOf(current) === shopProductOf(url)) {
      navigationSequence += 1;
      replaceReactHistory(next);
      commit();
      return;
    }
    const kind = shopTransitionBetween(current, url, anchor.dataset.shopStep === 'previous');
    if (kind) {
      // The catalog and its products share one page: switch in place, and
      // swap a brand's plans without stacking history entries.
      const token = ++navigationSequence;
      const from = kind === 'open' ? anchor.closest('[data-shop-card]') : kind === 'close' ? productHero() : null;
      runShopTransition(kind, onScreen(from) ? from : null, () => {
        if (token !== navigationSequence) return null;
        if (kind === 'next' || kind === 'previous') replaceReactHistory(next);
        else pushReactHistory(next);
        return enterShopView(kind, shopProductOf(current) ?? null, null, commit);
      });
      return;
    }
    // Change the URL only once the target page can render, so the address bar
    // and the content switch together. Only the latest click wins.
    const token = ++navigationSequence;
    void preloadLocation(next).then(
      () => { if (token !== navigationSequence) return; pushReactHistory(next); onNavigate(); },
      () => { if (token === navigationSequence) window.location.assign(next); },
    );
  };
  window.addEventListener('popstate', onPopState);
  window.addEventListener(CLOSE_PRODUCT_EVENT, onCloseProduct);
  document.addEventListener('click', onClick);
  return () => { window.removeEventListener('popstate', onPopState); window.removeEventListener(CLOSE_PRODUCT_EVENT, onCloseProduct); document.removeEventListener('click', onClick); };
}

function applyRouteDocument(route: string): void {
  const metadata = ROUTE_METADATA[route];
  if (!metadata) return;
  const view = new URLSearchParams(window.location.search).get('view');
  document.title = route === PORTAL_ROUTE && view === 'sms' ? '短信接码 · LancoAI' : metadata.title;
  if (metadata.bodyClass) document.body.className = metadata.bodyClass;
  else document.body.removeAttribute('class');
  if (metadata.shell) applyInitialShellPreferences();
}

function passwordMaxLength(): number {
  const value = Number(appRoot.dataset.passwordMaxLength);
  // The portal document provides the server value; the guarded console may
  // surface the modal without it. 256 is the network service's documented
  // default and its login API stays authoritative for validation.
  return Number.isInteger(value) && value > 0 ? value : 256;
}

function pagesForLocation(url: URL): Array<{ preload: () => Promise<void> }> {
  const route = normalizeRoute(url.pathname);
  if (route === PORTAL_ROUTE) return url.searchParams.get('view') === 'sms' ? [SmsPage] : [];
  if (route === ADMIN_ROUTE) return [ShopAdminPage];
  return [];
}

function preloadLocation(href: string): Promise<void> {
  return Promise.all(pagesForLocation(new URL(href, window.location.origin)).map(page => page.preload())).then(() => undefined);
}

function AdminPlaceholder({ status }: { status: 'loading' | 'anonymous' | 'unavailable' }) {
  if (status === 'loading') return null;
  const label = status === 'unavailable' ? '暂时无法确认登录状态。' : '请登录后继续访问此页面。';
  return <CodexShell active="shop" pageTitle="商品管理" authStatus={status}><section className="card"><p>{label}</p></section></CodexShell>;
}

function PortalRoute({ authenticated, status, loginOpen, onAuthenticated, onUnauthenticated, onClose }: {
  authenticated: boolean; status: 'loading' | 'anonymous' | 'authenticated' | 'unavailable'; loginOpen: boolean; onAuthenticated: (returnTo?: string) => Promise<void>; onUnauthenticated: () => void; onClose: () => void;
}) {
  const location = new URL(window.location.href);
  const view = location.searchParams.get('view');
  const returnTo = sanitizeReturnTo(location.searchParams.get('next') || `${PORTAL_ROUTE}${location.search}`);
  return <PortalSessionContext.Provider value={{ authenticated, status, onLogin: onUnauthenticated }}>
    <div inert={loginOpen ? true : undefined} aria-hidden={loginOpen ? true : undefined}><Suspense fallback={null}>{view === 'sms' ? <SmsPage/> : <ShopPage/>}</Suspense></div>
    <LoginModal open={loginOpen} realm="admin" passwordMaxLength={passwordMaxLength()} {...(returnTo ? { returnTo } : {})} onAuthenticated={onAuthenticated} onClose={onClose}/>
  </PortalSessionContext.Provider>;
}

function App() {
  const [locationKey, setLocationKey] = useState(currentLocationKey);
  const [loginRequested, setLoginRequested] = useState(false);
  const location = new URL(locationKey, window.location.origin);
  const route = normalizeRoute(location.pathname);
  const isAdminRoute = route === ADMIN_ROUTE;
  const session = useSession(undefined, REACT_DOCUMENT_ROUTES.has(route));
  const authenticated = session.status === 'authenticated' && session.role === 'admin';
  const sessionStatus = session.status === 'authenticated' ? 'anonymous' : session.status;
  const needsAdminLogin = isAdminRoute && !authenticated && session.status !== 'loading';
  const shouldOpenLogin = loginRequested || needsAdminLogin;

  // Back/forward has already changed the URL: load the page's code, then render
  // it. A chunk that cannot load (for example a stale tab after a deploy
  // replaced the release) falls back to a full load.
  const syncLocation = useCallback(() => {
    const target = currentLocationKey();
    return preloadLocation(target).then(
      () => { if (currentLocationKey() === target) setLocationKey(target); },
      () => { if (currentLocationKey() === target) window.location.replace(target); },
    );
  }, []);
  // On the console the modal stays while the page still needs a login, as before.
  const closeLogin = useCallback(() => { setLoginRequested(false); }, []);
  const requestLogin = useCallback(() => { setLoginRequested(true); }, []);
  const handleAuthenticated = useCallback(async (candidate?: string) => {
    await session.refresh();
    setLoginRequested(false);
    const returnTo = sanitizeReturnTo(candidate) || PORTAL_ROUTE;
    const destination = serviceHref(previewPath(returnTo));
    if (new URL(destination, window.location.origin).origin !== window.location.origin) {
      window.location.assign(destination);
      return;
    }
    // Pages of the other services are separate documents.
    if (!isShopDocument(new URL(returnTo, window.location.origin))) {
      window.location.assign(destination);
      return;
    }
    try {
      await preloadLocation(destination);
    } catch {
      window.location.assign(destination);
      return;
    }
    navigationSequence += 1;
    pushReactHistory(returnTo);
    if (destination !== returnTo) replaceReactHistory(destination);
    setLocationKey(currentLocationKey());
  }, [session]);

  // Shop views animate between snapshots, so they render inside the transition.
  const commitLocation = useCallback(() => { flushSync(() => { setLocationKey(currentLocationKey()); }); }, []);

  useLayoutEffect(() => { applyRouteDocument(route); }, [route, location.search]);
  useLayoutEffect(() => {
    // The shop restores after the catalog fetch, when its page has a height.
    if (route !== PORTAL_ROUTE || location.searchParams.get('view') === 'sms') restoreScrollPosition();
  }, [locationKey, route, location.search]);
  useEffect(() => installClientNavigation(() => { void syncLocation(); }, commitLocation), [syncLocation, commitLocation]);

  if (route === PORTAL_ROUTE) return <PortalRoute authenticated={authenticated} status={authenticated ? 'authenticated' : sessionStatus} loginOpen={shouldOpenLogin} onAuthenticated={handleAuthenticated} onUnauthenticated={requestLogin} onClose={closeLogin}/>;
  if (isAdminRoute) {
    const returnTo = sanitizeReturnTo(`${ADMIN_ROUTE}${location.search}`);
    return <>
      <Suspense fallback={null}>{authenticated ? <ShopAdminPage/> : <AdminPlaceholder status={sessionStatus}/>}</Suspense>
      <LoginModal open={shouldOpenLogin} realm="admin" passwordMaxLength={passwordMaxLength()} {...(returnTo ? { returnTo } : {})} onAuthenticated={handleAuthenticated} onClose={closeLogin}/>
    </>;
  }
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}

// A full page load waits for its own page chunk, so it never shows a fallback.
void preloadLocation(window.location.href).catch(() => undefined).then(() => { reactRoot.render(<App/>); });
