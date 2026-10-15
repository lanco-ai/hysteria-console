import { serviceHref } from './shared/serviceHref';
import { createRoot } from 'react-dom/client';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useState } from 'react';
import type { ComponentType } from 'react';
import './styles/index.css';
import { LoginModal, resolveSameOriginReturnTo } from './features/auth/LoginModal';
import { applyInitialShellPreferences, CodexShell } from './shared/CodexShell';
import { useSession } from './shared/session';

// The network service serves the login pages, the network console and the user
// panel. The portal (/) and 商品管理 belong to the shop service; 今日计划, AI 对话,
// AI 视频, 服务中心 and 开源发现 to the workbench + AI service. Links to those are
// ordinary full page loads. Pages load on demand; navigation preloads the target
// page first (see preloadLocation), so a page normally renders synchronously.
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

const LogoutPage = lazyPage(() => import('./features/auth/LogoutPage').then(module => module.LogoutPage));
const UserPasswordPage = lazyPage(() => import('./features/auth/UserPasswordPage').then(module => module.UserPasswordPage));
const UserPanelPage = lazyPage(() => import('./features/user/UserPanelPage').then(module => module.UserPanelPage));
const SettingsPage = lazyPage(() => import('./features/network-admin/settings/SettingsPage').then(module => module.SettingsPage));
const UsagePage = lazyPage(() => import('./features/network-admin/usage/UsagePage').then(module => module.UsagePage));
const OperationsPage = lazyPage(() => import('./features/network-admin/operations/OperationsPage').then(module => module.OperationsPage));
const TemplateRulesPage = lazyPage(() => import('./features/network-admin/template-rules/TemplateRulesPage').then(module => module.TemplateRulesPage));
const UserDetailPage = lazyPage(() => import('./features/network-admin/user-detail/UserDetailPage').then(module => module.UserDetailPage));
const OverviewPage = lazyPage(() => import('./features/network-admin/overview/OverviewPage').then(module => module.OverviewPage));

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');
const appRoot = root;
const reactRoot = createRoot(appRoot);
const REACT_PREVIEW_PREFIX = '/__react';

const LOGIN_ROUTES = new Set(['/auth', '/login', '/user/login']);
const ADMIN_ROUTES = new Set([
  '/admin', '/admin/logs', '/admin/settings', '/admin/usage', '/admin/health',
  '/admin/incidents', '/admin/config', '/admin/rules', '/admin/landing-egresses',
]);
const SAFE_LOGIN_QUERY_KEYS = new Set(['msg', 'tab', 'range', 'window', 'page', 'filter', 'view', 'conversation']);
const REACT_DOCUMENT_ROUTES = new Set([
  ...LOGIN_ROUTES, '/logout', '/user/logout', '/user/change-password', '/user/panel', ...ADMIN_ROUTES,
]);

type RouteMetadata = { title: string; bodyClass: string; shell?: boolean };
const ROUTE_METADATA: Record<string, RouteMetadata> = {
  '/auth': { title: '登录 · LancoAI', bodyClass: 'page-portal page-workbench' },
  '/login': { title: '登录 · LancoAI', bodyClass: 'page-portal page-workbench' },
  '/user/login': { title: '登录 · LancoAI', bodyClass: 'page-portal page-workbench' },
  '/admin': { title: '用户', bodyClass: 'has-shell', shell: true },
  '/admin/logs': { title: '运维', bodyClass: 'has-shell', shell: true },
  '/admin/settings': { title: '设置', bodyClass: 'has-shell', shell: true },
  '/admin/usage': { title: '流量分析', bodyClass: 'has-shell', shell: true },
  '/admin/health': { title: '运维', bodyClass: 'has-shell', shell: true },
  '/admin/incidents': { title: '流量分析', bodyClass: 'has-shell', shell: true },
  '/admin/config': { title: '路由与出口', bodyClass: 'has-shell', shell: true },
  '/admin/rules': { title: '路由与出口', bodyClass: 'has-shell', shell: true },
  '/admin/landing-egresses': { title: '路由与出口', bodyClass: 'has-shell', shell: true },
  '/user/change-password': { title: '修改面板密码', bodyClass: 'page-auth' },
  '/user/panel': { title: '用户面板 · Hysteria', bodyClass: '' },
  '/logout': { title: '确认退出', bodyClass: '' },
  '/user/logout': { title: '确认退出', bodyClass: '' },
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
// The intent names the location it was pushed for, so it can never fire on a
// later navigation; every push replaces it and back/forward clears it.
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

function isReactDocumentPath(pathname: string): boolean {
  const route = normalizeRoute(pathname);
  return REACT_DOCUMENT_ROUTES.has(route) || /^\/admin\/user\/[^/]+$/.test(route);
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

function protectedRouteReturnTo(route: string, search: string): string | undefined {
  const query = new URLSearchParams(search);
  if (query.has('next')) return sanitizeReturnTo(query.get('next') || undefined);
  return sanitizeReturnTo(`${route}${search}`);
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
    const url = new URL(serviceHref(anchor.href), window.location.href);
    if (url.origin !== window.location.origin) {
      if (url.href !== anchor.href) { event.preventDefault(); window.location.assign(url.href); }
      return;
    }
    if (url.origin !== window.location.origin || !isReactDocumentPath(url.pathname)) return;
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

function decodeRouteSegment(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

function applyRouteDocument(route: string): void {
  const detail = route.match(/^\/admin\/user\/([^/]+)$/);
  const metadata = detail
    ? { title: `${decodeRouteSegment(detail[1] || '')} · 用量画像`, bodyClass: 'has-shell', shell: true }
    : ROUTE_METADATA[route];
  if (!metadata) return;
  document.title = metadata.title;
  if (metadata.bodyClass) document.body.className = metadata.bodyClass;
  else document.body.removeAttribute('class');
  if (metadata.shell) applyInitialShellPreferences();
}

function passwordMaxLength(): number {
  const value = Number(appRoot.dataset.passwordMaxLength);
  // Login-compatible documents provide the server value.  A guarded admin
  // document may surface the modal after its session check without that
  // bootstrap attribute; 256 is the server's documented default and the API
  // remains authoritative for validation.
  return Number.isInteger(value) && value > 0 ? value : 256;
}

const ADMIN_ROUTE_DETAILS: Record<string, { active: string; title: string }> = {
  '/admin': { active: 'dashboard', title: '用户' },
  '/admin/logs': { active: 'operations', title: '运维' },
  '/admin/settings': { active: 'settings', title: '设置' },
  '/admin/usage': { active: 'usage', title: '流量分析' },
  '/admin/health': { active: 'operations', title: '运维' },
  '/admin/incidents': { active: 'usage', title: '流量分析' },
  '/admin/config': { active: 'config', title: '路由与出口' },
  '/admin/rules': { active: 'config', title: '路由与出口' },
  '/admin/landing-egresses': { active: 'config', title: '路由与出口' },
};

function AdminPlaceholder({ route, status }: { route: string; status: 'loading' | 'anonymous' | 'unavailable' }) {
  if (status === 'loading') return null;
  const detail = route.match(/^\/admin\/user\/([^/]+)$/);
  const metadata = detail
    ? { active: 'dashboard', title: `${decodeRouteSegment(detail[1] || '')} · 用量画像` }
    : ADMIN_ROUTE_DETAILS[route] || { active: 'dashboard', title: 'Hysteria 工作台' };
  const label = status === 'unavailable' ? '暂时无法确认登录状态。' : '请登录后继续访问此页面。';
  return <CodexShell active={metadata.active} pageTitle={metadata.title} authStatus={status}><section className="card"><p>{label}</p></section></CodexShell>;
}

function AdminRoute({ route, locationKey, publicHost, authenticated, status }: { route: string; locationKey: string; publicHost: string; authenticated: boolean; status: 'loading' | 'anonymous' | 'unavailable' }) {
  const detail = route.match(/^\/admin\/user\/([^/]+)$/);
  if (!authenticated) return <AdminPlaceholder route={route} status={status}/>;
  if (detail) return <UserDetailPage publicHost={publicHost} uid={decodeRouteSegment(detail[1] || '')}/>;
  if (route === '/admin') return <OverviewPage publicHost={publicHost}/>;
  if (route === '/admin/logs' || route === '/admin/health') return <OperationsPage locationKey={locationKey} publicHost={publicHost}/>;
  if (route === '/admin/settings') return <SettingsPage publicHost={publicHost}/>;
  if (route === '/admin/usage' || route === '/admin/incidents') return <UsagePage publicHost={publicHost}/>;
  if (route === '/admin/config' || route === '/admin/rules' || route === '/admin/landing-egresses') return <TemplateRulesPage locationKey={locationKey} publicHost={publicHost}/>;
  return <OverviewPage publicHost={publicHost}/>;
}

// Mirrors AdminRoute and the user routes in App.
const ADMIN_PAGE_BY_ROUTE: Record<string, { preload: () => Promise<void> }> = {
  '/admin': OverviewPage,
  '/admin/logs': OperationsPage,
  '/admin/health': OperationsPage,
  '/admin/incidents': UsagePage,
  '/admin/settings': SettingsPage,
  '/admin/usage': UsagePage,
  '/admin/config': TemplateRulesPage,
  '/admin/rules': TemplateRulesPage,
  '/admin/landing-egresses': TemplateRulesPage,
};

// The console's own pages are small; once an admin page is up they are fetched
// in the background so moving between admin pages stays instant.
const ADMIN_CONSOLE_PAGES = [...new Set([...Object.values(ADMIN_PAGE_BY_ROUTE), UserDetailPage])];

// Old links follow moved pages: 事故处理 left 运维 for 流量分析.
function legacyToolLocation(url: URL): string | null {
  if (normalizeRoute(url.pathname) === '/admin/health' && url.searchParams.get('tab') === 'incidents') return previewPath('/admin/incidents');
  return null;
}

function pagesForLocation(url: URL): Array<{ preload: () => Promise<void> }> {
  const route = normalizeRoute(url.pathname);
  if (/^\/admin\/user\/[^/]+$/.test(route)) return [UserDetailPage];
  if (ADMIN_ROUTES.has(route)) return [ADMIN_PAGE_BY_ROUTE[route] ?? OverviewPage];
  if (route === '/user/change-password') return [UserPasswordPage];
  if (route === '/user/panel') return [UserPanelPage];
  if (route === '/logout' || route === '/user/logout') return [LogoutPage];
  return [];
}

function preloadLocation(href: string): Promise<void> {
  return Promise.all(pagesForLocation(new URL(href, window.location.origin)).map(page => page.preload())).then(() => undefined);
}

// Login pages: the modal over an empty page. The storefront that used to sit
// behind it belongs to the shop service now.
function LoginRoute({ route, loginOpen, onAuthenticated, onClose }: {
  route: string; loginOpen: boolean; onAuthenticated: (returnTo?: string) => Promise<void>; onClose: () => void;
}) {
  const location = new URL(window.location.href);
  const returnTo = sanitizeReturnTo(location.searchParams.get('next') || undefined);
  return <>
    <main className="portal-main" id="main-content" tabIndex={-1} inert={loginOpen ? true : undefined} aria-hidden={loginOpen ? true : undefined}/>
    <LoginModal open={loginOpen} realm={route === '/user/login' ? 'user' : 'admin'} passwordMaxLength={passwordMaxLength()} {...(returnTo ? { returnTo } : {})} onAuthenticated={onAuthenticated} onClose={onClose}/>
  </>;
}

function App() {
  const [locationKey, setLocationKey] = useState(currentLocationKey);
  const [loginRequested, setLoginRequested] = useState(false);
  const location = new URL(locationKey, window.location.origin);
  const route = normalizeRoute(location.pathname);
  const publicHost = appRoot.dataset.publicHost?.trim() || window.location.hostname;
  const isProtectedAdminRoute = ADMIN_ROUTES.has(route) || /^\/admin\/user\/[^/]+$/.test(route);
  const needsSession = LOGIN_ROUTES.has(route) || isProtectedAdminRoute;
  const session = useSession(undefined, needsSession);
  const authenticated = session.status === 'authenticated' && session.role === 'admin';
  const sessionStatus = session.status === 'authenticated' ? 'anonymous' : session.status;
  const needsAdminLogin = isProtectedAdminRoute && !authenticated && session.status !== 'loading';
  const shouldOpenLogin = LOGIN_ROUTES.has(route) || loginRequested || needsAdminLogin;
  const legacyTool = legacyToolLocation(location);
  const protectedReturnTo = protectedRouteReturnTo(route, location.search);

  // Back/forward has already changed the URL: load the page's code, then render
  // it (usually instant, the page was visited). A chunk that cannot load (for
  // example a stale tab after a deploy replaced the release) falls back to a
  // full load.
  const syncLocation = useCallback(() => {
    const target = currentLocationKey();
    return preloadLocation(target).then(
      () => { if (currentLocationKey() === target) setLocationKey(target); },
      () => { if (currentLocationKey() === target) window.location.replace(target); },
    );
  }, []);
  // The portal at / is the shop service's page: a full page load.
  const closeLogin = useCallback(() => { setLoginRequested(false); if (LOGIN_ROUTES.has(route)) window.location.assign(previewPath('/')); }, [route]);
  const handleAuthenticated = useCallback(async (candidate?: string) => {
    await session.refresh();
    setLoginRequested(false);
    const returnTo = sanitizeReturnTo(candidate) || (route === '/user/login' ? '/user/panel' : '/');
    const destination = serviceHref(previewPath(returnTo));
    if (new URL(destination, window.location.origin).origin !== window.location.origin) {
      window.location.assign(destination);
      return;
    }
    // Pages of the other services are separate documents.
    if (!isReactDocumentPath(new URL(returnTo, window.location.origin).pathname)) {
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
  }, [route, session]);

  useLayoutEffect(() => { applyRouteDocument(route); }, [route, location.search]);
  useLayoutEffect(() => {
    if (scrollToTopFor === null || scrollToTopFor !== locationKey) return;
    scrollToTopFor = null;
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' });
  }, [locationKey]);
  useEffect(() => installClientNavigation(() => { void syncLocation(); }), [syncLocation]);
  useEffect(() => {
    if (!isProtectedAdminRoute || !authenticated) return undefined;
    const timer = window.setTimeout(() => {
      for (const page of ADMIN_CONSOLE_PAGES) void page.preload().catch(() => undefined);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [isProtectedAdminRoute, authenticated]);
  useEffect(() => { if (LOGIN_ROUTES.has(route)) setLoginRequested(true); }, [route]);
  useLayoutEffect(() => { if (legacyTool) window.location.replace(legacyTool); }, [legacyTool]);

  if (legacyTool) return null;
  if (LOGIN_ROUTES.has(route)) return <LoginRoute route={route} loginOpen={shouldOpenLogin} onAuthenticated={handleAuthenticated} onClose={closeLogin}/>;
  if (isProtectedAdminRoute) return <><Suspense fallback={null}><AdminRoute route={route} locationKey={locationKey} publicHost={publicHost} authenticated={authenticated} status={sessionStatus}/></Suspense><LoginModal open={shouldOpenLogin} realm="admin" passwordMaxLength={passwordMaxLength()} {...(protectedReturnTo ? { returnTo: protectedReturnTo } : {})} onAuthenticated={handleAuthenticated} onClose={closeLogin}/></>;
  if (route === '/user/change-password') return <Suspense fallback={null}><UserPasswordPage publicHost={publicHost}/></Suspense>;
  if (route === '/user/panel') return <Suspense fallback={null}><UserPanelPage publicHost={publicHost}/></Suspense>;
  if (route === '/logout' || route === '/user/logout') return <Suspense fallback={null}><LogoutPage realm={route === '/logout' ? 'admin' : 'user'} publicHost={publicHost}/></Suspense>;
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}

// A full page load waits for its own page chunk, so it never shows a fallback.
void preloadLocation(window.location.href).catch(() => undefined).then(() => { reactRoot.render(<App/>); });
