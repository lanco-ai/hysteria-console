# HTTP cutover audit notes

These record boundary requirements and their source-level progress, not a
completed migration or production-readiness claim. Historical preparation
sections below explain the constraints used by the reviewed slices.

- The initial JSON prototype omitted Permissions-Policy and Content-Security-Policy. The login transport work now shares all six legacy invariant security headers through `http_utils.SECURITY_HEADERS`; review acceptance is recorded in the FastAPI login plan. Before document cutover, still verify success, error, redirect, HEAD and static response parity.
- `http_utils.SECURITY_HEADERS` is the shared invariant policy, consumed by the legacy Handler and API. `send_response_body`, `_serve_static` and `redirect` separately preserve Content-Length, ETag/304, cache-control and Set-Cookie behavior. New document/static responses must be checked against these transport behaviors too; JSON header checks do not establish document or deployment parity.
- Existing write routes already distinguish JSON responses by Accept while receiving bounded form data. React writes can retain the existing external method/path/form contracts; do not duplicate user-state/accounting rules merely to rename transport endpoints. Any FastAPI transport adapter must preserve body/field bounds, same-origin checks, revision conflicts, draft retention, actor/client-IP attribution and post-commit failure semantics.
- Current Handler owns `_mutation_conflict`, `_mutation_user_not_found`, `_send_toggle_json`, actor lookup and audit append response orchestration. These require explicit extraction/adaptation tests, not a blind whole-Handler ASGI wrapper.
- New query-free JSON reads do not replace existing credential-bearing subscription and dedicated-link exchange routes. Preserve exchange ordering and HEAD side-effect safety before any catch-all frontend routing.
- The controlled React preview deliberately links bare `/static/style.css` to prove current CSS reuse. Production document generation must attach the corresponding release's style version/ETag (as legacy html_page does) or an equivalent immutable release URL, and serve matching CSS/fonts/JS on rollback. A hashed JavaScript filename alone does not make the whole UI release cache-safe.

No production host, certificate, port mapping or proxy configuration has been read or changed for these notes.

## Write-boundary prerequisites inspected before React forms

The current `http_utils.parse_form` accepts only a single unambiguous decimal
Content-Length, rejects Transfer-Encoding, bounds the body at 256 KiB and fields
at 128, uses strict UTF-8/percent/form parsing, and permits an explicitly empty
body for action forms. An ASGI adapter must retain raw repeated header values
for this validation: the initial read-only `dict(request.headers.items())`
bridge is not sufficient for parsing writes. Bound the streamed body before
buffering or dispatching it; do not call an unbounded `request.body()` and check
its size afterwards. Parser rejection must not load or mutate runtime state.

The legacy POST order is same-origin check (except `/login`), bounded parsing,
metadata load, revision extraction, then exact route dispatch. The revision is
the first form `user_revision` when present, otherwise first query `revision`.
Write failures pass `post_path` to `_state_failure_requires_static_stop`; the
initial read adapter's no-post-path error handling cannot simply be reused for
mutations. A worker's admission slot must remain held until synchronous work
actually finishes, even if the client disconnects.

Login depends on the real immediate peer for `request_client_ip` and trusts
X-Real-IP/X-Forwarded-For only for a loopback peer. A future write bridge needs
`client_address` from the ASGI transport, not a client-selected forwarded value.
At production server configuration time, preserve that immediate-peer value:
do not let an upstream ASGI proxy-header middleware rewrite scope.client before
this trust decision without an explicit equivalent trust-boundary review.
Preserve the existing throttle buckets, password length checks, credential
generation, cookie attributes and success redirects. Failed admin credentials
currently render HTTP 200 with feedback; throttling is 429 plus Retry-After.
Compatibility behavior cannot silently change merely because JSON is added.

Do not replay a mutation automatically after a transport error: the server may
already have committed before the response was lost. Existing reset and refresh
operations differ deliberately: refresh banks cleared bytes in the preserved
bucket, reset does not. Tests must verify accounting and audit records, not just
response shape or a disabled button.

## Historical login extraction requirements

Before extraction, `auth_routes._login` combined credential verification/session creation
with rendered feedback and redirects. A shared internal login decision can
separate those responsibilities before adding a JSON login transport. Preserve
admin-first field precedence, username trimming, password length bounds,
separate administrator/user throttle buckets, disabled/expired/must-change
user handling, and the current session-generation binding. The public form
remains administrator-only; compatibility user credentials are not permission
to add a new visible user-login feature.

The old HTML adapter still needs the submitted administrator username and
message for escaped feedback; session IDs remain internal and are delivered
only through the existing HttpOnly/SameSite cookie helpers, never in JSON.
Client-IP selection belongs to the HTTP adapter and must use the existing
loopback-only forwarded-header trust rule. A shared decision helper should not
read a request socket, set cookies, redirect or render HTML itself.

Before implementing this extraction, add parity tests against the actual
legacy login route for successful login, wrong credentials, missing fields,
429/Retry-After, user lifecycle states, cookie generation and redirects.
The existing concurrent-verification reservation test is load-bearing.
Inspect exception paths so a failed state read or verifier cannot leave an
in-flight throttle reservation permanently occupied; test this explicitly
rather than rewriting or weakening rate limiting.

The login-service plan records acceptance of this extraction. It did not by
itself implement the login API or React login form.

## Prepared seams after login extraction

`login_service.LoginService.authenticate(form=..., meta=..., client_ip=...)`
now returns an internal `LoginResult`; `subscription_service._login_service()`
constructs it from authoritative current helpers. The legacy HTML adapter and
new login API consume this service. Session creation happens after exactly-once throttle
reservation release. Errors propagate without turning state failures into a
successful empty or invalid-password response.

The accepted form extraction exposes `http_utils.form_content_length(headers,
max_bytes=...)` and `decode_form_body(raw, max_bytes=...)`. The latter does not
validate claimed length or own stream reading. An ASGI consumer must preserve
duplicate raw Content-Length headers, reject header/length failures before
receiving or loading state, cap each received chunk before appending, reject
truncation and length mismatches, and bound body-read duration and admission.

The login JSON slice adds an explicit POST-only `/api/v1/login`
using the existing form fields. Keep legacy `/login` behavior unchanged. The
JSON endpoint needs existing same-origin protection, typed allowlisted feedback
and redirect destinations, matching cookie helpers, no session identifiers in
JSON, real immediate-peer attribution and `/login` write-path strict-state
failure classification. It must consume the shared service, not duplicate
credential checks. Preserve neutral compatibility-user feedback on this
administrator-facing transport. Do not automatically retry a login mutation
after a transport error; a session may already have been committed.

Keep admission held while the request body is received and until synchronous
credential work really finishes, including disconnect/cancellation. Existing
read admission tests must still pass after adapting the dispatcher. Add raw
ASGI tests for duplicate headers, short/oversized/chunked bodies and cancellation,
not only TestClient requests (which may normalize the malformed wire shapes).

The login slice also extracts the complete security-header policy into the
shared constant used by both HTTP boundaries. Static cache and conditional
response rules remain outside that invariant set; document routing and
production cache/cookie/proxy verification are still outstanding.

See the FastAPI login plan for the POST route, bounded ASGI reader and full-header
policy review gate. React login and production cutover are separate tasks.

## Inspected logout boundary — API and React accepted

`public_page_routes` GET/HEAD `/logout` and `/user/logout` only render the
appropriate confirmation when their respective session exists; otherwise they
redirect to `/login`. Merely reading these URLs must not revoke a session.
`console_shell_views.render_logout_confirmation` has distinct administrator/user
titles, POST targets and cancel destinations (`/admin` versus `/user/panel`).
The existing administrator sidebar submits POST `/logout` directly; preserving
that behavior is distinct from preserving the separately addressable confirmation
page. Do not silently introduce a new confirmation requirement on that control.

`auth_routes` POST handlers delete only the supplied `sid` or `usid` through
the corresponding shared session-store helper, clear only that realm's cookie,
and return 303 to `/login`. They deliberately do not require a currently valid
session first. Empty or stale sessions still permit cookie clearing; logout
does not mean revoking every device or the other realm. The shared store owns
its file lock and expiry pruning, which should not be reimplemented in an API.

The legacy POST wrapper checks origin, validates bounded form input and loads
metadata before invoking either handler. Any future adapter must decide and
test this ordering explicitly, reuse the accepted admission/body boundary, and
classify strict-state failures with the respective legacy POST path. Do not
report successful logout or clear a cookie after a failed session deletion.
The API boundary is accepted in `f82653e` with 255 covering tests and an
independent clean review. React logout is accepted in `6388522` after full
frontend checks, 25 preview/isolation tests and independent clean review. It
permits only the two exact additional logout APIs in guarded fixtures.
Neither slice changes the legacy routes or production routing.

## Inspected password-change boundary — shared service and API accepted

Source inspection: `auth_routes._change_user_password`,
`auth_routes._change_admin_password`, `identity_service._change_admin_password`,
`session_store._replace_sessions_with_new`, `user_panel_routes._password` and
`user_views.render_user_change_password`.

Administrator changes authenticate the administrator first, then delegate
current-password verification and new length/confirmation validation to the
identity service under its metadata lock. That helper persists the new hash
before the route replaces all administrator sessions with one generation-bound
session. Failure redirects to `/admin/settings?msg=err:<code>`; success redirects
to `/admin/settings?msg=password+changed`. The administrator rule currently does
not reject reusing the current password. Do not invent that rule in an adapter.

User changes require a password-kind user session; subscription-token sessions
cannot use the change form. The route checks lifecycle, then new length and
confirmation, then acquires the usage lock and rechecks the current user/lifecycle
before validating the current password and rejecting same-password reuse. It
persists the new hash and removes the must-change flag under that lock. Only
afterward it replaces that user's sessions with a new password-generation-bound
session; other users and administrator sessions remain unchanged. Preserve the
existing access-error priority (including must-change) through the shared helper,
not a separately interpreted frontend policy.

Both flows have a credential-write/session-replacement boundary, not a single
transaction across both files. A later session-store error cannot truthfully be
reported as an ordinary validation failure, nor safely retried automatically:
the password may already have changed. Preserve strict-state error classification
and generation-based rejection of old sessions. Do not roll back an authoritative
hash using a stale snapshot or return a success cookie after replacement fails.

The user mutation orchestration was extracted from the HTTP handler into
`password_change_service.PasswordChangeService`; both legacy and JSON consumers
now use shared decisions. Source `b8d4a71` passed independent review and426
covering tests. Cookies/redirect presentation remain adapter-owned; password
hashes/session IDs stay out of public JSON and result repr. The service retains
the inspected validation, locking, persistence and replacement sequence.

Existing tests provide partial evidence only: the initial-user-password HTTP
test exercises a real successful write, but the same-password test checks only
rendered copy, and the administrator invalidation test manually reproduces an
older clear/create sequence instead of calling the current handler. New seam
tests must exercise actual handler/service paths, real temporary credentials
and sessions, failure ordering and a state change between initial and locked
user checks. Do not weaken or replace accounting/security assertions elsewhere.

## Authentication-page data needed after password transport

`operations_views.render_settings` currently renders only administrator name,
the browser-local sidebar-motion checkbox, and the three-field administrator
password form. It does not expose the administrator token or full metadata.
React settings therefore needs an authenticated allowlisted read of administrator
name and existing password limits, not an entire metadata payload. Keep the
motion preference local to the browser, with the existing storage key and shell
behavior; do not invent a backend preference setting.

`user_views.render_user_change_password` needs username, public host and existing
password limits. The general session API rejects password-change-required users,
so using that API as the page's sole access gate would deadlock their required
password change. A dedicated minimal read must use the actual password-page
identity/lifecycle rules and retain password-kind-only access, instead of
weakening the general session endpoint. Its unauthorized/disabled/expired
navigation must follow the existing user password document.

The next React authentication-page slice should integrate these small reads
with settings and user password UI together. Keep all three native field names,
autocomplete/length attributes, current copy/classes and adapter-owned redirects.
Server-rendered user feedback includes known message mappings; arbitrary query
text remains escaped. Never persist password drafts or send them in URLs.
Success returns to the existing settings/user-panel URL; a transport failure
must warn that the outcome is uncertain, preserve an editable form and avoid
an automatic second password mutation.

Verified `shell.js` preference contract: toggling on adds the root
`sidebar-motion-enabled` class and stores `hy2.sidebar-motion=enabled`; toggling
off removes the class and removes that storage key (not a stored false string).
Storage exceptions must not stop the current visible preference change. Reuse
the React shell's initial preference application and preserve reduced-motion
behavior, mobile drawer focus and desktop collapse tests.

The current `useReadResource` keeps only an HTTP status for failed responses.
User password-page navigation distinguishes forbidden from disabled/expired,
so its eventual consumer must retain a strictly validated access-error code,
not interpret every403 identically or display arbitrary server text. Any shared
reader extension requires existing logs recovery/stale tests to remain green.

Current React preview seeds real administrator sessions but user cookies are
subscription-token-kind. Password-page browser tests will need explicit fictional
password-kind credentials/sessions, not a weakened kind check or mocked verifier.
Password writes revoke existing fixture sessions; isolate browser suites with
fresh guarded preview state (or an equally explicit isolated fixture lifetime)
rather than rely on a lucky suite order or bypass revocation. Preserve the shared
draining server lifetime and explicit anonymous Cookie forwarding during that
test-fixture change. No production state is a permissible fixture source.

## Overview migration boundary audit

The accepted `/api/v1/admin/overview` payload is deliberately a small frequent
refresh contract (`usage_dashboard.build_overview_json_payload`): timestamp,
total usage and per-user usage/online/revision/disabled fields only. It is not
the complete page bootstrap. Do not inflate each frequent poll with account
metadata, chart arrays, subscription secrets or the full users file. A later
page bootstrap must explicitly supply the missing display/form configuration
while retaining the small refresh contract and shared multiplier snapshot.

`admin_views.render_admin` also owns billing range/anchor controls, five-column
user layout, create-form non-sensitive draft recovery, field-specific errors
and optional enabled egress choices. Its cycle save carries an explicit
re-anchor confirmation. A React migration that drops any of those to render
only existing summary fields is not page parity.

Mutations currently span account add/update, traffic cycle/reset/refresh,
pause/toggle/delete and credential rotation modules. Their responses mix302
redirects,422 validation HTML and existing JSON row patches. Shared business
decisions must preserve revision checks under the usage lock, audit actor
snapshots, usage preservation/reset rules, static-access sync/reload markers
and credential revocation. Do not copy handler code into a second API policy
implementation or silently flatten pending/durable-error outcomes into success.

In particular, `_configure_cycle` rereads under the metadata lock so a cycle
update cannot restore an older administrator password hash. Preserve that helper
and the subsequent locked static-access sync. Reset-user reads before/after
usage, zeroes cycle/daily/hourly state, clears quota-only alert dedup, then logs
the action and can return a fresh row/reload status. User revision conflicts
must not become blind last-write-wins updates in new forms.

This audit does not authorize real proxy reloads or credential rotations.
Future mutation tests must use isolated state and safe service doubles for
external effects while verifying the actual state transactions and decisions.
