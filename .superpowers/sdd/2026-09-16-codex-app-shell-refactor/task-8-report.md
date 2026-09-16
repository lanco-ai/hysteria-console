# Task 8 report — stale workbench test-contract audit

## Scope

Updated only stale test contracts exposed by the workbench migration. No
production source files changed.

## Changes

- `tests/test_react_route_parity.py` now verifies the client-side dynamic
  `/admin/user/<uid>` matcher and the isolated preview's dynamic user-detail
  branch. It no longer requires the removed `demo_alex` fixture literal in
  `main.tsx`.
- `tests/react_logs_browser.cjs` now verifies that anonymous and non-admin
  sessions remain in the shared workbench frame and receive the
  `LoginModal` headed “登录控制台”. Existing assertions still verify that the
  admin identity and log table are not exposed.

## Root cause

The route implementation was intentionally generalized to match any single
user-detail segment. Separately, the unified Shell moved protected-route login
from inline, standalone links into its modal. Both old assertions therefore
described retired implementation details rather than the current route and
access boundaries.

## Validation

- Before update: `pytest -q tests/test_react_route_parity.py` failed at the
  obsolete literal `'/__react/admin/user/demo_alex'` assertion.
- `pytest -q tests/test_react_route_parity.py`: **4 passed**.
- `REACT_BROWSER_TEST=react_logs_browser.cjs ... tests/run_react_browser.py`:
  **passed**.
- `pytest -q tests/test_react_route_parity.py
  tests/test_workbench_frontend_contract.py tests/test_workbench_routes.py
  tests/test_react_navigation.py`: **26 passed**.
- A full `tests/run_react_browser.py` run passed logs, home, and login suites,
  then stopped at the pre-existing unrelated
  `tests/react_logout_browser.cjs:81` assertion (`inert`: expected `null`,
  received `''`). This audit intentionally did not change logout coverage.
- `git diff --check`: **passed**.

## Production changes

None.

## Follow-up: password-page access boundary

The full browser audit subsequently reached an additional stale assertion in
`tests/react_password_pages_browser.cjs`. Its anonymous `/admin/settings`
scenario expected a document navigation to `/login`; protected admin routes
now retain the shared Shell and open the “登录控制台” `LoginModal` in place.

The scenario now waits for that dialog and verifies one `.app` frame with no
protected `.settings-page` or administrator password form. All other
password-page lifecycle behavior remains unchanged.

- Before update: the focused suite waited for the retired `/login` navigation.
- `npm run build:react`: **passed**.
- `REACT_BROWSER_TEST=react_password_pages_browser.cjs ...
  tests/run_react_browser.py`: **passed**.

## Follow-up: normalized preview-route contracts

The full pytest run also exposed eight source contracts that still required
`main.tsx` to contain the preview-only `/__react/...` literals. The client now
normalizes that prefix before matching its routes, so its route registrations
correctly use canonical paths while `react_preview_server.py` retains the
preview fixtures.

Updated only these test contracts:

- config, health, incidents, landing, rules, and usage assert the canonical
  `/admin/...` registration in `main.tsx` and the `/__react/...` fixture in the
  preview server;
- user detail asserts the canonical dynamic `/admin/user/<uid>` matcher and
  keeps the preview fixture assertion;
- user panel asserts the canonical `/user/panel` registration and its preview
  fixture.

- Before update: the eight affected contracts failed because the retired
  preview literals are intentionally absent from `main.tsx`.
- `pytest -q tests/test_config_react_contract.py tests/test_health_react_contract.py
  tests/test_incidents_react_contract.py tests/test_landing_react_contract.py
  tests/test_rules_react_contract.py tests/test_usage_react_contract.py
  tests/test_user_detail_react_contract.py tests/test_user_panel_react_contract.py`:
  **19 passed**.
- `git diff --check`: **passed**.
