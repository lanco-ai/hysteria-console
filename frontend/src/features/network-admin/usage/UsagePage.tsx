import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
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

function TopUsers({ usage }: { usage: Usage }) {
  if (!usage.top_n.length) return <div className="empty">暂无活跃用户</div>;
  return <div id="top-n-host">{usage.top_n.map(user => <a className="top-row" href={`/admin/user/${encodeURIComponent(user.uid)}`} key={user.uid}>
    <span className="top-uid">{user.uid} ↗</span>
    <span className="top-spark"><Spark values={user.spark.map((bytes, index) => [`${index}`, bytes])}/></span>
    <span className="top-bytes">{fmtBytes(user.last_24h_bytes)}</span>
  </a>)}</div>;
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

export function UsagePage({ publicHost }: { publicHost: string }) {
  const usage = useReadResource('/api/v1/admin/usage', { validate: parseUsage });
  const [polling, setPolling] = useState('自动更新 · 30 s');
  useEffect(() => {
    if (usage.status !== 'success') return;
    const timer = window.setInterval(() => {
      setPolling('正在更新…');
      usage.retry();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [usage.status, usage.retry]);
  const refresh = () => { setPolling('正在更新…'); usage.retry(); };

  return <AdminShell active="usage" pageTitle="流量分析" badge={usage.status === 'success' ? `${usage.data.stats.online} 个在线` : ''} subtitle={`${publicHost} · 实时数据`} topbarExtra={<><button className="btn ghost btn-sm" type="button" onClick={refresh} disabled={usage.status === 'loading'}>立即刷新</button><span className="badge poll-status" data-role="usage-poll-status">{polling}</span><span className="sr-only" role="status" aria-live="polite">{polling}</span></>}>
    <div className="admin-page usage-page">
      {usage.status === 'error' ? <ErrorState error={usage.error} retry={usage.retry}/> : null}
      {usage.status === 'loading' ? <LoadingState label="正在加载流量分析…"/> : null}
      {usage.status === 'success' ? <>
      <div className="metric-grid">
        <div className="metric-card"><div className="metric-k">当小时</div><div className="metric-v big">{fmtBytes(usage.data.stats.current_hour_bytes)}</div><div className="metric-sub">{usage.data.stats.online} 在线</div></div>
        <div className="metric-card"><div className="metric-k">今日</div><div className="metric-v">{fmtBytes(usage.data.stats.today_bytes)}</div><div className="metric-sub">昨日 {fmtBytes(usage.data.stats.yesterday_bytes)}</div></div>
        <div className="metric-card"><div className="metric-k">近 7 天</div><div className="metric-v">{fmtBytes(usage.data.stats.last_7d_bytes)}</div><div className="metric-sub">日均 {fmtBytes(Math.floor(usage.data.stats.last_7d_bytes / 7))}</div></div>
        <div className="metric-card"><div className="metric-k">本周期</div><div className="metric-v">{fmtBytes(usage.data.stats.cycle_bytes)}</div><div className="metric-sub">第 {usage.data.stats.cycle_day} / {usage.data.stats.cycle_total_days} 天</div></div>
      </div>
      <section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">过去 7 天 · 每小时</h2><div className="chart-panel-desc">基于滚动小时桶聚合。</div></div></div><HourlyChart points={usage.data.hourly_totals}/></section>
      <div className="grid grid-2 analytics-grid"><section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">7 天 × 24 小时 热图</h2><div className="chart-panel-desc">颜色越深代表流量越高。</div></div></div><Heatmap rows={usage.data.heatmap} ts={usage.data.ts}/></section><section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">Top 5 · 近 24 小时</h2><div className="small">活跃用户</div></div><TopUsers usage={usage.data}/></section></div>
      <LazyHistory/>
      </> : null}
    </div>
  </AdminShell>;
}
