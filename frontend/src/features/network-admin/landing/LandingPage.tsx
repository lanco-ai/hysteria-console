import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { useFormAction } from '../../../shared/useFormAction';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { LANDING_ENDPOINT, mutateLanding, parseLanding } from './requests';
import type { LandingFormFields } from './requests';
import type { LandingAccess, LandingNode, LandingOperationAction } from './types';

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

const pad = (value: number) => String(value).padStart(2, '0');

function checkedAt(value: string | undefined): string {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())} 检查`;
}

function health(node: LandingNode): { tone: 'ok' | 'bad' | 'unknown'; label: string } {
  if (!node.health?.status) return { tone: 'unknown', label: '未检查' };
  if (node.health.status === 'healthy') return { tone: 'ok', label: node.health.observed_ip ? `健康 · 实测出口 ${node.health.observed_ip}` : '健康' };
  const reasons: Record<string, string> = { exit_ip_mismatch: '不可用 · 出口 IP 与预期不符', probe_failed: '不可用 · 探测失败' };
  return { tone: 'bad', label: reasons[node.health.error_code ?? ''] ?? '不可用' };
}

type Run = (action: LandingOperationAction, payload: LandingFormFields, handlers?: { ok?: () => void; fail?: (message: string) => void }) => void;

function NodeCard({ node, users, busy, run, edit }: { node: LandingNode; users: number; busy: boolean; run: Run; edit: (button: HTMLButtonElement) => void }) {
  const state = health(node);
  return <li className={`landing-card${node.enabled ? '' : ' is-disabled'}`}>
    <div className="landing-card-head">
      <div className="landing-card-identity"><strong className="landing-card-name">{node.name}</strong><code className="landing-card-id">{node.id}</code></div>
      <span className={`badge ${node.enabled ? 'badge-success' : 'badge-neutral'}`}>{node.enabled ? '启用' : '禁用'}</span>
    </div>
    <div className={`landing-health is-${state.tone}`}><span className="landing-health-dot" aria-hidden="true"/><span>{state.label}</span>{node.health?.checked_at ? <span className="landing-health-time">{checkedAt(node.health.checked_at)}</span> : null}</div>
    <dl className="landing-card-facts">
      <div><dt>预期出口</dt><dd className="mono">{node.exit_ip}</dd></div>
      <div><dt>地区 / 运营商</dt><dd>{[node.region, node.isp].filter(Boolean).join(' · ') || '—'}</dd></div>
      <div><dt>已授权</dt><dd>{users} 位用户</dd></div>
    </dl>
    <div className="landing-card-actions">
      <button className="btn btn-sm" type="button" disabled={busy} onClick={() => run('check', { id: node.id })}>健康检查</button>
      <button className="btn btn-sm" type="button" disabled={busy} onClick={event => edit(event.currentTarget)}>编辑</button>
      <button className="btn btn-sm danger-btn" type="button" disabled={busy} onClick={() => { if (window.confirm(`删除家宽出口「${node.name}」？此操作不可撤销。`)) run('delete', { id: node.id }); }}>删除</button>
    </div>
  </li>;
}

function NodeDialog({ node, revision, busy, run, close }: { node: LandingNode | null; revision: string; busy: boolean; run: Run; close: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState('');
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError('');
    run('save', fields(event.currentTarget), { ok: close, fail: setError });
  };
  return <dialog className="admin-dialog landing-dialog" aria-labelledby="landing-dialog-title" ref={dialog} onCancel={event => { event.preventDefault(); if (!busy) close(); }}>
    <div className="dialog-inner">
      <div className="dialog-head">
        <h2 className="dialog-title" id="landing-dialog-title">{node ? `编辑 ${node.name}` : '新增家宽出口'}</h2>
        <button type="button" className="dialog-close" aria-label="关闭" disabled={busy} onClick={close}>×</button>
      </div>
      {error ? <div className="err" role="alert">{error}</div> : null}
      <form method="post" action="/admin/landing-egress/save" className="landing-node-form" onSubmit={submit}>
        <input type="hidden" name="registry_revision" value={revision}/>
        <div className="form-grid landing-node-grid">
          <div className="form-field"><label htmlFor="landing-node-id">节点 ID</label><input id="landing-node-id" name="id" required autoComplete="off" pattern="[a-z0-9][a-z0-9\-]{0,62}" title="小写字母、数字或连字符，最多 63 位" defaultValue={node?.id ?? ''} readOnly={Boolean(node)} disabled={busy}/><span className="hint">{node ? '编辑时不可修改' : '小写字母、数字或连字符；与已有节点相同则更新该节点'}</span></div>
          <div className="form-field"><label htmlFor="landing-node-name">显示名称</label><input id="landing-node-name" name="name" required defaultValue={node?.name ?? ''} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-socks-ip">SOCKS5 IP</label><input id="landing-socks-ip" name="socks_ip" required={!node} autoComplete="off" placeholder={node ? '留空保持不变' : undefined} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-socks-port">SOCKS5 端口</label><input id="landing-socks-port" name="socks_port" type="number" min={1} max={65535} required={!node} placeholder={node ? '留空保持不变' : undefined} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-socks-username">SOCKS5 用户名</label><input id="landing-socks-username" name="socks_username" autoComplete="off" placeholder={node ? '留空保持不变' : '可选'} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-socks-password">SOCKS5 密码</label><input id="landing-socks-password" name="socks_password" type="password" autoComplete="new-password" placeholder={node ? '留空保持不变' : '可选'} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-expected-exit-ip">预期出口 IP</label><input id="landing-expected-exit-ip" name="expected_exit_ip" required autoComplete="off" defaultValue={node?.exit_ip ?? ''} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-isp">运营商</label><input id="landing-isp" name="isp" placeholder="可选" defaultValue={node?.isp ?? ''} disabled={busy}/></div>
          <div className="form-field"><label htmlFor="landing-region">地区</label><input id="landing-region" name="region" placeholder="可选" defaultValue={node?.region ?? ''} disabled={busy}/></div>
        </div>
        <p className="small faint landing-dialog-note">{node ? 'SOCKS5 地址、端口和凭据不会回传到页面；留空的项保持原值，填写则替换。' : '用户名和密码需同时填写或同时留空。凭据只写入服务器，不会在页面回显。'}</p>
        <div className="dialog-foot">
          <label className="switch"><input name="enabled" type="checkbox" value="1" defaultChecked={node ? node.enabled : true} disabled={busy}/>启用节点</label>
          <span className="dialog-foot-spacer"/>
          <button type="button" className="btn ghost btn-sm" disabled={busy} onClick={close}>取消</button>
          <button type="submit" className="btn primary btn-sm" disabled={busy}>保存节点</button>
        </div>
      </form>
    </div>
  </dialog>;
}

function AccessRow({ item, nodes, busy, run }: { item: LandingAccess; nodes: LandingNode[]; busy: boolean; run: Run }) {
  const [selected, setSelected] = useState(() => new Set(item.allowed_ids));
  const saved = item.allowed_ids.join('\n');
  useEffect(() => { setSelected(new Set(saved ? saved.split('\n') : [])); }, [item.revision, saved]);
  const dirty = selected.size !== item.allowed_ids.length || item.allowed_ids.some(id => !selected.has(id));
  const toggle = (id: string, on: boolean) => setSelected(current => { const next = new Set(current); if (on) next.add(id); else next.delete(id); return next; });
  return <form method="post" action="/admin/user-landing-access" className="landing-access-row" onSubmit={event => { event.preventDefault(); run('access', fields(event.currentTarget)); }}>
    <input type="hidden" name="user" value={item.user}/>
    <input type="hidden" name="user_revision" value={item.revision}/>
    <div className="landing-access-user"><strong>{item.user}</strong><span className="small faint">{selected.size ? `已授权 ${selected.size} 个出口` : '未授权'}</span></div>
    <fieldset className="landing-access-choices">
      <legend className="sr-only">{item.user} 可使用的家宽出口</legend>
      {nodes.map(node => <label className={`landing-access-choice${selected.has(node.id) ? ' is-on' : ''}`} key={node.id}><input type="checkbox" name="egress_id" value={node.id} checked={selected.has(node.id)} onChange={event => toggle(node.id, event.target.checked)} disabled={!node.enabled || busy}/><span>{node.name}{node.enabled ? '' : '（已禁用）'}</span></label>)}
    </fieldset>
    <button className={`btn btn-sm${dirty ? ' primary' : ''}`} type="submit" disabled={busy || !dirty}>保存授权</button>
  </form>;
}

export function LandingPanel() {
  const resource = useReadResource(LANDING_ENDPOINT, { validate: parseLanding });
  const formAction = useFormAction();
  const [message, setMessage] = useState('');
  const [editing, setEditing] = useState<LandingNode | 'new' | null>(null);
  const trigger = useRef<HTMLButtonElement | null>(null);

  const run: Run = (action, payload, handlers = {}) => {
    setMessage('正在提交…');
    void formAction.run(
      signal => mutateLanding(action, payload, signal),
      {
        onResult: result => {
          const text = operationMessage(action, result);
          if (result.ok) { setMessage(text); resource.retry(); handlers.ok?.(); }
          else if (handlers.fail) { setMessage(''); handlers.fail(text); }
          else setMessage(text);
        },
        onError: () => { if (handlers.fail) { setMessage(''); handlers.fail('请求失败或超时，请刷新核对'); } else setMessage('请求失败或超时，请刷新核对'); },
      },
    );
  };
  const open = (node: LandingNode | 'new', button: HTMLButtonElement) => { trigger.current = button; setEditing(node); };
  const close = () => { setEditing(null); window.requestAnimationFrame(() => { if (trigger.current?.isConnected) trigger.current.focus(); }); };

  if (resource.status === 'error') return <ErrorState status={resource.error.status ?? 0} message={resource.error.message} retry={resource.retry}/>;
  if (resource.status !== 'success') return <LoadingState label="正在加载家宽出口…"/>;
  const { nodes, users } = resource.data;
  const enabled = nodes.filter(node => node.enabled).length;
  const healthy = nodes.filter(node => node.health?.status === 'healthy').length;
  return <div className="admin-page landing-page">
    {message ? <div className="flash" role="status">{message}</div> : null}
    <section className="admin-section landing-node-list" aria-labelledby="landing-nodes-title">
      <div className="admin-section-header">
        <div>
          <h2 className="admin-section-title" id="landing-nodes-title">家宽出口节点 <span className="badge">{nodes.length} 个节点</span></h2>
          <div className="small">{nodes.length ? `${enabled} 个启用 · ${healthy} 个健康 · ` : ''}凭据只写入服务器，不会通过 API 返回或在页面回显</div>
        </div>
        {nodes.length ? <button className="btn btn-sm primary" type="button" disabled={formAction.busy} onClick={event => open('new', event.currentTarget)}>+ 新增节点</button> : null}
      </div>
      <div className="admin-section-body">
        {nodes.length ? <ul className="landing-cards">{nodes.map(node => <NodeCard key={node.id} node={node} users={users.filter(item => item.allowed_ids.includes(node.id)).length} busy={formAction.busy} run={run} edit={button => open(node, button)}/>)}</ul> : <div className="landing-empty">
          <strong>尚未配置家宽出口</strong>
          <p>添加真实的家宽 SOCKS5 出口后，可把它授权给用户作为落地出口；健康检查会验证实际出口 IP。</p>
          <button className="btn primary" type="button" disabled={formAction.busy} onClick={event => open('new', event.currentTarget)}>+ 新增节点</button>
        </div>}
      </div>
    </section>
    <section className="admin-section landing-access-section" aria-labelledby="landing-access-title">
      <div className="admin-section-header">
        <div><h2 className="admin-section-title" id="landing-access-title">用户授权</h2><div className="small">只会把已启用节点授权给用户；用户切换时仍会再次健康检查</div></div>
        <span className="small">{users.length} 位用户</span>
      </div>
      <div className="admin-section-body">
        {!nodes.length ? <div className="empty">添加家宽出口节点后，可在这里为用户授权</div>
          : users.length ? <div className="landing-access-list">{users.map(item => <AccessRow key={item.user} item={item} nodes={nodes} busy={formAction.busy} run={run}/>)}</div>
            : <div className="empty">暂无用户</div>}
      </div>
    </section>
    {editing ? <NodeDialog node={editing === 'new' ? null : editing} revision={resource.data.revision} busy={formAction.busy} run={run} close={close}/> : null}
  </div>;
}

export function LandingPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="config" pageTitle="路由与出口" subtitle={`${publicHost} · 真实 SOCKS5 出口`}><LandingPanel/></AdminShell>;
}
