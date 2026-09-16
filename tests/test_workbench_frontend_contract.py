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
