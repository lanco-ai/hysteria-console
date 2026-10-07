import { createRoot } from 'react-dom/client';
import { lazy, Suspense, useCallback, useEffect, useLayoutEffect, useState } from 'react';
import type { ComponentType } from 'react';
import './styles/index.css';
// Kept in the entry stylesheet: the console pages are prefetched, and a chunk's
// CSS is injected as soon as it loads, so it would otherwise appear on every page.
import './features/services/services.css';
import { LoginModal, resolveSameOriginReturnTo } from './features/auth/LoginModal';
import { PortalSessionContext, PortalShell } from './features/public/PortalShell';
import { ShopPage } from './features/public/ShopPage';
import { VideoAccessState } from './features/public/VideoAccessState';
import { applyInitialShellPreferences, CodexShell } from './shared/CodexShell';
import { useSession } from './shared/session';

// The public shop page stays in the entry chunk. Everything behind a login or
// on another view (admin console, AI chat with KaTeX, video, user panel) loads
// on demand, so shoppers no longer download the whole console. Navigation
// preloads the target page first (see preloadLocation), so a page normally
// renders synchronously; the React.lazy path is only a fallback.
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
const LandingPage = lazyPage(() => import('./features/network-admin/landing/LandingPage').then(module => module.LandingPage));
const UserDetailPage = lazyPage(() => import('./features/network-admin/user-detail/UserDetailPage').then(module => module.UserDetailPage));
const OverviewPage = lazyPage(() => import('./features/network-admin/overview/OverviewPage').then(module => module.OverviewPage));
const ChatPage = lazyPage(() => import('./features/chat/ChatPage').then(module => module.ChatPage));
const ServicesPage = lazyPage(() => import('./features/services/ServicesPage').then(module => module.ServicesPage));
const VideoPage = lazyPage(() => import('./features/video/VideoPage').then(module => module.VideoPage));
const PlansPage = lazyPage(() => import('./features/plans/PlansPage').then(module => module.PlansPage));
const GithubTrendingPage = lazyPage(() => import('./features/github-trending/GithubTrendingPage').then(module => module.GithubTrendingPage));
const ShopAdminPage = lazyPage(() => import('./features/shop/ShopAdminPage').then(module => module.ShopAdminPage));

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');
const appRoot = root;
const reactRoot = createRoot(appRoot);
const REACT_PREVIEW_PREFIX = '/__react';

const WORKBENCH_ROUTES = new Set(['/', '/auth', '/login', '/user/login', '/admin/chat', '/admin/video']);
const LOGIN_ROUTES = new Set(['/auth', '/login', '/user/login']);
const ADMIN_ROUTES = new Set([
  '/admin', '/admin/logs', '/admin/settings', '/admin/usage', '/admin/health',
  '/admin/incidents', '/admin/config', '/admin/rules', '/admin/landing-egresses', '/admin/services',
  '/admin/plans',
  '/admin/shop',
  '/admin/github-trending',
]);
const SAFE_LOGIN_QUERY_KEYS = new Set(['msg', 'tab', 'range', 'window', 'page', 'filter', 'view', 'conversation']);
const REACT_DOCUMENT_ROUTES = new Set([
  ...WORKBENCH_ROUTES, '/logout', '/user/logout', '/user/change-password', '/user/panel', ...ADMIN_ROUTES,
]);

type RouteMetadata = { title: string; bodyClass: string; shell?: boolean };
const ROUTE_METADATA: Record<string, RouteMetadata> = {
  '/': { title: '购物 · Hysteria', bodyClass: 'page-portal page-workbench' },
  '/auth': { title: '购物 · Hysteria', bodyClass: 'page-portal page-workbench' },
  '/login': { title: '购物 · Hysteria', bodyClass: 'page-portal page-workbench' },
  '/user/login': { title: '购物 · Hysteria', bodyClass: 'page-portal page-workbench' },
  '/admin': { title: '用户', bodyClass: 'has-shell', shell: true },
  '/admin/logs': { title: '运维', bodyClass: 'has-shell', shell: true },
  '/admin/settings': { title: '设置', bodyClass: 'has-shell', shell: true },
  '/admin/usage': { title: '流量分析', bodyClass: 'has-shell', shell: true },
  '/admin/health': { title: '运维', bodyClass: 'has-shell', shell: true },
  '/admin/incidents': { title: '运维', bodyClass: 'has-shell', shell: true },
  '/admin/config': { title: '模板与路由', bodyClass: 'has-shell', shell: true },
  '/admin/rules': { title: '模板与路由', bodyClass: 'has-shell', shell: true },
  '/admin/landing-egresses': { title: '家宽出口', bodyClass: 'has-shell', shell: true },
  '/admin/chat': { title: 'AI 对话', bodyClass: 'page-portal page-workbench' },
  '/admin/services': { title: '服务中心', bodyClass: 'has-shell', shell: true },
  '/admin/video': { title: 'AI 视频', bodyClass: 'page-portal page-workbench' },
  '/admin/plans': { title: '今日计划', bodyClass: 'has-shell', shell: true },
  '/admin/shop': { title: '商品管理', bodyClass: 'has-shell', shell: true },
  '/admin/github-trending': { title: 'GitHub 热榜', bodyClass: 'has-shell', shell: true },
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

function pushReactHistory(path: string): void {
  reactHistoryIndex += 1;
  window.history.pushState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', path);
  reactHistoryLocation = window.location.href;
}

function replaceReactHistory(path: string): void {
  window.history.replaceState(withReactHistoryIndex(window.history.state, reactHistoryIndex), '', path);
  reactHistoryLocation = window.location.href;
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
    navigationSequence += 1;
    onNavigate();
  };
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
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
      () => { window.location.assign(next); },
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
  const view = new URLSearchParams(window.location.search).get('view');
  document.title = route === '/' && (view === 'chat' || view === 'video')
    ? `${view === 'chat' ? 'AI 对话' : 'AI 视频'} · Hysteria` : metadata.title;
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
  '/admin/incidents': { active: 'operations', title: '运维' },
  '/admin/config': { active: 'config', title: '模板与路由' },
  '/admin/rules': { active: 'config', title: '模板与路由' },
  '/admin/landing-egresses': { active: 'landing-egresses', title: '家宽出口' },
  '/admin/services': { active: 'services', title: '服务中心' },
  '/admin/plans': { active: 'plans', title: '今日计划' },
  '/admin/shop': { active: 'shop', title: '商品管理' },
  '/admin/github-trending': { active: 'github-trending', title: 'GitHub 热榜' },
  '/admin/video': { active: 'video', title: 'AI 视频' },
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
  if (route === '/admin/logs' || route === '/admin/health' || route === '/admin/incidents') return <OperationsPage locationKey={locationKey} publicHost={publicHost}/>;
  if (route === '/admin/settings') return <SettingsPage publicHost={publicHost}/>;
  if (route === '/admin/usage') return <UsagePage publicHost={publicHost}/>;
  if (route === '/admin/config' || route === '/admin/rules') return <TemplateRulesPage publicHost={publicHost}/>;
  if (route === '/admin/services') return <ServicesPage publicHost={publicHost}/>;
  if (route === '/admin/plans') return <PlansPage/>;
  if (route === '/admin/shop') return <ShopAdminPage/>;
  if (route === '/admin/github-trending') return <GithubTrendingPage/>;
  if (route === '/admin/video') return <VideoPage publicHost={publicHost}/>;
  return <LandingPage publicHost={publicHost}/>;
}

// Mirrors WorkbenchRoute, AdminRoute and the user routes in App.
const ADMIN_PAGE_BY_ROUTE: Record<string, { preload: () => Promise<void> }> = {
  '/admin': OverviewPage,
  '/admin/logs': OperationsPage,
  '/admin/health': OperationsPage,
  '/admin/incidents': OperationsPage,
  '/admin/settings': SettingsPage,
  '/admin/usage': UsagePage,
  '/admin/config': TemplateRulesPage,
  '/admin/rules': TemplateRulesPage,
  '/admin/services': ServicesPage,
  '/admin/plans': PlansPage,
  '/admin/shop': ShopAdminPage,
  '/admin/github-trending': GithubTrendingPage,
};

// The console's own pages are small; once an admin page is up they are fetched
// in the background so moving between admin pages stays instant. Chat and
// video (KaTeX, canvas) stay on demand.
const ADMIN_CONSOLE_PAGES = [...new Set([...Object.values(ADMIN_PAGE_BY_ROUTE), LandingPage, UserDetailPage])];

function pagesForLocation(url: URL): Array<{ preload: () => Promise<void> }> {
  const route = normalizeRoute(url.pathname);
  if (WORKBENCH_ROUTES.has(route)) {
    const view = route === '/admin/chat' ? 'chat' : route === '/admin/video' ? 'video' : url.searchParams.get('view');
    return view === 'chat' ? [ChatPage] : view === 'video' ? [VideoPage] : [];
  }
  if (/^\/admin\/user\/[^/]+$/.test(route)) return [UserDetailPage];
  if (ADMIN_ROUTES.has(route)) return [ADMIN_PAGE_BY_ROUTE[route] ?? LandingPage];
  if (route === '/user/change-password') return [UserPasswordPage];
  if (route === '/user/panel') return [UserPanelPage];
  if (route === '/logout' || route === '/user/logout') return [LogoutPage];
  return [];
}

function preloadLocation(href: string): Promise<void> {
  return Promise.all(pagesForLocation(new URL(href, window.location.origin)).map(page => page.preload())).then(() => undefined);
}

function WorkbenchRoute({ route, publicHost, authenticated, status, loginOpen, onAuthenticated, onUnauthenticated, onClose }: {
  route: string; publicHost: string; authenticated: boolean; status: 'loading' | 'anonymous' | 'authenticated' | 'unavailable'; loginOpen: boolean; onAuthenticated: (returnTo?: string) => Promise<void>; onUnauthenticated: () => void; onClose: () => void;
}) {
  const location = new URL(window.location.href);
  const view = route === '/admin/chat' ? 'chat' : route === '/admin/video' ? 'video' : location.searchParams.get('view');
  const returnTo = sanitizeReturnTo(location.searchParams.get('next') || (!LOGIN_ROUTES.has(route) ? `${route}${location.search}` : undefined));
  return <PortalSessionContext.Provider value={{ authenticated, status, onLogin: onUnauthenticated }}>
    <div inert={loginOpen ? true : undefined} aria-hidden={loginOpen ? true : undefined}><Suspense fallback={null}>{view === 'chat' ? <ChatPage publicHost={publicHost} authenticated={authenticated} onUnauthenticated={onUnauthenticated} shell={PortalShell}/>
      : view === 'video' ? authenticated ? <VideoPage publicHost={publicHost} shell={PortalShell}/> : <VideoAccessState/>
        : <ShopPage key={location.search}/>}</Suspense></div>
    <LoginModal open={loginOpen} realm={route === '/user/login' ? 'user' : 'admin'} passwordMaxLength={passwordMaxLength()} {...(returnTo ? { returnTo } : {})} onAuthenticated={onAuthenticated} onClose={onClose}/>
  </PortalSessionContext.Provider>;
}

function App() {
  const [locationKey, setLocationKey] = useState(currentLocationKey);
  const [loginRequested, setLoginRequested] = useState(false);
  const location = new URL(locationKey, window.location.origin);
  const route = normalizeRoute(location.pathname);
  const publicHost = appRoot.dataset.publicHost?.trim() || window.location.hostname;
  const isProtectedAdminRoute = ADMIN_ROUTES.has(route) || /^\/admin\/user\/[^/]+$/.test(route);
  const needsSession = WORKBENCH_ROUTES.has(route) || isProtectedAdminRoute;
  const session = useSession(undefined, needsSession);
  const authenticated = session.status === 'authenticated' && session.role === 'admin';
  const sessionStatus = session.status === 'authenticated' ? 'anonymous' : session.status;
  const needsAdminLogin = isProtectedAdminRoute && !authenticated && session.status !== 'loading';
  const shouldOpenLogin = LOGIN_ROUTES.has(route) || loginRequested || ((route === '/admin/chat' || route === '/admin/video') && !authenticated && session.status !== 'loading') || needsAdminLogin;
  const protectedReturnTo = protectedRouteReturnTo(route, location.search);

  // Back/forward has already changed the URL: load the page's code, then render
  // it (usually instant, the page was visited). A chunk that cannot load (for
  // example a stale tab after a deploy replaced the release) falls back to a
  // full load.
  const syncLocation = useCallback(() => {
    const target = currentLocationKey();
    return preloadLocation(target).then(
      () => { if (currentLocationKey() === target) setLocationKey(target); },
      () => { window.location.replace(target); },
    );
  }, []);
  const navigate = useCallback((path: string) => {
    const token = ++navigationSequence;
    void preloadLocation(path).then(
      () => { if (token !== navigationSequence) return; pushReactHistory(path); setLocationKey(currentLocationKey()); },
      () => { window.location.assign(path); },
    );
  }, []);
  const closeLogin = useCallback(() => { setLoginRequested(false); if (LOGIN_ROUTES.has(route)) navigate(previewPath('/')); }, [navigate, route]);
  const requestLogin = useCallback(() => { setLoginRequested(true); }, []);
  const handleAuthenticated = useCallback(async (candidate?: string) => {
    await session.refresh();
    setLoginRequested(false);
    const returnTo = sanitizeReturnTo(candidate) || (route === '/user/login' ? '/user/panel' : '/');
    const destination = previewPath(returnTo);
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
  useEffect(() => installClientNavigation(() => { void syncLocation(); }), [syncLocation]);
  useEffect(() => {
    if (!isProtectedAdminRoute || !authenticated) return undefined;
    const timer = window.setTimeout(() => {
      for (const page of ADMIN_CONSOLE_PAGES) void page.preload().catch(() => undefined);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [isProtectedAdminRoute, authenticated]);
  useEffect(() => { if (LOGIN_ROUTES.has(route)) setLoginRequested(true); }, [route]);

  if (WORKBENCH_ROUTES.has(route)) return <WorkbenchRoute route={route} publicHost={publicHost} authenticated={authenticated} status={authenticated ? 'authenticated' : sessionStatus} loginOpen={shouldOpenLogin} onAuthenticated={handleAuthenticated} onUnauthenticated={requestLogin} onClose={closeLogin}/>;
  if (isProtectedAdminRoute) return <><Suspense fallback={null}><AdminRoute route={route} locationKey={locationKey} publicHost={publicHost} authenticated={authenticated} status={sessionStatus}/></Suspense><LoginModal open={shouldOpenLogin} realm="admin" passwordMaxLength={passwordMaxLength()} {...(protectedReturnTo ? { returnTo: protectedReturnTo } : {})} onAuthenticated={handleAuthenticated} onClose={closeLogin}/></>;
  if (route === '/user/change-password') return <Suspense fallback={null}><UserPasswordPage publicHost={publicHost}/></Suspense>;
  if (route === '/user/panel') return <Suspense fallback={null}><UserPanelPage publicHost={publicHost}/></Suspense>;
  if (route === '/logout' || route === '/user/logout') return <Suspense fallback={null}><LogoutPage realm={route === '/logout' ? 'admin' : 'user'} publicHost={publicHost}/></Suspense>;
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}

// A full page load waits for its own page chunk, so it never shows a fallback.
void preloadLocation(window.location.href).catch(() => undefined).then(() => { reactRoot.render(<App/>); });
