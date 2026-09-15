import { useEffect, useState, type FormEvent } from 'react';
import { USER_PANEL_ENDPOINT, parseUserPanel } from './requests';
import { ResourceError, useReadResource } from '../../shared/readResource';
import { LoadingState } from '../../shared/LoadingState';
import { useFormAction } from '../../shared/useFormAction';
import { fmtBytes } from '../network-admin/overview/presentation';
import { mutateLanding } from '../network-admin/landing/requests';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  useEffect(() => {
    if (error.status === 403 && error.code === 'password_change_required') {
      window.location.assign('/user/change-password');
    }
  }, [error]);
  if (error.status === 401) return <main className="auth-scene"><div className="card"><div className="err" role="alert">用户登录已失效。</div><a className="btn btn-primary" href="/login">前往登录</a></div></main>;
  if (error.status === 403 && error.code === 'disabled') return <main className="auth-scene"><div className="card"><div className="err" role="alert">账号已停用，请联系管理员。</div></div></main>;
  if (error.status === 403 && error.code === 'expired') return <main className="auth-scene"><div className="card"><div className="err" role="alert">账号已到期，请联系管理员续费。</div></div></main>;
  return <main className="auth-scene"><div className="card"><div className="err" role="alert">用户面板加载失败，请稍后重试。</div><button className="btn secondary" type="button" onClick={retry}>重试</button></div></main>;
}

export function UserPanelPage({ publicHost }: { publicHost: string }) {
  const resource = useReadResource(USER_PANEL_ENDPOINT, { validate: parseUserPanel });
  const [selectedProfile, setSelectedProfile] = useState('');
  const [copied, setCopied] = useState(false);
  const [showQr, setShowQr] = useState(false);
  const egressAction = useFormAction();
  const [egressMessage, setEgressMessage] = useState('');
  useEffect(() => { if (resource.status === 'success' && !selectedProfile) setSelectedProfile(resource.data.subscription_profiles[0]?.key || ''); }, [resource.status, resource.data, selectedProfile]);
  useEffect(() => { if (resource.status !== 'success' || resource.data.disabled || resource.data.expired) return; const timer = window.setInterval(resource.retry, 30_000); return () => window.clearInterval(timer); }, [resource.status, resource.data, resource.retry]);
  if (resource.status === 'error') return <ErrorState error={resource.error} retry={resource.retry}/>;
  if (resource.status !== 'success') return <main className="auth-scene"><LoadingState label="正在加载用户面板…"/></main>;
  const data = resource.data;
  const current = data.subscription_profiles.find(profile => profile.key === selectedProfile) || data.subscription_profiles[0];
  const quotaUnlimited = data.total_bytes === 0;
  const percent = quotaUnlimited ? 0 : Math.min(100, data.percent);
  const copy = async () => { if (!current) return; try { await navigator.clipboard.writeText(current.url); setCopied(true); window.setTimeout(() => setCopied(false), 1600); } catch { setCopied(false); } };
  const selectEgress = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload: Record<string, string> = {};
    for (const [key, value] of new FormData(form)) if (typeof value === 'string') payload[key] = value;
    setEgressMessage('正在切换…');
    void egressAction.run(
      signal => mutateLanding('select', payload, signal),
      {
        onResult: result => {
          if (result.ok) {
            setEgressMessage('家宽出口已切换');
            resource.retry();
          } else if (result.error === 'rate_limited') {
            setEgressMessage(`切换过于频繁，请 ${result.retry_after ?? 60} 秒后重试`);
          } else if (result.error === 'revision_conflict') {
            setEgressMessage('账户配置已更新，请刷新后重试');
          } else if (result.error === 'forbidden') {
            setEgressMessage('当前会话无权切换此出口');
          } else {
            setEgressMessage('出口健康检查失败，未修改选择');
          }
        },
        onError: () => setEgressMessage('切换失败或超时，请刷新核对'),
      },
    );
  };
  return <div className="wrap user-panel">
    <header className="user-panel-header"><div className="user-panel-brand"><div><div className="small faint">Hysteria · {publicHost}</div><h1 className="user-panel-title">个人控制台</h1><div className="user-panel-subtitle faint">订阅、用量与设备</div></div></div><div className="user-panel-account"><div className="user-panel-name mono">{data.username}</div><span className={`badge ${data.disabled || data.expired ? 'is-error' : 'is-live'}`}>{data.disabled ? '停用' : data.expired ? '已到期' : '正常'}</span><div className="user-panel-actions"><form method="post" action="/user/logout"><button className="btn ghost btn-sm" type="submit">退出登录</button></form></div></div></header>
    {(data.disabled || data.expired) ? <div className="err" role="alert">{data.disabled ? '账号已停用，请联系管理员。' : '账号已到期，请联系管理员续费。'}</div> : null}
    <section className="usage-section" aria-label="本周期用量"><header className="section-head"><h2 className="section-title">本周期用量</h2><div className="poll-status small">自动更新 · 30 s</div></header><div className="usage-kpis"><div className="usage-kpi"><div className="k">已用流量</div><div className="v">{fmtBytes(data.used_bytes)}</div><div className="sub">{quotaUnlimited ? '不限额' : `${data.percent.toFixed(1)}%`}</div></div><div className="usage-kpi"><div className="k">剩余额度</div><div className="v">{quotaUnlimited ? '不限' : fmtBytes(data.remain_bytes)}</div><div className="sub">在线 {data.online} / {data.max_devices || '不限'} 台</div></div><div className="usage-kpi"><div className="k">计费周期</div><div className="v usage-date">{data.cycle_reset_date}</div><div className="sub">还剩 {data.cycle_days_left} 天 · 周期 {data.cycle_length_days} 天</div></div></div><div className="usage-progress"><div className="usage-progress-head"><span className="k">本周期</span><span className="bold numeric">{quotaUnlimited ? '不限' : `${data.percent.toFixed(2)}%`}</span></div><div className="bar"><div className="fill" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent} style={{ width: `${percent}%` }}/></div><div className="small mt-sm">上传 {fmtBytes(data.tx_bytes)} · 下载 {fmtBytes(data.rx_bytes)}</div></div></section>
    <section className="connection-section" aria-label="订阅链接"><header className="section-head"><h2 className="section-title">订阅链接</h2></header>{current ? <div className="connection-panel"><div className="profile-tabs" role="group" aria-label="选择订阅模式">{data.subscription_profiles.map(profile => <button className={`profile-tab${profile.key === current.key ? ' is-active' : ''}`} type="button" key={profile.key} onClick={() => { setSelectedProfile(profile.key); setShowQr(false); }}>{profile.label}</button>)}</div><div className="connection-url"><code className="mono url-text">{current.url}</code><button className="btn primary btn-sm" type="button" onClick={() => void copy()}>{copied ? '已复制' : '复制链接'}</button></div><div className="connection-actions"><a className="btn secondary btn-sm" href={current.url}>打开配置</a><button className="btn ghost btn-sm" type="button" onClick={() => setShowQr(value => !value)} aria-expanded={showQr}>{showQr ? '隐藏二维码' : '显示二维码'}</button></div>{showQr ? <div className="profile-qr-panel"><div className="qr-wrap"><img width="220" height="220" alt={`${current.label}订阅二维码`} src={current.qr_path}/></div><div className="small faint" role="status">用客户端扫码导入当前订阅。</div></div> : null}<div className="small faint mt-sm">{current.description}</div></div> : <div className="small faint">当前账号暂无可用订阅。</div>}</section>
    <aside className="plan-section" aria-label="套餐与设备"><header className="section-head"><h2 className="section-title">套餐与设备</h2></header><dl className="user-kv"><div><dt>用户名</dt><dd className="mono">{data.username}</dd></div><div><dt>有效期</dt><dd>{data.expiry_label}</dd></div><div><dt>上传 / 下载</dt><dd>{fmtBytes(data.tx_bytes)} / {fmtBytes(data.rx_bytes)}</dd></div></dl></aside>
    {data.landing_nodes.length ? <section className="plan-section" aria-label="家宽出口"><header className="section-head"><h2 className="section-title">家宽出口</h2></header>{egressMessage ? <div className="flash" role="status">{egressMessage}</div> : null}<div className="user-kv">{data.landing_nodes.map(node => <div key={node.id}><dt>{node.name}</dt><dd><code>{node.exit_ip}</code> · {node.region || node.isp || '未标注'} {node.selected ? <span className="badge is-live">当前</span> : null}{data.can_select_egress ? <form method="post" action="/user/landing-egress/select" className="inline-form-row" onSubmit={selectEgress}><input type="hidden" name="egress_id" value={node.id}/><input type="hidden" name="user_revision" value={data.revision}/><button className="btn ghost btn-sm" type="submit" disabled={node.selected || egressAction.busy}>切换</button></form> : null}</dd></div>)}</div></section> : null}
    <section className="account-section" aria-label="账户与安全"><header className="section-head"><h2 className="section-title">账户与安全</h2></header>{data.can_change_password ? <a className="btn ghost btn-sm" href="/user/change-password">修改面板密码</a> : <span className="small faint">当前登录方式不支持修改密码。</span>}</section>
  </div>;
}
