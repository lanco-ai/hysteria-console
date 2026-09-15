import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { HEALTH_ENDPOINT, parseHealth } from './requests';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">健康探测加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function StatusBadge({ status }: { status: { ok: boolean; label: string } }) {
  return <span className={`badge ${status.ok ? 'badge-success' : 'badge-danger'}`}>{status.label}</span>;
}

export function HealthPage({ publicHost }: { publicHost: string }) {
  const health = useReadResource(HEALTH_ENDPOINT, { validate: parseHealth });
  const [polling, setPolling] = useState('自动更新 · 30 s');
  useEffect(() => {
    if (health.status !== 'success') return;
    const timer = window.setInterval(() => {
      setPolling('正在更新…');
      health.retry();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [health.status, health.retry]);
  const refresh = () => { setPolling('正在更新…'); health.retry(); };

  return <AdminShell active="health" pageTitle="健康状态" badge={health.status === 'success' ? '实时探测' : ''} subtitle={`${publicHost} · 服务与基础设施`} topbarExtra={<><button className="btn ghost btn-sm" type="button" onClick={refresh} disabled={health.status === 'loading'}>立即刷新</button><span className="badge poll-status">{health.status === 'loading' ? '加载中…' : polling}</span><span className="sr-only" role="status" aria-live="polite">{polling}</span></>}>
    {health.status === 'error' ? <ErrorState error={health.error} retry={health.retry}/> : null}
    {health.status === 'loading' ? <div className="card" role="status">正在运行健康探测…</div> : null}
    {health.status === 'success' ? <div className="admin-page health-page">
      <div className="health-top-kpis" aria-label="健康概览">{health.data.kpis.map(kpi => <div className="health-kpi-card" key={kpi.title}><div className="health-kpi-label">{kpi.title}</div><div className="health-kpi-value"><StatusBadge status={kpi}/></div></div>)}</div>
      <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">核心服务与基础设施</h2><div className="small">{health.data.ts.replace('T', ' · ')}</div></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="健康探测结果，可横向滚动"><table className="data-table"><thead><tr><th>项目</th><th>状态</th></tr></thead><tbody>{health.data.services.map(item => <tr key={item.title}><th scope="row">{item.title}</th><td><StatusBadge status={item}/></td></tr>)}</tbody></table></div></div></section>
    </div> : null}
  </AdminShell>;
}
