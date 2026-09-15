import { useState, type FormEvent } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { useFormAction } from '../../../shared/useFormAction';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { LANDING_ENDPOINT, mutateLanding, parseLanding } from './requests';
import type { LandingFormFields } from './requests';
import type { LandingOperationAction } from './types';

function ErrorState({ status, message, retry }: { status: number; message: string; retry: () => void }) {
  if (status === 401) return <div className="card"><div className="err" role="alert">管理员登录已失效。</div><a className="btn secondary mt-md" href="/login">前往登录</a></div>;
  return <div className="card"><div className="err" role="alert">家宽出口加载失败：{message}</div><button className="btn secondary mt-md" type="button" onClick={retry}>重试</button></div>;
}

function fields(form: HTMLFormElement): LandingFormFields {
  const result: LandingFormFields = {};
  for (const [key, value] of new FormData(form)) {
    if (typeof value !== 'string') continue;
    const previous = result[key];
    if (previous === undefined) result[key] = value;
    else if (typeof previous === 'string') result[key] = [previous, value];
    else result[key] = [...previous, value];
  }
  return result;
}

function operationMessage(action: LandingOperationAction, result: Awaited<ReturnType<typeof mutateLanding>>): string {
  if (result.ok) {
    return {
      save: '家宽出口节点已保存',
      delete: '家宽出口节点已删除',
      check: '健康检查已完成',
      access: '用户授权已更新',
      select: '家宽出口已切换',
    }[action];
  }
  if (result.error === 'revision_conflict') return '配置已被其他操作更新，请刷新后重试';
  if (result.error === 'not_found') return '节点或用户不存在，请刷新后重试';
  if (result.error === 'forbidden') return '当前会话无权执行此操作';
  if (result.error === 'rate_limited') return `切换过于频繁，请 ${result.retry_after ?? 60} 秒后重试`;
  const codes: Record<string, string> = {
    node_text_invalid: '节点文字字段无效',
    node_id_invalid: '节点 ID 无效',
    node_ip_invalid: 'SOCKS5 或出口 IP 无效',
    node_port_invalid: 'SOCKS5 端口无效',
    node_credentials_invalid: 'SOCKS5 凭据无效',
    missing_result: '服务器未返回操作结果',
  };
  return codes[result.code ?? ''] ?? '节点配置无效，服务器未修改';
}

export function LandingPage({ publicHost }: { publicHost: string }) {
  const resource = useReadResource(LANDING_ENDPOINT, { validate: parseLanding });
  const formAction = useFormAction();
  const [message, setMessage] = useState('');

  const submit = (event: FormEvent<HTMLFormElement>, action: LandingOperationAction) => {
    event.preventDefault();
    const payload = fields(event.currentTarget);
    setMessage('正在提交…');
    void formAction.run(
      signal => mutateLanding(action, payload, signal),
      {
        onResult: result => {
          setMessage(operationMessage(action, result));
          if (result.ok) resource.retry();
        },
        onError: () => setMessage('请求失败或超时，请刷新核对'),
      },
    );
  };

  return <AdminShell active="landing-egresses" pageTitle="家宽出口" badge={resource.status === 'success' ? `${resource.data.nodes.length} 个节点` : ''} subtitle={`${publicHost} · 真实 SOCKS5 出口`}>
    {message ? <div className="flash" role="status">{message}</div> : null}
    {resource.status === 'error' ? <ErrorState status={resource.error.status ?? 0} message={resource.error.message} retry={resource.retry}/> : null}
    {resource.status === 'loading' ? <div className="card" role="status">正在加载家宽出口…</div> : null}
    {resource.status === 'success' ? <div className="admin-page landing-page">
      <section className="form-section landing-node-list"><div className="form-section-title">家宽出口节点</div><div className="form-section-desc">凭据只写入服务器，不会通过 API 返回或在页面回显。健康检查会验证实际出口 IP。</div><div className="data-table-wrap" tabIndex={0} aria-label="家宽出口节点表格，可横向滚动"><table className="data-table"><thead><tr><th>名称</th><th>预期出口</th><th>地区 / 运营商</th><th>状态</th><th>操作</th></tr></thead><tbody>{resource.data.nodes.length ? resource.data.nodes.map(node => <tr key={node.id}><th scope="row">{node.name}<div className="small faint"><code>{node.id}</code></div></th><td><code>{node.exit_ip}</code></td><td>{node.region || '—'}{node.isp ? ` · ${node.isp}` : ''}</td><td><span className={`badge ${node.enabled ? 'badge-success' : 'badge-neutral'}`}>{node.enabled ? '启用' : '禁用'}</span><div className="small faint">{node.health?.status || '未检查'}</div></td><td><div className="row gap-sm"><form method="post" action="/admin/landing-egress/check" onSubmit={event => submit(event, 'check')}><input type="hidden" name="id" value={node.id}/><button className="btn btn-ghost btn-sm" type="submit" disabled={formAction.busy}>健康检查</button></form><form method="post" action="/admin/landing-egress/delete" onSubmit={event => submit(event, 'delete')}><input type="hidden" name="id" value={node.id}/><button className="btn danger-btn btn-sm" type="submit" disabled={formAction.busy}>删除</button></form></div></td></tr>) : <tr><td colSpan={5} className="empty">尚未配置家宽出口</td></tr>}</tbody></table></div></section>
      <section className="form-section"><div className="form-section-title">新增或更新节点</div><div className="form-section-desc">节点 ID 已存在时会更新配置；留空凭据则保留原值。</div><form method="post" action="/admin/landing-egress/save" className="landing-node-form" onSubmit={event => submit(event, 'save')}><input type="hidden" name="registry_revision" value={resource.data.revision}/><div className="form-grid landing-node-grid"><div className="form-field"><label htmlFor="landing-node-id">节点 ID</label><input id="landing-node-id" name="id" required autoComplete="off" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-node-name">显示名称</label><input id="landing-node-name" name="name" required disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-socks-ip">SOCKS5 IP</label><input id="landing-socks-ip" name="socks_ip" required autoComplete="off" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-socks-port">SOCKS5 端口</label><input id="landing-socks-port" name="socks_port" type="number" min={1} max={65535} required disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-socks-username">SOCKS5 用户名</label><input id="landing-socks-username" name="socks_username" autoComplete="off" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-socks-password">SOCKS5 密码</label><input id="landing-socks-password" name="socks_password" type="password" autoComplete="new-password" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-expected-exit-ip">预期出口 IP</label><input id="landing-expected-exit-ip" name="expected_exit_ip" required autoComplete="off" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-isp">运营商</label><input id="landing-isp" name="isp" disabled={formAction.busy}/></div><div className="form-field"><label htmlFor="landing-region">地区</label><input id="landing-region" name="region" disabled={formAction.busy}/></div></div><div className="landing-node-actions"><label className="switch"><input name="enabled" type="checkbox" value="1" defaultChecked disabled={formAction.busy}/>启用节点</label><button className="btn btn-primary" type="submit" disabled={formAction.busy}>保存节点</button></div></form></section>
      <section className="form-section"><div className="form-section-title">用户授权</div><div className="form-section-desc">只会把已启用节点授权给用户；用户切换时仍会再次健康检查。</div>{resource.data.users.length ? <div className="landing-access-list">{resource.data.users.map(item => <form method="post" action="/admin/user-landing-access" className="landing-access-row" key={item.user} onSubmit={event => submit(event, 'access')}><input type="hidden" name="user" value={item.user}/><input type="hidden" name="user_revision" value={item.revision}/><div className="landing-access-user"><strong>{item.user}</strong><span className="small faint">可授权一个或多个出口</span></div><fieldset className="landing-access-choices"><legend className="sr-only">{item.user} 可使用的家宽出口</legend>{resource.data.nodes.map(node => <label className="landing-access-choice" key={node.id}><input type="checkbox" name="egress_id" value={node.id} defaultChecked={item.allowed_ids.includes(node.id)} disabled={!node.enabled || formAction.busy}/><span>{node.name}{node.enabled ? '' : '（已禁用）'}</span></label>)}</fieldset><button className="btn btn-secondary btn-sm" type="submit" disabled={formAction.busy}>保存授权</button></form>)}</div> : <div className="empty">暂无用户</div>}</section>
    </div> : null}
  </AdminShell>;
}
