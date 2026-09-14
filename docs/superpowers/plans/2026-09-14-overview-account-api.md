# Overview account API implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** Expose accepted account creation/edit orchestration to the React overview
through bounded authenticated form endpoints, without repeating domain policy.
**Prerequisite:** Account mutation boundary independently accepted first.
**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md
**Status:** Accepted locally at `c2e276d` (implementation `a4b78da`, strict-result
fix `c2e276d`). Independent review and scoped re-review passed; covering294 tests
passed, amended focused52 tests passed, lint/diff clean. Two inherited dependency
warnings remain maintenance. React overview is still a follow-on; no deployment.

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- API responses use explicit field allowlists: never serialize an entire runtime
  object containing password hashes, proxy secrets or account credentials.
- Preserve usage units, timezone, billing anchors, multiplier snapshots,
  resets, online counts and per-user action semantics.
- No production state, dependencies, proxy operations, deployment or push.

### Task 1: Account form API adapter and explicit replies

**Files:** create `hysteria/web_api/account_routes.py`,
`hysteria/web_api/account_models.py`, `tests/test_web_api_accounts.py`;
modify `hysteria/web_api/app.py`, `hysteria/web_api/services.py`,
`scripts/check-quality.sh` only.

Add exact POST `/api/v1/admin/users/create` and
`/api/v1/admin/users/update`, registered from a focused account route group.
Group registration receives the existing bounded `dispatch_form_write` closure,
not a second request executor. Reject non-POST methods, trailing slashes and
malformed/oversized/cross-origin forms using existing admission behavior.

`LegacyPanelServices.submit_account_mutation(*, headers, path, form,
client_address, action)` accepts only `create` or `update`. Build the existing
query-free bridge; execute inside `_run_operation` with corresponding legacy
post path `/admin/add` or `/admin/update`. Preserve metadata-first read, then
authenticate administrator before invoking the account service. Unauthenticated
access raises `LoginRequired`; account route group translates it to401
`{'error':'login_required'}`. Never authorize query token or user cookie.
Update passes `(form.get('user_revision') or [''])[0]` as expected revision;
legacy query fallback remains exclusively the legacy handler's contract.
Call the accepted service once, without reconstructing validation or writes.

The JSON presenter operates on AccountMutationResult directly, with no raw
dataclass/config/draft serialization and no HTML parsing. Explicit response models:

- created/updated ->200 `{'ok':true,'outcome':'created'|'updated','user':username}`.
- invalid ->422 `{'ok':false,'error':'validation_error','code':code,'field_id':field_id}`.
- conflict ->409 `{'ok':false,'error':'revision_conflict'}`.
- not_found ->404 `{'ok':false,'error':'user_not_found'}`.

Success outcome must match requested action. Validate nonempty successful
username and empty success code/field/draft; malformed internal results500,
sanitized by outer boundary. Use strict strings and literal tags. Validation
code and field id must belong to the accepted service's exact finite outputs,
including Chinese unavailable-egress code; use exhaustive Literal models or
sets checked by the presenter. Empty field id is valid for update. Do not return
the service draft: React owns the submitted draft and retains it on errors.
No response contains passwords, token/UUID/hash, internal paths or result repr.

Successful response deliberately performs no post-commit bootstrap read. The
frontend will refetch separately; a read failure must not mislabel a completed
write as failed or encourage replay. Do not add retries or idempotency policy.
Future frontend transport errors/timeouts also mean an unknown outcome, not
proof of rollback: retain the draft and ask the operator to refresh/check before
retrying. The shared form hook aborts its client request but cannot undo a
server-side commit; its consumer must supply appropriate feedback.

- [x] TDD missing exact route/service then focused implementation.
- [x] Real temporary-state successful create/update, session invalidation,
  password/hash changes, usage/unrelated state preservation and credential
  non-disclosure; external reload/sync intercepted safely.
- [x] Auth tests anonymous/user cookie/query token before mutation; CSRF,
  duplicate form values, malformed form, size/time bounds, exact paths/methods,
  security headers and no-store. Include bounded admission reuse evidence.
- [x] Real stale revision, missing user, invalid values and duplicate creation;
  status/code matching; secret fields never returned in any response.
- [x] Critical/malformed state ->sanitized503; unexpected error ->sanitized500;
  prove no post-success state read and no duplicate invocation/retry.
- [x] Invalid internal result/output tests for success/action mismatch,
  extra credential data, unexpected validation code/field and unknown outcome.
  Explicit allowlist strips unrecognized fields, invalid required values fail.
- [x] Adopt new files in quality list; existing web_api directory packaging
  should already include them, verify without deploying.
- [x] Run new API tests plus `test_account_mutation_service.py`,
  `test_web_api_login.py`, `test_web_api_logout.py`,
  `test_web_api_password_changes.py`, `test_web_api_reads.py`;
  lint-only and `git diff --check`.
- [x] Self-review and owned local commit; full report with RED/GREEN and exact
  covering results. Independent review before React consumption.
