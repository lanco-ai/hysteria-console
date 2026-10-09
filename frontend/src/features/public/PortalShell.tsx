import { createContext, useContext } from 'react';
import type { CodexShellProps } from '../../shared/CodexShell';
import type { SessionStatus } from '../../shared/session';
import { Icon } from '../../shared/icons';
import { LancoAgent } from '../agent/LancoAgent';
import { PlanReminderCenter } from '../plans/PlanReminderCenter';

export type PortalView = 'shop' | 'trending';
export const PortalSessionContext = createContext({
  authenticated: false,
  status: 'anonymous' as SessionStatus,
  onLogin: () => {},
});

export function ShopIcon() {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 3h2l2.2 11h11.6L21 6H6"/><circle cx="9" cy="20" r="1"/><circle cx="18" cy="20" r="1"/></svg>;
}

// Public site identity. The admin console keeps its own Hysteria Network Console brand.
function BrandMark() {
  return <svg className="portal-brand-mark" viewBox="0 0 40 40" aria-hidden="true" focusable="false">
    <rect width="40" height="40" rx="12" fill="currentColor"/>
    <path d="M14.5 11.5v17h12" fill="none" stroke="#fff" strokeWidth="4" strokeLinecap="round" strokeLinejoin="round"/>
    <path d="M29 8.5l1.15 3.1 3.1 1.15-3.1 1.15L29 17l-1.15-3.1-3.1-1.15 3.1-1.15z" fill="#8fd3c7"/>
  </svg>;
}

export function PortalShell({ active, children, agentEnabled = false }: CodexShellProps) {
  const session = useContext(PortalSessionContext);
  return <div className={`portal portal-${active}`}>
    <a className="skip-link" href="#main-content">跳到主内容</a>
    <header className="portal-header">
      <a className="portal-brand" href="/" aria-label="LancoAI 首页"><BrandMark/><strong>Lanco<span>AI</span></strong></a>
      <nav className="portal-nav" aria-label="主导航">
        <a href="/" aria-current={active === 'shop' ? 'page' : undefined}><ShopIcon/><span>购物</span></a>
        <a href="/?view=trending" aria-current={active === 'trending' ? 'page' : undefined}><Icon name="trending"/><span>开源发现</span></a>
      </nav>
      {session.authenticated
        ? <a className="portal-account" href="/admin/plans"><Icon name="dashboard"/><span>管理后台</span></a>
        : <button className="portal-account" type="button" onClick={session.onLogin}><Icon name="logout"/><span>登录</span></button>}
    </header>
    <main className={`portal-main portal-main-${active}`} id="main-content" tabIndex={-1}>
      {session.status === 'unavailable' ? <p className="portal-session-notice" role="status">暂时无法确认登录状态，请稍后重试。</p> : null}
      {children}
    </main>
    {session.authenticated ? <PlanReminderCenter/> : null}
    {agentEnabled && session.authenticated ? <LancoAgent/> : null}
  </div>;
}
