import { useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { fmtBytes } from '../overview/presentation';
import { INCIDENTS_ENDPOINT, parseIncidents, writeIncidentAction } from './requests';
import type { IncidentUser, Incidents } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) { if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>; return <div className="card"><div className="err" role="alert">事故数据加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>; }
function Status({ ok, label }: { ok: boolean; label: string }) { return <span className={`badge ${ok ? 'badge-success' : 'badge-danger'}`}>{label}</span>; }
function ActionButton({ action, row, disabled, done }: { action: 'pause-user' | 'rotate-token'; row: IncidentUser; disabled: boolean; done: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const label = action === 'pause-user' ? '暂停 1 小时' : '轮换 Token';
  const submit = async () => {
    const question = action === 'pause-user' ? `暂停 ${row.user} 1 小时？` : `轮换 ${row.user} 的 Token？`;
    if (!window.confirm(question)) return;
    setBusy(true);
    const controller = new AbortController();
    try { await writeIncidentAction(action, { user: row.user, user_revision: row.revision, minutes: '60' }, controller.signal); done(`${label}已提交：${row.user}`); }
    catch (error) { done(error instanceof Error ? error.message : '操作失败，请刷新核对'); }
    finally { setBusy(false); }
  };
  return <button className="btn secondary btn-sm" type="button" disabled={disabled || busy} onClick={() => void submit()}>{busy ? '处理中…' : label}</button>;
}
function Content({ data, retry }: { data: Incidents; retry: () => void }) {
  const [message, setMessage] = useState('');
  const affected = data.peak_hour.users.length;
  return <div className="admin-page incidents-page">
    {message ? <div className="flash" role="status">{message}</div> : null}
    <div className="incident-page-header"><a className="btn ghost btn-sm" href="/api/v1/admin/incidents/evidence">下载证据 JSON</a><button className="btn ghost btn-sm" type="button" onClick={retry}>刷新数据</button></div>
    <div className="health-top-kpis"><div className="health-kpi-card"><div className="health-kpi-label">峰值小时</div><div className="health-kpi-value mono">{fmtBytes(data.peak_hour.bytes)}</div><div className="health-kpi-sub">{data.peak_hour.hour || '—'}</div></div><div className="health-kpi-card"><div className="health-kpi-label">受影响用户</div><div className="health-kpi-value mono">{affected}</div><div className="health-kpi-sub">峰值小时内</div></div><div className="health-kpi-card"><div className="health-kpi-label">活跃告警</div><div className="health-kpi-value mono">{data.alerts.length}</div><div className="health-kpi-sub">当前状态</div></div></div>
    <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">推荐处置</h2></div><div className="admin-section-body"><span className="badge">{data.line_radar.recommendation}</span><p className="small mt-sm">{data.line_radar.reason}</p></div></section>
    <div className="incident-dual-grid"><section className="admin-section incident-panel"><div className="admin-section-header"><h2 className="admin-section-title">峰值小时相关用户</h2></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="峰值小时相关用户，可横向滚动"><table className="data-table"><thead><tr><th>用户</th><th>峰值小时流量</th></tr></thead><tbody>{data.peak_hour.users.length ? data.peak_hour.users.map(row => <tr key={row.user}><th scope="row">{row.user}</th><td className="mono">{fmtBytes(row.bytes)}</td></tr>) : <tr><td className="empty" colSpan={2}>峰值小时暂无用户流量</td></tr>}</tbody></table></div></div></section><section className="admin-section incident-panel"><div className="admin-section-header"><h2 className="admin-section-title">近期告警状态</h2></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="近期告警状态，可横向滚动"><table className="data-table"><thead><tr><th>类型</th><th>用户</th><th>键</th></tr></thead><tbody>{data.alerts.length ? data.alerts.map(row => <tr key={`${row.kind}-${row.user}-${row.key}`}><td>{row.label}</td><td>{row.user}</td><td><code>{row.key}</code></td></tr>) : <tr><td className="empty" colSpan={3}>暂无告警状态</td></tr>}</tbody></table></div></div></section></div>
    <section className="admin-section"><div className="admin-section-header"><div><h2 className="admin-section-title">处置候选用户</h2><div className="small">按近 24 小时流量排序</div></div></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="处置候选用户，可横向滚动"><table className="data-table"><thead><tr><th>用户</th><th>24h 流量</th><th>操作</th></tr></thead><tbody>{data.users.length ? data.users.map(row => <tr key={row.user}><th scope="row"><div>{row.user}</div><div className="small faint">{row.note || row.expiry_label}</div></th><td className="mono">{fmtBytes(row.last_24h_bytes)}</td><td><div className="row gap-sm"><ActionButton action="pause-user" row={row} disabled={false} done={setMessage}/><ActionButton action="rotate-token" row={row} disabled={false} done={setMessage}/><a className="btn ghost btn-sm" href={`/admin/user/${encodeURIComponent(row.user)}`}>画像</a></div></td></tr>) : <tr><td className="empty" colSpan={3}>暂无用户</td></tr>}</tbody></table></div></div></section>
    <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">线路质量摘要</h2></div><div className="admin-section-body">{data.line_radar.rows.map(row => <div className="radar-summary-row" key={row.key}><span>{row.label}</span><span className="mono">{row.share.toFixed(1)}%</span><Status ok={row.ok} label={row.status}/></div>)}</div></section>
  </div>;
}
export function IncidentsPanel() { const incidents = useReadResource(INCIDENTS_ENDPOINT, { validate: parseIncidents }); return <>{incidents.status === 'error' ? <ErrorState error={incidents.error} retry={incidents.retry}/> : null}{incidents.status === 'loading' ? <LoadingState label="正在加载事故数据…"/> : null}{incidents.status === 'success' ? <Content data={incidents.data} retry={incidents.retry}/> : null}</>; }

export function IncidentsPage({ publicHost }: { publicHost: string }) { return <AdminShell active="operations" pageTitle="运维" subtitle={publicHost}><IncidentsPanel/></AdminShell>; }
