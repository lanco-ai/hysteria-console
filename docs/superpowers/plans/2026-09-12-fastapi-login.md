# FastAPI login transport implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Add an isolated JSON login transport that consumes the accepted login service and bounded form helpers, ready for the React login page.

**Architecture:** POST-only `/api/v1/login` accepts the existing URL-encoded fields. A bounded ASGI reader validates raw repeated headers before receiving bytes; the existing admission dispatcher owns both receipt and synchronous service execution. Existing `/login`, preview routing and production deployment remain unchanged.

**Tech Stack:** Installed FastAPI/AnyIO/Pydantic, Python and current pytest/ASGI fixtures. No dependency upgrade.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Administrator access and user-panel access stay separate. A user session cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation, revision conflicts, strict-state failure behavior, request size limits, request concurrency bounds and cache/security headers.
- API responses use explicit field allowlists: never serialize an entire runtime object containing password hashes, proxy secrets or account credentials.
- Keep existing URLs, subscription formats, dedicated-user link exchanges, cookie separation, redirects, downloads, QR responses and status semantics.
- Blocking file locks, subprocesses and synchronous probes must not execute directly on an async event loop. Keep their existing bounds and cancellation semantics.
- Source/temporary fixtures only. No push, deploy, runtime state reads, proxy/service operations, routing switch, preview POST activation or frontend changes.

## Interface decisions

The new JSON endpoint is not a replacement of legacy POST `/login`. It applies
`http_utils.is_same_origin_post` before reading the body; the legacy exception
for `/login` remains unchanged. Local scripts without Origin retain that helper's
existing behavior. Accept the existing `admin_username/admin_password` and
compatibility `user_username/user_password` fields, preserving first-value and
administrator precedence through `LoginService`, not new credential logic.

| Result | Status | JSON | Additional header |
| --- | --- | --- | --- |
| Success | 200 | `{"ok":true,"redirect_to":"/admin?msg=login+success"}` or existing user destination | Matching existing Set-Cookie only |
| Invalid/missing/ineligible | 200 | `{"ok":false,"message":"existing visible feedback"}` | No Set-Cookie |
| Throttled | 429 | Same failure model | Retry-After from result |
| Cross-site | 403 | `{"error":"cross_site_request"}` | None |
| Invalid form/header/truncation | 400 | `{"error":"bad_request"}` | None |
| Claimed/actual body above 256 KiB | 413 | `{"error":"request_too_large"}` | None |
| Body receipt exceeds 10 seconds | 408 | `{"error":"request_timeout"}` | None |
| State failure / server busy / unexpected | Existing 503 / 503 / 500 | Existing sanitized error models | Existing busy Retry-After |

All compatibility-user failures use the current neutral visible text
`请使用管理员账号登录控制台。`; no username, role, session identifier or lifecycle
details are added to failure JSON. Success destinations are a Literal allowlist:
`/admin?msg=login+success`, `/user/panel`, `/user/change-password`. No redirect
target is accepted from submitted data. GET/HEAD on this endpoint are 405 and
perform no authentication; HEAD body suppression remains global.

### Task 1: Bounded, cookie-preserving login endpoint

**Files:** Create `hysteria/web_api/requests.py`, `tests/test_web_api_login.py`.
Modify `hysteria/web_api/app.py`, `hysteria/web_api/services.py`,
`hysteria/web_api/models.py`, `hysteria/http_utils.py`,
`hysteria/subscription_service.py`, `hysteria/auth_views.py`,
`hysteria/auth_routes.py`, `tests/test_auth_views.py`,
`tests/test_web_api_reads.py`, and `scripts/check-quality.sh`.
No deployment inventory change: new web_api files are still not installed.

**Request interfaces:** `requests.RequestHeaders` is a read-only case-insensitive
Mapping preserving repeated raw ASGI headers and exposing `get_all(name,
default=None)`. Mapping lookups use the first value, matching the legacy HTTP
header object. It may accept an existing string Mapping for legacy unit-test
callers. Move/reuse the existing `_RequestHeaders` logic rather than retaining
two independent normalizers. Decode raw bytes with Latin-1, not lossy UTF-8.

`async read_form(request, headers)` uses `http_utils.form_content_length` first,
then receives within `anyio.fail_after(FORM_READ_TIMEOUT)` where
`FORM_READ_TIMEOUT = 10.0`. For each chunk, check total against MAX_FORM_BYTES
and claimed length before appending. At end require exact claimed length and
call `decode_form_body`. Oversize maximum is 413; over-claimed but under-maximum
and truncation are 400. Disconnect during receipt is invalid/incomplete input;
cancelled tasks propagate cancellation. Translate only receipt timeout into a
dedicated `requests.FormReadTimeout` exception; do not map a service/verifier
TimeoutError to HTTP 408. No unbounded `request.body()`, no state
access and no background orphan reader. Timeout maps to the 408 above.

**Service interface:** `LegacyPanelServices.submit_login(*, headers, path, form,
client_address)` constructs a legacy-shaped request including the actual ASGI
peer tuple, then calls `service._login_service().authenticate` with current
`service.load_meta()` and `http_utils.request_client_ip(request)`. Return an
internal frozen `LoginReply(result: LoginResult, cookie: str | None)` with cookie
repr-hidden; only success calls the matching current service cookie helper and
`is_secure_request(request)`. Never expose LoginReply or LoginResult via Pydantic
automatic serialization. The HTTP route builds only the table's public fields.

Extend the current read error wrapper into one shared operation wrapper with
optional `post_path`; reads retain their exact behavior, login failures pass
logical `post_path='/login'` to `_state_failure_requires_static_stop`. Preserve
request multiplier snapshot lifetime, existing fail-closed invocation and
sanitized StateUnavailable translation. Do not treat exceptions as bad passwords.

**Shared presentation/security:** Move the existing login-message dictionary
into `auth_views.login_feedback_message(outcome, realm='admin')`. Reuse a private
visible-message helper from that function and `render_login` for the existing
neutral user override. Legacy auth_routes calls the new function with default
realm and still passes `active_tab=result.realm` to its renderer, preserving its
adapter seam tests. The new API passes the actual realm. No rendered markup changes.

Move the exact six invariant values in Handler._send_security_headers to
`http_utils.SECURITY_HEADERS`, preserving order and values. Handler iterates that
mapping; API middleware adds those same headers plus its existing no-store.
Do not include cache-control in the shared invariant set: legacy static caching
must remain unchanged. No change to CSP contents, ETag, 304, redirects or cookies.

- [ ] Write endpoint tests RED against current missing route, using real
temporary metadata/users/session files and deterministic credential verification.
Assert successful JSON and exact cookie attributes/generation binding, invalid,
missing, user compatibility success/ineligible/forced-change, and throttled
behavior. Assert body fields exactly, not just presence; no secrets in any JSON.

```python
response = client.post('/api/v1/login', data={
    'admin_username': ' admin ', 'admin_password': 'fixture-correct',
})
assert response.status_code == 200
assert response.json() == {'ok': True, 'redirect_to': '/admin?msg=login+success'}
assert response.headers['set-cookie'].startswith('sid=')
assert 'HttpOnly' in response.headers['set-cookie']
assert 'SameSite=Lax' in response.headers['set-cookie']
```

- [ ] Add raw ASGI tests for duplicate equal/unequal Content-Length, invalid
lengths, Transfer-Encoding, invalid type, short/overlong/multi-chunk bodies,
actual maximum overflow, malformed UTF-8/percent/fields, explicit empty input,
and timeout/disconnect. For rejected headers, a receive function that raises if
called proves no read; a recording fake service proves no state call. Exercise
origin rejection before receipt and unchanged old `/login` behavior separately.
Use the 10-second constant patched to a small test value only for timeout tests.

```python
async def forbidden_receive():
    raise AssertionError('invalid headers must be rejected before receiving')
# Drive app with repeated raw header tuples and collect response events.
# Assert status 400, error bad_request, and submit_login call count zero.
```

- [ ] Implement RequestHeaders and bounded read_form using the accepted helpers.
Extend the existing dispatcher, not a second semaphore: acquire before parsing
or body receipt; for login prepare origin/header/body data and real peer inside
its existing try/finally, then offload submit_login with the same shielded worker
lifetime. All early rejection, timeout and cancellation paths release exactly
once. While a synchronous login worker is still running after caller cancellation,
another request at capacity must get server_busy. Once work finishes, capacity
must become usable. Existing read cancellation/handoff regressions remain unchanged.

- [ ] Implement submit_login and explicit public success/failure models. Register
POST `/api/v1/login` only; do not bind missing methods at factory construction so
existing read-only fake services still instantiate the app. Translate known
parser/origin/timeout/state outcomes as specified; unexpected failures remain
sanitized 500. Never retry service calls automatically or include submitted
passwords in raised public errors, logs or model validation responses.

- [ ] Share feedback text and the six security headers as specified. Verify
complete header equality against a real legacy response, including CSP and
Permissions-Policy, on login success/error/429/HEAD and existing API reads.
Keep existing legacy static/cache and login-design tests passing.

- [ ] Add concurrency tests with synchronization events, not timing guesses:
pending body receipt owns capacity; body cancellation frees it without a service
call; cancelled synchronous verifier retains admission until released; event
loop remains responsive; parser failure/timeout/worker exceptions cannot leak
slots. Test immediate peer versus forged forwarded IP with separate throttle
buckets, reusing existing real LoginThrottle. Verify state exceptions classify
with post_path='/login' and never mutate sessions on failed state reads.

- [ ] Add new API module/test to adopted quality files. Run focused API login
and reads, shared form, login service/routes/views/design, reliability and
subscription security tests; then lint-only. Run full backend quality once
after focused green because the shared security-header extraction touches the
legacy HTTP boundary. No frontend build, preview POST activation or deployment.

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_web_api_login.py tests/test_web_api_reads.py tests/test_http_utils.py tests/test_login_service.py tests/test_auth_routes.py tests/test_auth_views.py tests/test_admin_login_design.py tests/test_reliability_regressions.py tests/test_subscription_security_regressions.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh
git diff --check
```

- [ ] Self-review, commit owned source/tests locally, and record exact RED/GREEN,
covering/full commands and outputs. No push or runtime operations.

## Scope limit

This adds a fixture-testable login JSON endpoint, not complete HTTP cutover.
React login, controlled preview forwarding of this exact mutation, immutable
assets, all remaining routes, staging and approved deployment remain separate
deliverables. The production service still uses its existing entry point.
