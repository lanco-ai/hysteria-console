import { useLayoutEffect, useRef, useState } from 'react';
import { AdminShell } from '../../../shared/AdminShell';
import { LoadingState } from '../../../shared/LoadingState';
import { useInitialFragmentNavigation } from '../../../shared/useInitialFragmentNavigation';
import { OverviewTable } from './OverviewTable';
import { CreateForm, CycleForm, EditDialog } from './UserForms';
import { useOverview } from './useOverview';
import type { OverviewUser } from './types';

const targets = new Set(['main-content']);
const emptyTargets = new Set<string>();

export function OverviewPage({ publicHost }: { publicHost: string }) {
  const overview = useOverview();
  const { data, busy, blocked } = overview;
  const [selected, setSelected] = useState<OverviewUser>();
  const trigger = useRef<HTMLButtonElement>(null);
  const returning = useRef(false);
  useInitialFragmentNavigation(data ? targets : emptyTargets);
  useLayoutEffect(() => {
    if (!selected && !busy && returning.current) {
      returning.current = false;
      trigger.current?.focus();
    }
  }, [selected, busy]);
  const close = () => {
    returning.current = true;
    setSelected(undefined);
  };
  const disabled = busy || blocked;
  const topbarExtra = data ? <>
    <button
      className="badge poll-status poll-status-button"
      data-role="admin-poll-status"
      type="button"
      title="立即更新"
      disabled={busy || overview.loading}
      onClick={overview.retryPoll}
    >{overview.pollStatus}</button>
    <span className="sr-only" id="admin-poll-announcer" role="status" aria-live="polite">
      {overview.pollStatus}
    </span>
  </> : undefined;

  return <AdminShell
    active="dashboard"
    pageTitle="用户"
    badge={data ? `${data.users.length} 个用户` : ''}
    subtitle={publicHost}
    topbarExtra={topbarExtra}
  >
    {overview.message ? <div className="flash" role="status">{overview.message}</div> : null}
    {overview.reloadStatus ? <div className="flash" role="status">{overview.reloadStatus}</div> : null}
    {overview.readError ? <div className="err" role="alert">
      {overview.readError}
      {overview.auth ? <a href="/login">管理员登录</a> : <button
        className="btn btn-sm"
        type="button"
        disabled={busy || overview.loading}
        onClick={overview.refresh}
      >刷新核对</button>}
    </div> : null}
    {overview.loading && !data ? <LoadingState label="正在加载用户…"/> : null}
    {data ? <>
      <section className="overview-cycle" aria-label="计费周期">
        <div className="overview-cycle-info">
          <span className="overview-cycle-label">计费周期</span>
          <strong>{data.cycle.key}</strong>
          <span className="small">{data.cycle.range}</span>
        </div>
        <CycleForm cycle={data.cycle} disabled={disabled} mutate={overview.mutate}/>
        <div className="overview-cycle-actions">
          <a className="btn btn-sm" href="/admin/usage.csv?window=cycle">导出 CSV</a>
          <form method="post" action="/admin/reset-usage-all" onSubmit={event => {
            event.preventDefault();
            if (!disabled && window.confirm('确认清空全部用户本周期已用流量？')) {
              void overview.mutate('reset-usage-all', {});
            }
          }}>
            <button className="btn btn-sm danger-btn reset-all-button" type="submit" disabled={disabled}>
              清空本周期用量
            </button>
          </form>
        </div>
      </section>
      <CreateForm data={data} disabled={disabled} mutate={overview.mutate}/>
      <OverviewTable rows={data.users} disabled={disabled} mutate={overview.mutate} edit={(row, button) => {
        trigger.current = button;
        setSelected({ ...row });
      }}/>
      {selected ? <EditDialog
        row={selected}
        currentRevision={data.users.find(row => row.user === selected.user)?.revision}
        disabled={busy}
        blocked={blocked}
        mutate={overview.mutate}
        refresh={overview.refresh}
        close={close}
      /> : null}
    </> : null}
  </AdminShell>;
}
