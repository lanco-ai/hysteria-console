import { createContext, useContext } from 'react';
import type { CodexShellProps } from '../../shared/CodexShell';
import type { SessionStatus } from '../../shared/session';
import { Icon } from '../../shared/icons';
import { LancoAgent } from '../agent/LancoAgent';
import { PlanReminderCenter } from '../plans/PlanReminderCenter';

export type PortalView = 'shop' | 'chat' | 'video';
export const PortalSessionContext = createContext({
  authenticated: false,
  status: 'anonymous' as SessionStatus,
  onLogin: () => {},
});

export function ShopIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 3h2l2.2 11h11.6L21 6H6"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></svg>;
}

export function PortalShell({ active, pageTitle, topbarExtra, children, agentEnabled = false }: CodexShellProps) {
  const session = useContext(PortalSessionContext);
  return <div className={`portal portal-${active}`}>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <header className="portal-header">
      <a className="portal-brand" href="/" aria-label="Hysteria 首页"><span aria-hidden="true">H</span><strong>Hysteria</strong></a>
      <nav className="portal-nav" aria-label="主导航">
        <a href="/" aria-current={active === 'shop' ? 'page' : undefined}><ShopIcon/><span>购物</span></a>
        <a href="/?view=chat" aria-current={active === 'chat' ? 'page' : undefined}><Icon name="chat"/><span>AI 对话</span></a>
        <a href="/?view=video" aria-current={active === 'video' ? 'page' : undefined}><Icon name="video"/><span>AI 视频</span></a>
      </nav>
      {session.authenticated
        ? <a className="portal-account" href="/admin"><Icon name="dashboard"/><span>管理后台</span></a>
        : <button className="portal-account" type="button" onClick={session.onLogin}><Icon name="logout"/><span>登录</span></button>}
    </header>
    <main className={`portal-main portal-main-${active}`} id="main-content" tabIndex={-1}>
      {active !== 'shop' ? <header className="portal-feature-heading"><h1>{pageTitle}</h1>{topbarExtra}</header> : null}
      {session.status === 'unavailable' ? <p className="portal-session-notice" role="status">暂时无法确认登录状态，请稍后重试。</p> : null}
      {children}
    </main>
    {session.authenticated ? <PlanReminderCenter/> : null}
    {agentEnabled && session.authenticated ? <LancoAgent/> : null}
  </div>;
}
