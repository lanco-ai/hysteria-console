import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ConfigPanel } from '../config/ConfigPage';
import { LandingPanel } from '../landing/LandingPage';
import { RulesPanel } from '../rules/RulesPage';

type TemplateRulesTab = 'template' | 'rules' | 'landing';

const tabs: { key: TemplateRulesTab; label: string }[] = [
  { key: 'template', label: '订阅模板' },
  { key: 'rules', label: '路由规则' },
  { key: 'landing', label: '家宽出口' },
];

function readTab(): TemplateRulesTab {
  const path = window.location.pathname.startsWith('/__react')
    ? window.location.pathname.slice('/__react'.length) || '/'
    : window.location.pathname;
  const queryTab = new URLSearchParams(window.location.search).get('tab');
  if (path === '/admin/landing-egresses' || queryTab === 'landing') return 'landing';
  return path === '/admin/rules' || queryTab === 'rules' ? 'rules' : 'template';
}

export function TemplateRulesPage({ publicHost }: { publicHost: string }) {
  const [tab, setTab] = useState<TemplateRulesTab>(readTab);

  useEffect(() => {
    const onPopState = () => setTab(readTab());
    window.addEventListener('popstate', onPopState);
    return () => window.removeEventListener('popstate', onPopState);
  }, []);

  const selectTab = (next: TemplateRulesTab) => {
    const prefix = window.location.pathname.startsWith('/__react') ? '/__react' : '';
    // 家宽出口 keeps its own address: the server's form redirects return to it.
    const path = next === 'landing' ? `${prefix}/admin/landing-egresses` : `${prefix}/admin/config?tab=${next}`;
    window.history.pushState(window.history.state, '', path);
    setTab(next);
  };

  return <AdminShell active="config" pageTitle="路由与出口" subtitle={publicHost}>
    <div className="template-rules-page admin-page">
      <div className="template-rules-tabs" role="tablist" aria-label="路由与出口设置">
        {tabs.map(item => <button key={item.key} type="button" role="tab" aria-selected={tab === item.key} className={`template-rules-tab${tab === item.key ? ' is-active' : ''}`} onClick={() => selectTab(item.key)}>{item.label}</button>)}
      </div>
      <div role="tabpanel" aria-label="订阅模板" hidden={tab !== 'template'}>
        <ConfigPanel active={tab === 'template'} openRules={() => selectTab('rules')}/>
      </div>
      <div role="tabpanel" aria-label="路由规则" hidden={tab !== 'rules'}>
        <RulesPanel active={tab === 'rules'}/>
      </div>
      <div role="tabpanel" aria-label="家宽出口" hidden={tab !== 'landing'}>
        {tab === 'landing' ? <LandingPanel/> : null}
      </div>
    </div>
  </AdminShell>;
}
