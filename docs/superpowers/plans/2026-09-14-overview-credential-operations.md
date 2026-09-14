# Overview credential operations implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track checkboxes.

**Goal:** Connect administrator subscription rotation and deletion to the new
overview without weakening durable revocation or misreporting pending outcomes.
**Architecture:** Extract the two existing domain flows into narrow services,
retain legacy presenters, and extend the accepted operation API group. Leave
the distinct end-user rotation receipt/session-recovery flow untouched.
Existing `credential_service.CredentialService` remains authoritative for save
durability detection and end-user receipts; inject/use its established helpers,
do not copy `_save_users_for_rotation` or receipt logic into the new service.
**Tech Stack:** Existing Python/FastAPI and isolated pytest state.
**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md
**Prerequisite:** overview-basic-operations independently accepted first.

**Acceptance (2026-09-14):** `a8a63e7` independently accepted for shared services
and operation APIs. Covering493passed with66 inherited deprecations; lint,
format, shell and diff gates passed. User-panel rotation retained. React
refetch/replay handling and full page acceptance remain separate work.

## Global Constraints

- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- API responses use explicit field allowlists: never serialize an entire runtime
  object containing password hashes, proxy secrets or account credentials.
  Existing authorized subscription delivery is distinct from summary endpoints.
- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- No production state, dependencies, real proxy operations, deployment or push.

### Task 1: Credential and deletion services plus operation API

**Files:** create `hysteria/admin_credential_service.py`,
`hysteria/user_deletion_service.py`, `tests/test_admin_credential_services.py`,
`tests/test_web_api_credential_operations.py`. Modify
`hysteria/credential_routes.py`, `hysteria/admin_user_delete_routes.py`,
`hysteria/subscription_service.py`, `hysteria/web_api/services.py`,
`hysteria/web_api/operation_routes.py`, `hysteria/web_api/operation_models.py`,
`scripts/check-quality.sh`, `deploy.sh`, `scripts/hy2-deploy-recovery.py`.

Explicit frozen/slotted service interfaces:
```python
@dataclass(frozen=True, slots=True)
class CredentialMutationResult:
    outcome: Literal['success', 'not_found', 'conflict']
    username: str = ''
    code: str = ''
    subscription_token: str = field(default='', repr=False)

# AdminCredentialService.rotate(*, form, expected_revision)
# UserDeletionService.delete(*, form, expected_revision)
# Both return CredentialMutationResult (defined in admin_credential_service).
# Explicit storage/domain dependencies, no Handler/HTTP or root import.
```

Root factories `_admin_credential_service(audit)` and `_user_deletion_service()`.
Rotation's audit callback accepts action,target,before,after; the legacy route
resolves `handler.get_admin_actor()` BEFORE invoking the service and captures it
in the callback. API authenticates then resolves shared `_admin_actor(request)`
before invoking the service. Do not move this lookup after credential commit.

Extract `_rotate_admin` body between authenticated actor lookup and presentation
into `rotate`, with its current locked existence/type/revision checks, generation
calculation, token/UUID creation, durable WAL prepare BEFORE users save,
`_save_users_for_rotation` uncertain-durability result, critical sync handling,
unlocked fail-closed/reload/stop/retry records/kick records/audit order intact.
Compute flash prefix with exact current precedence:
`err:rotated_retry`, `err:rotated_pending`, `err:rotated_static_pending`, `rotated`.
Return the captured new token privately for legacy authorized link construction;
never return raw config or expose token through repr or new operation JSON.
All existing retry helper decisions and exceptions remain authoritative.

Extract deletion body with its exact WAL prepare/random identity/generation,
users removal/save error capture, sync/history cleanup attempts and unlocked
`_attempt_revocation_side_effects`. Preserve `err:deleted_retry` versus `deleted`.
Save/replace uncertainty is not ordinary rollback and must not be converted into
an exception or retried synchronously by API. No audit added to deletion.

Legacy handlers retain auth, safe next URL, exact error/redirect/JSON responses,
fresh row/reload status and authorized rotation links. Do not parse rendered HTML
or invoke a legacy Handler through a response-capture bridge from FastAPI.
Credential Context also serves `_rotate_panel`; keep all still-used dependencies
and that entire user rotation behavior intact, adding only the admin service seam.

Extend accepted operation dispatcher with exact POST action suffixes
`rotate-token` and `delete` at `/api/v1/admin/operations/`. First body user/revision,
query-free cookie auth, existing bounded form dispatcher, `_run_operation` with
legacy `/admin/rotate-token` or `/admin/delete` failure classification.

Explicit response contract:
```python
# success200; exact finite code values, no post-success state read:
{'ok': True, 'action': 'rotate-token', 'user': 'fixture', 'code': 'rotated'}
{'ok': True, 'action': 'delete', 'user': 'fixture', 'code': 'err:deleted_retry'}
# conflict409 and missing404 reuse accepted operation errors.
```
Require nonempty strict user and action-specific allowed code. Unknown outcome,
action/code mismatch or malformed required data ->sanitized500. Only minimal
allowlisted model fields reach JSON; the private result token never does.
Pending result remains success with explicit code, never generic failure or
automatic retry. The frontend refetches authorized bootstrap separately to update
links and rows; if it fails, retain pending/unknown feedback and suppress replay.

- [ ] TDD missing interfaces/routes first, then extraction and focused GREEN.
- [ ] Real fictional state tests for successful rotation/deletion, missing/type/
  stale revision no writes, generated credentials/generation invalidation,
  exact WAL-before-save order, protected token exclusion and actor-before-write.
- [ ] Characterize every current pending branch: save durability uncertainty,
  critical sync failure, reload scheduling failure and fail-closed success/failure,
  retry-record/kick-record uncertainty; exact code precedence and exception
  propagation. Deletion save/sync/history/sideeffect failures retain durable tasks.
- [ ] Trace lock boundaries and no real system effects; preserve existing
  revocation/credential legacy tests and explicit user-panel flow coverage.
- [ ] New API all success/pending codes, wrong realm/query token, stale/missing,
  sanitized state/unexpected failure, result/action mismatch, strict allowlist,
  no post-commit reload-status/bootstrap reads or retry, exact method/path/headers.
- [ ] Register root modules at every deploy inventory and exact recovery paths;
  quality adoption. No actual deployment or live token changes.
- [ ] Run new tests plus existing `test_operator_concurrency_regressions.py`,
  `test_admin_user_routes.py`, `test_share_panel_and_landing.py`,
  `test_reliability_regressions.py`, `test_web_api_operations.py`,
  `test_deploy_durable_recovery.py`, `test_credential_routes.py`,
  `test_credential_service.py`, `test_revocation_service.py`,
  `test_rotation_recovery.py`, `test_meta_token_concurrency.py`,
  `test_new_features.py`; lint-only, shell syntax, diffcheck.
- [ ] Owned local commit, self-review, detailed RED/GREEN and covering report;
  independent review before React integration.
