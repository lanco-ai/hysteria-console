# Shared login decision implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Extract the existing login decision and session creation from HTML response orchestration, retaining the legacy route as the first verified consumer.

**Architecture:** A dependency-injected `LoginService` returns a typed internal result. `auth_routes` chooses existing HTML feedback, cookie helpers and redirects from that result. A later FastAPI adapter will consume the same service; this task does not add an API or change frontend behavior.

**Tech Stack:** Existing Python dataclasses, pytest, legacy HTTP fixtures and deployment inventories. No dependencies added.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Administrator access and user-panel access stay separate. A user session cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation, revision conflicts, strict-state failure behavior, request size limits, request concurrency bounds and cache/security headers.
- Keep existing URLs, subscription formats, dedicated-user link exchanges, cookie separation, redirects, downloads, QR responses and status semantics.
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444, runtime secrets, proxy configuration and all persistent user data.
- Source and isolated temporary fixtures only: no production file reads, service operations, push, deployment or routing changes.
- The visible login remains administrator-only. Existing compatibility user credentials are not permission to add another login form.

## File responsibilities

- Create `hysteria/login_service.py`: credential decision, bounded verification reservation and session creation only; no sockets, HTTP responses, cookies or rendering.
- Modify `hysteria/auth_routes.py`: replace `_login` decision branches with an injected callable plus existing response adaptation; keep logout and password changes unchanged.
- Modify `hysteria/subscription_service.py`: construct the new service from authoritative helpers at request time and inject its callable into `auth_routes.Context`; compatibility exports remain authoritative.
- Create `tests/test_login_service.py`: typed result, precedence, lifecycle, reservation cleanup and failure behavior with fictional fixtures.
- Modify `tests/test_auth_routes.py`: focused adapter seam tests for exactly one service call and typed-result HTTP mapping, alongside the real route characterization.
- Modify `tests/test_reliability_regressions.py`: focused actual `/login` HTTP characterization cases; keep existing concurrency and realm/IP partition tests.
- Modify `deploy.sh`, `scripts/hy2-deploy-recovery.py`: register the new flat module in the five established inventories; no deployment execution.
- Modify `scripts/check-quality.sh`: adopt new module/test for import and format checks.

### Task 1: Shared decision service with legacy HTTP parity

**Prerequisite:** Shared-module-deploy-contract repair is complete; its import-closure test must include this new extraction.

**Interfaces:**

```python
from dataclasses import dataclass, field
from typing import Literal, Mapping

LoginOutcome = Literal['success', 'invalid', 'missing', 'throttled', 'disabled', 'expired']
LoginRealm = Literal['admin', 'user']

@dataclass(frozen=True, slots=True)
class LoginResult:
    outcome: LoginOutcome
    realm: LoginRealm = 'admin'
    username: str = ''
    session_id: str = field(default='', repr=False)
    redirect_to: str = ''
    retry_after: int | None = None

# LoginService.authenticate signature:
def authenticate(
    self, *, form: Mapping[str, list[str]], meta: Mapping[str, object], client_ip: str
) -> LoginResult: ...
```

`LoginService` is a frozen dependency dataclass following existing service style.
Its explicit fields are `PASSWORD_MAX_LENGTH`, `USERS_FILE`, `_LOGIN_WINDOW`,
`_begin_login_attempt`, `_finish_login_attempt`, `_user_login_failures`,
`_credential_generation`, `create_session`, `create_user_session`,
`is_valid_username`, `load_json`, `local_now`, `verify_secret`.
Use appropriate existing Callable/Path/dict annotations, not a whole server-module
object. The composition root exposes `_login_service()` constructing it from
current exports and paths, then passes `_login_service().authenticate` as
`authenticate_login` to `auth_routes.Context`. Remove context fields used only by
the moved login decision; preserve fields used by password changes or logout.

- [x] Characterize the actual HTTP route before changing it. Extend the existing
temporary-state `_running_server`/`_request` fixtures in reliability regressions:
trimmed administrator username; administrator precedence when both credential
sets are supplied; wrong, missing and overlong credentials; successful user
compatibility login; disabled/expired user; forced password change; exact
302 destinations, only the matching cookie name, HttpOnly/SameSite/Secure
behavior through existing helpers, failure feedback and no password reflection.
Use fictional stored hashes and a deterministic verifier; keep real session
creation against temporary session files. Assert saved credential-generation
binding via existing session readers. Run these characterization cases green
on the old implementation so they constrain extraction.

```python
response = _request(server, 'POST', '/login', body=urlencode({
    'admin_username': ' admin ', 'admin_password': 'correct-password',
    'user_username': 'alice', 'user_password': 'correct-password',
}))
assert response.status == 302
assert response.headers['location'] == '/admin?msg=login+success'
assert response.headers['set-cookie'].startswith('sid=')
assert 'HttpOnly' in response.headers['set-cookie']
assert 'SameSite=Lax' in response.headers['set-cookie']
assert 'correct-password' not in response.body.decode()
```

- [x] Write new direct service tests red against the missing module/service.
Use temporary state and a real `LoginThrottle` with private dictionaries, lock
and fixed clock; inject a verifier and session creators to record exact calls.
Check all outcomes, first form value/trim behavior, administrator precedence,
max password length avoids verification, independent buckets and generation
arguments. Results must not contain passwords or hashes, and repr must not show
the internal session id. Throttling returns `_LOGIN_WINDOW` without creating a
session or releasing another request's reservation.

```python
result = service.authenticate(form={'admin_username': ['admin'],
    'admin_password': ['wrong']}, meta=meta, client_ip='198.51.100.8')
assert result.outcome == 'invalid'
assert result.session_id == ''
assert throttle.inflight == {}
assert len(throttle.failures['198.51.100.8']) == 1
```

- [x] Add exception regressions: user-state read or verifier raises after a
reservation was acquired; the original exception propagates, inflight is empty,
prior failed-attempt history is unchanged, and a subsequent attempt can reserve.
Cover administrator and user verifier failures. Session creation raising must
not leave a reservation or return success. Disabled/expired correct passwords
release with `None`, preserving prior failures. Preserve successful verification
clearing failures before session creation, as the old implementation does.

```python
with pytest.raises(OSError, match='fixture unavailable'):
    service.authenticate(form=form, meta=meta, client_ip=client_ip)
assert throttle.inflight == {}
assert failures[client_ip] == prior_failures
assert throttle._begin_login_attempt(client_ip, failures)
throttle._finish_login_attempt(client_ip, None, failures)
```

- [x] Implement the decision with the same branch order and checks as `_login`.
After a successful reservation, initialize its outcome to `None`, evaluate
verification/lifecycle within `try`, and finish exactly once in `finally`.
Use `False` for invalid credentials, `True` for accepted verification, `None`
for exceptions and correct-but-ineligible user credentials. Do not swallow
exceptions, clear failure history on exceptions, or hold the throttle lock
during verification. Only create sessions after the reservation is finished.
Return `/admin?msg=login+success`, `/user/panel`, or `/user/change-password`
exactly; user session creation retains the existing default credential kind.
Missing credentials acquire no reservation. Never add transport or state copies.

- [x] Adapt `_login` with the current trusted client-IP and configured host
helpers, then call the injected service once. Success calls the current matching
cookie helper and `handler.redirect` with its unchanged default 302. Failure
renders the existing administrator-only page with the submitted realm/username;
throttled is 429 with `Retry-After`, all other login feedback is 200. Preserve
these exact messages in an adapter-local mapping:

```python
messages = {
    'invalid': '用户名或密码错误',
    'missing': '请输入用户名和密码',
    'throttled': '登录尝试过于频繁，请 1 小时后再试',
    'disabled': '账号已停用，请联系管理员',
    'expired': '账号已到期，请联系管理员续费',
}
```

Keep `active_tab='user'` on compatibility-user feedback: the existing renderer
intentionally substitutes `请使用管理员账号登录控制台。` and clears the displayed
username. HTTP tests must assert this neutral rendered response, not expect the
internal disabled/expired message to become visible on the administrator form.

The internal result is not a JSON response model and must not be serialized.
Retain outer Handler request parsing, meta read ordering, same-origin exception
for `/login`, error translation and strict-state handling without modification.

- [x] Register `login_service.py` beside `login_throttle.py` in durable artifacts,
snapshot loop, exact render call, chmod 700 list and recovery exact allowlist.
The destination is `$HY_DIR/login_service.py` / `/root/hysteria/login_service.py`.
Add the new module and test to adopted quality files. Run the existing deploy
dependency closure and allowlist tests; do not broaden recovery permissions.

- [x] Run new direct tests, complete reliability regressions, auth routes/views,
admin login design, login throttle/bounded failures, session/identity tests,
deployment durable/security tests and API reads. Run lint-only, `bash -n deploy.sh`
and diff check. Because this changes authentication shared by the legacy
application, run the full backend quality command once after focused checks are
green, before committing. No full frontend rerun is needed for unchanged UI;
existing login-design/HTTP tests verify the adapter.

```bash
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh
bash -n deploy.sh
git diff --check
```

- [x] Self-review and commit only owned source/tests locally; report exact
characterization, red/green and full-check commands/results, changed fields,
deployment closure and any known warnings. Do not push or deploy.

## Scope and next consumer

This task establishes the authoritative login decision and closes the existing
exception reservation leak. It does not claim a FastAPI write transport or React
login form exists. The next HTTP task must separately preserve raw header/body
bounds, cookie generation, trusted client attribution, admission cancellation
and sanitized failures before the React form can use it. Other parity-register
areas remain on their established legacy paths until their own migrations.

## Acceptance record

Implemented locally as `4734582`; independent task review approved with no
functional findings. Original HTTP characterization passed before extraction;
direct service and adapter-seam tests followed RED/GREEN. Full backend quality
passed with 1,572 tests and 71 existing deprecation warnings. Parent separately
verified the 248-test covering set (five existing warnings), lint-only, shell
syntax and diff hygiene on the committed source. A minor historical-command
selector omission in the scratch report is explicitly documented as unrecoverable;
the historical output is retained, with no fabricated selector or replacement run.
No FastAPI write route, React login page, production deployment or push occurred.
