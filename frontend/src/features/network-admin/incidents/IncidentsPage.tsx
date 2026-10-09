import { useState } from 'react';
import { LoadingState } from '../../../shared/LoadingState';
import type { ReadResourceResult } from '../../../shared/readResource';
import { fmtBytes } from '../overview/presentation';
import { writeIncidentAction } from './requests';
import type { Incidents } from './types';

function ErrorState({ status, message, retry }: { status: number; message: string; retry: () => void }) {
  if (status === 401) return <div className="err" role="alert">管理员登录已失效。<a href="/login">前往登录</a></div>;
  return <div className="err" role="alert">事故数据加载失败：{message} <button className="btn secondary btn-sm" type="button" onClick={retry}>重试</button></div>;
}

/** Pause or rotate one user; the revision guards against acting on stale data. */
export function ActionButton({ action, user, revision, done }: { action: 'pause-user' | 'rotate-token'; user: string; revision: string; done: (message: string) => void }) {
  const [busy, setBusy] = useState(false);
  const label = action === 'pause-user' ? '暂停 1 小时' : '轮换 Token';
  const submit = async () => {
    const question = action === 'pause-user' ? `暂停 ${user} 1 小时？` : `轮换 ${user} 的 Token？`;
    if (!window.confirm(question)) return;
    setBusy(true);
    const controller = new AbortController();
    try { await writeIncidentAction(action, { user, user_revision: revision, minutes: '60' }, controller.signal); done(`${label}已提交：${user}`); }
    catch (error) { done(error instanceof Error ? error.message : '操作失败，请刷新核对'); }
    finally { setBusy(false); }
  };
  return <button className="btn secondary btn-sm" type="button" disabled={busy} onClick={() => void submit()}>{busy ? '处理中…' : label}</button>;
}

/** Peak hour and alert state; the per-user ranking with actions lives beside the heatmap. */
export function IncidentsSection({ incidents }: { incidents: ReadResourceResult<Incidents> }) {
  return <section className="admin-section incidents-section" id="incidents" aria-labelledby="incidents-title" tabIndex={-1}>
    <div className="admin-section-header">
      <div><h2 className="admin-section-title" id="incidents-title">异常与告警</h2><div className="small">峰值小时与当前告警；处置操作在上方用户排行中。</div></div>
      <div className="row gap-sm"><a className="btn ghost btn-sm" href="/api/v1/admin/incidents/evidence">下载证据 JSON</a><button className="btn ghost btn-sm" type="button" onClick={incidents.retry} disabled={incidents.status === 'loading'}>刷新数据</button></div>
    </div>
    <div className="admin-section-body">
      {incidents.status === 'error' ? <ErrorState status={incidents.error.status ?? 0} message={incidents.error.message} retry={incidents.retry}/> : null}
      {incidents.status === 'loading' || incidents.status === 'idle' ? <LoadingState label="正在加载事故数据…" variant="table"/> : null}
      {incidents.status === 'success' ? <div className="incidents-summary">
        <div className="incident-stat">
          <span className="incident-stat-label">峰值小时</span>
          <strong>{fmtBytes(incidents.data.peak_hour.bytes)}</strong>
          <span className="small faint">{incidents.data.peak_hour.hour ? incidents.data.peak_hour.hour.replace('T', ' ') + ':00' : '—'}</span>
        </div>
        <div className="incident-stat">
          <span className="incident-stat-label">峰值小时用户 · {incidents.data.peak_hour.users.length}</span>
          {incidents.data.peak_hour.users.length ? <div className="incident-chips">{incidents.data.peak_hour.users.map(row => <a className="incident-chip" key={row.user} href={`/admin/user/${encodeURIComponent(row.user)}`}>{row.user}<span className="mono">{fmtBytes(row.bytes)}</span></a>)}</div> : <span className="small faint">峰值小时暂无用户流量</span>}
        </div>
        <div className="incident-stat incident-alerts">
          <span className="incident-stat-label">活跃告警 · {incidents.data.alerts.length}</span>
          {incidents.data.alerts.length ? <ul>{incidents.data.alerts.map(row => <li key={`${row.kind}-${row.user}-${row.key}`}><span>{row.label}</span><a href={`/admin/user/${encodeURIComponent(row.user)}`}>{row.user}</a><code>{row.key}</code></li>)}</ul> : <span className="small faint">暂无告警</span>}
        </div>
      </div> : null}
    </div>
  </section>;
}
