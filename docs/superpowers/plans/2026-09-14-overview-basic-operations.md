# Overview basic operations implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track checkboxes.

**Goal:** Connect traffic/cycle and user status operations through shared services
and bounded FastAPI endpoints, preserving legacy HTTP behavior.
**Architecture:** Extract domain orchestration from admin traffic/status routes;
keep HTTP presenters in those routes and add a focused API route group. Audit
callbacks retain their existing execution order without passing a Handler into
the domain service. React consumes explicit minimal outcomes then refetches.
**Tech Stack:** Existing Python dataclasses, FastAPI/Pydantic, pytest fixtures.
**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- Preserve usage units, timezone, billing anchors, multiplier snapshots,
  resets, online counts and per-user action semantics.
- Preserve confirmation, loading, disabled, empty, validation-error, conflict
  and retry states. Canceling a destructive dialog sends no mutation.
- No production state, dependencies, real proxy operations, deployment or push.

### Task 1: Shared traffic/status services and HTTP adapters

**Files:** create `hysteria/overview_mutation_result.py`,
`hysteria/traffic_mutation_service.py`, `hysteria/user_status_service.py`,
`hysteria/web_api/operation_models.py`, `hysteria/web_api/operation_routes.py`,
`tests/test_overview_operation_services.py`, `tests/test_web_api_operations.py`.
Modify `hysteria/admin_traffic_routes.py`, `hysteria/admin_user_status_routes.py`,
`hysteria/subscription_service.py`, `hysteria/web_api/services.py`,
`hysteria/web_api/app.py`, `scripts/check-quality.sh`, `deploy.sh`,
`scripts/hy2-deploy-recovery.py`. Existing tests may be extended, never weakened.

Interfaces (implement real bodies by extracting the current route bodies):

```python
@dataclass(frozen=True, slots=True)
class OverviewMutationResult:
    outcome: Literal['success', 'invalid', 'not_found', 'conflict']
    username: str = ''
    code: str = ''
    day: int | None = None
    disabled_until: str = ''

# Frozen services with explicit storage/domain dependencies, no HTTP or root import.
# TrafficMutationService.configure_cycle(*, form)
# TrafficMutationService.reset_user(*, form, expected_revision)
# TrafficMutationService.refresh_user(*, form, expected_revision)
# TrafficMutationService.reset_all()
# UserStatusService.pause(*, form, expected_revision)
# UserStatusService.toggle(*, form, desired, expected_revision)
# Each returns OverviewMutationResult; dependency audit(action,target,before,after).
```

Expose root `_traffic_mutation_service(audit)` and `_user_status_service(audit)`.
Extract existing Handler actor/logging bodies verbatim into root `_admin_actor(request)`
and `_write_reset_log(request, actor, action, target, before, after)` helpers;
Handler methods delegate to them. Legacy route audit callback resolves actor at
the original AFTER-mutation point. New API uses the query-free request bridge
and the same helpers. Do not move actor lookup ahead of traffic/status writes;
token rotation has its own separate ordering and is not in this task.

Shrink old Context objects to service factories plus actual presentation helpers.
Keep path dispatch/unknown false, authentication, `safe_admin_next`, `with_flash`,
all existing redirects, status/content/JSON shapes and `_send_toggle_json` behavior.
Legacy success may still render the old post-write payload; the new API does not
perform such a read. API exceptions mean unknown outcome where a commit preceded
the exception; no added rollback/retry policy.

Traffic extraction:
- Cycle first day value int1..28; blank length passes None; nonblank uses existing
  CYCLE_LENGTH bounds. Exact errors settlement_invalid/cycle_length_invalid.
  `_update_cycle_meta` retains locked metadata reread; sync users under usage lock;
  xray then TUIC reload outside. No audit added to cycle.
- Reset/refresh preserve existence/revision checks inside usage lock, one now,
  before raw usage, zero cycle/daily/hourly, quota-only alert-dedup clear, sync,
  unlocked reloads, then audit. Refresh alone banks preserved raw bytes before
  clearing. Share the common implementation with only this explicit distinction;
  do not change empty-user/type/revision behavior.
- Reset-all preserves per-user before/after map, order and audit action/target.
- Services return success (username for per-user; day for cycle; empty for all),
  not_found/conflict or invalid with exact `err:` codes; no raw configs/counters.

Status extraction:
- Pause parses minutes using existing parser/default60/bounds1..1440, computes
  until before lock, requires existing dict and matching revision, saves disabled
  and disabled_until, syncs under lock. Session deletion, xray/TUIC reload, kick,
  audit remain outside lock in that order; return until text.
- Toggle accepts only disabled/enabled; existing dict/revision checks, save
  disabled/remove disabled_until/sync under lock. Disable revokes sessions then
  reloads/kicks/audits; enable reloads/audits without revocation or kick.
- Preserve exceptions and committed state; no swallowing or new compensation.

Exact new endpoints (all POST except the last):
`/api/v1/admin/operations/cycle`, `/reset-usage`, `/refresh-usage`,
`/reset-usage-all`, `/pause-user`, `/toggle-user` under the same operations prefix.
GET/HEAD `/api/v1/admin/reload-status` returns the explicit three booleans
`pending`, `xray`, `tuic`, is read-only and authenticated, bodyless HEAD.
Service method `read_admin_reload_status(*, headers, path)` authenticates inside
`_run_read` before invoking the existing `_static_reload_status` helper.

`LegacyPanelServices.submit_overview_operation(*, headers,path,form,client_address,action)`
whitelists the six exact action suffixes; metadata-first, admin-cookie auth,
then accepted service inside `_run_operation(post_path=corresponding legacy path)`.
Use first body `user_revision`, and for toggle first body `desired`; legacy query
fallback stays unchanged in legacy routes. New group registers against existing
`dispatch_form_write` and translates LoginRequired401. No second dispatcher.
Pass the existing `dispatch` closure to that same group for reload-status GET/HEAD;
it must share the same capacity bound, off-thread execution and error boundary,
not call the synchronous reload-status service directly from the async route.

Explicit response contract:
```python
# success200 (all five keys always present; no post-commit state reread):
{'ok': True, 'action': 'reset-usage', 'user': 'fixture', 'day': None, 'disabled_until': ''}
# invalid422 (only codes from cycle/desired validation):
{'ok': False, 'error': 'validation_error', 'code': 'err:settlement_invalid'}
# conflict409 / missing404:
{'ok': False, 'error': 'revision_conflict'}
{'ok': False, 'error': 'user_not_found'}
```
Success action literals must match requested operation. Strict fields and
operation-dependent invariants: day only cycle, until only pause, user required
per-user and empty for cycle/all. Allowed invalid codes are
`err:settlement_invalid`, `err:cycle_length_invalid`, `invalid_desired`; reject
unknown/mismatched internal outcomes as sanitized500. Never serialize audit
maps, service dictionaries or secrets. State failures503, auth401, CSRF403 and
request bounds/status/header policies reuse existing API boundary.
Check domain metadata with strict types, not truthiness: e.g. a success code
must be a string equal to `''`; None/0/False/containers are not empty strings.

- [ ] TDD missing service/route tests, then focused implementation and GREEN.
- [ ] Temporary-state service tests: reset vs refresh total/preserved arithmetic,
  history clearing and alert dedup; reset-all maps; cycle locked metadata merge;
  minutes/default/bounds; pause/toggle state, session revoke/kick differences;
  lock trace proving writes/sync inside and reload/kick/audit outside.
- [ ] Invalid/missing/stale paths no protected writes, first form values,
  validation before lock, sync/session/reload/audit failures propagate and do not
  trigger duplicate operations or newly invented rollback. Preserve old tests.
- [ ] API real-state success for each operation, anonymous/user/query token denial,
  invalid/conflict/missing outcomes, output allowlists, sanitized exceptions,
  malformed/oversized/cross-origin forms and exact paths/methods/security headers.
  Prove admin auth precedes writes and no post-success bootstrap/read/retry.
- [ ] Reload read auth, bool-only model, strict invalid payload500, unchanged
  temporary files, no scheduling/reload calls, GET/HEAD/security coverage.
- [ ] Register all new root modules at every deploy source inventory and exact
  recovery path; web_api directory inclusion unchanged. No real deployment.
- [ ] Run new suites plus `test_operator_concurrency_regressions.py`,
  `test_admin_user_routes.py`, `test_new_features.py`, `test_form_recovery.py`,
  `test_reliability_regressions.py`, `test_web_api_accounts.py`,
  `test_deploy_durable_recovery.py`; lint-only, bash-n, diffcheck.
- [ ] Local owned commit and detailed TDD/gate report. Independent review before
  subsequent credential operations and React overview consumption.
