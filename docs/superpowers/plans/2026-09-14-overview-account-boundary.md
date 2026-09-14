# Overview account mutation boundary implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Share authoritative user creation and plan editing between legacy HTTP
and the forthcoming overview API without copying write policy.

**Architecture:** Move domain validation and locked writes from account routes to
a transport-free AccountMutationService. Legacy routes remain thin authenticated
presenters. The API consumer follows after this seam is independently accepted.

**Tech Stack:** Existing Python dataclasses, temporary-state pytest tests.

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
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444,
  runtime secrets, proxy configuration and all persistent user data.
- No dependency, database, CSS, runtime, deploy, push or new password policy.
- Tests use exclusively temporary fictional state, never production accounts or
  proxy operations. No actual reload/kick/sync against live services.

### Task 1: Extract account orchestration with legacy parity

**Files:**
- Create `hysteria/account_mutation_service.py`, `tests/test_account_mutation_service.py`.
- Modify `hysteria/admin_account_routes.py`, `hysteria/subscription_service.py`,
  `scripts/check-quality.sh`, `deploy.sh`.
- Modify `scripts/hy2-deploy-recovery.py` for the new source's exact allowlist entry.
- Extend `tests/test_form_recovery.py`, `tests/test_operator_concurrency_regressions.py`
  only where needed for actual routed parity; do not weaken existing assertions.

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class AccountMutationResult:
    outcome: Literal['created', 'updated', 'invalid', 'conflict', 'not_found']
    username: str = ''
    code: str = ''
    field_id: str = ''
    draft: dict | None = field(default=None, repr=False)

# Frozen/slotted service with explicit existing dependencies (as
# PasswordChangeService does), no handler, HTTP, render or framework dependency.
def create(self, *, form: Mapping[str, list[str]]) -> AccountMutationResult:
    ...

def update(self, *, form: Mapping[str, list[str]], expected_revision: str) -> AccountMutationResult:
    ...
```

The displayed signatures define interfaces, not placeholder implementations.
Move existing bodies from admin_account_routes._add/_update; transform each
presentation return into the corresponding result. Existing account field parsing
and validation are the source of truth; do not replace with a different schema.
`subscription_service._account_mutation_service()` composes the dependencies.
Legacy `admin_account_routes.Context` shrinks to the service factory plus
is_logged_in/configured_public_host/safe_base_url/render_admin. Keep `handle_write`
signature/path dispatch and unknown-route false/no-touch behavior unchanged.

Only authorized route callers invoke the domain service. Metadata-first and
same-origin/form decoding remain in Handler._do_POST. Service tests call it in an
explicit isolated context; this service is not an externally exposed auth bypass.

Map old outcomes exactly:
- create success -> created(username), legacy302 `/admin?msg=created+<username>`.
- update success -> updated(username), legacy302 `/admin?msg=updated+<username>`.
- missing update user -> not_found, legacy302 `/admin?msg=user+not+found`.
- update revision/config conflict -> conflict with the same current safe draft;
  legacy handler.send_user_state_conflict('/admin', draft=...).
- create validation -> invalid(code=existing message, field_id=existing DOM id,
  draft=existing explicit non-sensitive create_draft); legacy422 recovered form.
- update invalid parsing/note/date/landing -> invalid(code=existing message);
  legacy422 render_admin flash, not recovered create draft.
- update panel/proxy password length failures -> invalid with existing exact
  err:code; legacy redirects exactly as before, not a blanket422.

Codes retain exact existing strings, including `user empty`,
`user_exists_use_reset_token`, all `err:...` values and the existing Chinese
unavailable-egress message. HTTP presenters handle encoding and rendering; no
HTTP statuses/redirects/HTML in the service. Both service and route must avoid
ever echoing password fields, token inputs, raw config or hashes into a result.

Create must preserve: first form values, panel password untrimmed vs connection
password trimmed; guest/TUIC presence booleans; username/quota/expiry/note bounds;
first enabled-egress check then independent locked registry recheck; duplicate
username error instead of updating/resetting an existing account; generated
token/UUID, max_devices2, optional initial landing authorization; original raw
users text captured inside lock; save+sync and exact raw-text rollback on sync
exception, best-effort resync then re-raise; session deletion and reloads outside
lock. No novel transaction/recovery policy in this extraction.

Update must preserve: max_devices upper bound100 (HTML currently999 does not
authorize loosening backend); quota/base extra limits; expiry/note/landing parsers;
password checks before users load; existence/type/revision inside usage lock;
do not alter subscription token/UUID unless missing; do not clear usage histories;
remove legacy plaintext password and absent optionalfields; preserve unrelated
configuration including disabled state; panel password sets must-change and
revokes that user's sessions after commit; reload scheduling outside lock.

Explicit service dependencies may retain imports for existing landing registry,
atomic rollback and xray/TUIC reload helpers, as original code does. No service
import of subscription_service and no Handler-shaped callback object. Preserve
exception propagation and reload order, including committed-but-error behavior.
No exception swallowing beyond the existing best-effort rollback resync.

- [ ] Add tests for missing service, run to expected RED before source extraction:

```python
def test_create_does_not_return_password_or_token_fields(account_service, good_form):
    result = account_service.create(form=good_form)
    assert result.outcome == 'created'
    assert result.username == 'fixture_new'
    assert result.draft is None
    assert 'fixture-panel-password' not in repr(result)
```

Use existing form_recovery and operator-concurrency fixtures as behavioral
references. Fixture construction redirects every touched state file and
monkeypatches external sync/reload with recording safe doubles. Use real temporary
hashing/config writes and real session invalidation for accepted success cases.

- [ ] Extract service and wire legacy routes. Add service table tests for every
validation/first-value/boolean branch and matching safe draft/field id. Assert
no protected writes on invalid/conflict, quota and unrelated state preservation,
generated credential fields never returned, absence/presence of optional hashes,
and legacy route responses still exact. Trace lock-sensitive side effects to
assert locked checks/writes and outside-lock session removal/reload ordering.
- [ ] Test real add/update, existing-user rejection, stale revision, change between
initial and locked reads, initial egress disappears under lock, raw users text
rollback+resync after sync failure, best-effort resync failure rethrows original,
save failure propagates, session invalidation errors do not silently turn success.
Do not add retry or compensate a committed update beyond existing behavior.
- [ ] Register source module in deploy source list and adopted quality checks.
  Register its exact deployed source path in the durable recovery helper too;
  keep the dynamic frozen-artifact equality test unchanged.
- [ ] Run focused tests while iterating; final covering gates:

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_account_mutation_service.py tests/test_admin_user_routes.py tests/test_form_recovery.py tests/test_operator_concurrency_regressions.py tests/test_landing_user_flow.py tests/test_new_features.py tests/test_reliability_regressions.py tests/test_deploy_durable_recovery.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
bash -n deploy.sh
git diff --check
```

- [ ] Self-review and local owned commit; report exact RED/GREEN and gate output.
Independent review required before exposing this service through a new API.

## Follow-on contract

The next API slice calls the accepted service from an authenticated, bounded
FastAPI form adapter and presents explicit JSON success/validation/conflict.
It will not reimplement validation or parse HTML to infer mutation results.
Remaining overview operations and the React page follow as separate slices.
