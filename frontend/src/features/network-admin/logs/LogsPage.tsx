import { useMemo, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { validateLogs, validateSession, type LogRow } from './types';

function ErrorState({ error, retry }: { error: ResourceError; retry: () => void }) {
  return <div className="card">
    <div className="err" role="alert" aria-live="assertive" aria-atomic="true">
      加载失败：{error.message}
    </div>
    <div className="row mt-md"><button className="btn secondary" type="button" onClick={retry}>重试</button></div>
  </div>;
}

function LoginState({ userSession = false }: { userSession?: boolean }) {
  return <div className="card">
    <div className="err" role="alert" aria-live="assertive" aria-atomic="true">
      {userSession ? '此页面仅限管理员使用。' : '管理员登录已失效。'}
    </div>
    <div className="row mt-md"><a className="btn secondary" href="/login">{userSession ? '管理员登录' : '前往登录'}</a></div>
  </div>;
}

type LogKind = 'reset' | 'access' | 'token' | 'other';

const KIND_LABELS: Record<LogKind, string> = { reset: '流量清零', access: '启用 / 停用', token: '订阅令牌', other: '其他' };
const RAW_ACTIONS: Record<string, string> = {
  reset_user: '清除用户流量',
  reset_usage_user: '清除用户流量',
  reset_usage_all: '清空全部流量',
  refresh_usage_user: '刷新用户流量（保留总计）',
  rotate_token: '重置订阅令牌',
  disable_user: '停用用户',
  enable_user: '启用用户',
};
const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];

/** The server labels manual actions; scheduled resets arrive as raw keys. */
function actionLabel(action: string): string {
  const auto = action.match(/^reset_usage_all_auto_day(\d+)$/);
  if (auto) return `周期自动清零（每月 ${Number(auto[1])} 日）`;
  return RAW_ACTIONS[action] ?? action;
}

function actionKind(label: string): LogKind {
  if (/清零|清除|清空|刷新/.test(label)) return 'reset';
  if (/停用|启用/.test(label)) return 'access';
  if (/令牌|token/i.test(label)) return 'token';
  return 'other';
}

const pad = (value: number) => String(value).padStart(2, '0');

type LogEntry = LogRow & { label: string; kind: LogKind; day: string; clock: string; date: Date | null };

function entry(row: LogRow): LogEntry {
  const label = actionLabel(row.action);
  const date = new Date(row.time);
  if (Number.isNaN(date.getTime())) return { ...row, label, kind: actionKind(label), day: '', clock: row.time || '—', date: null };
  return {
    ...row, label, kind: actionKind(label), date,
    day: `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`,
    clock: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
  };
}

function dayHeading(day: string, sample: Date | null): string {
  if (!day || !sample) return '时间未知';
  const now = new Date();
  const today = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const yesterdayDate = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1);
  const yesterday = `${yesterdayDate.getFullYear()}-${pad(yesterdayDate.getMonth() + 1)}-${pad(yesterdayDate.getDate())}`;
  const label = `${sample.getFullYear() === now.getFullYear() ? '' : `${sample.getFullYear()} 年 `}${sample.getMonth() + 1} 月 ${sample.getDate()} 日 ${WEEKDAYS[sample.getDay()]}`;
  if (day === today) return `今天 · ${label}`;
  if (day === yesterday) return `昨天 · ${label}`;
  return label;
}

function targetLabel(target: string): string {
  return target === 'all_users' ? '全部用户' : target || '—';
}

function LogTimeline({ rows, limit }: { rows: LogRow[]; limit: number }) {
  const [kind, setKind] = useState<LogKind | 'all'>('all');
  const [query, setQuery] = useState('');
  const entries = useMemo(() => rows.map(entry), [rows]);
  const counts = useMemo(() => entries.reduce<Record<LogKind, number>>((total, item) => ({ ...total, [item.kind]: total[item.kind] + 1 }), { reset: 0, access: 0, token: 0, other: 0 }), [entries]);
  const needle = query.trim().toLowerCase();
  const visible = entries.filter(item => (kind === 'all' || item.kind === kind)
    && (!needle || [item.label, item.target, targetLabel(item.target), item.actor, item.ip, item.detail].some(value => value.toLowerCase().includes(needle))));
  const days: { day: string; sample: Date | null; items: LogEntry[] }[] = [];
  for (const item of visible) {
    const last = days[days.length - 1];
    if (last && last.day === item.day) last.items.push(item);
    else days.push({ day: item.day, sample: item.date, items: [item] });
  }
  const chips: (LogKind | 'all')[] = ['all', 'reset', 'access', 'token', ...(counts.other ? ['other' as const] : [])];
  return <>
    <div className="log-toolbar">
      <div className="log-filters" role="group" aria-label="按类型筛选">
        {chips.map(key => <button key={key} type="button" className={`chip${kind === key ? ' active' : ''}`} aria-pressed={kind === key} onClick={() => setKind(key)}>
          {key === 'all' ? '全部' : KIND_LABELS[key]}<span className="chip-count">{key === 'all' ? entries.length : counts[key]}</span>
        </button>)}
      </div>
      <label className="sr-only" htmlFor="log-search">搜索清零日志</label>
      <input id="log-search" className="log-search" type="search" placeholder="搜索用户、操作人或 IP" value={query} onChange={event => setQuery(event.target.value)}/>
    </div>
    {!entries.length ? <div className="empty">暂无日志记录</div> : null}
    {entries.length > 0 && !visible.length ? <div className="empty">没有符合筛选的记录</div> : null}
    {days.map(group => <section className="log-day" key={group.day || 'unknown'} aria-labelledby={`log-day-${group.day || 'unknown'}`}>
      <h3 className="log-day-title" id={`log-day-${group.day || 'unknown'}`}>{dayHeading(group.day, group.sample)}<span className="chip-count">{group.items.length}</span></h3>
      <ol className="log-entries">
        {group.items.map((item, index) => <li className={`log-entry is-${item.kind}`} key={`${item.time}-${item.actor}-${item.target}-${index}`}>
          <time className="log-time" dateTime={item.time}>{item.clock}</time>
          <span className="log-kind">{KIND_LABELS[item.kind]}</span>
          <div className="log-main">
            <div className="log-title"><strong>{item.label}</strong><span className="log-target">{targetLabel(item.target)}</span></div>
            <div className="log-meta"><span className="log-actor">{item.actor === 'system' ? '系统自动' : item.actor || '—'}</span>{item.ip ? <> · <span className="mono">{item.ip}</span></> : null}{item.month && item.kind === 'reset' ? <> · 周期 {item.month}</> : null}</div>
          </div>
          {item.detail ? <span className="log-detail mono">{item.detail}</span> : null}
        </li>)}
      </ol>
    </section>)}
    {entries.length >= limit ? <div className="small faint log-footnote">只显示最近 {limit} 条</div> : null}
  </>;
}

export function LogsPanel() {
  const session = useReadResource('/api/v1/session', { validate: validateSession });
  const isAdmin = session.status === 'success' && session.data.role === 'admin';
  const logs = useReadResource('/api/v1/admin/logs', { enabled: isAdmin, validate: validateLogs });

  if (session.status !== 'success') {
    if (session.status === 'error') {
      if (session.error.status === 401) return <LoginState/>;
      return <ErrorState error={session.error} retry={session.retry}/>;
    }
    return <LoadingState label="正在验证管理员会话…"/>;
  }
  if (session.data.role !== 'admin') return <LoginState userSession/>;
  if (logs.status === 'error') {
    if (logs.error.status === 401 || logs.error.status === 403) return <LoginState/>;
    return <ErrorState error={logs.error} retry={logs.retry}/>;
  }

  return <section className="admin-section logs-section" aria-labelledby="logs-title">
    <div className="admin-section-header">
      <div>
        <h2 className="admin-section-title" id="logs-title">操作记录</h2>
        <div className="small">流量清零、启停用户与订阅令牌变更 · 最新在上</div>
      </div>
      <div className="small">{logs.status === 'success' ? `${logs.data.rows.length} 条` : ''}</div>
    </div>
    <div className="admin-section-body">
      {logs.status === 'success' ? <LogTimeline rows={logs.data.rows} limit={logs.data.limit}/> : <LoadingState label="正在加载日志…" variant="table"/>}
    </div>
  </section>;
}

export function LogsPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="operations" pageTitle="运维" subtitle={publicHost}><LogsPanel/></AdminShell>;
}
