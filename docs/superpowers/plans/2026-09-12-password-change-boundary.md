# Password-change boundary implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Share existing password-change decisions between legacy routes and
two explicit FastAPI endpoints, preserving actual credential/session behavior.

**Architecture:** A transport-free service owns current orchestration and calls
the existing identity/session/state helpers. Legacy adapters retain redirects
and cookies; JSON adapters use explicit result allowlists and the existing
bounded form-write dispatcher. No React or production cutover in this task.

**Tech Stack:** Existing Python, FastAPI/Pydantic/AnyIO and pytest.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- Existing strict JSON storage, file locks and recovery protocols remain
  authoritative. Do not introduce an independently maintained second copy of
  users, session state or usage balances.
- Blocking file locks, subprocesses and synchronous probes must not execute
  directly on an async event loop. Keep their existing bounds and cancellation
  semantics. Do not increase worker count without a shared-state review.
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444,
  runtime secrets, proxy configuration and all persistent user data.
- No runtime writes, deploy execution, push, frontend, preview permission,
  dependency, password policy, token/proxy configuration or database changes.

### Task 1: Shared decisions, legacy consumers and exact JSON adapters

**Files:** Create `hysteria/password_change_service.py`,
`tests/test_password_change_service.py`, `tests/test_web_api_password_changes.py`.
Modify `hysteria/auth_routes.py`, `hysteria/subscription_service.py`,
`hysteria/web_api/services.py`, `hysteria/web_api/app.py`,
`hysteria/web_api/models.py`, `tests/test_auth_routes.py`, `deploy.sh`,
`scripts/check-quality.sh`. Deployment file changes only register the new module
in existing package/snapshot/render/compile lists. No deploy execution.

**Read context:** The inspected-password-change section of
`docs/superpowers/specs/2026-09-12-http-cutover-notes.md` records current ordering
and incomplete legacy tests. Also inspect actual `auth_routes`,
`identity_service._change_admin_password` and session replacement helpers.

**Service interface:** Frozen/slotted dependency-injected `PasswordChangeService`
constructed by `subscription_service._password_change_service()`, using current
helpers dynamically as the login-service factory does. No runtime import from
the new module. Frozen/slotted internal result has a hidden session identifier:

```python
@dataclass(frozen=True, slots=True)
class PasswordChangeResult:
    outcome: Literal['success', 'invalid', 'login_required', 'forbidden', 'disabled', 'expired']
    code: str = ''
    session_id: str = field(default='', repr=False)

# Service methods:
# change_admin(*, form: Mapping[str, list[str]]) -> PasswordChangeResult
# change_user(*, username: str, session_kind: str,
#             form: Mapping[str, list[str]]) -> PasswordChangeResult
```

Administrator authentication stays in adapters before `change_admin`; it must
never be inferred from the presence of a user cookie. Service delegates to the
existing `_change_admin_password(current, new, confirm)` (including validation
order and allowing same-password reuse), then replaces all administrator
sessions with the existing generation-bound replacement helper. Do not copy
identity validation or implement a new hash algorithm.

User service returns login_required for missing identity or non-password session
kind before reading users. Move the existing initial lifecycle check, new-field
length/confirmation checks, usage-lock reload/recheck, current-password validation,
same-password rejection, hash write and must-change removal verbatim in ordering.
Use shared lifecycle helper, including its current must-change priority. Keep
session replacement after the usage lock is released. Replace only this user's
sessions; leave other users, administrator state and subscription tokens alone.

Preserve existing validation codes exactly: admin `password_wrong`,
`password_short`, `password_long`, `password_mismatch`; user `current password wrong`,
`new password short`, `new password long`, `new password mismatch`, `new password same`.
No secrets, hashes or supplied form values in result repr/error messages.

Legacy Context replaces moved dependencies with the two injected service methods;
retain dependencies still used by login/logout. Legacy handlers keep current
authentication calls, status302, exact validation query encoding, forbidden-only
user clearing cookie, disabled/expired redirect and matching success cookies:
admin `/admin/settings?msg=password+changed`, user `/user/panel`. Keep existing
POST wrapper metadata-before-handler behavior. No broad handler bridge.

**JSON interface:** Fixed POST `/api/v1/admin/change-password` and
`/api/v1/user/change-password`, fields `current`, `new`, `confirm` use existing
first-value/empty semantics. Both call one narrow `submit_password_change` adapter
with fixed realm literals; reject an invalid internal realm before side effects.
Under `_run_operation`, load metadata first, authenticate only the targeted realm,
then call the shared service. Use corresponding legacy post_path for strict-state
classification. Preserve snapshots and existing lazy route method binding.

| Outcome | Status and exact JSON | Cookie |
| --- | --- | --- |
| admin success | 200 `{"ok":true,"redirect_to":"/admin/settings?msg=password+changed"}` | new sid |
| user success | 200 `{"ok":true,"redirect_to":"/user/panel"}` | new usid |
| invalid | 200 `{"ok":false,"code":<exact validation code>}` | none |
| login_required | 401 `{"error":"login_required"}` | none |
| forbidden user | 403 `{"error":"forbidden"}` | clear usid |
| disabled/expired user | 403 `{"error":"disabled"}` or `{"error":"expired"}` | none |

Use explicit Pydantic Literal models for success destinations, validation codes
and access-error codes. Internal reply cookie must be repr-hidden. Response
builder validates outcome/realm/code combinations, never reflects raw input.
GET/HEAD405 with HEAD bodyless; trailing slashes404. Shared origin/framing/timeout/
busy/state/unexpected-error behavior and security headers remain unchanged.

Credential persistence and session replacement remain two transactions. Any
failure propagates; no automatic retry, stale rollback, success cookie or ordinary
validation response after a storage failure. If replacement fails after password
write, the new hash remains authoritative and old-generation sessions must no
longer authorize. Test this explicitly without triggering real fail-closed services.

- [x] Add characterization tests invoking actual legacy handlers with temporary
  identity/session/state helpers. Cover success, validation ordering and realm
  isolation; record baseline GREEN. Add RED for absent service/API before changes:

```python
response = client.post('/api/v1/admin/change-password',
    headers={'Cookie': admin_cookie, 'Origin': 'http://testserver'},
    data={'current': 'old-password', 'new': 'new-password', 'confirm': 'new-password'})
assert response.status_code == 200  # missing route currently 404
assert response.json() == {'ok': True, 'redirect_to': '/admin/settings?msg=password+changed'}
```

- [x] Implement shared service and legacy consumer extraction. Tests must prove
  actual stored hash, removed must-change flag, fresh session kind/generation,
  all old admin sessions invalidated, only target user's sessions invalidated,
  other users/realm still usable and tokens/quotas/proxy fields unchanged. Test
  wrong current/short/long/mismatch/empty fields and user same-password rejection;
  administrator same-password acceptance must remain. Use actual helper paths,
  not manually clearing/recreating sessions as a simulation of the handler.
- [x] Test initial and locked lifecycle states, including a deterministic state
  change between the first check and lock acquisition; no sleeps. Test token-kind
  user session rejection, missing/stale/opposite-realm-only cookies and both
  cookies together. Record no credential/session write on rejected validation.
- [x] Implement models, narrow adapter and two fixed routes via shared form
  dispatcher. No new semaphore, catch-all route or duplicate form parser. Test
  exact response keys/codes/cookies, secure cookie attributes, no identifiers or
  hashes in JSON/repr, absent-origin legacy-compatible behavior and rejected
  cross-origin before service dispatch. Parameterize both endpoints for malformed
  framing, duplicate length, oversized/truncated input, timeout and405/404/HEAD.
- [x] Test metadata/read/hash-write/session-replacement failures, sanitized500,
  strict-state503 without success cookie and snapshot cleanup. Use safe recording
  fail-closed doubles. Test real old-generation rejection after post-write failure.
  Event-controlled pending mutation must hold capacity until worker completion
  after cancellation; existing login/logout/read admission tests remain covering.
- [x] Register module packaging and lint paths. Retain existing module-closure
  regression. Run focused tests while iterating, then covering commands once:

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_password_change_service.py tests/test_web_api_password_changes.py tests/test_auth_routes.py tests/test_identity_service.py tests/test_session_store.py tests/test_web_api_login.py tests/test_web_api_logout.py tests/test_web_api_reads.py tests/test_new_features.py tests/test_subscription_security_regressions.py tests/test_reliability_regressions.py
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_deploy_durable_recovery.py::test_rendered_python_sources_include_their_local_import_closure
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
bash -n deploy.sh
git diff --check
```

- [x] Self-review and locally commit only owned files; report exact baseline/
  RED/GREEN and covering output, files and concerns in ignored task report.
  Independent task review follows. No deploy/push/preview mutation permission.

## Acceptance evidence

Accepted source `b8d4a71daf2d80eb83f0a21baaee2519895646f9`, independent
Spec PASS / Quality Approved, no Critical or Important findings. Parent reran
the exact covering pytest command:426 passed,50 existing warnings in64.76s.
Parent exact lint-only90 adopted files plus composition root, shell syntax and
whitespace checks passed. Implementer import-closure gate1passed. Characterization
baseline10passed before extraction; missing service/API RED recorded before code.

Review confirmed legacy parity, fixed realms, authoritative post-write state and
generation rejection, lock/replacement ordering, explicit JSON models and shared
admission. Existing deprecation warnings are Minor(deferred), not suppressed.
Runtime state and historical test order cannot be reconstructed from a source
diff alone; report/recorded milestones provide test order, and no runtime action
was authorized or performed. Deployment script changed only module registration.

## Remaining scope

React password form/settings, the other pages, deployment packaging for the
complete API app, staging and approved cutover remain separate parity work.
