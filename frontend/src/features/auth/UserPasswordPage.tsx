import { useEffect } from 'react';
import { useReadResource } from '../../shared/readResource';
import { useInitialFragmentNavigation } from '../../shared/useInitialFragmentNavigation';
import { validatePasswordPage, type PasswordPageData } from './passwordPageTypes';
import { usePasswordChange } from './usePasswordChange';
import { LoadingState } from '../../shared/LoadingState';

const fragmentTargets = new Set(['main-content']);
const initialMessages = new Map<string, string>([
  ['current password wrong', '当前密码不正确'],
  ['new password short', '新密码至少需要 8 位'],
  ['new password mismatch', '两次输入的新密码不一致'],
  ['new password same', '新密码不能与当前密码相同'],
]);

function initialFeedback(max: number) {
  const value = new URLSearchParams(window.location.search).get('msg') || '';
  if (!value) return '';
  return value === 'new password long' ? `新密码不能超过 ${max} 位` : (initialMessages.get(value) ?? value);
}

function ShieldIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>;
}

function PasswordIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>;
}

function CheckIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>;
}

function UserPasswordContent({ data, publicHost }: { data: PasswordPageData; publicHost: string }) {
  const password = usePasswordChange('user', data.password_max_length, initialFeedback(data.password_max_length));
  useInitialFragmentNavigation(fragmentTargets);
  return <div className="auth-scene">
    <div className="auth-body">
      <div className="auth-brand">
        <div className="auth-brand-content">
          <div className="auth-brand-eyebrow">Security · 安全</div>
          <h1 className="auth-brand-title">保护你的<br/>账户安全。</h1>
          <div className="auth-brand-features">
            <div className="auth-brand-feature"><PasswordIcon/>使用强密码</div>
            <div className="auth-brand-feature"><ShieldIcon/>保护订阅访问</div>
            <div className="auth-brand-feature"><CheckIcon/>实时生效</div>
          </div>
        </div>
      </div>

      <div className="auth-form-panel">
        <div className="auth-card">
          <div className="auth-card-brand">
            <div className="auth-card-logo">H</div>
            <div className="auth-card-brand-text"><strong>Hysteria</strong><small>用户面板</small></div>
          </div>
          <h2 className="auth-card-title">修改面板密码</h2>
          <p className="auth-card-subtitle">{data.username} · {publicHost}</p>
          {password.feedback ? <div className="err" role="alert" aria-live="assertive" aria-atomic="true">{password.feedback}</div> : null}
          <form method="post" action="/user/change-password" className="auth-form" onSubmit={password.onSubmit}>
            <div className="field">
              <label className="label" htmlFor="user-current-password">当前密码</label>
              <input className="input" id="user-current-password" name="current" type="password" required maxLength={data.password_max_length} autoFocus ref={element => element?.setAttribute('autofocus', '')} autoComplete="current-password" placeholder="输入当前密码" value={password.draft.current} onChange={event => password.change('current', event.target.value)}/>
            </div>
            <div className="field">
              <label className="label" htmlFor="user-new-password">新密码</label>
              <input className="input" id="user-new-password" name="new" type="password" required minLength={data.password_min_length} maxLength={data.password_max_length} autoComplete="new-password" placeholder="输入新密码" value={password.draft.new} onChange={event => password.change('new', event.target.value)}/>
            </div>
            <div className="field">
              <label className="label" htmlFor="user-confirm-password">再次输入新密码</label>
              <input className="input" id="user-confirm-password" name="confirm" type="password" required minLength={data.password_min_length} maxLength={data.password_max_length} autoComplete="new-password" placeholder="再次输入新密码" value={password.draft.confirm} onChange={event => password.change('confirm', event.target.value)}/>
            </div>
            <button className="btn btn-primary btn-full" type="submit" disabled={password.busy} aria-busy={password.busy ? true : undefined}>{password.busy ? '正在保存…' : '保存新密码'}</button>
            <span className="sr-only" role="status" aria-live="polite">{password.progress}</span>
            <div className="auth-note">使用至少 {data.password_min_length} 位、且未在其他网站使用的密码。保存后其他设备上的用户面板会话将自动退出。</div>
          </form>
          <a className="auth-back" href="/user/panel">返回用户面板</a>
        </div>
      </div>
    </div>
  </div>;
}

function UserFrame({ children }: { children: React.ReactNode }) {
  return <>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <main id="main-content" tabIndex={-1}>
      <header className="auth-header">
        <div className="auth-header-inner">
          <a href="/" className="auth-header-logo"><ShieldIcon/>Hysteria</a>
          <a href="/user/panel" className="auth-header-back">← 返回面板</a>
        </div>
      </header>
      {children}
    </main>
  </>;
}

export function UserPasswordPage({ publicHost }: { publicHost: string }) {
  const resource = useReadResource('/api/v1/user/password', { validate: validatePasswordPage });
  useInitialFragmentNavigation(fragmentTargets);

  useEffect(() => {
    if (resource.status !== 'error') return;
    if (resource.error.status === 401 || (resource.error.status === 403 && resource.error.code === 'forbidden')) {
      window.location.assign('/login');
    } else if (resource.error.status === 403 && (resource.error.code === 'disabled' || resource.error.code === 'expired')) {
      window.location.assign('/user/panel');
    }
  }, [resource]);

  if (resource.status === 'success') return <UserFrame><UserPasswordContent data={resource.data} publicHost={publicHost}/></UserFrame>;
  if (resource.status === 'error' && !(
    resource.error.status === 401
    || (resource.error.status === 403 && resource.error.code === 'forbidden')
    || (resource.error.status === 403 && (resource.error.code === 'disabled' || resource.error.code === 'expired'))
  )) {
    return <UserFrame><div className="auth-scene"><div className="auth-body"><div className="auth-form-panel"><div className="auth-card"><div className="err" role="alert" aria-live="assertive" aria-atomic="true">加载失败：{resource.error.message}</div><button className="btn secondary mt-md" type="button" onClick={resource.retry}>重试</button></div></div></div></div></UserFrame>;
  }
  return <UserFrame><div className="auth-scene"><div className="auth-body"><div className="auth-form-panel"><div className="auth-card"><LoadingState label="正在加载密码设置…" variant="auth"/></div></div></div></div></UserFrame>;
}
