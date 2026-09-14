# Overview page data implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Provide the complete authenticated overview bootstrap from shared
presentation data without changing the existing lightweight polling contract.

**Architecture:** Extract the existing overview display calculations into a
small data-only module used by the legacy renderer and a new allowlisted API.
Keep domain accounting, expiry, revisions and multiplier snapshots authoritative.
This is a prerequisite for the complete React overview, not acceptance of that page.

**Tech Stack:** Existing Python/FastAPI/Pydantic/pytest and isolated previews.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

**Acceptance (2026-09-14):** Shared data and bootstrap API accepted at `b5155d2`
(implementation `8a692db`, review coverage fix `b5155d2`). Independent spec/quality
review and scoped re-review are clear. Focused coverage18 tests, legacy subset174,
covering run383 passed plus the corrected full deployment contract102 passed;
full frontend, lint and shell gates passed. Existing dependency/datetime warnings
remain separate maintenance. No production deployment or complete React overview
acceptance is implied.

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
  Existing authorized subscription delivery is distinct from summary endpoints.
- Preserve usage units, timezone, billing anchors, multiplier snapshots,
  resets, online counts and per-user action semantics.
- Preserve existing CSS cascade and design tokens initially; replace DOM
  ownership with components, not a default component-library theme.
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444,
  runtime secrets, proxy configuration and all persistent user data.
- No dependencies, runtime/deployment/push, mutation-policy changes, CSS changes,
  production fixtures, database changes or removal of legacy rendering.

### Task 1: Shared overview presentation data and bootstrap API

**Files:**
- Create `hysteria/admin_overview_data.py`, `hysteria/web_api/overview_models.py`.
- Modify `hysteria/admin_views.py`, `hysteria/web_api/services.py`,
  `hysteria/web_api/app.py`, `scripts/check-quality.sh`, `deploy.sh`.
- Modify `scripts/hy2-deploy-recovery.py` only to register the two missing exact
  deployed source paths: admin_overview_data.py and password_change_service.py.
- Create `tests/test_admin_overview_data.py`, `tests/test_web_api_overview_page.py`.

**Interfaces:**
Extract data calculations from `admin_views.row_form` and `render_admin` into
`admin_overview_data.build_user(ctx, user, cfg, online, base_url, *, daily=None,
now=None)` and `build_page(ctx, base_url)`. Consume the existing admin Context
structurally; do not import subscription_service or create a second domain Context.
`row_form` remains signature-compatible (including unused usage_month and host),
uses build_user and a private data-to-row renderer. `render_admin` uses build_page
and that renderer; preserve all existing HTML including recovered non-sensitive
create drafts/field errors, optional landing options and conditional trend cell.
Presentation strings are escaped ONLY by HTML/React consumers, not data builders.
Do not return raw config dictionaries, rendered HTML/SVG or a generic blob.

Exact GET/HEAD `/api/v1/admin/overview-page` calls
`LegacyPanelServices.read_admin_overview_page(*, headers, path)`, authenticates
administrator before protected page loads, and runs under existing `_run_read`
snapshot/state handling and FastAPI bounded dispatch. Compute public base URL
using existing configured_public_host + safe_base_url with forwarded scheme/port,
as the legacy document does. Do not use a token query as administrator auth.

Explicit payload shape (the numeric samples below are illustrative only):

```python
{
    'cycle': {
        'key': '2026-09', 'total_used': 100, 'range': '09/01 → 09/30 · 第 14/30 天',
        'settlement_day': 1, 'length_days': 30,
        'length_min': 1, 'length_max': 365,
    },
    'users': [{
        'user': 'alice', 'tx': 20, 'rx': 80, 'used': 100, 'total': 1000,
        'percent': 10.0, 'online': 1, 'revision': 'opaque-revision',
        'disabled': False, 'max_devices': 2, 'base_quota_gb': 100,
        'quota_extra_gb': 0, 'metered': True, 'tuic_enabled': True,
        'expires_at': '', 'expired': False, 'expiry_label': '', 'note': '',
        'landing_isp': '', 'landing_region': '', 'landing_note': '', 'landing_ip': '',
        'panel_url': 'https://panel.invalid/panel/alice?token=fictional',
        'subscription_url': 'https://panel.invalid/sub/alice?token=fictional',
        'spark': [['2026-08-16', 0]],
    }],
    'landing_options': [{'id': 'node-id', 'name': 'Node name'}],
}
```

Use existing constants/helpers for every value, not sample constants. Preserve
user order. `spark` is the existing30-day dated byte pairs (or None for standalone
row_form without daily, retaining no spark-cell); full page always supplies daily
data. Keep existing rounded GB, unlimited0, expiry labels, note conversion,
landing compatibility and TUIC/metered semantics. Period total includes preserved
raw bytes scaled by the same request multiplier snapshot. No second independent
billing calculation. Page only emits public id/name for enabled landing choices.

Authenticated admin's existing dedicated panel/subscription links necessarily
contain that user's subscription token. Return ONLY these two already-authorized
URLs (never a standalone token or password); never add them to the polling payload,
logs or unauthenticated responses. Tests use only fictional tokens.

Models: one focused `overview_models.py` with explicit nested PublicModel subclasses
for cycle, bootstrap user and landing choice. Use strict string/bool/int fields,
finite numeric percent, explicit tuple[str,int] spark items, and no generic dict
fields. Existing PublicModel extra=ignore strips unexpected private fields at
every nested level. Invalid output is sanitized500, not a coercion or empty page.
Preserve legacy outputs; do not add min-length/nonnegative restrictions that
would newly reject data the existing formatter accepts.

The old `/api/v1/admin/overview` response, existing JSON summary and its builders
stay unchanged. New HEAD bodyless, no-store/securityheaders, POST405 and trailing404
use the existing route boundary. No new write endpoint or preview write permission.

- [ ] Write missing-builder/route tests and run to record expected RED:

```python
def test_overview_page_route_exists(authenticated_client):
    response = authenticated_client.get('/api/v1/admin/overview-page')
    assert response.status_code == 200
    assert set(response.json()) == {'cycle', 'users', 'landing_options'}
```

Use local fixtures patterned on test_web_api_reads.real_state; redirect ALL touched
paths, including landing registry and multiplier files, to tmp_path. Never read
runtime /root/hysteria or /etc/hysteria. Guard any external sync/reload to fail.

- [ ] Implement data extraction and API/model, retaining old row_form/render_admin
signatures and HTML. Capture representative legacy output before extraction and
compare afterward in tests with fictional clock/data: metered/normal, TUIC off,
disabled/expired, unlimited devices/quota, notes/HTML escaping, optional trend,
base+extra quota, landing compatibility, create error drafts without passwords.
Do not freeze a huge duplicated HTML fixture; assert a byte hash plus focused
content or normalized parsed output captured from pre-change baseline.
- [ ] Test complete field allowlists at every level, token URLs only in authenticated
bootstrap, no private metadata/hash/token fields; anonymous/wrong realm/querytoken401;
auth before page reads; strict invalid models500; known stale/malformed state503;
GET/HEAD/header/path/method contract; no state writes on successful reads. Verify
original polling shape unchanged and never includes bootstrap secrets/metadata.
- [ ] Test multiplier changes during one call cannot mix row/period/spark scaling;
reuse existing request snapshot, test preserved total and cycle boundaries.
Check empty users and enabled/disabled landing choices and safe URL helpers.
- [ ] Add new imported module to existing deploy source registration and quality
  adoption, without executing deployment or editing nginx/runtime files.
  Keep the recovery helper exact allowlist synchronized, including the existing
  password_change_service omission found by the full regression gate.
- [ ] Run focused tests while iterating, then covering gates:

```bash
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_admin_overview_data.py tests/test_web_api_overview_page.py tests/test_web_api_reads.py tests/test_form_recovery.py tests/test_sparkline.py tests/test_share_panel_and_landing.py tests/test_operator_concurrency_regressions.py tests/test_product_ux_regressions.py tests/test_new_features.py tests/test_usage_page.py tests/test_landing_user_flow.py tests/test_preview_isolation.py tests/test_deploy_durable_recovery.py
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
bash -n deploy.sh
git diff --check
```

- [ ] Self-review, commit owned files locally, report exact RED/GREEN and covering
results plus concerns. Independent review before acceptance. Do not call the
React overview complete: mutation transport and React interactions follow.

## Follow-on sequence

After this data contract is accepted, extract/adapt overview account and operation
mutations with retained locks/revisions/revocation results; then implement the full
React overview and live refresh. Usage, health, incidents, templates, rules, egress
and complete user panel remain separate parity slices under the approved spec.
