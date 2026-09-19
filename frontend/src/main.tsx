import { createRoot } from 'react-dom/client';
import { useCallback, useEffect, useLayoutEffect, useState } from 'react';
import './styles/index.css';
import { LoginModal, resolveSameOriginReturnTo } from './features/auth/LoginModal';
import { LogoutPage } from './features/auth/LogoutPage';
import { UserPasswordPage } from './features/auth/UserPasswordPage';
import { UserPanelPage } from './features/user/UserPanelPage';
import { LogsPage } from './features/network-admin/logs/LogsPage';
import { SettingsPage } from './features/network-admin/settings/SettingsPage';
import { UsagePage } from './features/network-admin/usage/UsagePage';
import { HealthPage } from './features/network-admin/health/HealthPage';
import { IncidentsPage } from './features/network-admin/incidents/IncidentsPage';
import { ConfigPage } from './features/network-admin/config/ConfigPage';
import { RulesPage } from './features/network-admin/rules/RulesPage';
import { LandingPage } from './features/network-admin/landing/LandingPage';
import { UserDetailPage } from './features/network-admin/user-detail/UserDetailPage';
import { OverviewPage } from './features/network-admin/overview/OverviewPage';
import { ChatPage } from './features/chat/ChatPage';
import { ServicesPage } from './features/services/ServicesPage';
import { VideoPage } from './features/video/VideoPage';
import { applyInitialShellPreferences, CodexShell } from './shared/CodexShell';
import { useSession } from './shared/session';

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');
const appRoot = root;
const reactRoot = createRoot(appRoot);
const REACT_PREVIEW_PREFIX = '/__react';

const WORKBENCH_ROUTES = new Set(['/', '/auth', '/login', '/user/login', '/admin/chat']);
const LOGIN_ROUTES = new Set(['/auth', '/login', '/user/login']);
const ADMIN_ROUTES = new Set([
  '/admin', '/admin/logs', '/admin/settings', '/admin/usage', '/admin/health',
  '/admin/incidents', '/admin/config', '/admin/rules', '/admin/landing-egresses', '/admin/video', '/admin/services',
]);
const SAFE_LOGIN_QUERY_KEYS = new Set(['msg', 'tab', 'range', 'window', 'page', 'filter']);
const REACT_DOCUMENT_ROUTES = new Set([
  ...WORKBENCH_ROUTES, '/logout', '/user/logout', '/user/change-password', '/user/panel', ...ADMIN_ROUTES,
]);

type RouteMetadata = { title: string; bodyClass: string; shell?: boolean };
const ROUTE_METADATA: Record<string, RouteMetadata> = {
  '/': { title: 'Hysteria 工作台', bodyClass: 'has-shell page-workbench', shell: true },
  '/auth': { title: 'Hysteria 工作台', bodyClass: 'has-shell page-workbench', shell: true },
  '/login': { title: 'Hysteria 工作台', bodyClass: 'has-shell page-workbench', shell: true },
  '/user/login': { title: 'Hysteria 工作台', bodyClass: 'has-shell page-workbench', shell: true },
  '/admin': { title: '总览', bodyClass: 'has-shell', shell: true },
  '/admin/logs': { title: '清零日志', bodyClass: 'has-shell', shell: true },
  '/admin/settings': { title: '设置', bodyClass: 'has-shell', shell: true },
  '/admin/usage': { title: '流量分析', bodyClass: 'has-shell', shell: true },
  '/admin/health': { title: '健康状态', bodyClass: 'has-shell', shell: true },
  '/admin/incidents': { title: '事故处理', bodyClass: 'has-shell', shell: true },
  '/admin/config': { title: '模板配置', bodyClass: 'has-shell', shell: true },
  '/admin/rules': { title: '路由规则', bodyClass: 'has-shell', shell: true },
  '/admin/landing-egresses': { title: '家宽出口', bodyClass: 'has-shell', shell: true },
  '/admin/chat': { title: 'AI 对话', bodyClass: 'has-shell page-workbench', shell: true },
  '/admin/services': { title: '服务中心', bodyClass: 'has-shell', shell: true },
  '/admin/video': { title: 'AI 视频', bodyClass: 'has-shell page-workbench', shell: true },
  '/user/change-password': { title: '修改面板密码', bodyClass: 'page-auth' },
  '/user/panel': { title: '用户面板 · Hysteria', bodyClass: '' },
  '/logout': { title: '确认退出', bodyClass: '' },
  '/user/logout': { title: '确认退出', bodyClass: '' },
};

function normalizeRoute(pathname: string): string {
  return pathname.startsWith(REACT_PREVIEW_PREFIX) ? pathname.slice(REACT_PREVIEW_PREFIX.length) || '/' : pathname;
}

function currentLocationKey(): string { return window.location.href; }

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
    if (SAFE_LOGIN_QUERY_KEYS.has(key)) query.append(key, item);
  }
  const suffix = query.toString();
  return `${destination.pathname}${suffix ? `?${suffix}` : ''}`;
}

function protectedRouteReturnTo(route: string, search: string): string | undefined {
  const query = new URLSearchParams(search);
  if (query.has('next')) return sanitizeReturnTo(query.get('next') || undefined);
  return sanitizeReturnTo(`${route}${search}`);
}

function installClientNavigation(onNavigate: () => void): () => void {
  const onPopState = () => onNavigate();
  const onClick = (event: MouseEvent) => {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    if (!(event.target instanceof Element)) return;
    const anchor = event.target.closest<HTMLAnchorElement>('a[href]');
    if (!anchor || anchor.target || anchor.hasAttribute('download')) return;
    const url = new URL(anchor.href, window.location.href);
    if (url.origin !== window.location.origin || !isReactDocumentPath(url.pathname)) return;
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return;
    event.preventDefault();
    const next = `${previewPath(normalizeRoute(url.pathname))}${url.search}${url.hash}`;
    if (next === `${window.location.pathname}${window.location.search}${window.location.hash}`) return;
    window.history.pushState({}, '', next);
    onNavigate();
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
  '/admin': { active: 'dashboard', title: '总览' },
  '/admin/logs': { active: 'logs', title: '清零日志' },
  '/admin/settings': { active: 'settings', title: '设置' },
  '/admin/usage': { active: 'usage', title: '流量分析' },
  '/admin/health': { active: 'health', title: '健康状态' },
  '/admin/incidents': { active: 'incidents', title: '事故处理' },
  '/admin/config': { active: 'config', title: '模板配置' },
  '/admin/rules': { active: 'rules', title: '路由规则' },
  '/admin/landing-egresses': { active: 'landing-egresses', title: '家宽出口' },
  '/admin/services': { active: 'services', title: '服务中心' },
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

function AdminRoute({ route, publicHost, authenticated, status }: { route: string; publicHost: string; authenticated: boolean; status: 'loading' | 'anonymous' | 'unavailable' }) {
  const detail = route.match(/^\/admin\/user\/([^/]+)$/);
  if (!authenticated) return <AdminPlaceholder route={route} status={status}/>;
  if (detail) return <UserDetailPage publicHost={publicHost} uid={decodeRouteSegment(detail[1] || '')}/>;
  if (route === '/admin') return <OverviewPage publicHost={publicHost}/>;
  if (route === '/admin/logs') return <LogsPage publicHost={publicHost}/>;
  if (route === '/admin/settings') return <SettingsPage publicHost={publicHost}/>;
  if (route === '/admin/usage') return <UsagePage publicHost={publicHost}/>;
  if (route === '/admin/health') return <HealthPage publicHost={publicHost}/>;
  if (route === '/admin/incidents') return <IncidentsPage publicHost={publicHost}/>;
  if (route === '/admin/config') return <ConfigPage publicHost={publicHost}/>;
  if (route === '/admin/rules') return <RulesPage publicHost={publicHost}/>;
  if (route === '/admin/services') return <ServicesPage publicHost={publicHost}/>;
  if (route === '/admin/video') return <VideoPage publicHost={publicHost}/>;
  return <LandingPage publicHost={publicHost}/>;
}

function WorkbenchRoute({ route, publicHost, authenticated, loginOpen, onAuthenticated, onUnauthenticated, onClose }: {
  route: string; publicHost: string; authenticated: boolean; loginOpen: boolean; onAuthenticated: (returnTo?: string) => Promise<void>; onUnauthenticated: () => void; onClose: () => void;
}) {
  const returnTo = sanitizeReturnTo(new URL(window.location.href).searchParams.get('next') || undefined);
  return <>
    <ChatPage publicHost={publicHost} authenticated={authenticated} onUnauthenticated={onUnauthenticated}/>
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
  const needsSession = WORKBENCH_ROUTES.has(route) || isProtectedAdminRoute;
  const session = useSession(undefined, needsSession);
  const authenticated = session.status === 'authenticated' && session.role === 'admin';
  const sessionStatus = session.status === 'authenticated' ? 'anonymous' : session.status;
  const needsAdminLogin = isProtectedAdminRoute && !authenticated && session.status !== 'loading';
  const shouldOpenLogin = LOGIN_ROUTES.has(route) || loginRequested || ((route === '/' || route === '/admin/chat') && session.status === 'anonymous') || needsAdminLogin;
  const protectedReturnTo = protectedRouteReturnTo(route, location.search);

  const navigate = useCallback((path: string) => { window.history.pushState({}, '', path); setLocationKey(currentLocationKey()); }, []);
  const closeLogin = useCallback(() => { setLoginRequested(false); if (LOGIN_ROUTES.has(route)) navigate(previewPath('/')); }, [navigate, route]);
  const requestLogin = useCallback(() => { setLoginRequested(true); }, []);
  const handleAuthenticated = useCallback(async (candidate?: string) => {
    await session.refresh();
    setLoginRequested(false);
    const returnTo = sanitizeReturnTo(candidate) || (route === '/user/login' ? '/user/panel' : '/');
    const destination = previewPath(returnTo);
    window.history.pushState({}, '', returnTo);
    if (destination !== returnTo) window.history.replaceState({}, '', destination);
    setLocationKey(currentLocationKey());
  }, [route, session]);

  useLayoutEffect(() => { applyRouteDocument(route); }, [route]);
  useEffect(() => installClientNavigation(() => setLocationKey(currentLocationKey())), []);
  useEffect(() => { if (LOGIN_ROUTES.has(route)) setLoginRequested(true); }, [route]);

  if (WORKBENCH_ROUTES.has(route)) return <WorkbenchRoute route={route} publicHost={publicHost} authenticated={authenticated} loginOpen={shouldOpenLogin} onAuthenticated={handleAuthenticated} onUnauthenticated={requestLogin} onClose={closeLogin}/>;
  if (isProtectedAdminRoute) return <><AdminRoute route={route} publicHost={publicHost} authenticated={authenticated} status={sessionStatus}/><LoginModal open={shouldOpenLogin} realm="admin" passwordMaxLength={passwordMaxLength()} {...(protectedReturnTo ? { returnTo: protectedReturnTo } : {})} onAuthenticated={handleAuthenticated} onClose={closeLogin}/></>;
  if (route === '/user/change-password') return <UserPasswordPage publicHost={publicHost}/>;
  if (route === '/user/panel') return <UserPanelPage publicHost={publicHost}/>;
  if (route === '/logout' || route === '/user/logout') return <LogoutPage realm={route === '/logout' ? 'admin' : 'user'} publicHost={publicHost}/>;
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}

reactRoot.render(<App/>);
