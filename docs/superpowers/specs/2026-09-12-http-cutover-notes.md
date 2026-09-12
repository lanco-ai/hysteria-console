# HTTP cutover audit notes

These are outstanding cutover requirements, not completed migration claims.

- The initial FastAPI JSON-only prototype supplies no-store, nosniff, no-referrer, frame denial and same-origin opener policy. The legacy Handler additionally supplies Permissions-Policy and Content-Security-Policy. Before serving React documents or cutting over, extract/reuse the full existing header policy and verify it on success, error, redirect, HEAD and static responses; do not silently drop those two headers.
- `subscription_service.Handler._send_security_headers` is the authoritative current policy. `send_response_body`, `_serve_static` and `redirect` also preserve Content-Length, ETag/304, cache-control and Set-Cookie behavior. New document/static responses must be checked against all of these, not only the five initial JSON API header tests.
- Existing write routes already distinguish JSON responses by Accept while receiving bounded form data. React writes can retain the existing external method/path/form contracts; do not duplicate user-state/accounting rules merely to rename transport endpoints. Any FastAPI transport adapter must preserve body/field bounds, same-origin checks, revision conflicts, draft retention, actor/client-IP attribution and post-commit failure semantics.
- Current Handler owns `_mutation_conflict`, `_mutation_user_not_found`, `_send_toggle_json`, actor lookup and audit append response orchestration. These require explicit extraction/adaptation tests, not a blind whole-Handler ASGI wrapper.
- New query-free JSON reads do not replace existing credential-bearing subscription and dedicated-link exchange routes. Preserve exchange ordering and HEAD side-effect safety before any catch-all frontend routing.

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
