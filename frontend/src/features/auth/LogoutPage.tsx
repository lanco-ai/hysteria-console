import { useLogout } from './useLogout';
import type { LogoutRealm } from './logoutRequest';
import { useInitialFragmentNavigation } from '../../shared/useInitialFragmentNavigation';

const fragmentTargets = new Set(['main-content']);

const content = {
  admin: {
    heading: '退出管理后台？',
    action: '/logout',
    cancelHref: '/admin',
  },
  user: {
    heading: '退出用户面板？',
    action: '/user/logout',
    cancelHref: '/user/panel',
  },
} as const;

export function LogoutPage({ realm, publicHost }: { realm: LogoutRealm; publicHost: string }) {
  const page = content[realm];
  const logout = useLogout(realm);
  useInitialFragmentNavigation(fragmentTargets);
  return <>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <main id="main-content" tabIndex={-1}>
      <div className="auth-page">
        <div className="auth-wrap">
          <div className="auth-card">
            <div className="auth-brand">
              <span className="auth-logo">H</span>
              <div className="auth-brand-text">
                <strong>{publicHost}</strong>
                <small>安全退出</small>
              </div>
            </div>
            <h1 className="auth-title">{page.heading}</h1>
            <p className="auth-subtitle">确认后会结束当前设备的登录会话；其他设备不受影响。</p>
            {logout.feedback ? <div className="err" role="alert" aria-live="assertive" aria-atomic="true">{logout.feedback}</div> : null}
            <form method="post" action={page.action} onSubmit={logout.onSubmit}>
              <button className="btn danger-btn mt-md btn-full" type="submit" disabled={logout.busy} aria-busy={logout.busy ? true : undefined}>{logout.busy ? '正在退出…' : '确认退出'}</button>
            </form>
            <a className="auth-back" href={page.cancelHref}>返回</a>
          </div>
        </div>
      </div>
    </main>
  </>;
}
