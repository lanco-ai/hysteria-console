import { fmtBytes } from './presentation';
import type { OverviewSummary } from './useOverview';

function TrafficValue({ bytes }: { bytes: number | undefined }) {
  if (bytes === undefined) return <span className="overview-traffic-number">—</span>;
  const [number, unit] = fmtBytes(bytes).split(' ');
  return <><span className="overview-traffic-number">{number}</span>{' '}<span className="overview-traffic-unit">{unit}</span></>;
}

export function TrafficSummary({ summary, busy, retry }: { summary: OverviewSummary; busy: boolean; retry: () => void }) {
  if (summary.auth) return null;
  const stats = summary.data?.stats;
  const metrics = [
    { label: '当小时', bytes: stats?.current_hour_bytes, detail: stats ? `${stats.online} 在线` : '在线状态待更新' },
    { label: '今日', bytes: stats?.today_bytes, detail: stats ? `昨日 ${fmtBytes(stats.yesterday_bytes)}` : '昨日用量待更新' },
    { label: '近 7 天', bytes: stats?.last_7d_bytes, detail: stats ? `日均 ${fmtBytes(Math.floor(stats.last_7d_bytes / 7))}` : '日均用量待更新' },
    { label: '本周期', bytes: stats?.cycle_bytes, detail: stats ? `第 ${stats.cycle_day} / ${stats.cycle_total_days} 天` : '周期进度待更新' },
  ];
  return <section className="overview-traffic" aria-labelledby="overview-traffic-title">
    <div className="overview-traffic-header">
      <div className="overview-traffic-heading">
        <h2 id="overview-traffic-title">流量概览</h2>
        <span className="overview-traffic-caption">全站用量</span>
        {summary.loading && stats ? <span className="overview-traffic-caption" role="status">正在更新…</span> : null}
      </div>
      <a className="overview-traffic-link" href="/admin/usage">查看流量分析 <span aria-hidden="true">→</span></a>
    </div>
    <div className="overview-traffic-grid" aria-busy={summary.loading}>
      {metrics.map(metric => <article className="overview-traffic-card" key={metric.label}>
        <h3 className="overview-traffic-label">{metric.label}</h3>
        <p className="overview-traffic-value"><TrafficValue bytes={metric.bytes}/></p>
        <p className="overview-traffic-detail">{metric.detail}</p>
      </article>)}
    </div>
    {summary.error ? <div className="overview-traffic-feedback" role="alert">
      <span>{summary.error}</span>
      <button className="btn btn-sm" type="button" disabled={busy || summary.loading} onClick={retry}>重试流量概览</button>
    </div> : !stats ? <p className="overview-traffic-feedback" role="status">{busy ? '操作完成后将更新流量概览…' : summary.loading ? '正在加载全站流量…' : '流量数据待更新'}</p> : null}
  </section>;
}
