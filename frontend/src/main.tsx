import { createRoot } from 'react-dom/client';
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

const reactRoot = createRoot(root);
// Keep preview aliases explicit while allowing the same build to serve the
// production document paths without a reverse-proxy pathname rewrite.
const REACT_PREVIEW_ROUTES = [
  '/__react/', '/__react/login', '/__react/logout', '/__react/user/logout',
  '/__react/user/change-password', '/__react/user/panel', '/__react/admin',
  '/__react/admin/logs', '/__react/admin/settings', '/__react/admin/usage',
  '/__react/admin/health', '/__react/admin/incidents', '/__react/admin/config',
  '/__react/admin/rules', '/__react/admin/landing-egresses',
  '/__react/admin/user/demo_alex',
] as const;
const REACT_PREVIEW_USER_DETAIL_PREFIX = '/__react/admin/user/';
void REACT_PREVIEW_ROUTES;
void REACT_PREVIEW_USER_DETAIL_PREFIX;
const route = window.location.pathname.startsWith('/__react')
  ? window.location.pathname.slice('/__react'.length) || '/'
  : window.location.pathname;

function decodeRouteSegment(value: string): string {
  try { return decodeURIComponent(value); } catch { return value; }
}

const userDetailMatch = route.match(/^\/admin\/user\/([^/]+)$/);
const userDetailSegment = userDetailMatch?.[1] ?? '';
const userDetailUid = userDetailSegment ? decodeRouteSegment(userDetailSegment) : '';

if (route === '/') {
  document.title = 'Hysteria · 连接网络，掌控全局';
  document.body.className = 'page-home page-site';
  reactRoot.render(<HomePage/>);
} else if (route === '/admin') {
  document.title = '总览';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  reactRoot.render(<OverviewPage publicHost={root.dataset.publicHost?.trim() || window.location.hostname}/>);
} else if (route === '/admin/logs') {
  document.title = '清零日志';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<LogsPage publicHost={publicHost}/>);
} else if (route === '/admin/settings') {
  document.title = '设置';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<SettingsPage publicHost={publicHost}/>);
} else if (route === '/admin/usage') {
  document.title = '流量分析';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UsagePage publicHost={publicHost}/>);
} else if (route === '/admin/health') {
  document.title = '健康状态';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<HealthPage publicHost={publicHost}/>);
} else if (route === '/admin/incidents') {
  document.title = '事故处理';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<IncidentsPage publicHost={publicHost}/>);
} else if (route === '/admin/config') {
  document.title = '模板配置';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<ConfigPage publicHost={publicHost}/>);
} else if (route === '/admin/rules') {
  document.title = '路由规则';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<RulesPage publicHost={publicHost}/>);
} else if (route === '/admin/landing-egresses') {
  document.title = '家宽出口';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<LandingPage publicHost={publicHost}/>);
} else if (route === '/user/change-password') {
  document.title = '修改面板密码';
  document.body.className = 'page-auth';
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UserPasswordPage publicHost={publicHost}/>);
} else if (route === '/user/panel') {
  document.title = '用户面板 · Hysteria';
  document.body.className = '';
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UserPanelPage publicHost={publicHost}/>);
} else if (route === '/login') {
  document.title = '管理员登录 · Hysteria';
  document.body.className = 'page-auth page-admin-login';
  const passwordMaxLength = Number(root.dataset.passwordMaxLength);
  if (!Number.isInteger(passwordMaxLength) || passwordMaxLength <= 0) {
    throw new Error('Invalid login password length');
  }
  reactRoot.render(<LoginPage passwordMaxLength={passwordMaxLength}/>);
} else if (route === '/logout' || route === '/user/logout') {
  document.title = '确认退出';
  document.body.removeAttribute('class');
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  const realm = route === '/logout' ? 'admin' : 'user';
  reactRoot.render(<LogoutPage realm={realm} publicHost={publicHost}/>);
} else if (userDetailUid) {
  document.title = `${userDetailUid} · 用量画像`;
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UserDetailPage publicHost={publicHost} uid={userDetailUid}/>);
} else {
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}
