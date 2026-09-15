import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { fmtBytes } from '../overview/presentation';
import { HEALTH_ENDPOINT, parseHealth, runHealthAction } from './requests';
import type { HealthCalibration, HealthLineRadar, HealthUpdate } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">健康探测加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function StatusBadge({ status }: { status: { ok: boolean; label: string } }) {
  return <span className={`badge ${status.ok ? 'badge-success' : 'badge-danger'}`}>{status.label}</span>;
}

function UpdateCard({ update, onAction, busy }: { update: HealthUpdate; onAction: (action: string, fields?: Record<string, string>, confirmation?: string) => void; busy: boolean }) {
  const label = update.status === 'idle' ? '尚未检查' : update.status;
  return <section className="admin-section hysteria-update-history"><div className="admin-section-header"><div><h2 className="admin-section-title">Hysteria 更新</h2><div className="small">状态：{label} · 记录：{update.ts || '—'}</div></div><div className="row gap-sm"><button className="btn ghost btn-sm" type="button" onClick={() => onAction('update-check')} disabled={busy}>检查更新</button><button className="btn secondary btn-sm" type="button" onClick={() => onAction('update-apply', {}, '将更新任务加入后台队列，确认继续？')} disabled={busy}>立即更新</button></div></div><div className="admin-section-body"><div className="grid grid-3"><div><div className="small faint">当前版本</div><div className="mono">{update.previous_version || '—'}</div></div><div><div className="small faint">目标版本</div><div className="mono">{update.version || '—'}</div></div><div><div className="small faint">队列状态</div><div>{update.pending ? <span className="badge">后台处理中</span> : <span className="badge badge-neutral">空闲</span>}</div></div></div>{update.reason ? <div className="small faint mt-sm">{update.reason}</div> : null}</div></section>;
}

function LineRadar({ radar }: { radar: HealthLineRadar }) {
  return <section className="admin-section health-radar-section"><div className="admin-section-header"><div><h2 className="admin-section-title">线路质量雷达</h2><div className="small">近 {radar.window_hours} 小时 · 总量 {fmtBytes(radar.total_bytes)}</div></div><span className="badge">推荐 {radar.recommendation}</span></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="线路质量雷达，可横向滚动"><table className="data-table"><thead><tr><th>线路</th><th>状态</th><th>流量</th><th>占比</th><th>可用用户</th><th>在线</th><th>说明</th></tr></thead><tbody>{radar.rows.map(row => <tr key={row.key}><th scope="row"><div>{row.label}</div><div className="small faint"><code>{row.profile}</code></div></th><td><StatusBadge status={{ ok: row.ok, label: row.status }}/></td><td className="mono">{fmtBytes(row.bytes)}</td><td className="mono">{row.share.toFixed(1)}%</td><td>{row.active_users}</td><td>{row.online ?? '—'}</td><td className="small">{row.note}</td></tr>)}</tbody></table></div><div className="small faint mt-sm">{radar.reason}</div></div></section>;
}

function CalibrationPanel({ calibration, onAction, busy }: { calibration: HealthCalibration; onAction: (action: string, fields?: Record<string, string>, confirmation?: string) => void; busy: boolean }) {
  const [enabled, setEnabled] = useState(calibration.policy.enabled);
  const [mode, setMode] = useState(calibration.policy.mode);
  const [minConfidence, setMinConfidence] = useState(calibration.policy.min_confidence);
  const [maxDelta, setMaxDelta] = useState(String(calibration.policy.max_delta_percent));
  const [minDelta, setMinDelta] = useState(String(calibration.policy.min_delta_percent));
  const [cooldown, setCooldown] = useState(String(calibration.policy.cooldown_hours));
  useEffect(() => {
    setEnabled(calibration.policy.enabled);
    setMode(calibration.policy.mode);
    setMinConfidence(calibration.policy.min_confidence);
    setMaxDelta(String(calibration.policy.max_delta_percent));
    setMinDelta(String(calibration.policy.min_delta_percent));
    setCooldown(String(calibration.policy.cooldown_hours));
  }, [calibration.policy]);
  const canApply = calibration.suggested_multiplier !== null && ['medium', 'high'].includes(calibration.confidence);
  return <section className="admin-section health-calibrator-section"><div className="admin-section-header"><div><h2 className="admin-section-title">成本校准器</h2><div className="small">近 {calibration.window_hours} 小时 · {calibration.confidence} 置信度</div></div>{canApply ? <button className="btn secondary btn-sm" type="button" onClick={() => onAction('multiplier-apply', {}, '应用建议倍率并重启面板服务，确认继续？')} disabled={busy}>应用建议倍率</button> : <span className="small faint">暂无可应用建议</span>}</div><div className="admin-section-body no-pad"><div className="calibrator-decision-bar"><div className="calibrator-multiple"><div className="calibrator-multiple-label">当前倍率</div><div className="calibrator-multiple-value">{calibration.current_multiplier.toFixed(2)}x</div></div><div className="calibrator-arrow">→</div><div className="calibrator-multiple"><div className="calibrator-multiple-label">建议倍率</div><div className="calibrator-multiple-value">{calibration.suggested_multiplier === null ? '—' : `${calibration.suggested_multiplier.toFixed(2)}x`}</div></div><div className="calibrator-delta">{calibration.delta_percent === null ? '—' : `${calibration.delta_percent >= 0 ? '↑' : '↓'} ${Math.abs(calibration.delta_percent).toFixed(1)}%`}</div></div><div className="data-table-wrap" tabIndex={0} aria-label="多窗口倍率对比，可横向滚动"><table className="data-table"><thead><tr><th>窗口</th><th>总量建议</th><th>出站建议</th><th>纳入流量</th><th>样本</th><th>置信度</th></tr></thead><tbody>{calibration.windows.map(window => <tr key={window.window_hours}><th scope="row">{window.window_hours}h</th><td className="mono">{window.suggested_multiplier === null ? '—' : `${window.suggested_multiplier.toFixed(2)}x`}</td><td className="mono">{window.egress_multiplier === null ? '—' : `${window.egress_multiplier.toFixed(2)}x`}</td><td className="mono">{fmtBytes(window.app_raw_bytes)}</td><td>{window.included_sample_count}/{window.sample_count}</td><td><span className="badge">{window.confidence}</span></td></tr>)}</tbody></table></div><div className="calibrator-auto"><div className="calibrator-auto-header"><label className="calibrator-auto-toggle"><input type="checkbox" checked={enabled} onChange={event => setEnabled(event.target.checked)}/>自动调倍率</label></div><div className="grid grid-3"><label>依据<select value={mode} onChange={event => setMode(event.target.value)}><option value="total">公网 RX+TX 总量</option><option value="egress">公网 TX 出站</option></select></label><label>最低置信度<select value={minConfidence} onChange={event => setMinConfidence(event.target.value)}><option value="none">无样本</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label><label>最大变化 (%)<input type="number" min={1} max={100} value={maxDelta} onChange={event => setMaxDelta(event.target.value)}/></label><label>最小变化 (%)<input type="number" min={0} max={50} value={minDelta} onChange={event => setMinDelta(event.target.value)}/></label><label>冷却时间 (小时)<input type="number" min={1} max={168} value={cooldown} onChange={event => setCooldown(event.target.value)}/></label></div><button className="btn secondary btn-sm mt-md" type="button" onClick={() => onAction('multiplier-auto', { enabled: enabled ? '1' : '', mode, min_confidence: minConfidence, max_delta_percent: maxDelta, min_delta_percent: minDelta, cooldown_hours: cooldown })} disabled={busy}>保存自动策略</button><div className="small faint mt-sm">系统出站参考 {calibration.egress_multiplier === null ? '—' : `${calibration.egress_multiplier.toFixed(2)}x`} · 网卡 {calibration.ifaces.join(', ') || '未识别'} · 最后采样 {calibration.last_ts || '—'}</div></div></div></section>;
}

export function HealthPage({ publicHost }: { publicHost: string }) {
  const health = useReadResource(HEALTH_ENDPOINT, { validate: parseHealth });
  const [polling, setPolling] = useState('自动更新 · 30 s');
  const [actionBusy, setActionBusy] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  useEffect(() => {
    if (health.status !== 'success') return;
    const timer = window.setInterval(() => {
      setPolling('正在更新…');
      health.retry();
    }, 30_000);
    return () => window.clearInterval(timer);
  }, [health.status, health.retry]);
  const refresh = () => { setPolling('正在更新…'); health.retry(); };
  const runAction = async (action: string, fields: Record<string, string> = {}, confirmation = '') => {
    if (confirmation && !window.confirm(confirmation)) return;
    setActionBusy(action);
    setActionMessage('正在处理…');
    const controller = new AbortController();
    try {
      const result = await runHealthAction(action, fields, controller.signal);
      if (result.status === 'checked') setActionMessage(`更新检查完成：${result.current || '当前版本'} → ${result.latest || '未知'}`);
      else if (result.status === 'scheduled') setActionMessage('更新已进入后台队列');
      else if (result.status === 'alert_dispatched') setActionMessage('测试告警已后台发送，请在接收端确认');
      else if (result.status === 'multiplier_applied') setActionMessage('建议倍率已应用');
      else if (result.status === 'multiplier_auto_saved') setActionMessage('自动调倍率策略已保存');
      else setActionMessage(result.reason || '操作完成');
      health.retry();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : '操作失败，请刷新核对');
    } finally {
      setActionBusy('');
    }
  };

  return <AdminShell active="health" pageTitle="健康状态" badge={health.status === 'success' ? '实时探测' : ''} subtitle={`${publicHost} · 服务与基础设施`} topbarExtra={<><button className="btn ghost btn-sm" type="button" onClick={refresh} disabled={health.status === 'loading'}>立即刷新</button><button className="btn ghost btn-sm" type="button" onClick={() => void runAction('update-check')} disabled={Boolean(actionBusy)}>检查更新</button><button className="btn secondary btn-sm" type="button" onClick={() => void runAction('update-apply', {}, '将更新任务加入后台队列，确认继续？')} disabled={Boolean(actionBusy)}>立即更新</button><button className="btn ghost btn-sm" type="button" onClick={() => void runAction('test-alert')} disabled={Boolean(actionBusy)}>测试告警</button><span className="badge poll-status">{health.status === 'loading' ? '加载中…' : polling}</span><span className="sr-only" role="status" aria-live="polite">{polling}</span></>}>
    {actionMessage ? <div className="flash" role="status">{actionMessage}</div> : null}
    {health.status === 'error' ? <ErrorState error={health.error} retry={health.retry}/> : null}
    {health.status === 'loading' ? <div className="card" role="status">正在运行健康探测…</div> : null}
    {health.status === 'success' ? <div className="admin-page health-page">
      <div className="health-top-kpis" aria-label="健康概览">{health.data.kpis.map(kpi => <div className="health-kpi-card" key={kpi.title}><div className="health-kpi-label">{kpi.title}</div><div className="health-kpi-value"><StatusBadge status={kpi}/></div></div>)}</div>
      <section className="admin-section"><div className="admin-section-header"><h2 className="admin-section-title">核心服务与基础设施</h2><div className="small">{health.data.ts.replace('T', ' · ')}</div></div><div className="admin-section-body no-pad"><div className="data-table-wrap" tabIndex={0} aria-label="健康探测结果，可横向滚动"><table className="data-table health-service-table"><thead><tr><th>项目</th><th>状态</th></tr></thead><tbody>{health.data.services.map(item => <tr key={item.title}><th scope="row">{item.title}</th><td><StatusBadge status={item}/></td></tr>)}</tbody></table></div></div></section>
      <LineRadar radar={health.data.line_radar}/>
      <CalibrationPanel calibration={health.data.calibration} onAction={(action, fields, confirmation) => void runAction(action, fields, confirmation)} busy={Boolean(actionBusy)}/>
      <UpdateCard update={health.data.update} onAction={(action, fields, confirmation) => void runAction(action, fields, confirmation)} busy={Boolean(actionBusy)}/>
    </div> : null}
  </AdminShell>;
}
