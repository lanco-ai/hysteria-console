import { useEffect, useState, type KeyboardEvent } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { HealthPanel, type CalibrationDraft } from '../health/HealthPage';
import { IncidentsPanel } from '../incidents/IncidentsPage';
import { LogsPanel } from '../logs/LogsPage';

type OperationsTab = 'health' | 'incidents' | 'logs';

const tabs: { key: OperationsTab; label: string }[] = [
  { key: 'health', label: '健康状态' },
  { key: 'incidents', label: '事故处理' },
  { key: 'logs', label: '清零日志' },
];

function readTab(): OperationsTab {
  const query = new URLSearchParams(window.location.search).get('tab');
  if (query === 'health' || query === 'incidents' || query === 'logs') return query;
  const path = window.location.pathname.replace(/^\/__react/, '');
  if (path === '/admin/incidents') return 'incidents';
  if (path === '/admin/logs') return 'logs';
  return 'health';
}

export function OperationsPage({ locationKey, publicHost }: { locationKey: string; publicHost: string }) {
  const [tab, setTab] = useState<OperationsTab>(readTab);
  const [calibrationDraft, setCalibrationDraft] = useState<CalibrationDraft | null>(null);

  useEffect(() => { setTab(readTab()); }, [locationKey]);

  const onTabKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(item => item.key === tab);
    const next = event.key === 'ArrowRight' ? tabs[(index + 1) % tabs.length]
      : event.key === 'ArrowLeft' ? tabs[(index + tabs.length - 1) % tabs.length]
      : event.key === 'Home' ? tabs[0]
      : event.key === 'End' ? tabs[tabs.length - 1]
      : undefined;
    if (!next) return;
    event.preventDefault();
    const control = document.getElementById(`operations-tab-${next.key}`);
    control?.focus();
    control?.click();
  };

  const prefix = window.location.pathname.startsWith('/__react') ? '/__react' : '';
  return <AdminShell active="operations" pageTitle="运维" badge={publicHost}>
    <div className="operations-page">
      <div className="template-rules-tabs" role="tablist" aria-label="运维视图" onKeyDown={onTabKeyDown}>
        {tabs.map(item => <a key={item.key} href={`${prefix}/admin/health?tab=${item.key}`} role="tab" id={`operations-tab-${item.key}`} aria-controls={`operations-panel-${item.key}`} aria-selected={tab === item.key} tabIndex={tab === item.key ? 0 : -1} className={`template-rules-tab${tab === item.key ? ' is-active' : ''}`}>{item.label}</a>)}
      </div>
      <div role="tabpanel" id="operations-panel-health" aria-labelledby="operations-tab-health" tabIndex={0} hidden={tab !== 'health'}>{tab === 'health' ? <HealthPanel calibrationDraft={calibrationDraft} setCalibrationDraft={setCalibrationDraft}/> : null}</div>
      <div role="tabpanel" id="operations-panel-incidents" aria-labelledby="operations-tab-incidents" tabIndex={0} hidden={tab !== 'incidents'}>{tab === 'incidents' ? <IncidentsPanel/> : null}</div>
      <div role="tabpanel" id="operations-panel-logs" aria-labelledby="operations-tab-logs" tabIndex={0} hidden={tab !== 'logs'}>{tab === 'logs' ? <LogsPanel/> : null}</div>
    </div>
  </AdminShell>;
}
