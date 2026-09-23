import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ConfigPanel } from '../config/ConfigPage';
import { RulesPanel } from '../rules/RulesPage';

type TemplateRulesTab = 'template' | 'rules';

function readTab(): TemplateRulesTab {
  const path = window.location.pathname.startsWith('/__react')
    ? window.location.pathname.slice('/__react'.length) || '/'
    : window.location.pathname;
  const queryTab = new URLSearchParams(window.location.search).get('tab');
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
    const path = `${prefix}/admin/config?tab=${next}`;
    window.history.pushState(window.history.state, '', path);
    setTab(next);
  };

  return <AdminShell active="config" pageTitle="模板与路由" subtitle={publicHost}>
    <div className="template-rules-page admin-page">
      <div className="template-rules-tabs" role="tablist" aria-label="模板与路由设置">
        <button type="button" role="tab" aria-selected={tab === 'template'} className={`template-rules-tab${tab === 'template' ? ' is-active' : ''}`} onClick={() => selectTab('template')}>订阅模板</button>
        <button type="button" role="tab" aria-selected={tab === 'rules'} className={`template-rules-tab${tab === 'rules' ? ' is-active' : ''}`} onClick={() => selectTab('rules')}>路由规则</button>
      </div>
      <div role="tabpanel" aria-label="订阅模板" hidden={tab !== 'template'}>
        <ConfigPanel active={tab === 'template'}/>
      </div>
      <div role="tabpanel" aria-label="路由规则" hidden={tab !== 'rules'}>
        <RulesPanel active={tab === 'rules'}/>
      </div>
    </div>
  </AdminShell>;
}
