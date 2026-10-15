import { createRoot } from 'react-dom/client';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useState } from 'react';
import type { ComponentType } from 'react';
import './styles/index.css';
import { LoginModal, resolveSameOriginReturnTo } from './features/auth/LoginModal';
import { PortalSessionContext } from './features/public/PortalShell';
import { ShopPage } from './features/public/ShopPage';
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

// Opening another page starts at its top, as a full page load would; back and
// forward keep the browser's scroll restoration, and #fragments their target.
let scrollToTopFor: string | null = null;

function pushReactHistory(path: string): void {
  const leaving = window.location.pathname;
  reactHistoryIndex += 1;
  window.history.pushState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', path);
  reactHistoryLocation = window.location.href;
  scrollToTopFor = window.location.pathname !== leaving && !window.location.hash ? window.location.href : null;
}

function replaceReactHistory(path: string): void {
  window.history.replaceState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', path);
  reactHistoryLocation = window.location.href;
  if (scrollToTopFor !== null) scrollToTopFor = window.location.href;
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

function installClientNavigation(onNavigate: () => void): () => void {
  const onPopState = (event: PopStateEvent) => {
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
    if (nextIndex !== null) reactHistoryIndex = nextIndex;
    reactHistoryLocation = nextLocation;
    scrollToTopFor = null;
    navigationSequence += 1;
    onNavigate();
  };
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
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
    // Change the URL only once the target page can render, so the address bar
    // and the content switch together. Only the latest click wins.
    const token = ++navigationSequence;
    void preloadLocation(next).then(
      () => { if (token !== navigationSequence) return; pushReactHistory(next); onNavigate(); },
      () => { if (token === navigationSequence) window.location.assign(next); },
    );
  };
  window.addEventListener('popstate', onPopState);
  document.addEventListener('click', onClick);
  return () => { window.removeEventListener('popstate', onPopState); document.removeEventListener('click', onClick); };
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
    <div inert={loginOpen ? true : undefined} aria-hidden={loginOpen ? true : undefined}><Suspense fallback={null}>{view === 'sms' ? <SmsPage/> : <ShopPage key={location.search}/>}</Suspense></div>
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
    const destination = previewPath(returnTo);
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

  useLayoutEffect(() => { applyRouteDocument(route); }, [route, location.search]);
  useLayoutEffect(() => {
    if (scrollToTopFor === null || scrollToTopFor !== locationKey) return;
    scrollToTopFor = null;
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }, [locationKey]);
  useEffect(() => installClientNavigation(() => { void syncLocation(); }), [syncLocation]);

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
