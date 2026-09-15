import { useState } from 'react';
import { Icon } from '../../../shared/icons';
import { fmtBytes, Spark } from './presentation';
import type { Action, Mutate, OverviewUser } from './types';

const actions: { action: Action; label: string; title: string; confirm: (user: string) => string }[] = [
  { action: 'reset-usage', label: '清流量', title: '清空该用户已用流量，且从服务器总流量中扣除', confirm: user => `确认清零用户 ${user} 的本周期用量？该流量也会从服务器本周期总计中扣除。` },
  { action: 'refresh-usage', label: '刷新流量', title: '清空该用户已用流量，但保留在服务器总流量中', confirm: user => `确认将用户 ${user} 的用量归零？服务器本周期总计会保留这部分流量。` },
  { action: 'rotate-token', label: '重置订阅', title: '重置该用户订阅令牌，旧订阅/面板链接立即失效', confirm: user => `确认重置用户 ${user} 的订阅令牌？旧订阅/面板链接将立即失效。` },
];

function Links({ row, disabled }: { row: OverviewUser; disabled: boolean }) {
  const [message, setMessage] = useState('');
  const copy = async (text: string) => {
    if (disabled) return;
    try {
      try { await navigator.clipboard.writeText(text); }
      catch {
        const focused = document.activeElement;
        const input = document.createElement('textarea');
        input.value = text; input.readOnly = true; input.style.position = 'fixed'; input.style.opacity = '0';
        document.body.appendChild(input);
        try { input.select(); if (!document.execCommand('copy')) throw new Error('复制失败'); }
        finally { input.remove(); if (focused instanceof HTMLElement) focused.focus(); }
      }
      setMessage('已复制');
    } catch { setMessage('复制失败，请打开链接后手动复制'); }
  };
  return <td className="link-cell" headers="users-col-links" data-label="链接">
    <div className="link-row">
      <a href={disabled ? undefined : row.panel_url} aria-disabled={disabled || undefined} target="_blank" rel="noopener">
        <Icon name="dashboard"/>
        <span>面板</span>
      </a>
      <button type="button" className="btn ghost btn-sm copy-link" title="复制专属面板链接（首次打开后地址栏不再含密钥）" aria-label={`复制 ${row.user} 的专属面板链接`} disabled={disabled} onClick={() => { void copy(row.panel_url); }}>
        <Icon name="copy"/>
        <span className="copy-label">复制专属面板</span>
      </button>
    </div>
    <div className="link-row">
      <a href={disabled ? undefined : row.subscription_url} aria-disabled={disabled || undefined} target="_blank" rel="noopener">
        <Icon name="open"/>
        <span>订阅</span>
      </a>
      <button type="button" className="btn ghost btn-sm copy-link" title="复制订阅链接" aria-label={`复制 ${row.user} 的订阅链接`} disabled={disabled} onClick={() => { void copy(row.subscription_url); }}>
        <Icon name="copy"/>
      </button>
    </div>
    {message ? <span className="small" role="status">{message}</span> : null}
  </td>;
}

export function OverviewTable({ rows, disabled, mutate, edit }: { rows: OverviewUser[]; disabled: boolean; mutate: Mutate; edit: (row: OverviewUser, trigger: HTMLButtonElement) => void }) {
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState('all');
  const visible = rows.filter(row => row.user.toLowerCase().includes(query.toLowerCase()) && (filter === 'all' || (filter === 'online' ? row.online > 0 : row.percent >= 90)));
  const action = (row: OverviewUser, which: Action, confirmation = '') => {
    if (disabled || (confirmation && !window.confirm(confirmation))) return;
    void mutate(which, { user: row.user, user_revision: row.revision, ...(which === 'toggle-user' ? { desired: row.disabled ? 'enabled' : 'disabled' } : {}) });
  };
  return <div className="users-section">
    <div className="users-header">
      <h2 className="users-title">用户列表</h2>
      <div className="users-toolbar">
        <input id="user-filter" type="search" placeholder="搜索…" aria-label="搜索用户名" autoComplete="off" className="user-filter-input" value={query} onChange={event => setQuery(event.target.value)}/>
        <div className="filter-chips" role="group" aria-label="状态筛选">
          {[['all', '全部'], ['online', '在线'], ['over', '超限']].map(([value, label]) => <button
            key={value} type="button" className={`chip${filter === value ? ' active' : ''}`}
            data-filter={value} aria-pressed={filter === value} onClick={() => setFilter(value!)}
          >{label}</button>)}
        </div>
        <span className="filter-count" id="filter-count" role="status" aria-live="polite">{`${visible.length} / ${rows.length} 个`}</span>
      </div>
    </div>
    <div className="small faint mt-sm">“复制专属面板”会复制每位用户各自的安全入口；打开后地址栏会安全归一为 <code>/user/panel</code>。</div>
    <div className="users-table-wrap">
      <table className="users-table" data-user-count={rows.length}>
        <caption className="sr-only">用户、套餐用量、管理操作与订阅链接</caption>
        <thead>
          <tr>{[['user', '用户'], ['trend', '趋势'], ['usage', '用量'], ['actions', '操作'], ['links', '链接']].map(([id, label]) => <th key={id} id={`users-col-${id}`} scope="col">{label}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map(row => {
            const unlimited = row.total <= 0;
            const percent = unlimited ? '不限' : `${row.percent.toFixed(1)}%`;
            const width = unlimited ? '0.0' : row.percent.toFixed(1);
            const quota = unlimited ? '不限' : `${row.base_quota_gb} GB${row.quota_extra_gb ? ` · 加量 ${row.quota_extra_gb} GB` : ''}`;
            const devices = row.max_devices === 0 ? '不限设备' : `${row.max_devices} 设备`;
            const summary = `${quota} · ${devices}${row.metered ? ' · 按量' : ''}${row.expires_at ? ` · ${row.expiry_label}` : ''}`;
            return <tr key={row.user} hidden={!visible.includes(row)} data-user={row.user} data-online={row.online} data-percent={row.percent.toFixed(1)} data-revision={row.revision}>
            <td headers="users-col-user" data-label="用户">
              <div className="row gap-sm user-identity">
                <div className="user-avatar" aria-hidden="true">{row.user.slice(0, 1).toUpperCase()}</div>
                <div className="user-identity-copy">
                  <div className="bold">
                    {row.user}{' '}
                    {row.metered ? <span className="badge badge-info">按量</span> : null}
                    <span className={`badge${row.tuic_enabled ? '' : ' badge-danger'}`}>{row.tuic_enabled ? 'TUIC' : 'TUIC 关闭'}</span>
                    <span className="badge badge-danger" data-role="disabled-badge" hidden={!row.disabled}>已停用</span>
                    {row.expired ? <span className="badge badge-danger">已过期</span> : null}
                  </div>
                  <div className="small">在线 <span data-role="online">{row.online}</span>{row.max_devices === 0 ? ' · 设备不限' : ` / ${row.max_devices} 设备`}</div>
                  {row.note ? <div className="small faint">{row.note}</div> : null}
                </div>
              </div>
            </td>
            <td className="spark-cell" headers="users-col-trend" data-label="30 天趋势" data-role="spark">
              <Spark values={row.spark}/>
            </td>
            <td headers="users-col-usage" data-label="本周期用量">
              <div className="row usage-label">
                <span className="bold" data-role="used">{fmtBytes(row.used)}</span>
                <span className="small">/ {unlimited ? '不限' : fmtBytes(row.total)}</span>
              </div>
              <div className="mini-bar">
                <div className={`mini-fill ${unlimited ? 'unlimited' : row.percent >= 90 ? 'danger' : ''}`} data-role="bar" role="progressbar" aria-label={`${row.user} 本周期流量`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Number(width)} aria-valuetext={percent} style={{ width: `${width}%` }}/>
              </div>
              <div className="small mt-sm" data-role="detail">{percent} · ↑{fmtBytes(row.tx)} ↓{fmtBytes(row.rx)}</div>
            </td>
            <td headers="users-col-actions" data-label="操作">
              <div className="edit-user-control">
                <button type="button" className="btn secondary btn-sm edit-user" disabled={disabled} onClick={event => edit(row, event.currentTarget)}>编辑套餐</button>
                <span className="summary-preview">{summary}</span>
              </div>
              <div className="row gap-sm mt-sm user-actions">
                {actions.map(item => <button
                  key={item.action} className="btn ghost btn-sm user-action" type="button"
                  title={item.title} disabled={disabled}
                  onClick={() => action(row, item.action, item.confirm(row.user))}
                >{item.label}</button>)}
                <button
                  className="btn ghost btn-sm user-action" type="button" disabled={disabled}
                  title={row.disabled ? '恢复该用户的连接权限' : '临时停用：拒绝新连接并断开现有会话，不删除用户'}
                  onClick={() => action(row, 'toggle-user', row.disabled ? '' : `确认停用用户 ${row.user}？将拒绝新连接并断开其现有会话。`)}
                >{row.disabled ? '启用' : '暂停'}</button>
                <button
                  className="btn danger-btn btn-sm user-action" type="button" disabled={disabled}
                  onClick={() => action(row, 'delete', `确认删除用户 ${row.user}？此操作不可撤销。`)}
                >删除</button>
              </div>
            </td>
            <Links row={row} disabled={disabled}/>
          </tr>;
          })}
          {!rows.length ? <tr>
            <td colSpan={5} className="empty">暂无用户，使用下方表单创建第一个用户</td>
          </tr> : <tr id="filter-empty" hidden={visible.length > 0}>
            <td colSpan={5} className="empty">没有符合当前筛选条件的用户</td>
          </tr>}
        </tbody>
      </table>
    </div>
  </div>;
}
