# HTTP cutover audit notes

These are outstanding cutover requirements, not completed migration claims.

- The initial FastAPI JSON-only prototype supplies no-store, nosniff, no-referrer, frame denial and same-origin opener policy. The legacy Handler additionally supplies Permissions-Policy and Content-Security-Policy. Before serving React documents or cutting over, extract/reuse the full existing header policy and verify it on success, error, redirect, HEAD and static responses; do not silently drop those two headers.
- `subscription_service.Handler._send_security_headers` is the authoritative current policy. `send_response_body`, `_serve_static` and `redirect` also preserve Content-Length, ETag/304, cache-control and Set-Cookie behavior. New document/static responses must be checked against all of these, not only the five initial JSON API header tests.
- Existing write routes already distinguish JSON responses by Accept while receiving bounded form data. React writes can retain the existing external method/path/form contracts; do not duplicate user-state/accounting rules merely to rename transport endpoints. Any FastAPI transport adapter must preserve body/field bounds, same-origin checks, revision conflicts, draft retention, actor/client-IP attribution and post-commit failure semantics.
- Current Handler owns `_mutation_conflict`, `_mutation_user_not_found`, `_send_toggle_json`, actor lookup and audit append response orchestration. These require explicit extraction/adaptation tests, not a blind whole-Handler ASGI wrapper.
- New query-free JSON reads do not replace existing credential-bearing subscription and dedicated-link exchange routes. Preserve exchange ordering and HEAD side-effect safety before any catch-all frontend routing.

No production host, certificate, port mapping or proxy configuration has been read or changed for these notes.
