import { useEffect, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { fmtBytes } from '../overview/presentation';
import { HEALTH_ENDPOINT, parseHealth, runHealthAction } from './requests';
import type { HealthCalibration, HealthLineRadar, HealthUpdate } from './types';

type RunAction = (action: string, fields?: Record<string, string>, confirmation?: string) => void;

const CONFIDENCE: Record<string, string> = { none: '无样本', low: '低', medium: '中', high: '高' };
const UPDATE_STATUS: Record<string, string> = {
  idle: '尚未检查', checked: '已检查', scheduled: '已排队', pending: '等待中', downloading: '下载中', verifying: '校验中',
  applying: '安装中', done: '已完成', skipped: '已跳过', failed: '失败', rolled_back: '已回滚',
};

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  if (error.status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">健康探测加载失败：{error.message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function StatusBadge({ status }: { status: { ok: boolean; label: string } }) {
  return <span className={`badge ${status.ok ? 'badge-success' : 'badge-danger'}`}>{status.label}</span>;
}

export type CalibrationDraft = {
  enabled: boolean;
  mode: string;
  minConfidence: string;
  maxDelta: string;
  minDelta: string;
  cooldown: string;
};

function calibrationDraftFromPolicy(policy: HealthCalibration['policy']): CalibrationDraft {
  return {
    enabled: policy.enabled,
    mode: policy.mode,
    minConfidence: policy.min_confidence,
    maxDelta: String(policy.max_delta_percent),
    minDelta: String(policy.min_delta_percent),
    cooldown: String(policy.cooldown_hours),
  };
}

const multiple = (value: number | null) => value === null ? '—' : `${value.toFixed(2)}x`;

function UpdateCard({ update, onAction, busy }: { update: HealthUpdate; onAction: RunAction; busy: boolean }) {
  return <section className="admin-section hysteria-update-history" aria-labelledby="update-title">
    <div className="update-row">
      <div className="update-heading">
        <h2 className="admin-section-title" id="update-title">Hysteria 更新</h2>
        <div className="small">{UPDATE_STATUS[update.status] ?? update.status}{update.ts ? ` · ${update.ts.replace('T', ' ')}` : ''}</div>
      </div>
      <dl className="update-versions">
        <div><dt>当前版本</dt><dd className="mono">{update.previous_version || '—'}</dd></div>
        <div><dt>目标版本</dt><dd className="mono">{update.version || '—'}</dd></div>
        <div><dt>队列</dt><dd>{update.pending ? <span className="badge">后台处理中</span> : <span className="badge badge-neutral">空闲</span>}</dd></div>
      </dl>
      <div className="update-actions">
        <button className="btn ghost btn-sm" type="button" onClick={() => onAction('update-check')} disabled={busy}>检查更新</button>
        <button className="btn secondary btn-sm" type="button" onClick={() => onAction('update-apply', {}, '将更新任务加入后台队列，确认继续？')} disabled={busy}>立即更新</button>
      </div>
    </div>
    {update.reason ? <div className="small faint update-reason">{update.reason}</div> : null}
  </section>;
}

function LineRadar({ radar }: { radar: HealthLineRadar }) {
  return <section className="admin-section health-radar-section" aria-labelledby="radar-title">
    <div className="admin-section-header">
      <div><h2 className="admin-section-title" id="radar-title">线路质量雷达</h2><div className="small">近 {radar.window_hours} 小时 · 总量 {fmtBytes(radar.total_bytes)}</div></div>
      <span className="badge">推荐 {radar.recommendation}</span>
    </div>
    <div className="admin-section-body">
      <ul className="line-cards">{radar.rows.map(row => <li className={`line-card${row.ok ? '' : ' is-failing'}`} key={row.key}>
        <div className="line-card-head"><strong>{row.label}</strong><StatusBadge status={{ ok: row.ok, label: row.status }}/></div>
        <code className="line-card-profile">{row.profile}</code>
        <div className="line-card-traffic"><span className="line-card-bytes">{fmtBytes(row.bytes)}</span><span className="small">{row.share.toFixed(1)}%</span></div>
        <div className="line-share" aria-hidden="true"><span style={{ width: `${Math.min(100, Math.max(0, row.share))}%` }}/></div>
        <dl className="line-card-stats"><div><dt>可用用户</dt><dd>{row.active_users}</dd></div><div><dt>在线</dt><dd>{row.online ?? '—'}</dd></div></dl>
        {row.note ? <p className="line-card-note">{row.note}</p> : null}
      </li>)}</ul>
      {radar.reason ? <div className="small faint line-radar-reason">{radar.reason}</div> : null}
    </div>
  </section>;
}

function CalibrationPanel({ calibration, onAction, busy, draft, setDraft }: { calibration: HealthCalibration; onAction: RunAction; busy: boolean; draft: CalibrationDraft | null; setDraft: (draft: CalibrationDraft | null) => void }) {
  const form = draft ?? calibrationDraftFromPolicy(calibration.policy);
  const updateDraft = (patch: Partial<CalibrationDraft>) => setDraft({ ...form, ...patch });
  const canApply = calibration.suggested_multiplier !== null && ['medium', 'high'].includes(calibration.confidence);
  return <section className="admin-section health-calibrator-section" aria-labelledby="calibrator-title">
    <div className="admin-section-header">
      <div>
        <h2 className="admin-section-title" id="calibrator-title">成本校准器</h2>
        <div className="small">近 {calibration.window_hours} 小时 · 置信度 {CONFIDENCE[calibration.confidence] ?? calibration.confidence}</div>
      </div>
      {canApply ? <button className="btn primary btn-sm" type="button" onClick={() => onAction('multiplier-apply', {}, '应用建议倍率并重启面板服务，确认继续？')} disabled={busy}>应用建议倍率</button> : <span className="small faint">暂无可应用建议</span>}
    </div>
    <div className="calibrator-layout">
      <div className="calibrator-main">
        <div className="calibrator-decision-bar">
          <div className="calibrator-multiple"><div className="calibrator-multiple-label">当前倍率</div><div className="calibrator-multiple-value">{calibration.current_multiplier.toFixed(2)}x</div></div>
          <div className="calibrator-arrow" aria-hidden="true">→</div>
          <div className="calibrator-multiple"><div className="calibrator-multiple-label">建议倍率</div><div className="calibrator-multiple-value">{multiple(calibration.suggested_multiplier)}</div></div>
          <div className="calibrator-delta">{calibration.delta_percent === null ? '—' : `${calibration.delta_percent >= 0 ? '↑' : '↓'} ${Math.abs(calibration.delta_percent).toFixed(1)}%`}</div>
        </div>
        <div className="data-table-wrap" tabIndex={0} aria-label="多窗口倍率对比，可横向滚动">
          <table className="data-table"><thead><tr><th>窗口</th><th>总量建议</th><th>出站建议</th><th>纳入流量</th><th>样本</th><th>置信度</th></tr></thead>
            <tbody>{calibration.windows.map(window => <tr key={window.window_hours}><th scope="row">{window.window_hours}h</th><td className="mono">{multiple(window.suggested_multiplier)}</td><td className="mono">{multiple(window.egress_multiplier)}</td><td className="mono">{fmtBytes(window.app_raw_bytes)}</td><td>{window.included_sample_count}/{window.sample_count}</td><td><span className="badge">{CONFIDENCE[window.confidence] ?? window.confidence}</span></td></tr>)}</tbody>
          </table>
        </div>
        <div className="small faint calibrator-facts">
          {calibration.cycle_net ? <span data-role="cycle-net">本周期网卡流量 {fmtBytes(calibration.cycle_net.total)}（入 {fmtBytes(calibration.cycle_net.rx)} · 出 {fmtBytes(calibration.cycle_net.tx)}）· 与服务商同口径，按 UTC 结算周期</span> : null}
          <span>系统出站参考 {multiple(calibration.egress_multiplier)} · 网卡 {calibration.ifaces.join(', ') || '未识别'} · 最后采样 {calibration.last_ts || '—'}</span>
        </div>
      </div>
      <div className="calibrator-auto">
        <div className="calibrator-auto-header">
          <label className="calibrator-auto-toggle"><input type="checkbox" checked={form.enabled} onChange={event => updateDraft({ enabled: event.target.checked })}/>自动调倍率</label>
          <span className="small faint">满足置信度且变化在范围内时自动应用</span>
        </div>
        <div className="calibrator-auto-grid">
          <label className="calibrator-auto-field">依据<select value={form.mode} onChange={event => updateDraft({ mode: event.target.value })}><option value="total">公网 RX+TX 总量</option><option value="egress">公网 TX 出站</option></select></label>
          <label className="calibrator-auto-field">最低置信度<select value={form.minConfidence} onChange={event => updateDraft({ minConfidence: event.target.value })}><option value="none">无样本</option><option value="low">低</option><option value="medium">中</option><option value="high">高</option></select></label>
          <label className="calibrator-auto-field">最大变化 (%)<input type="number" min={1} max={100} value={form.maxDelta} onChange={event => updateDraft({ maxDelta: event.target.value })}/></label>
          <label className="calibrator-auto-field">最小变化 (%)<input type="number" min={0} max={50} value={form.minDelta} onChange={event => updateDraft({ minDelta: event.target.value })}/></label>
          <label className="calibrator-auto-field">冷却时间 (小时)<input type="number" min={1} max={168} value={form.cooldown} onChange={event => updateDraft({ cooldown: event.target.value })}/></label>
        </div>
        <button className="btn primary btn-sm calibrator-save" type="button" onClick={() => onAction('multiplier-auto', { enabled: form.enabled ? '1' : '', mode: form.mode, min_confidence: form.minConfidence, max_delta_percent: form.maxDelta, min_delta_percent: form.minDelta, cooldown_hours: form.cooldown })} disabled={busy}>保存自动策略</button>
      </div>
    </div>
  </section>;
}

export function HealthPanel({ calibrationDraft, setCalibrationDraft }: { calibrationDraft: CalibrationDraft | null; setCalibrationDraft: (draft: CalibrationDraft | null) => void }) {
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
      else if (result.status === 'multiplier_auto_saved') { setActionMessage('自动调倍率策略已保存'); setCalibrationDraft(null); }
      else setActionMessage(result.reason || '操作完成');
      health.retry();
    } catch (error) {
      setActionMessage(error instanceof Error ? error.message : '操作失败，请刷新核对');
    } finally {
      setActionBusy('');
    }
  };
  const onAction: RunAction = (action, fields, confirmation) => void runAction(action, fields, confirmation);
  const failing = health.status === 'success' ? health.data.services.filter(item => !item.ok).length : 0;

  return <>
    <section className="health-statusbar" aria-label="健康概览">
      <div className="health-top-kpis">{health.status === 'success' ? health.data.kpis.map(kpi => <div className="health-kpi-card" key={kpi.title}><span className="health-kpi-label">{kpi.title}</span><StatusBadge status={kpi}/></div>) : null}</div>
      <div className="health-statusbar-actions">
        <span className="badge poll-status">{polling}</span>
        <span className="sr-only" role="status" aria-live="polite">{polling}</span>
        <button className="btn ghost btn-sm" type="button" onClick={refresh} disabled={health.status === 'loading'}>立即刷新</button>
        <button className="btn ghost btn-sm" type="button" onClick={() => void runAction('test-alert')} disabled={Boolean(actionBusy)}>测试告警</button>
      </div>
    </section>
    {actionMessage ? <div className="flash" role="status">{actionMessage}</div> : null}
    {health.status === 'error' ? <ErrorState error={health.error} retry={health.retry}/> : null}
    {health.status === 'loading' ? <LoadingState label="正在运行健康探测…"/> : null}
    {health.status === 'success' ? <div className="admin-page health-page">
      <section className="admin-section health-services" aria-labelledby="services-title">
        <div className="admin-section-header">
          <div><h2 className="admin-section-title" id="services-title">核心服务与基础设施</h2><div className={`small${failing ? ' health-failing-count' : ''}`}>{health.data.services.length} 项 · {failing ? `${failing} 项异常` : '全部正常'}</div></div>
          <div className="small">{health.data.ts.replace('T', ' · ')}</div>
        </div>
        <div className="admin-section-body"><ul className="health-checks" aria-label="健康探测结果">{[...health.data.services].sort((a, b) => Number(a.ok) - Number(b.ok)).map(item => <li className={`health-check${item.ok ? '' : ' is-failing'}`} key={item.title}><span>{item.title}</span><StatusBadge status={item}/></li>)}</ul></div>
      </section>
      <LineRadar radar={health.data.line_radar}/>
      <CalibrationPanel calibration={health.data.calibration} onAction={onAction} busy={Boolean(actionBusy)} draft={calibrationDraft} setDraft={setCalibrationDraft}/>
      <UpdateCard update={health.data.update} onAction={onAction} busy={Boolean(actionBusy)}/>
    </div> : null}
  </>;
}

export function HealthPage({ publicHost }: { publicHost: string }) {
  const [calibrationDraft, setCalibrationDraft] = useState<CalibrationDraft | null>(null);
  return <AdminShell active="operations" pageTitle="运维" subtitle={publicHost}><HealthPanel calibrationDraft={calibrationDraft} setCalibrationDraft={setCalibrationDraft}/></AdminShell>;
}
