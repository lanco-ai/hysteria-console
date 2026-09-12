# HTTP cutover audit notes

These are outstanding cutover requirements, not completed migration claims.

- The initial FastAPI JSON-only prototype supplies no-store, nosniff, no-referrer, frame denial and same-origin opener policy. The legacy Handler additionally supplies Permissions-Policy and Content-Security-Policy. Before serving React documents or cutting over, extract/reuse the full existing header policy and verify it on success, error, redirect, HEAD and static responses; do not silently drop those two headers.
- `subscription_service.Handler._send_security_headers` is the authoritative current policy. `send_response_body`, `_serve_static` and `redirect` also preserve Content-Length, ETag/304, cache-control and Set-Cookie behavior. New document/static responses must be checked against all of these, not only the five initial JSON API header tests.
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
Preserve the existing throttle buckets, password length checks, credential
generation, cookie attributes and success redirects. Failed admin credentials
currently render HTTP 200 with feedback; throttling is 429 plus Retry-After.
Compatibility behavior cannot silently change merely because JSON is added.

Do not replay a mutation automatically after a transport error: the server may
already have committed before the response was lost. Existing reset and refresh
operations differ deliberately: refresh banks cleared bytes in the preserved
bucket, reset does not. Tests must verify accounting and audit records, not just
response shape or a disabled button.

## Login extraction boundary for the following slice

`auth_routes._login` currently combines credential verification/session creation
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

These are implementation preparation notes, not claims that the login API or
React login form already exists.

## Prepared seams after login extraction

`login_service.LoginService.authenticate(form=..., meta=..., client_ip=...)`
now returns an internal `LoginResult`; `subscription_service._login_service()`
constructs it from authoritative current helpers. The legacy HTML adapter is
the only consumer so far. Session creation happens after exactly-once throttle
reservation release. Errors propagate without turning state failures into a
successful empty or invalid-password response.

The planned form extraction exposes `http_utils.form_content_length(headers,
max_bytes=...)` and `decode_form_body(raw, max_bytes=...)`; its acceptance record
must be checked before any ASGI consumer uses those names. The latter does not
validate claimed length or own stream reading. An ASGI consumer must preserve
duplicate raw Content-Length headers, reject header/length failures before
receiving or loading state, cap each received chunk before appending, reject
truncation and length mismatches, and bound body-read duration and admission.

For the next login JSON slice, prefer an explicit POST-only `/api/v1/login`
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

Before expanding to document routing, extract the existing complete security
header policy from the legacy Handler into a shared constant/helper and use it
from both HTTP boundaries; retain static cache and conditional-response rules
outside that invariant security-header set. The current prototype's five
headers are still not the complete legacy policy.

These are next-slice requirements, not claims of an implemented POST route,
bounded ASGI reader, shared full-header policy, or production readiness.
