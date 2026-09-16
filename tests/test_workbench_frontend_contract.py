from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_session_hook_exposes_safe_typed_session_states():
    """A session reader must not cache credentials or accept malformed roles."""
    session = source("frontend/src/shared/session.ts")

    assert "'loading' | 'anonymous' | 'authenticated' | 'unavailable'" in session
    assert "requiredRole?: 'admin' | 'user'" in session
    assert "fetch('/api/v1/session'" in session
    assert "credentials: 'same-origin'" in session
    assert "cache: 'no-store'" in session
    assert "headers: { Accept: 'application/json' }" in session
    assert "AbortController" in session
    assert "readSession(controller.signal)" in session
    assert "error === 'login_required'" in session
    assert "response.status === 401" in session
    assert "value === 'admin' || value === 'user'" in session
    assert "useCallback" in session
    assert "localStorage" not in session


def test_login_modal_reuses_login_action_without_persisting_sensitive_data():
    """The workbench login stays in a labelled modal and leaves navigation to App."""
    modal = source("frontend/src/features/auth/LoginModal.tsx")

    assert "import { submitLogin, type LoginRealm }" in modal
    assert "useFormAction" in modal
    assert 'role="dialog"' in modal
    assert 'aria-modal="true"' in modal
    assert 'aria-labelledby="login-modal-title"' in modal
    assert "usernameInput.current?.focus()" in modal
    assert "event.key === 'Escape'" in modal
    assert "submitLogin({ username, password }, signal, realm)" in modal
    assert "resolveSameOriginReturnTo(returnTo)" in modal
    assert "onAuthenticated: (returnTo?: string) => Promise<void> | void" in modal
    assert "onAuthenticated(safeReturnTo)" in modal
    assert "onAuthenticated" in modal
    assert "dialogRef = useRef<HTMLDivElement>(null)" in modal
    assert "localStorage" not in modal
    assert "sessionStorage" not in modal


def test_login_modal_contains_focus_and_feedback_safety_contracts():
    """Tab focus must stay in the modal and API messages must be allowlisted."""
    modal = source("frontend/src/features/auth/LoginModal.tsx")

    assert "event.key !== 'Tab'" in modal
    assert "dialogRef.current?.querySelectorAll<HTMLElement>" in modal
    assert "document.activeElement === first" in modal
    assert "document.activeElement === last" in modal
    assert "function loginFeedback(message: string, realm: LoginRealm): string" in modal
    assert "'用户名或密码错误'" in modal
    assert "'请输入用户名和密码'" in modal
    assert "'登录尝试过于频繁，请 1 小时后再试'" in modal
    assert "'账号已停用，请联系管理员'" in modal
    assert "'账号已到期，请联系管理员续费'" in modal
    assert "setFeedback(loginFeedback(result.message, realm))" in modal
    assert "setFeedback(result.message)" not in modal


def test_login_browser_keeps_modal_selectors_ready_for_route_integration():
    """Task 5 can move browser scenarios to the modal without reintroducing legacy ids."""
    browser = source("tests/react_login_browser.cjs")

    assert "#login-modal-username" in browser
    assert "#login-modal-password" in browser
    assert "[role=\"dialog\"][aria-labelledby=\"login-modal-title\"]" in browser


def test_codex_shell_owns_the_single_responsive_workbench_tree():
    """Removing the shell frame or its drawer safety would leave pages stranded."""
    shell = source("frontend/src/shared/CodexShell.tsx")

    assert "export type CodexShellProps" in shell
    assert "sidebarTop?: ReactNode" in shell
    assert "sidebarBottom?: ReactNode" in shell
    assert "authStatus?: SessionStatus" in shell
    assert "const MOBILE_BREAKPOINT = 880" in shell
    assert 'className={`app${effectiveCollapsed' in shell
    assert 'className={`sidebar${effectiveCollapsed' in shell
    assert 'className="main"' in shell
    assert 'className="scrim"' in shell
    assert "inert={mobile && !open ? true : undefined}" in shell
    assert "inert={mobile && open ? true : undefined}" in shell
    assert "aria-current={item.key === active ? 'page' : undefined}" in shell
    assert "event.key === 'Escape'" in shell
    assert "event.key !== 'Tab'" in shell
    assert "last.focus()" in shell
    assert "first.focus()" in shell
    assert "{sidebarTop}" in shell
    assert "{sidebarBottom}" in shell


def test_admin_shell_adapts_to_codex_shell_and_keeps_admin_routes_available():
    """A second frame or a missing route link would break admin page compatibility."""
    adapter = source("frontend/src/shared/AdminShell.tsx")
    navigation = source("frontend/src/shared/navigation.ts")

    assert "import { CodexShell }" in adapter
    assert "return <CodexShell" in adapter
    assert 'className="app"' not in adapter
    assert 'className="sidebar"' not in adapter
    assert 'className="main"' not in adapter
    assert "navigationGroups" in navigation
    assert "placement: 'bottom'" in navigation
    for label in ("概览与用量", "运行维护", "网络配置"):
        assert label in navigation
    for href in (
        "/admin", "/admin/usage", "/admin/health", "/admin/incidents",
        "/admin/logs", "/admin/settings", "/admin/config", "/admin/rules",
        "/admin/landing-egresses", "/admin/chat",
    ):
        assert href in navigation


def test_chat_page_uses_the_unified_shell_and_defers_private_state_until_authenticated():
    """Anonymous chat must not read browser history or invoke the chat proxy."""
    page = source("frontend/src/features/chat/ChatPage.tsx")
    sidebar = source("frontend/src/features/chat/ChatSidebar.tsx")
    api = source("frontend/src/features/chat/chatApi.ts")
    styles = source("frontend/src/styles/sections/18-chat.css")
    browser = source("tests/react_chat_browser.cjs")

    assert "export type ChatPageProps" in page
    assert "authenticated?: boolean" in page
    assert "onUnauthenticated?: () => void" in page
    assert "import { CodexShell }" in page
    assert "import { ChatSidebar," in page
    assert "sidebarTop={<ChatSidebar" in page
    assert "<AdminShell" not in page
    assert "chat-sidebar" not in page
    assert "if (!authenticated) return;" in page
    assert "disabled={!authenticated || busy}" in page
    assert "onUnauthenticated?.()" in page
    assert "setSessions([]);" in page
    assert "setActiveId('');" in page
    assert "setLocalStateReady(false);" in page
    assert "fetch('/api/v1/session'" in page
    assert "setContextMax(model?.context_window ?? null);" in page
    assert "export type ChatSidebarProps" in sidebar
    assert "sessions: ChatSession[]" in sidebar
    assert "onRename: (session: ChatSession) => void" in sidebar
    assert "onDelete: (session: ChatSession) => void" in sidebar
    assert "reasoning_unsupported" in page
    assert "reasoning_effort !== 'auto'" in api
    assert "ChatApiError" in api
    assert ".sidebar-top" in styles
    assert ".chat-history" in styles
    assert ".sidebar.collapsed .sidebar-top" in styles
    assert "anonymousContext" in browser
    assert "anonymousChatRequests" in browser
    assert "anonymousStorageCalls" in browser


def test_router_composes_every_workbench_alias_through_the_session_gate():
    """Root and legacy login paths must share one shell instead of old page layouts."""
    router = source("frontend/src/main.tsx")

    assert "LoginModal" in router
    assert "import { useSession }" in router
    assert "'/auth'" in router
    assert "REACT_PREVIEW_PREFIX" in router
    assert "const WORKBENCH_ROUTES" in router
    assert "function WorkbenchRoute" in router
    assert "<ChatPage publicHost={publicHost} authenticated={authenticated}" in router
    assert "<LoginModal" in router
    assert "return <HomePage/>" not in router
    assert "return <LoginPage" not in router
    assert "resolveSameOriginReturnTo" in router
    assert "window.history.pushState({}, '', returnTo)" in router
    assert "window.history.replaceState({}, '', destination)" in router
    assert "session.status === 'authenticated' && session.role === 'admin'" in router
