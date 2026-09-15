import { useEffect } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { fmtBytes } from '../overview/presentation';
import { parseUserDetail, USER_DETAIL_ENDPOINT } from './requests';
import type { UserDetail } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  if (error.status === 404) return <div className="card"><div className="err" role="alert">找不到该用户，可能已被删除或链接已过期。</div><a className="btn secondary mt-md" href="/admin/usage">返回流量分析</a></div>;
  return <div className="card"><div className="err" role="alert">用户用量加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function HourlyBars({ points }: { points: UserDetail['hourly_bars'] }) {
  const max = Math.max(1, ...points.map(point => point.bytes));
  return <div className="usage-hourly" role="img" aria-label="该用户过去 7 天每小时流量柱状图">
    {points.map(point => <span className="usage-hourly-bar" key={point.hour} title={`${point.hour} · ${fmtBytes(point.bytes)}`} style={{ height: `${Math.max(3, point.bytes / max * 100)}%` }}/>) }
  </div>;
}

function Heatmap({ rows }: { rows: UserDetail['heatmap'] }) {
  const max = Math.max(1, ...rows.flatMap(row => row.hours));
  return <div className="usage-heatmap" role="grid" aria-label="该用户 7 天 24 小时流量热图">
    {rows.map(row => <div className="usage-heatmap-row" role="row" key={row.date}>
      <span className="usage-heatmap-label">{row.date.slice(5)}</span>
      {row.hours.map((bytes, hour) => <span className="usage-heatmap-cell" role="gridcell" key={`${row.date}-${hour}`} title={`${row.date} ${String(hour).padStart(2, '0')}:00 · ${fmtBytes(bytes)}`} style={{ opacity: bytes ? 0.16 + bytes / max * 0.84 : 0.08 }}/>) }
    </div>)}
  </div>;
}

function Content({ data }: { data: UserDetail }) {
  const quota = data.cycle_quota_bytes > 0 ? `${fmtBytes(data.cycle_used_bytes)} / ${fmtBytes(data.cycle_quota_bytes)}` : `${fmtBytes(data.cycle_used_bytes)} · 不限额`;
  const state = data.disabled ? '已停用' : data.expired ? '已过期' : '正常';
  return <div className="admin-page user-detail-page">
    <div className="row gap-sm mb-md"><a className="btn ghost btn-sm" href="/admin/usage">← 返回流量分析</a><span className={`badge ${data.disabled || data.expired ? 'badge-danger' : 'badge-success'}`}>{state}</span>{data.metered ? <span className="badge">按量</span> : null}</div>
    <section className="admin-section user-detail-heading"><div className="admin-section-header"><div><h2 className="admin-section-title">{data.uid} · 用量画像</h2><div className="small faint">有效期：{data.expiry_label}{data.note ? ` · ${data.note}` : ''}</div></div><div className="small">在线 {data.online}{data.max_devices ? ` / ${data.max_devices} 台` : ' · 设备不限'}</div></div></section>
    <div className="metric-grid"><div className="metric-card"><div className="metric-k">本周期</div><div className="metric-v">{quota}</div></div><div className="metric-card"><div className="metric-k">今日</div><div className="metric-v">{fmtBytes(data.today_bytes)}</div></div><div className="metric-card"><div className="metric-k">当小时</div><div className="metric-v">{fmtBytes(data.current_hour_bytes)}</div></div><div className="metric-card"><div className="metric-k">加量包</div><div className="metric-v">{data.quota_extra_bytes ? fmtBytes(data.quota_extra_bytes) : '—'}</div></div></div>
    <section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">7 天 · 每小时</h2><div className="chart-panel-desc">按小时汇总该用户的流量。</div></div></div><div id="hourly-bars-host"><HourlyBars points={data.hourly_bars}/></div></section>
    <section className="chart-panel"><div className="chart-panel-header"><div><h2 className="chart-panel-title">个人 7×24 热图</h2><div className="chart-panel-desc">颜色越深代表该小时流量越高。</div></div></div><Heatmap rows={data.heatmap}/></section>
    <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">最近告警</h2></div><div className="admin-section-body">{data.recent_alerts.length ? data.recent_alerts.map(alert => <div className="alert-row" key={`${alert.ts}-${alert.kind}`}>{alert.ts} — {alert.kind}：{alert.details}</div>) : <div className="empty">无近期告警</div>}</div></section>
  </div>;
}

export function UserDetailPage({ publicHost, uid }: { publicHost: string; uid: string }) {
  const resource = useReadResource(`${USER_DETAIL_ENDPOINT}${encodeURIComponent(uid)}`, { validate: parseUserDetail });
  useEffect(() => {
    if (resource.status !== 'success') return;
    const timer = window.setInterval(resource.retry, 30_000);
    return () => window.clearInterval(timer);
  }, [resource.status, resource.retry]);
  return <AdminShell active="usage" pageTitle={`${uid} · 用量画像`} badge={resource.status === 'success' ? `${resource.data.online} 在线` : ''} subtitle={`${publicHost} · 用户详情`} topbarExtra={<span className="badge poll-status">自动更新 · 30 s</span>}>
    {resource.status === 'loading' ? <div className="card" role="status">正在加载用户用量…</div> : null}
    {resource.status === 'error' ? <ErrorState error={resource.error} retry={resource.retry}/> : null}
    {resource.status === 'success' ? <Content data={resource.data}/> : null}
  </AdminShell>;
}
