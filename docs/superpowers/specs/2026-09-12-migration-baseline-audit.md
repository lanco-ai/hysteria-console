# Migration baseline audit

Reference: `f7ec53f`; inspected on 2026-09-12. This records existing behavior and
coverage gaps, not completed migration acceptance. No production data was used.

## HTTP dispatch inventory

All listed reads also pass through the legacy HEAD entry point. HEAD must not
acquire unintended GET-only credential-exchange side effects after migration.
Query variants and dynamic paths require separate behavior fixtures.

| Owner | GET/HEAD paths or patterns |
| --- | --- |
| Public pages | `/`, `/login`, `/user/login`, `/logout`, `/user/logout` |
| Admin console | `/admin`, `/admin/logs`, `/admin/overview.json`, `/admin/reload-status.json`, `/admin/hysteria-update/status.json`, `/admin/incidents`, `/admin/incidents/evidence.json`, `/admin/user/{id}`, `/admin/user/{id}.json`, `/admin/daily`, `/admin/settings`, `/admin/landing-egresses`, `/admin/config`, `/admin/rules` |
| Admin reads | `/admin/usage`, `/admin/analytics.json`, `/admin/usage-history`, `/admin/usage.json`, `/admin/usage.csv`, `/admin/health`, `/admin/health.fragment` |
| User panel | `/user/change-password`, `/user/panel.json`, `/user/panel` |
| Readiness | `/healthz`: 204 only after required metadata/users/usage/daily state is readable; state failures use the existing 503/fail-closed boundary |
| Subscription routes | `/sub/*`, `/panel/*/qr.svg`, `/panel/*.json`, `/panel/*` (ordered matching; do not flatten into SPA routes) |
| Static resources | Explicit CSS, JS and font registry in `subscription_service.py` and `web_assets.py`; unknown assets remain unavailable |

Readiness handling lives in the composition root before document dispatch and
must be carried separately from the SPA. `/livez` and `/readyz` are not current
registered endpoints and must not be advertised as existing compatibility APIs.

| Owner | POST paths or patterns | Side effects to preserve |
| --- | --- | --- |
| Authentication | `/login`, `/logout`, `/user/logout`, `/user/change-password`, `/admin/change-password` | Session issuance/revocation, throttling, password generation invalidation |
| Credentials | `/panel/*/rotate-token`, `/admin/rotate-token` | Token rotation, dependent credential revocation and conflict handling |
| Users | `/admin/add`, `/admin/update` | Identity/config updates and proxy synchronization |
| User status | `/admin/pause-user`, `/admin/toggle-user`, `/admin/delete` | Revision checks, disable/remove semantics, device revocation and recovery |
| Traffic | `/admin/cycle-config`, `/admin/settlement-day`, `/admin/reset-usage`, `/admin/refresh-usage`, `/admin/reset-usage-all` | Billing anchor, preserved usage, audit records and multiplier consistency |
| Operations | `/admin/test-alert`, `/admin/hysteria-update/check`, `/admin/hysteria-update/apply`, `/admin/cost-multiplier/apply`, `/admin/cost-multiplier/auto` | Notifications, policy-gated update jobs and multiplier settings |
| Templates/rules | `/admin/config/save`, `/admin/rules/add`, `/admin/rules/delete`, `/admin/rules/raw`, `/admin/rule-pack/apply` | Existing validation, persistence and configuration side effects |
| Egress | `/user/landing-egress/select`, `/admin/landing-egress/save`, `/admin/landing-egress/delete`, `/admin/landing-egress/check`, `/admin/user-landing-access` | User authorization, registry/config changes and probes |

Source owners are `hysteria/*routes.py`; the composition-root dispatcher orders
authentication, credentials, egress, users, traffic, operations, status/deletion,
configuration and rule-pack handlers. An unknown route must not accidentally
consume an unrelated command or trigger service work.

## Request boundary findings

- Current POST processing rejects cross-origin requests except the login path,
  then parses form input, loads metadata and extracts the user revision before
  dispatch. Preserve protections; do not invent a second independent session
  scheme for the SPA.
- Some mutation responses vary with Accept: JSON success/error versus legacy
  flash redirects. Versioned API normalization does not authorize removal of
  compatibility behavior used by existing clients.
- GET/HEAD/POST catch persistent-state failures. Certain authorization failures
  also invoke static proxy fail-closed actions. Returning a generic framework
  500 is not equivalent to this behavior.
- Over-capacity requests currently receive 503 with Retry-After and no-store.
  Merely using an ASGI server is not evidence that backpressure is preserved.
- Administrator URL bearer exchange precedes document dispatch and removes the
  token via a redirect. User-panel bearer exchange has its own handler.
- API conversion cannot expose raw runtime dictionaries. Existing permitted
  subscription/QR delivery must be distinguished from list/detail DTOs.

## Browser coverage actually present

The runner executes three Node/Playwright scripts through an isolated loopback
preview with fictional data. It is not an authenticated production browser.

| Evidence | What is checked | What it does not prove |
| --- | --- | --- |
| `workspace_visual.cjs` | Login password toggle; overview five columns/actions; dialog open/cancel; sidebar collapse; no horizontal overflow at 1920/1024/390; selected config/rules/history/chart interactions | Successful writes, full screenshot equivalence, all page coverage |
| `usage_refresh_browser.cjs` | Live usage metrics, explicit history refresh and collapsed invalidation | Every usage filter, all export contracts and every error/permission combination |
| `user_panel_browser.cjs` | User metrics, subscription/copy/QR, hidden-page pause and retry | Full authenticated authorization matrix or every account state |
| `test_preview_isolation.py` | Temporary state, bounded loopback access, rejected service commands, closed server lifetime | Visual parity or production deployment compatibility |

Preview documents currently include login, overview, user panel, usage, settings,
templates, rules and a synthetic `/history` page. Home, health, incidents,
residential egress, logs, user detail, password and logout documents need fixture
coverage before migration acceptance. The synthetic history route is test-only,
not a new product route.

Preview POST always returns 405. Use isolated real HTTP/service fixtures for
mutation correctness; do not claim successful writes from button visibility.
The browser checks use assertions, not stored screenshot comparison baselines.

## Next implementation deliverable

Expand the isolated preview and parity checks without changing production
renderers or business services. Every added page must have fictional stable
fixtures; inherited filesystem/network/subprocess guards remain active.

Capture reviewable screenshot baselines under fixed browser/viewport/font/clock
conditions. Mask only known time-varying presentation after explicitly recording
it; do not mask real content or dialogs to hide differences. A passing new test
must identify its page and state, not just report a generic browser pass.

The first React slice is chosen only after this baseline coverage exists. The
new API contract must preserve the boundary behaviors above before supporting
write operations. No old renderer is removed based on a preview-only result.

## Status

- Source inventory and coverage gaps: inspected.
- Existing three browser groups: passed during the preceding design turn.
- Current audit verification: 53 tests passed in 22.27s using
  `/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_preview_isolation.py
  tests/test_admin_read_routes.py tests/test_public_read_routes.py
  tests/test_admin_console_routes.py tests/test_codex_removal.py`.
  This validates these existing contracts only, not full migration parity.
- Expanded preview, complete screenshot baseline and new-framework parity:
  not implemented or verified.
- Design review checkpoint: awaiting operator review of the September design.
- Production: unchanged by this audit.
