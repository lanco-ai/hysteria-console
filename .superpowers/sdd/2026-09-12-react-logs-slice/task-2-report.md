# Task 2 report: React logs vertical slice

## Status

Implemented the first controlled React administrator page at
`/__react/admin/logs`. The page owns the complete shell DOM, reads the existing
same-origin `/api/v1/session` and `/api/v1/admin/logs` endpoints with real
cookies, and retains the legacy stylesheet, fonts, navigation destinations,
table structure, labels, and logout POST form.

This is not a production cutover. Public `/admin/logs` and every navigation
destination remain legacy-rendered by design.

## TDD evidence

The browser acceptance test was added before any React component. Against the
existing preview, this failed at the intended missing boundary:

```text
$ node tests/react_logs_browser.cjs
AssertionError [ERR_ASSERTION]: controlled React entry must be available
404 !== 200
```

The Python preview test was also run before its adapter existed and failed
during collection because `tests.react_preview_server` was missing. Later HEAD
coverage failed because the preview omitted `Content-Length`; the adapter then
added representation-length handling and the test passed.

## Implementation

- Exact pinned React 19.3.0, React DOM 19.3.0, TypeScript 7.0.2, Vite 8.3.0,
  React plugin 6.1.1, and React type 19.3.0 dependencies and lockfile.
- Strict TypeScript build with Vite base `/static/react/`, hashed JavaScript,
  root-level `manifest.json`, and no generated CSS. The built page retains the
  external `/static/style.css` URL.
- Typed session/log DTO validation and a shared one-shot request hook. HTTP,
  malformed JSON, schema, network, and 10-second timeout failures remain error
  states with retry; request cleanup aborts stale work and clears prior data.
- React shell parity for grouped navigation, current item, badge, skip link,
  logout form, desktop collapse persistence, `hy2.sidebar-motion`, mobile
  open/close/scrim/Escape behavior, focus trap/return, inert regions, resize,
  and reduced-motion CSS behavior. No legacy shell scripts load on this page.
- Loopback-only preview using existing filesystem/network/process guards,
  temporary fictional state, real `LegacyPanelServices` + FastAPI TestClient,
  explicit real admin/user sessions, exact built entry, manifest-selected
  local assets, legacy comparison pages, and 404 behavior for unknown assets
  and APIs.
- Python transport tests use a complete temporary miniature dist fixture, so
  clean backend-only CI does not depend on Node. Browser acceptance refuses a
  missing real build, and `check:react` builds before starting it.

## Verification

Final frontend quality command:

```text
$ PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend
CSS build check: passed
ESLint: passed
Stylelint: passed
Node gates: 7 passed, 0 failed
Legacy browser scripts: 3 PASS lines
TypeScript: passed
Vite: 20 modules transformed; hashed JS emitted; no CSS emitted
React browser: PASS: React logs real API, auth boundaries, recovery,
shell parity, mobile focus, and preferences
```

Focused Python/static checks:

```text
$ PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
All checks passed; 81 files and subscription_service.py formatted

$ PATH=/tmp/hy2-quality-venv/bin:$PATH python -m pytest -q --tb=short tests/test_react_preview.py
3 passed, 2 dependency deprecation warnings

$ bash -n scripts/check-quality.sh
exit 0

$ npm ls --depth=0
all requested exact versions installed

$ git diff --check
exit 0
```

The focused Python tests were separately run with `frontend/dist` moved out of
the checkout: 3 passed. This verifies clean-checkout backend collection does
not silently depend on ignored build output.

The parent controller then ran the combined backend quality entry against the
shared tree: `scripts/check-quality.sh` passed lint/format and all 1534 tests in
233.14 seconds. Its 71 warnings are the already tracked httpx/AnyIO and datetime
deprecations; there were no test failures.

## Browser coverage and screenshots

Browser acceptance uses the real built artifact and real API for its primary
flow, with narrowly targeted Playwright interception only for empty, HTTP 503,
malformed JSON, invalid schema, and delayed-response cases. The delayed test
advances Playwright's controlled clock through the real 10-second timeout and
then proves an aborted stale response cannot overwrite a successful retry.

Paired legacy/React screenshots were captured and inspected at 1920, 1024, and
390 pixels under:

`.superpowers/sdd/2026-09-12-react-logs-slice/task-2-screenshots/`

The 1920 and 390 pairs are byte-identical. The 1024 pair has different PNG
hashes but matched headings, all cells, visible navigation, and the sidebar,
topbar, content, and section bounding boxes within two pixels; side-by-side
visual inspection found no visible difference.

## Known limits

- Controlled preview only; no nginx, production route, deployment, runtime
  data, domain authorization, legacy renderer, or production CSS was changed.
- Other pages remain ordinary legacy navigation targets. There is no React
  overview or half-implemented placeholder page.
- The logs page remains a one-shot read with no artificial polling.
- FastAPI TestClient currently emits upstream Starlette/httpx deprecation
  warnings; they do not affect the test result and were not suppressed.
