import type { KeyboardEvent } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ConfigPanel } from '../config/ConfigPage';
import { LandingPanel } from '../landing/LandingPage';
import { RulesPanel } from '../rules/RulesPage';

type TemplateRulesTab = 'template' | 'rules' | 'landing';

// 家宽出口 keeps its own address: the server's form redirects return to it.
const tabs: { key: TemplateRulesTab; label: string; path: string }[] = [
  { key: 'template', label: '订阅模板', path: '/admin/config' },
  { key: 'rules', label: '路由规则', path: '/admin/config?tab=rules' },
  { key: 'landing', label: '家宽出口', path: '/admin/landing-egresses' },
];

function tabFor(locationKey: string): TemplateRulesTab {
  const url = new URL(locationKey, window.location.origin);
  const path = url.pathname.replace(/^\/__react/, '') || '/';
  const queryTab = url.searchParams.get('tab');
  if (path === '/admin/landing-egresses' || queryTab === 'landing') return 'landing';
  return path === '/admin/rules' || queryTab === 'rules' ? 'rules' : 'template';
}

/** The tab follows the address. Tabs are links the app router follows, so the
 * sidebar, back and forward and the tabs themselves always agree. */
export function TemplateRulesPage({ locationKey, publicHost }: { locationKey: string; publicHost: string }) {
  const tab = tabFor(locationKey);
  const prefix = window.location.pathname.startsWith('/__react') ? '/__react' : '';

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(item => item.key === tab);
    const next = event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length]
      : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length]
      : event.key === 'Home' ? tabs[0]
      : event.key === 'End' ? tabs[tabs.length - 1]
      : undefined;
    if (!next) return;
    event.preventDefault();
    const control = document.getElementById(`template-rules-tab-${next.key}`);
    control?.focus();
    control?.click();
  };

  return <AdminShell active="config" pageTitle="路由与出口" subtitle={publicHost}>
    <div className="template-rules-page admin-page">
      <div className="template-rules-tabs" role="tablist" aria-label="路由与出口设置" onKeyDown={onTabKeyDown}>
        {tabs.map(item => <a key={item.key} href={`${prefix}${item.path}`} role="tab" id={`template-rules-tab-${item.key}`} aria-controls={`template-rules-panel-${item.key}`} aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} className={`template-rules-tab${tab === item.key ? ' is-active' : ''}`}>{item.label}</a>)}
      </div>
      <div role="tabpanel" id="template-rules-panel-template" aria-labelledby="template-rules-tab-template" hidden={tab !== 'template'}>
        <ConfigPanel active={tab === 'template'} rulesHref={`${prefix}/admin/config?tab=rules`}/>
      </div>
      <div role="tabpanel" id="template-rules-panel-rules" aria-labelledby="template-rules-tab-rules" hidden={tab !== 'rules'}>
        <RulesPanel active={tab === 'rules'}/>
      </div>
      <div role="tabpanel" id="template-rules-panel-landing" aria-labelledby="template-rules-tab-landing" hidden={tab !== 'landing'}>
        {tab === 'landing' ? <LandingPanel/> : null}
      </div>
    </div>
  </AdminShell>;
}
