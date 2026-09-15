import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { useLogout } from '../features/auth/useLogout';
import { Icon } from './icons';
import { navigationGroups } from './navigation';

type AdminShellProps = {
  active: string;
  badge: string;
  pageTitle: string;
  children: ReactNode;
  subtitle?: ReactNode;
  topbarExtra?: ReactNode;
};

const MOBILE_BREAKPOINT = 880;

function storedPreference(name: string, expected: string): boolean {
  try {
    return localStorage.getItem(name) === expected;
  } catch {
    return false;
  }
}

export function applyInitialShellPreferences(): void {
  if (storedPreference('hy2.sidebar', 'collapsed')) {
    document.documentElement.classList.add('sidebar-pre-collapsed');
  }
  if (storedPreference('hy2.sidebar-motion', 'enabled')) {
    document.documentElement.classList.add('sidebar-motion-enabled');
  }
}

export function AdminShell({ active, badge, pageTitle, children, subtitle, topbarExtra }: AdminShellProps) {
  const [mobile, setMobile] = useState(() => window.innerWidth <= MOBILE_BREAKPOINT);
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => storedPreference('hy2.sidebar', 'collapsed'));
  const [animationReady, setAnimationReady] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const restoreFocusRef = useRef(false);
  const logoutErrorRef = useRef<HTMLDivElement>(null);
  const handledLogoutFailureRef = useRef(0);
  const logout = useLogout('admin');
  const effectiveCollapsed = collapsed && !mobile;

  useLayoutEffect(() => {
    document.body.classList.add('has-shell');
    return () => document.body.classList.remove('has-shell', 'sidebar-open');
  }, []);

  useLayoutEffect(() => {
    if (!open && restoreFocusRef.current) {
      restoreFocusRef.current = false;
      toggleRef.current?.focus();
    }
  }, [open]);

  useLayoutEffect(() => {
    if (
      logout.failureCount === 0
      || handledLogoutFailureRef.current === logout.failureCount
    ) return;
    if (mobile && open) {
      restoreFocusRef.current = false;
      setOpen(false);
      return;
    }
    handledLogoutFailureRef.current = logout.failureCount;
    logoutErrorRef.current?.focus();
  }, [logout.failureCount, mobile, open]);

  useEffect(() => {
    let second = 0;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        document.documentElement.classList.remove('sidebar-pre-collapsed');
        setAnimationReady(true);
      });
    });
    return () => {
      cancelAnimationFrame(first);
      cancelAnimationFrame(second);
    };
  }, []);

  useEffect(() => {
    document.body.classList.toggle('sidebar-open', mobile && open);
  }, [mobile, open]);

  useEffect(() => {
    const onResize = () => {
      const nextMobile = window.innerWidth <= MOBILE_BREAKPOINT;
      setMobile(nextMobile);
      if (!nextMobile) setOpen(false);
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!mobile || !open) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        restoreFocusRef.current = true;
        setOpen(false);
        return;
      }
      if (event.key !== 'Tab' || !sidebarRef.current) return;
      const focusable = Array.from(sidebarRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      ));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      const activeElement = document.activeElement;
      if (event.shiftKey && (activeElement === first || !sidebarRef.current.contains(activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (activeElement === last || !sidebarRef.current.contains(activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [mobile, open]);

  const openSidebar = () => {
    if (!mobile) return;
    setOpen(true);
    requestAnimationFrame(() => sidebarRef.current?.querySelector<HTMLElement>('#sidebar-close')?.focus());
  };
  const closeSidebar = (restoreFocus: boolean) => {
    restoreFocusRef.current = restoreFocus;
    setOpen(false);
  };
  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    document.documentElement.classList.toggle('sidebar-pre-collapsed', next);
    try {
      localStorage.setItem('hy2.sidebar', next ? 'collapsed' : 'expanded');
    } catch {
      // Storage can be disabled; the current view still updates.
    }
  };

  return <>
    <a className="skip-link" href="#main-content" inert={mobile && open ? true : undefined}>跳到主内容</a>
    <div className={`app${effectiveCollapsed ? ' sidebar-collapsed' : ''}${animationReady ? ' anim-ready' : ''}`}>
      <aside
        className={`sidebar${effectiveCollapsed ? ' collapsed' : ''}${mobile && open ? ' open' : ''}`}
        id="sidebar"
        ref={sidebarRef}
        inert={mobile && !open ? true : undefined}
      >
        <div className="sidebar-brand">
          <span className="sidebar-logo">H</span>
          <div className="sidebar-brand-text"><strong>Hysteria</strong><small>Network Console</small></div>
          <button className="sidebar-close" id="sidebar-close" type="button" aria-label="关闭导航" aria-controls="sidebar" onClick={() => closeSidebar(true)}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <button className="sidebar-collapse" id="sidebar-collapse" type="button" aria-label={effectiveCollapsed ? '展开侧边栏' : '折叠侧边栏'} aria-pressed={effectiveCollapsed} title={effectiveCollapsed ? '展开侧边栏' : '折叠侧边栏'} onClick={toggleCollapsed}>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>
        </button>
        <nav className="sidebar-nav" aria-label="管理导航">
          {navigationGroups.map(group => <div key={group.label}>
            <div className="sidebar-section">{group.label}</div>
            {group.items.map(item => <a
              key={item.key}
              href={item.href}
              className={`sidebar-link ${item.key === active ? 'active' : ''}`}
              aria-current={item.key === active ? 'page' : undefined}
              title={item.label}
              aria-label={item.label}
              onClick={() => closeSidebar(false)}
            ><Icon name={item.icon}/><span>{item.label}</span></a>)}
          </div>)}
        </nav>
        <div className="sidebar-footer">
          <form method="post" action="/logout" onSubmit={logout.onSubmit}>
            <button type="submit" className="sidebar-logout" title="退出登录" aria-label="退出登录" disabled={logout.busy} aria-busy={logout.busy ? true : undefined}><Icon name="logout"/><span>{logout.busy ? '正在退出…' : '退出登录'}</span></button>
          </form>
        </div>
      </aside>
      <div className="scrim" id="scrim" aria-hidden="true" onClick={() => closeSidebar(true)}/>
      <div className="main" inert={mobile && open ? true : undefined}>
        <header className="topbar">
          <div className="topbar-inner">
            <div className="topbar-left">
              <button ref={toggleRef} className="sidebar-toggle" id="sidebar-toggle" type="button" aria-label="切换侧边栏" aria-expanded={mobile && open} onClick={() => open ? closeSidebar(true) : openSidebar()}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
              </button>
              <h1 className="page-title">{pageTitle}{subtitle === undefined ? null : <small>{subtitle}</small>}</h1>
            </div>
            <div className="topbar-right">{topbarExtra}{badge ? <span className="badge">{badge}</span> : null}</div>
          </div>
        </header>
        <main className="content" id="main-content" tabIndex={-1}>
          {logout.feedback ? <div ref={logoutErrorRef} className="err" role="alert" aria-live="assertive" aria-atomic="true" tabIndex={-1}>{logout.feedback}</div> : null}
          {children}
        </main>
      </div>
    </div>
  </>;
}
