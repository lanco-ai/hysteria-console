import { useEffect, useState } from 'react';
import { usePasswordChange } from '../../auth/usePasswordChange';
import { validatePasswordPage } from '../../auth/passwordPageTypes';
import { AdminShell } from '../../../shared/AdminShell';
import { useReadResource } from '../../../shared/readResource';
import { useInitialFragmentNavigation } from '../../../shared/useInitialFragmentNavigation';

const fragmentTargets = new Set(['main-content']);
const initialMessages = new Map<string, string>([
  ['password changed', '管理员密码已更新'],
  ['password_wrong', '当前密码不正确'],
  ['password_mismatch', '两次输入的新密码不一致'],
  ['password_short', '新密码至少 8 位'],
]);

function initialFeedback(passwordMaxLength: number) {
  const raw = new URLSearchParams(window.location.search).get('msg') || '';
  if (!raw) return { message: '', kind: 'flash' as const };
  const error = raw.startsWith('err:');
  const key = raw.replace(/^err:/, '');
  const message = key === 'password_long'
    ? `密码不能超过 ${passwordMaxLength} 位`
    : (initialMessages.get(key) ?? key);
  return { message, kind: error ? 'err' as const : 'flash' as const };
}

function SettingsContent({ username, min, max }: { username: string; min: number; max: number }) {
  const initial = initialFeedback(max);
  const password = usePasswordChange('admin', max, initial.message, initial.kind);
  const [motion, setMotion] = useState(() => document.documentElement.classList.contains('sidebar-motion-enabled'));

  const changeMotion = (checked: boolean) => {
    setMotion(checked);
    document.documentElement.classList.toggle('sidebar-motion-enabled', checked);
    try {
      if (checked) localStorage.setItem('hy2.sidebar-motion', 'enabled');
      else localStorage.removeItem('hy2.sidebar-motion');
    } catch {
      // Storage may be unavailable; the visible preference still applies.
    }
  };

  return <>
    {password.feedback ? <div className={password.feedbackKind} role={password.feedbackKind === 'err' ? 'alert' : 'status'} aria-live={password.feedbackKind === 'err' ? 'assertive' : 'polite'} aria-atomic="true">{password.feedback}</div> : null}
    <div className="admin-page settings-page">
      <section className="form-section">
        <div className="form-section-title">管理员账号</div>
        <div className="form-section-desc">当前登录的管理员账号名称。</div>
        <div className="small">账号：<code>{username}</code></div>
      </section>

      <section className="form-section" style={{ maxWidth: 560 }}>
        <div className="form-section-title">侧边栏动画</div>
        <div className="form-section-desc">默认跟随系统的减少动态效果设置；此偏好仅保存在当前浏览器，且只影响桌面侧边栏。</div>
        <label className="switch"><input type="checkbox" id="sidebar-motion-toggle" checked={motion} onChange={event => changeMotion(event.target.checked)}/>本浏览器强制显示侧边栏动画</label>
      </section>

      <section className="form-section" style={{ maxWidth: 560 }}>
        <div className="form-section-title">修改管理员密码</div>
        <div className="form-section-desc">定期更换管理员密码可以降低被泄露的风险。</div>
        <form method="post" action="/admin/change-password" className="inline-form" onSubmit={password.onSubmit}>
          <div className="form-field">
            <label htmlFor="settings-current-password">当前密码</label>
            <input id="settings-current-password" name="current" type="password" maxLength={max} autoComplete="current-password" required value={password.draft.current} onChange={event => password.change('current', event.target.value)}/>
          </div>
          <div className="form-field">
            <label htmlFor="settings-new-password">新密码（至少 {min} 位）</label>
            <input id="settings-new-password" name="new" type="password" minLength={min} maxLength={max} autoComplete="new-password" required value={password.draft.new} onChange={event => password.change('new', event.target.value)}/>
          </div>
          <div className="form-field">
            <label htmlFor="settings-confirm-password">确认新密码</label>
            <input id="settings-confirm-password" name="confirm" type="password" minLength={min} maxLength={max} autoComplete="new-password" required value={password.draft.confirm} onChange={event => password.change('confirm', event.target.value)}/>
          </div>
          <div className="row mt-md">
            <button className="btn btn-primary" type="submit" disabled={password.busy} aria-busy={password.busy ? true : undefined}>{password.busy ? '正在更新…' : '更新密码'}</button>
          </div>
          <span className="sr-only" role="status" aria-live="polite">{password.progress}</span>
        </form>
        <div className="small mt-sm faint">更新后将注销所有已登录会话（其它设备需重新登录），但本设备会保持登录。</div>
      </section>
    </div>
  </>;
}

export function SettingsPage({ publicHost }: { publicHost: string }) {
  const resource = useReadResource('/api/v1/admin/settings', { validate: validatePasswordPage });
  useInitialFragmentNavigation(fragmentTargets);

  useEffect(() => {
    if (resource.status === 'error' && resource.error.status === 401) {
      window.location.assign('/login');
    }
  }, [resource]);

  let content;
  if (resource.status === 'success') {
    content = <SettingsContent username={resource.data.username} min={resource.data.password_min_length} max={resource.data.password_max_length}/>;
  } else if (resource.status === 'error' && resource.error.status !== 401) {
    content = <div className="card"><div className="err" role="alert" aria-live="assertive" aria-atomic="true">加载失败：{resource.error.message}</div><div className="row mt-md"><button className="btn secondary" type="button" onClick={resource.retry}>重试</button></div></div>;
  } else {
    content = <div className="card" role="status" aria-live="polite">正在加载设置…</div>;
  }
  return <AdminShell active="settings" badge={publicHost} pageTitle="设置">{content}</AdminShell>;
}
