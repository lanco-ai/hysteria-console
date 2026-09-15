import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { ResourceError, useReadResource } from '../../../shared/readResource';
import { validateLogs, validateSession } from './types';

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

function LogsContent() {
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

  return <div className="admin-section">
    <div className="admin-section-header">
      <h2 className="admin-section-title">最近清零记录</h2>
      <div className="small">最近 {logs.status === 'success' ? logs.data.limit : 300} 条 · 最新在上</div>
    </div>
    <div className="data-table-wrap" tabIndex={0} aria-label="清零日志，可横向滚动">
      <table className="data-table">
        <thead><tr><th>时间</th><th>操作人</th><th>IP</th><th>操作</th><th>目标</th><th>月份</th><th>流量变化</th></tr></thead>
        <tbody>
          {logs.status !== 'success' ? <tr><td colSpan={7}><LoadingState label="正在加载日志…" variant="table"/></td></tr> : null}
          {logs.status === 'success' && logs.data.rows.length === 0 ? <tr><td colSpan={7} className="empty">暂无日志记录</td></tr> : null}
          {logs.status === 'success' ? logs.data.rows.map((row, index) => <tr key={`${row.time}-${row.actor}-${row.target}-${index}`}>
            <td className="small">{row.time}</td><td>{row.actor}</td><td className="small">{row.ip}</td><td>{row.action}</td><td>{row.target}</td><td className="small">{row.month}</td><td className="small">{row.detail}</td>
          </tr>) : null}
        </tbody>
      </table>
    </div>
  </div>;
}

export function LogsPage({ publicHost }: { publicHost: string }) {
  return <AdminShell active="logs" badge={publicHost} pageTitle="清零日志"><LogsContent/></AdminShell>;
}
