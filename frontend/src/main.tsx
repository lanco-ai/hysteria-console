import { createRoot } from 'react-dom/client';
import { useEffect, useLayoutEffect, useState } from 'react';
import './styles/index.css';
import { LoginPage } from './features/auth/LoginPage';
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
import { HomePage } from './features/public/HomePage';
import { OverviewPage } from './features/network-admin/overview/OverviewPage';
import { applyInitialShellPreferences } from './shared/AdminShell';

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');
const appRoot = root;

const reactRoot = createRoot(appRoot);
const REACT_PREVIEW_PREFIX = '/__react';

// Keep preview aliases explicit while allowing the same build to serve the
// production document paths without a reverse-proxy pathname rewrite.
const REACT_PREVIEW_ROUTES = [
  '/__react/', '/__react/login', '/__react/user/login', '/__react/logout', '/__react/user/logout',
  '/__react/user/change-password', '/__react/user/panel', '/__react/admin',
  '/__react/admin/logs', '/__react/admin/settings', '/__react/admin/usage',
  '/__react/admin/health', '/__react/admin/incidents', '/__react/admin/config',
  '/__react/admin/rules', '/__react/admin/landing-egresses',
  '/__react/admin/user/demo_alex',
] as const;
const REACT_PREVIEW_USER_DETAIL_PREFIX = '/__react/admin/user/';
void REACT_PREVIEW_ROUTES;
void REACT_PREVIEW_USER_DETAIL_PREFIX;

const REACT_DOCUMENT_ROUTES = new Set([
  // Login is intentionally left as a document navigation: the server adds
  // the configured password limit to that bootstrap document.
  '/', '/logout', '/login', '/user/login', '/user/logout', '/user/change-password', '/user/panel',
  '/admin', '/admin/logs', '/admin/settings', '/admin/usage', '/admin/health',
  '/admin/incidents', '/admin/config', '/admin/rules', '/admin/landing-egresses',
]);

function normalizeRoute(pathname: string): string {
  return pathname.startsWith(REACT_PREVIEW_PREFIX)
    ? pathname.slice(REACT_PREVIEW_PREFIX.length) || '/'
    : pathname;
}

function currentLocationKey(): string {
  return window.location.href;
}

function isReactDocumentPath(pathname: string): boolean {
  const route = normalizeRoute(pathname);
  return REACT_DOCUMENT_ROUTES.has(route) || /^\/admin\/user\/[^/]+$/.test(route);
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
    // Hash links (including the skip link) should keep their native scrolling behavior.
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) return;
    const destinationPathname = window.location.pathname.startsWith(REACT_PREVIEW_PREFIX) && !url.pathname.startsWith(REACT_PREVIEW_PREFIX)
      ? `${REACT_PREVIEW_PREFIX}${normalizeRoute(url.pathname)}`
      : url.pathname;
    const next = `${destinationPathname}${url.search}${url.hash}`;
    const current = `${window.location.pathname}${window.location.search}${window.location.hash}`;
    if (next === current) return;
    event.preventDefault();
    window.history.pushState({}, '', next);
    onNavigate();
  };

  window.addEventListener('popstate', onPopState);
  document.addEventListener('click', onClick);
  return () => {
    window.removeEventListener('popstate', onPopState);
    document.removeEventListener('click', onClick);
  };
}

function applyRouteDocument(route: string): void {
  if (route === '/') {
    document.title = 'Hysteria · 连接网络，掌控全局';
    document.body.className = 'page-home page-site';
  } else if (route === '/admin') {
    document.title = '总览';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/logs') {
    document.title = '清零日志';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/settings') {
    document.title = '设置';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/usage') {
    document.title = '流量分析';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/health') {
    document.title = '健康状态';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/incidents') {
    document.title = '事故处理';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/config') {
    document.title = '模板配置';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/rules') {
    document.title = '路由规则';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/admin/landing-egresses') {
    document.title = '家宽出口';
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  } else if (route === '/user/change-password') {
    document.title = '修改面板密码';
    document.body.className = 'page-auth';
  } else if (route === '/user/panel') {
    document.title = '用户面板 · Hysteria';
    document.body.className = '';
  } else if (route === '/login') {
    document.title = '管理员登录 · Hysteria';
    document.body.className = 'page-auth page-admin-login';
  } else if (route === '/user/login') {
    document.title = '用户登录 · Hysteria';
    // The user login intentionally shares the hardened split-login layout;
    // retain the layout's existing style scope while exposing a semantic
    // realm class for selectors and diagnostics.
    document.body.className = 'page-auth page-admin-login page-user-login';
  } else if (route === '/logout' || route === '/user/logout') {
    document.title = '确认退出';
    document.body.removeAttribute('class');
  } else if (/^\/admin\/user\/[^/]+$/.test(route)) {
    document.title = `${decodeRouteSegment(route.slice('/admin/user/'.length))} · 用量画像`;
    document.body.className = 'has-shell';
    applyInitialShellPreferences();
  }
}

function decodeRouteSegment(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

function RouteContent({ route }: { route: string }) {
  const publicHost = appRoot.dataset.publicHost?.trim() || window.location.hostname;
  const userDetailMatch = route.match(/^\/admin\/user\/([^/]+)$/);
  const userDetailUid = userDetailMatch?.[1] ? decodeRouteSegment(userDetailMatch[1]) : '';

  if (route === '/') return <HomePage/>;
  if (route === '/admin') return <OverviewPage publicHost={publicHost}/>;
  if (route === '/admin/logs') return <LogsPage publicHost={publicHost}/>;
  if (route === '/admin/settings') return <SettingsPage publicHost={publicHost}/>;
  if (route === '/admin/usage') return <UsagePage publicHost={publicHost}/>;
  if (route === '/admin/health') return <HealthPage publicHost={publicHost}/>;
  if (route === '/admin/incidents') return <IncidentsPage publicHost={publicHost}/>;
  if (route === '/admin/config') return <ConfigPage publicHost={publicHost}/>;
  if (route === '/admin/rules') return <RulesPage publicHost={publicHost}/>;
  if (route === '/admin/landing-egresses') return <LandingPage publicHost={publicHost}/>;
  if (route === '/user/change-password') return <UserPasswordPage publicHost={publicHost}/>;
  if (route === '/user/panel') return <UserPanelPage publicHost={publicHost}/>;
  if (route === '/login') {
    const passwordMaxLength = Number(appRoot.dataset.passwordMaxLength);
    if (!Number.isInteger(passwordMaxLength) || passwordMaxLength <= 0) {
      throw new Error('Invalid login password length');
    }
    return <LoginPage passwordMaxLength={passwordMaxLength}/>;
  }
  if (route === '/user/login') {
    const passwordMaxLength = Number(appRoot.dataset.passwordMaxLength);
    if (!Number.isInteger(passwordMaxLength) || passwordMaxLength <= 0) {
      throw new Error('Invalid login password length');
    }
    return <LoginPage passwordMaxLength={passwordMaxLength} realm="user"/>;
  }
  if (route === '/logout' || route === '/user/logout') {
    return <LogoutPage realm={route === '/logout' ? 'admin' : 'user'} publicHost={publicHost}/>;
  }
  if (userDetailUid) return <UserDetailPage publicHost={publicHost} uid={userDetailUid}/>;
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}

function App() {
  const [locationKey, setLocationKey] = useState(currentLocationKey);
  const route = normalizeRoute(new URL(locationKey, window.location.origin).pathname);

  useLayoutEffect(() => {
    applyRouteDocument(route);
  }, [route]);

  useEffect(() => installClientNavigation(() => setLocationKey(currentLocationKey())), []);

  return <RouteContent route={route}/>;
}

reactRoot.render(<App/>);
