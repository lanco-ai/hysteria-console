# FastAPI logout transport implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Add administrator/user logout JSON transports backed by the existing
session store, preserving separate cookies and current-device revocation.

**Architecture:** Two exact POST endpoints select a fixed realm and call a
single narrow adapter; the existing session-store helpers remain authoritative.
Reuse the login form receipt/origin/admission/error boundary. Legacy confirmation
documents and POST handlers remain unchanged in this slice.

**Tech Stack:** Current FastAPI/Pydantic/AnyIO, Python services and pytest.
No dependencies, storage schema, frontend, runtime or deployment changes.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- API responses use explicit field allowlists; never serialize runtime objects
  containing session identifiers, hashes, proxy secrets or credentials.
- Blocking file locks must not execute on the async event loop. Cancellation
  cannot release admission while an actual synchronous worker is still running.
- No production routing, proxy/TLS/ports, state, preview POST allowlist, frontend,
  dependency upgrades or changes to legacy logout behavior.

### Task 1: Separate current-device administrator/user logout endpoints

**Prerequisite:** Accepted FastAPI login task (`d596ab7`, `cff57c9`). Wait for
the current React-login implementation/review gate before dispatching another
source implementer. Its preview-only login permission must not expand here.

**Files:** Modify `hysteria/web_api/app.py`, `hysteria/web_api/models.py`,
`hysteria/web_api/services.py`, `scripts/check-quality.sh`; create
`tests/test_web_api_logout.py`. Existing API read/login tests should remain
unchanged unless a private helper rename requires a narrowly documented change.

**Public contract:**

| Endpoint | Fixed realm / legacy post_path | Revocation and cookie |
| --- | --- | --- |
| POST `/api/v1/logout` | admin / `/logout` | only supplied `sid`, only administrator clearing cookie |
| POST `/api/v1/user/logout` | user / `/user/logout` | only supplied `usid`, only user clearing cookie |

On success return HTTP 200 with exactly
`{"ok": true, "redirect_to": "/login"}` and the matching existing clearing
cookie (including Secure when the existing helper requires it). Use a dedicated
Pydantic `LogoutResponse` with Literal true and Literal `/login`. Never include
the old/new cookie value or session identifier in JSON. GET/HEAD return 405;
HEAD is bodyless. A trailing slash/unknown route remains 404, never a fallback.

Absent, stale, expired, already-revoked or wrong-realm cookies still allow a
successful response clearing only the targeted cookie. Do not add an active
session prerequisite: existing logout POST does not have one. Never switch the
realm based on supplied cookies, query or form input. Receiving both cookies
must still affect only the fixed endpoint's realm. Other live devices and the
opposite realm remain authenticated. Existing store expiry pruning is allowed
and must not be reimplemented here.

Origin, framing/body timeout, unavailable state, busy admission and unexpected
errors use the accepted login endpoint's JSON/status contract. Rejection before
service dispatch must not read state. Any state/deletion failure must not return
success or a clearing cookie. Do not automatically retry a mutation.

**Service seam:** Add `submit_logout(*, headers, path, form, client_address,
realm)` with explicit accepted realm validation before side effects. Endpoints
pass fixed literals; callers cannot choose realm through request data. Build
the existing request bridge; ignore parsed form fields deliberately (legacy
logout also ignores them). Within `_run_operation`, load_meta first to preserve
the old POST wrapper ordering, parse cookies through the current helper, call
`delete_session` or `delete_user_session`, then build `LogoutReply(cookie=...)`
using the matching clear-cookie helper and existing secure-request decision.
Make the internal reply frozen/slotted with cookie repr hidden. Use respective
legacy post_path for failure classification and preserve multiplier snapshot
cleanup. No new domain module is necessary: session_store already owns the
revocation logic, locks and atomic writes.

**Shared transport:** Rename `prepare_login` to a form-write name as needed;
reuse it for all three writes. Factor its existing known-error mapping/dispatch
wrapper once rather than copy the login try/except twice. Preserve the current
single capacity semaphore, prepare-before-worker ordering and shielded worker
lifetime unchanged. Resolve service methods lazily inside endpoints so existing
read-only test doubles still construct the app. Do not introduce a dynamic
catch-all route or a broad handler-to-ASGI wrapper.

- [x] Write RED for both missing endpoints. Use real temporary session stores,
  metadata and real cookie helpers via LegacyPanelServices. Seed two live admin
  sessions and two live user sessions with current credential generation/kind;
  prove successful targeted logout changes actual state, not only a mock count.

- [x] Implement the narrow service and explicit public/internal responses.
  Test each realm, both cookies together, missing/stale/expired/repeated logout,
  an opposite-realm-only cookie, and form/query attempts to change the realm.
  Verify cookie attributes and the other devices/realm remain usable via the
  real session API. Use explicit incoming Cookie headers to avoid shared client
  jar masking missing-cookie cases. Session IDs must never appear in response
  body, repr or public error text.

- [x] Share the form-write transport and register the two exact POST routes.
  Exercise origin rejection before receive, duplicate Content-Length,
  Transfer-Encoding, malformed UTF-8/form, zero-claim nonempty/oversized,
  truncation and valid empty terminal input for both endpoints. These tests
  prove shared parsing is actually wired, not a second exhaustive helper suite.
  Record no service calls on rejected raw ASGI input. Verify 405/404/HEAD and
  complete shared security headers on success/rejection/state error.

- [x] Test unavailable metadata prevents deletion; deletion OSError/state-store
  failures produce 503 without Set-Cookie and classify respective legacy paths.
  Critical-state fail-closed behavior uses existing helpers with recording safe
  doubles, never actual services. Unexpected deletion error produces sanitized
  500. Verify snapshot cleanup on success/failure.

- [x] Use event-controlled worker tests to prove pending deletion owns capacity,
  a cancelled caller does not free it until actual deletion finishes, unrelated
  requests remain responsive below capacity, and worker errors release capacity.
  Keep existing login/read admission tests as covering gates; no timing loops.

- [x] Run focused API/logout/login/read/parser/session/security coverage plus
  lint and whitespace checks. No full backend rerun is needed for this adapter
  slice unless implementation reaches beyond the listed files. Register the new
  test in the adopted lint file set, preserving every existing assertion.

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_web_api_logout.py tests/test_web_api_login.py tests/test_web_api_reads.py tests/test_http_utils.py tests/test_session_store.py tests/test_reliability_regressions.py tests/test_public_read_routes.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
git diff --check
```

- [x] Self-review, commit only owned files locally and record exact RED/GREEN,
  covering commands/output and concerns in the ignored task report. Independent
  review follows. No push, deployment or preview logout forwarding.

## Acceptance evidence

Implemented in `f82653e`; independent task review returned spec PASS and
quality Approved, with no findings. Parent reran the documented covering
command: 255 passed, 5 existing warnings in 16.35 seconds. The exact lint-only
wrapper exposed predecessor preview import/format drift, corrected separately
in `9bed63e` and independently approved as mechanical-only. Parent verified
the exact wrapper (87 adopted files and composition root formatted), shell
syntax and whitespace checks, all exit 0. No production or preview logout
forwarding changed. Full migration remains incomplete.

## Scope remainder

React confirmation documents, shell logout handling, password changes and
remaining administrator/user pages are separate parity slices. This API does
not change where existing browser links/forms go. Production cutover still
requires its separate approval and complete staging/rollback verification.
