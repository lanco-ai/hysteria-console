import { createRoot } from 'react-dom/client';
import { LogsPage } from './features/network-admin/logs/LogsPage';
import { HomePage } from './features/public/HomePage';
import { applyInitialShellPreferences } from './shared/AdminShell';

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');

const reactRoot = createRoot(root);

if (window.location.pathname === '/__react/') {
  document.title = 'Hysteria · 连接网络，掌控全局';
  document.body.className = 'page-home page-site';
  reactRoot.render(<HomePage/>);
} else if (window.location.pathname === '/__react/admin/logs') {
  document.title = '清零日志';
  document.body.className = 'has-shell';
  applyInitialShellPreferences();
  const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
  reactRoot.render(<LogsPage publicHost={publicHost}/>);
} else {
  throw new Error(`Unsupported React entry: ${window.location.pathname}`);
}
