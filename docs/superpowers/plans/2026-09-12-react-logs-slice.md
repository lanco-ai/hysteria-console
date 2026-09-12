# React logs vertical slice implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Render the existing administrator logs page in React with its current shell, styling and real FastAPI data in an isolated preview.

**Architecture:** Extract the existing log-read presentation data once, share it between the old renderer and typed API, then replace full-page DOM ownership for one controlled preview entry. Other navigation remains normal links to existing pages; no dead substitute buttons or duplicate legacy shell scripts.

**Tech Stack:** React/React DOM 19.3.0, TypeScript 7.0.2, Vite 8.3.0, @vitejs/plugin-react 6.1.1, @types/react and @types/react-dom 19.3.0 (npm metadata verified 2026-09-12). Existing Playwright and Python test environment. Existing CSS/fonts, no new theme or CDN.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Preserve existing layout, labels, seven log columns, ordering, limits, actions and accessibility. No product-feature additions.
- Do not alter production routing, auth stores, proxy configuration, deployment scripts or runtime data.
- Build artifacts are local and versioned; no production Vite development server, public API development port, external font/CDN dependency or additional Node server is required.
- During coexistence, one frontend owns each page's DOM. Do not run legacy polling and React polling against the same rendered page.
- API summaries use explicit allowlists. Browser errors never become successful empty data.

### Task 1: Shared logs data and authenticated FastAPI read

**Files:** Create hysteria/reset_log_data.py and tests/test_reset_log_data.py. Modify operations_views.py, web_api/services.py, models.py, app.py (or its established router split), tests/test_web_api_reads.py and scripts/check-quality.sh.

**Interfaces:** `read_reset_logs(path, *, limit=300, action_label, fmt_bytes)` returns `{'limit': limit, 'rows': [...]}`. Row keys: time, actor, ip, action, target, month, detail (all strings). action uses the existing translated label; detail uses existing fmt_bytes for before.total → after.total, otherwise ''. No raw before/after objects or credentials. Existing renderer consumes this unescaped data and HTML-escapes cells exactly once; React escapes text naturally.

- [x] Write tests with a temporary JSONL log: latest lines first; retain last 300 physical lines like current renderer; ignore blank/malformed/non-object records safely; absent file gives empty rows; permission/IO failures propagate, not empty success; script-shaped strings stay data. Tests assert hand-derived values and old rendered cell text.
- [x] Run tests red before extraction, implement shared loader, remove old parser from render_reset_logs. Preserve titles, limit description, classes and empty state.
- [x] Add GET/HEAD `/api/v1/admin/logs` to established FastAPI boundary using existing admin authentication/error/snapshot/admission wrappers and typed row DTO. No query-controlled unbounded limit, no user access. Test actual temporary logs, GET/HEAD and no raw secrets. Run focused domain/API/view tests and backend gates.
- [x] Review and commit locally; no push/deployment.

Task 1 completed at `98f77a6`: 89 focused tests passed; task review approved.
The existing HTTPX/AnyIO dependency deprecation warnings remain recorded, not suppressed.

### Task 2: React full-page shell and logs view

**Files:** Create frontend/index.html, vite.config.ts, tsconfig.json, src/main.tsx, src/shared/{AdminShell.tsx,icons.tsx,navigation.ts,readResource.ts}, src/features/network-admin/logs/{LogsPage.tsx,types.ts}; tests/react_logs_browser.cjs, tests/react_preview_server.py, tests/run_react_browser.py. Modify package.json/package-lock.json, eslint.config.cjs as needed for new source checks, .gitignore for frontend/dist, scripts/check-quality.sh only if adding Python test helpers to adopted checks, and existing frontend quality entry to include typecheck/build/browser tests. Do not put application code in test preview utilities.

**Interfaces:** React calls `/api/v1/session` then `/api/v1/admin/logs` with same-origin cookies. Main mounts once into root and owns the complete has-shell page. Use the API DTO from Task 1. One explicit controlled preview path `/__react/admin/logs`; public `/admin/logs` remains legacy until cutover authorization.

The shell badge consumes an escaped public display-host bootstrap value on the root element (for example `data-public-host`), falling back to location.hostname only when absent. The isolated preview supplies `preview.invalid`, matching the old renderer. Do not hardcode the fictional hostname in application components or expose runtime secrets through bootstrap data.

```tsx
// Component contract: markup/classes match render_reset_logs and render_admin_shell.
type LogRow = {
  time: string; actor: string; ip: string; action: string;
  target: string; month: string; detail: string;
};
type LogsPayload = {limit: number; rows: LogRow[]};
```

- [x] Add exact dependency pins and lockfile using npm; retain existing quality commands. Configure strict TypeScript checking, Vite hashed assets/manifest under frontend/dist with base `/static/react/`. Link existing `/static/style.css`; do not copy/retheme CSS. Existing local fonts remain served by the preview. No Vite server needed for browser acceptance.

```html
<!-- Existing stylesheet stays owned by the Python/static release. Verify the built URL. -->
<link rel="stylesheet" href="/static/style.css" vite-ignore>
```

Vite documents `vite-ignore` for externally served HTML asset references at https://vite.dev/guide/features.html#html. Verify the actual build keeps this URL and does not create a second CSS source. Existing font URLs are `/static/fonts/inter-var.woff2` and `/static/fonts/jetbrains-mono.woff2`.
- [x] Write failing browser tests before components: real authenticated logs show seven columns and fixture row, empty rows show 暂无日志记录, anonymous session shows login action without privileged content, usid-only cannot render logs. Match current sidebar groups/links/active item and title/badge.
- [x] Implement full React shell using console_shell_views.py markup and static/shell.js behavioral inventory: hy2.sidebar persistence, desktop collapse, mobile breakpoint <=880, open/close/scrim/Escape, focus return, focus trap, inert background/closed sidebar, skip link, resize handling, reduced-motion/current motion preference. Preserve logout POST form; no legacy shell.js or preferences script loads on React page.
- [x] Implement logs page and shared read hook with loading/error/empty states; HTTP and JSON-validation failures are errors with retry, not empty tables. Timeout aborts fetch; cleanup/retry ignores stale responses. Clear privileged data on authentication failure. No artificial polling added to a formerly non-polling logs page.
- [x] Build an isolated same-origin preview using existing filesystem/network/process guards, fictional logs and real FastAPI TestClient adapter. Serve only exact built entry and manifest-selected local assets; unknown API/assets stay 404. Use explicit fictional browser session cookies from temporary state, not disabled authentication. Keep existing legacy pages available for navigation/comparison. Never seed/access runtime files.
- [x] Run browser tests against built React with real API, plus targeted fault interception for delayed/failed JSON responses to verify retry/cancellation. Check mobile focus/keyboard and desktop saved preference. Compare legacy and React headings, cell text, visible links and key bounding boxes at 1920/1024/390; capture both screenshots for inspection. All scripts/network failures must be surfaced, not suppressed wholesale.
- [x] Run typecheck, bundle build, frontend quality command, focused Python preview/API tests and CSS build consistency. Review and local commit. Document controlled entry and limitations; do not call the full project migrated.

## Remaining full-goal scope

Task 2 completed at `27be0c3` with independent review approval. Parent reran the
complete frontend quality entry successfully and inspected paired screenshots;
1920/390 PNG pairs are byte-identical, 1024 key geometry/content matches.
Combined backend verification passed 1534 tests before the final extra
bootstrap-escaping test was added; all 3 final preview tests then passed
separately. Existing 71 combined-suite deprecation warnings remain recorded.
Minor review follow-ups are explicit row waits, consistent browser-error
collection, and discoverable preview documentation; the next public-home slice
includes those shared-harness/documentation improvements.

Logs are the first complete read-only slice, not a replacement for overview/user/traffic/configuration functionality. Migrate remaining pages and writes with authorization/CSRF/revision/refresh parity, then paired release/staging validation, explicitly approved cutover and old-render removal. This plan does not authorize deployment.
