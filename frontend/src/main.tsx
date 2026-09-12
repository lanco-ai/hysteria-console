import { createRoot } from 'react-dom/client';
import { LogsPage } from './features/network-admin/logs/LogsPage';
import { applyInitialShellPreferences } from './shared/AdminShell';

const root = document.getElementById('root');
if (!(root instanceof HTMLElement)) throw new Error('React root is missing');

applyInitialShellPreferences();
const publicHost = root.dataset.publicHost?.trim() || window.location.hostname;
createRoot(root).render(<LogsPage publicHost={publicHost}/>);
