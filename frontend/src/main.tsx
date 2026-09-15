import { createRoot } from 'react-dom/client';
import { LoginPage } from './features/auth/LoginPage';
import { LogoutPage } from './features/auth/LogoutPage';
import { UserPasswordPage } from './features/auth/UserPasswordPage';
import { LogsPage } from './features/network-admin/logs/LogsPage';
import { SettingsPage } from './features/network-admin/settings/SettingsPage';
import { UsagePage } from './features/network-admin/usage/UsagePage';
import { HomePage } from './features/public/HomePage';
import { OverviewPage } from './features/network-admin/overview/OverviewPage';
import { applyInitialShellPreferences } from './shared/AdminShell';

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');

const reactRoot = createRoot(root);

if (window.location.pathname === '/__react/') {
  document.title = 'Hysteria · 连接网络，掌控全局';
  document.body.className = 'page-home page-site';
  reactRoot.render(<HomePage/>);
} else if (window.location.pathname === '/__react/admin') {
  document.title = '总览';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  reactRoot.render(<OverviewPage publicHost={root.dataset.publicHost?.trim() || window.location.hostname}/>);
} else if (window.location.pathname === '/__react/admin/logs') {
  document.title = '清零日志';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<LogsPage publicHost={publicHost}/>);
} else if (window.location.pathname === '/__react/admin/settings') {
  document.title = '设置';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<SettingsPage publicHost={publicHost}/>);
} else if (window.location.pathname === '/__react/admin/usage') {
  document.title = '流量分析';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UsagePage publicHost={publicHost}/>);
} else if (window.location.pathname === '/__react/user/change-password') {
  document.title = '修改面板密码';
  document.body.className = 'page-auth';
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<UserPasswordPage publicHost={publicHost}/>);
} else if (window.location.pathname === '/__react/login') {
  document.title = '管理员登录 · Hysteria';
  document.body.className = 'page-auth page-admin-login';
  const passwordMaxLength = Number(root.dataset.passwordMaxLength);
  if (!Number.isInteger(passwordMaxLength) || passwordMaxLength <= 0) {
    throw new Error('Invalid login password length');
  }
  reactRoot.render(<LoginPage passwordMaxLength={passwordMaxLength}/>);
} else if (
  window.location.pathname === '/__react/logout'
  || window.location.pathname === '/__react/user/logout'
) {
  document.title = '确认退出';
  document.body.removeAttribute('class');
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  const realm = window.location.pathname === '/__react/logout' ? 'admin' : 'user';
  reactRoot.render(<LogoutPage realm={realm} publicHost={publicHost}/>);
} else {
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}
