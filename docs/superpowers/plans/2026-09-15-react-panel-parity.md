# React Panel Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the React migration for the public, administrator, and user panels without changing the production route until feature parity, security, and rollback evidence are complete.

**Architecture:** Keep `subscription_service.py` and the existing domain/view modules as the source of truth. Add narrow FastAPI read/write adapters with explicit response models, and implement React pages as route-level feature modules that consume only those adapters. Legacy routes remain available behind the current production paths during staged preview and are removed or switched only after parity checks.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, React 18, TypeScript, Vite, Playwright browser checks, existing CSS/design tokens.

**Spec:** `docs/superpowers/specs/2026-09-12-personal-site-refactor.md`

## Global Constraints

- Keep `/admin`, `/user/panel`, domain, certificates, and 443/9444 production routing unchanged until the cutover task is explicitly approved.
- Do not expose passwords, subscription tokens, proxy credentials, admin tokens, or raw configuration in public response models.
- Reuse existing domain builders (`usage_dashboard`, `operations_views`, `incident_console`, `configuration_views`, `landing_views`, `user_views`) instead of duplicating accounting or mutation logic.
- Every new endpoint has an explicit Pydantic response model and a TypeScript runtime validator.
- Every behavior change follows a failing test → minimal implementation → passing test cycle.
- Preserve unrelated working-tree changes and keep the Codex quota removal intact.

### Task 1: Flow analytics API and React page

**Files:**
- Create: `hysteria/web_api/usage_models.py`
- Modify: `hysteria/web_api/services.py`, `hysteria/web_api/app.py`
- Create: `frontend/src/features/network-admin/usage/types.ts`, `requests.ts`, `UsagePage.tsx`
- Modify: `frontend/src/main.tsx`, `tests/react_preview_server.py`
- Test: `tests/test_web_api_usage.py`, `tests/test_usage_react_contract.py`, `tests/usage_react_browser.cjs`

**Interfaces:**
- `LegacyPanelServices.read_admin_usage(include_charts: bool)` returns `usage_dashboard.build_analytics_json_payload(...)` after admin authentication.
- `GET /api/v1/admin/usage?summary=1` returns `{ts, stats}`; the full request returns `{ts, stats, hourly_totals, heatmap, top_n}`.
- React validators reject unknown shapes, non-finite numbers, and malformed chart entries; the page renders summary cards, hourly series, heatmap, top users, and a lazy historical detail panel with retry.

- [x] Write failing API contract tests for summary/full schemas, private-field stripping, and lazy history.
- [x] Run `pytest tests/test_web_api_usage.py -q` and confirm the new route/model failure.
- [x] Add the service adapter, Pydantic models, and routes using the existing dispatch/error boundary.
- [x] Add TypeScript/parser and browser contracts for refresh and history expansion.
- [x] Implement the React page and `/__react/admin/usage` preview route using existing shell/CSS primitives.
- [x] Run targeted Python tests, frontend lint/type/build checks, and the browser contract; commit only this slice.

### Task 2: Health and incident operations pages

**Files:**
- Create: `hysteria/web_api/operations_models.py` (read models only if existing operation models cannot cover the payload)
- Modify: `hysteria/web_api/services.py`, `hysteria/web_api/app.py`
- Create: `frontend/src/features/network-admin/health/*`, `frontend/src/features/network-admin/incidents/*`
- Modify: `frontend/src/main.tsx`, `tests/react_preview_server.py`
- Test: `tests/test_web_api_health.py`, `tests/test_web_api_incidents.py`, browser contracts

**Interfaces:**
- `GET /api/v1/admin/health` exposes structured probe/status/update fields from `operations_views`.
- `GET /api/v1/admin/incidents` exposes the incident list and supported actions; mutations use existing operation services and CSRF/form rules.

- [ ] Capture failing contracts for health status, update-check actions, incident rendering, and unauthorized access.
- [ ] Add adapters/models and React pages with accessible status, loading, retry, and mutation feedback.
- [ ] Verify health probes and incident tests plus browser navigation from the sidebar.

### Task 3: Network configuration pages

**Files:**
- Create: `hysteria/web_api/config_models.py`, `frontend/src/features/network-admin/config/*`, `rules/*`, `landing-egresses/*`
- Modify: `hysteria/web_api/services.py`, `hysteria/web_api/app.py`, `frontend/src/main.tsx`, preview server
- Test: `tests/test_web_api_config.py`, `tests/test_web_api_rules.py`, `tests/test_web_api_landing_egresses.py`, browser contracts

**Interfaces:**
- Read models expose only editable template/rule/egress fields and revision metadata.
- Writes delegate to existing configuration/landing services and return explicit success, validation, conflict, and reload-pending states.

- [ ] Write contract tests before each adapter and mutation.
- [ ] Implement pages with draft preservation and conflict handling.
- [ ] Verify config files are changed only through existing services and reload status remains observable.

### Task 4: Complete user panel

**Files:**
- Create: `hysteria/web_api/user_panel_models.py`, `frontend/src/features/user-panel/*`
- Modify: `hysteria/web_api/services.py`, `hysteria/web_api/app.py`, `frontend/src/main.tsx`, preview server
- Test: `tests/test_web_api_user_panel.py`, `tests/user_panel_react_browser.cjs`

**Interfaces:**
- `GET /api/v1/user/panel` returns the structured user payload used by `user_views` without credentials.
- Password, logout, subscription/token/QR links continue to use the established user-session and same-origin write boundary.

- [ ] Test lifecycle states (active, disabled, expired, password-change-required) and private-field stripping.
- [ ] Implement the responsive user panel and password flow, then verify subscription/QR/CSV links.

### Task 5: Cutover, parity, and rollback evidence

**Files:**
- Modify: route/preview/deployment wiring identified by `rg -n "__react|REACT_PAGES|/admin" tests hysteria nginx systemd`
- Create: `tests/test_react_route_parity.py`, `docs/superpowers/plans/2026-09-15-react-cutover-checklist.md`

- [ ] Run the complete backend suite, frontend checks/build, and all browser journeys under resource guards.
- [ ] Compare every route in the feature-parity register against React preview and legacy behavior.
- [ ] Verify legacy quota endpoints remain unavailable and no browser request references retired Codex quota assets.
- [ ] Inventory Nginx 443/9444/domain/certificate configuration without modifying it.
- [ ] Prepare a reversible route switch and explicit rollback command; obtain approval before production deployment.
- [ ] After approval, deploy only built assets and route wiring, smoke-test both domains/ports, and record rollback evidence.
