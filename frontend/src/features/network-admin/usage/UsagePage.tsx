import { useEffect, useRef, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, type ReadResourceResult, useReadResource } from '../../../shared/readResource';
import { ActionButton, IncidentsSection } from '../incidents/IncidentsPage';
import { INCIDENTS_ENDPOINT, parseIncidents } from '../incidents/requests';
import type { Incidents } from '../incidents/types';
import { Spark, fmtBytes } from '../overview/presentation';
import { parseUsage, parseUsageHistory } from './requests';
import type { Usage, UsageHistory } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">流量分析加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function HourlyChart({ points }: { points: Usage['hourly_totals'] }) {
  const max = Math.max(1, ...points.map(point => point.bytes));
  const [activeIndex, setActiveIndex] = useState<number | null>(null);
  const activePoint = activeIndex === null ? undefined : points[activeIndex];
  const formatHour = (value: string) => {
    const match = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2})/);
    return match ? `${match[2]}-${match[3]} ${match[4]}:00` : value;
  };
  return <div className="usage-hourly-wrap">
    <div className="usage-hourly" role="group" aria-label="过去 7 天每小时流量柱状图" onMouseLeave={() => setActiveIndex(null)}>
      {points.map((point, index) => {
        const label = `${formatHour(point.hour)} · ${fmtBytes(point.bytes)}`;
        return <span
          className="usage-hourly-bar"
          key={point.hour}
          role="img"
          tabIndex={0}
          aria-label={label}
          title={label}
          onMouseEnter={() => setActiveIndex(index)}
          onFocus={() => setActiveIndex(index)}
          onBlur={() => setActiveIndex(null)}
          style={{ height: `${Math.max(3, point.bytes / max * 100)}%` }}
        />;
      })}
    </div>
    {activePoint ? <div className="usage-hourly-tooltip" data-role="hourly-tooltip" role="status" aria-live="polite">
      <span>{formatHour(activePoint.hour)}</span>{' · '}<strong>{fmtBytes(activePoint.bytes)}</strong>
    </div> : null}
  </div>;
}

function Heatmap({ rows, ts }: { rows: Usage['heatmap']; ts: string }) {
  const max = Math.max(1, ...rows.flatMap(row => row.hours));
  const currentDate = ts.slice(0, 10);
  const currentHour = Number(ts.slice(11, 13));
  return <div className="usage-heatmap" role="grid" aria-label="7 天 24 小时流量热图">
    {rows.map(row => <div className="usage-heatmap-row" role="row" key={row.date}>
      <span className="usage-heatmap-label">{row.date.slice(5)}</span>
      {row.hours.map((bytes, hour) => <span
        className="usage-heatmap-cell"
        role="gridcell"
        key={`${row.date}-${hour}`}
        title={`${row.date} ${String(hour).padStart(2, '0')}:00 · ${fmtBytes(bytes)}`}
        style={{ opacity: bytes ? 0.16 + bytes / max * 0.84 : 0.08 }}
      />)}
    </div>)}
    <details className="heatmap-data-details mt-sm">
      <summary>查看每小时数据表</summary>
      <div className="scroll-x heatmap-data-scroll" tabIndex={0} aria-label="7 天每小时流量数据，可横向滚动">
        <table className="table heatmap-data-table">
          <caption className="sr-only">7 天每小时流量；列标题为 00 至 23 时</caption>
          <thead><tr><th scope="col">日期</th>{Array.from({ length: 24 }, (_, hour) => <th scope="col" key={hour}>{String(hour).padStart(2, '0')}</th>)}</tr></thead>
          <tbody>{rows.map(row => <tr key={`table-${row.date}`}><th scope="row">{row.date}</th>{row.hours.map((bytes, hour) => <td key={`${row.date}-${hour}`}>{row.date === currentDate && Number.isInteger(currentHour) && hour > currentHour ? '—' : bytes ? fmtBytes(bytes) : '—'}</td>)}</tr>)}</tbody>
        </table>
      </div>
    </details>
  </div>;
}

/** One 24-hour ranking: live sparklines from usage, revisions for the actions from incidents. */
function UserRanking({ usage, incidents, done }: { usage: Usage; incidents: ReadResourceResult<Incidents>; done: (message: string) => void }) {
  if (!usage.top_n.length) return <div className="empty">暂无活跃用户</div>;
  const accounts = new Map(incidents.status === 'success' ? incidents.data.users.map(row => [row.user, row]) : []);
  return <div id="top-n-host">{usage.top_n.map(user => {
    const account = accounts.get(user.uid);
    return <div className="top-row" key={user.uid}>
      <a className="top-uid" href={`/admin/user/${encodeURIComponent(user.uid)}`} title="查看用量画像">{user.uid} ↗</a>
      <span className="top-spark"><Spark values={user.spark.map((bytes, index) => [`${index}`, bytes])}/></span>
      <span className="top-bytes">{fmtBytes(user.last_24h_bytes)}</span>
      {account ? <span className="top-actions"><ActionButton action="pause-user" user={account.user} revision={account.revision} done={done}/><ActionButton action="rotate-token" user={account.user} revision={account.revision} done={done}/></span> : null}
    </div>;
  })}</div>;
}

function HistoryTable({ history }: { history: UsageHistory }) {
  return <div className="data-table-wrap daily-history-scroll" tabIndex={0} aria-label="每日用量明细，可横向滚动">
    <table className="data-table daily-table-collapsed"><thead><tr><th>用户</th>{history.dates.map(date => <th key={date}>{date.slice(5)}</th>)}</tr></thead>
      <tbody>{history.users.length ? history.users.map(user => <tr key={user.uid}><th scope="row">{user.uid}</th>{user.values.map((value, index) => <td key={`${user.uid}-${history.dates[index]}`}>{value ? fmtBytes(value) : '—'}</td>)}</tr>) : <tr><td className="empty" colSpan={history.retention_days + 1}>暂无数据</td></tr>}</tbody>
      <tfoot><tr><th>合计</th>{history.totals.map((value, index) => <td key={history.dates[index]}>{value ? fmtBytes(value) : '—'}</td>)}</tr></tfoot>
    </table>
  </div>;
}

function LazyHistory() {
  const [open, setOpen] = useState(false);
  const history = useReadResource('/api/v1/admin/usage-history', { enabled: open, validate: parseUsageHistory });
  return <details className="admin-section" id="usage-history" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>历史每日明细（可展开）</summary>
    <div className="admin-section-body">
      {history.status === 'loading' ? <LoadingState label="正在加载历史明细…" variant="table"/> : null}
      {history.status === 'error' ? <ErrorState error={history.error} retry={history.retry}/> : null}
      {history.status === 'success' ? <HistoryTable history={history.data}/> : null}
    </div>
  </details>;
}

function routeIsIncidents(): boolean {
  return window.location.pathname.replace(/^\/__react/, '') === '/admin/incidents';
}

export function UsagePage({ publicHost }: { publicHost: string }) {
  const usage = useReadResource('/api/v1/admin/usage', { validate: parseUsage });
  const incidents = useReadResource(INCIDENTS_ENDPOINT, { validate: parseIncidents });
  const [actionMessage, setActionMessage] = useState('');
  const pendingIncidentScroll = useRef(routeIsIncidents());
  const [polling, setPolling] = useState('自动更新 · 30 s');
  useEffect(() => {
    if (usage.status !== 'success') return;
    const timer = window.setInterval(() => {
      setPolling('正在更新…');
      usage.retry();
      incidents.retry();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [usage.status, usage.retry, incidents.retry]);
  const refresh = () => { setPolling('正在更新…'); usage.retry(); incidents.retry(); };
  const actionDone = (message: string) => { setActionMessage(message); usage.retry(); incidents.retry(); };
  useEffect(() => {
    // /admin/incidents opens this page at the incident section.
    if (!pendingIncidentScroll.current || usage.status !== 'success' || incidents.status === 'loading' || incidents.status === 'idle') return;
    pendingIncidentScroll.current = false;
    const section = document.getElementById('incidents');
    section?.scrollIntoView({ block: 'start' });
    section?.focus({ preventScroll: true });
  }, [usage.status, incidents.status]);

  return <AdminShell active="usage" pageTitle="流量分析" subtitle={`${publicHost} · 实时数据`} topbarExtra={<><button className="btn ghost btn-sm" type="button" onClick={refresh} disabled={usage.status === 'loading'}>立即刷新</button><span className="badge poll-status" data-role="usage-poll-status">{polling}</span><span className="sr-only" role="status" aria-live="polite">{polling}</span></>}>
    <div className="admin-page usage-page">
      {usage.status === 'error' ? <ErrorState error={usage.error} retry={usage.retry}/> : null}
      {usage.status === 'loading' ? <LoadingState label="正在加载流量分析…"/> : null}
      {actionMessage ? <div className="flash" role="status">{actionMessage}</div> : null}
      {usage.status === 'success' ? <>
      <div className="metric-grid">
        <div className="metric-card"><div className="metric-k">当小时</div><div className="metric-v big">{fmtBytes(usage.data.stats.current_hour_bytes)}</div><div className="metric-sub">{usage.data.stats.online} 在线</div></div>
        <div className="metric-card"><div className="metric-k">今日</div><div className="metric-v">{fmtBytes(usage.data.stats.today_bytes)}</div><div className="metric-sub">昨日 {fmtBytes(usage.data.stats.yesterday_bytes)}</div></div>
        <div className="metric-card"><div className="metric-k">近 7 天</div><div className="metric-v">{fmtBytes(usage.data.stats.last_7d_bytes)}</div><div className="metric-sub">日均 {fmtBytes(Math.floor(usage.data.stats.last_7d_bytes / 7))}</div></div>
        <div className="metric-card"><div className="metric-k">本周期</div><div className="metric-v">{fmtBytes(usage.data.stats.cycle_bytes)}</div><div className="metric-sub">第 {usage.data.stats.cycle_day} / {usage.data.stats.cycle_total_days} 天</div></div>
      </div>
      <section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">过去 7 天 · 每小时</h2><div className="chart-panel-desc">基于滚动小时桶聚合。</div></div></div><HourlyChart points={usage.data.hourly_totals}/></section>
      <div className="grid grid-2 analytics-grid"><section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">7 天 × 24 小时 热图</h2><div className="chart-panel-desc">颜色越深代表流量越高。</div></div></div><Heatmap rows={usage.data.heatmap} ts={usage.data.ts}/></section><section className="admin-section user-ranking"><div className="admin-section-header"><div><h2 className="admin-section-title">用户排行 · 近 24 小时</h2><div className="small">点名称看用量画像；可直接暂停或轮换 Token</div></div><a className="small" href="/admin">全部用户 →</a></div><UserRanking usage={usage.data} incidents={incidents} done={actionDone}/></section></div>
      <IncidentsSection incidents={incidents}/>
      <LazyHistory/>
      </> : null}
    </div>
  </AdminShell>;
}
