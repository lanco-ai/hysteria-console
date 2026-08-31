# React Panel Feature Parity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a production Vite application that implements every public, user, and administrator panel workflow against `/api/v1`, before adding the final Three.js visual layer or cutting production traffic over.

**Architecture:** A route-lazy React SPA uses a typed same-origin API client, TanStack Query for server state, and accessible feature modules. During this phase the production renderer stays unchanged; the Vite app runs in development/test preview and is verified against the real Python API.

**Tech Stack:** React 19.2.8, React DOM 19.2.8, TypeScript 7.0.2, Vite 8.2.2, React Router DOM 7.18.3, TanStack Query 5.102.8, React Hook Form 7.87.0, Zod 4.5.4, Motion 13.1.1, ECharts 6.1.0, Vitest 4.1.11, Testing Library, MSW 2.15.0, Playwright 1.62.1

**Spec:** `docs/superpowers/specs/2026-08-30-react-vite-panel-redesign.md`

## Global Constraints

- Use the exact dependency versions in this plan and commit `frontend/package-lock.json`.
- TypeScript uses `strict`, `noUncheckedIndexedAccess`, and `exactOptionalPropertyTypes`.
- No CDN, remote font, service worker, query persistence, raw HTML insertion, production source map, or secret-bearing storage.
- Sensitive values stay in component memory and are cleared on close, navigation, mutation settlement, and auth loss.
- Preserve all document URLs in spec section 5 and all API status semantics in section 6.
- Every page has one focusable `main`, a skip link, logical headings, keyboard access, and reduced-motion behavior.
- Do not stage or modify the operator's existing Clash template changes.

---

## File Structure

| Path | Responsibility |
|---|---|
| `frontend/src/app/` | providers, router, guards, route boundaries |
| `frontend/src/lib/api/` | envelopes, typed client, API modules, error decoding |
| `frontend/src/lib/query/` | keys, visibility-aware polling, mutation epochs |
| `frontend/src/layouts/` | public, user, and admin shells |
| `frontend/src/components/` | accessible buttons, forms, dialogs, tables, statuses, charts |
| `frontend/src/features/` | page-level capability modules |
| `frontend/src/styles/` | token, reset, layout, component, and utility layers |
| `frontend/tests/` | Vitest setup, MSW handlers, fixtures, component tests |
| `frontend/e2e/` | Playwright browser contracts |

### Task 1: Vite, TypeScript, lint, and test foundation

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/package-lock.json`
- Create: `frontend/index.html`
- Create: `frontend/vite.config.ts`
- Create: `frontend/tsconfig.json`
- Create: `frontend/tsconfig.app.json`
- Create: `frontend/eslint.config.js`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/app/App.tsx`
- Create: `frontend/tests/setup.ts`
- Create: `frontend/src/app/App.test.tsx`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `npm run typecheck`, `npm run lint`, `npm run test`, `npm run build`, root React mount

- [ ] **Step 1: Add package manifest with exact versions**

```json
{
  "name": "hy2-panel",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "engines": {"node": "24.20.0"},
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc -b --pretty false",
    "lint": "eslint . --max-warnings 0",
    "test": "vitest run",
    "test:watch": "vitest",
    "e2e": "playwright test"
  },
  "dependencies": {
    "@hookform/resolvers": "5.9.1",
    "@tanstack/react-query": "5.102.8",
    "echarts": "6.1.0",
    "lucide-react": "1.37.0",
    "motion": "13.1.1",
    "react": "19.2.8",
    "react-dom": "19.2.8",
    "react-hook-form": "7.87.0",
    "react-router-dom": "7.18.3",
    "zod": "4.5.4"
  },
  "devDependencies": {
    "@playwright/test": "1.62.1",
    "@testing-library/dom": "10.4.1",
    "@testing-library/jest-dom": "7.0.1",
    "@testing-library/react": "16.3.3",
    "@testing-library/user-event": "14.6.6",
    "@types/node": "24.13.3",
    "@types/react": "19.2.18",
    "@types/react-dom": "19.2.5",
    "@vitejs/plugin-react": "6.1.1",
    "eslint": "10.9.1",
    "jsdom": "30.0.1",
    "msw": "2.15.0",
    "typescript": "7.0.2",
    "typescript-eslint": "8.68.0",
    "vite": "8.2.2",
    "vitest": "4.1.11"
  }
}
```

- [ ] **Step 2: Install from the manifest and commit the generated lockfile**

Run: `cd frontend && npm install --ignore-scripts`

Expected: `package-lock.json` is generated with no lifecycle script execution.

- [ ] **Step 3: Write a failing app-shell test**

```tsx
it("renders one named main landmark", () => {
  render(<App />)
  expect(screen.getByRole("main", { name: "HY2 控制台" })).toBeInTheDocument()
})
```

- [ ] **Step 4: Add strict configs and minimal application**

```tsx
export function App() {
  return <main aria-label="HY2 控制台" tabIndex={-1}><h1>HY2</h1></main>
}
```

Configure Vite output with `manifest: true`, `sourcemap: false`, `assetsDir: "assets"`, and Vitest `environment: "jsdom"`, `setupFiles: ["./tests/setup.ts"]`.

- [ ] **Step 5: Verify all frontend gates**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build`

Expected: commands pass and `dist/.vite/manifest.json` exists.

- [ ] **Step 6: Commit the foundation**

```bash
git add frontend .gitignore
git commit -m "feat(frontend): scaffold typed Vite panel"
```

### Task 2: Typed API client, session state, and polling engine

**Files:**
- Create: `frontend/src/lib/api/envelope.ts`
- Create: `frontend/src/lib/api/client.ts`
- Create: `frontend/src/lib/api/session.ts`
- Create: `frontend/src/lib/query/client.ts`
- Create: `frontend/src/lib/query/polling.ts`
- Create: `frontend/src/lib/query/mutationEpoch.ts`
- Create: `frontend/src/app/Providers.tsx`
- Create: `frontend/tests/api-client.test.ts`
- Create: `frontend/tests/polling.test.ts`

**Interfaces:**
- Produces: `apiRequest<T>()`, `ApiError`, `SessionView`, `useSession()`, `visibilityPolling()`, `mutationEpoch`

- [ ] **Step 1: Write failing API error and polling tests**

```ts
it("decodes a conflict without losing field metadata", async () => {
  server.use(http.patch("/api/v1/admin/users/alice", () =>
    HttpResponse.json({error: {code: "user_revision_conflict", message: "冲突", fields: {}, retryable: false}, meta: {request_id: "r"}}, {status: 409})))
  await expect(apiRequest("/api/v1/admin/users/alice", {method: "PATCH", body: {}}))
    .rejects.toMatchObject({status: 409, code: "user_revision_conflict"})
})

it("does not schedule polling while hidden", () => {
  expect(visibilityPolling({hidden: true, failures: 0})).toBe(false)
})
```

- [ ] **Step 2: Run tests and confirm missing modules**

Run: `cd frontend && npm test -- api-client.test.ts polling.test.ts`

Expected: test collection fails on missing imports.

- [ ] **Step 3: Implement same-origin client and in-memory CSRF**

```ts
export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers({Accept: "application/json", ...options.headers})
  if (options.body !== undefined) headers.set("Content-Type", "application/json")
  if (options.mutation && csrfToken) headers.set("X-Hy2-CSRF", csrfToken)
  const response = await fetch(path, {
    method: options.method ?? "GET", headers, credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: AbortSignal.timeout(options.timeoutMs ?? 10_000),
  })
  const payload = await response.json()
  if (!response.ok) throw ApiError.fromResponse(response.status, payload)
  return payload.data as T
}
```

`setCsrfToken` is module-private to the session bootstrap and clears on logout/`401`. It is never exported as a readable application value.

- [ ] **Step 4: Implement query client, polling, and mutation epoch**

```ts
export function visibilityPolling({hidden, failures}: PollState): number | false {
  if (hidden) return false
  const base = Math.min(30_000 * 2 ** failures, 240_000)
  return base + Math.floor(Math.random() * 4_001)
}
```

Queries capture `mutationEpoch.current()` before fetch and discard a result if the epoch changes before resolution. Query retries exclude `401`, `403`, `404`, `409`, and `422`.

- [ ] **Step 5: Run focused tests and frontend gates**

Run: `cd frontend && npm test -- api-client.test.ts polling.test.ts && npm run typecheck && npm run lint`

Expected: all pass.

- [ ] **Step 6: Commit data foundation**

```bash
git add frontend/src/lib frontend/src/app/Providers.tsx frontend/tests
git commit -m "feat(frontend): add secure API and polling foundation"
```

### Task 3: Design tokens, accessible primitives, layouts, and routing

**Files:**
- Create: `frontend/src/styles/tokens.css`
- Create: `frontend/src/styles/reset.css`
- Create: `frontend/src/styles/layout.css`
- Create: `frontend/src/styles/components.css`
- Create: `frontend/src/components/Button.tsx`
- Create: `frontend/src/components/Dialog.tsx`
- Create: `frontend/src/components/Status.tsx`
- Create: `frontend/src/components/DataTable.tsx`
- Create: `frontend/src/components/FormField.tsx`
- Create: `frontend/src/layouts/PublicLayout.tsx`
- Create: `frontend/src/layouts/UserLayout.tsx`
- Create: `frontend/src/layouts/AdminLayout.tsx`
- Create: `frontend/src/app/router.tsx`
- Create: `frontend/src/app/RouteBoundary.tsx`
- Create: `frontend/tests/layout-accessibility.test.tsx`

**Interfaces:**
- Produces: shared semantic components and all spec document routes with lazy page imports

- [ ] **Step 1: Add failing landmark, navigation, and dialog tests**

```tsx
it("marks the active admin route and provides a skip link", () => {
  renderAdminAt("/admin/usage")
  expect(screen.getByRole("link", {name: "跳到主要内容"})).toHaveAttribute("href", "#main")
  expect(screen.getByRole("link", {name: "流量分析"})).toHaveAttribute("aria-current", "page")
  expect(screen.getAllByRole("main")).toHaveLength(1)
})
```

- [ ] **Step 2: Run and observe failures**

Run: `cd frontend && npm test -- layout-accessibility.test.tsx`

Expected: missing layout/component imports.

- [ ] **Step 3: Implement semantic tokens and primitives**

```css
:root {
  --bg-canvas: #050914;
  --bg-panel: #0a1222;
  --text-primary: #eef7ff;
  --text-muted: #9bb0c8;
  --accent-cyan: #37d7ff;
  --accent-violet: #9a7cff;
  --state-success: #45e0a8;
  --state-warning: #ffbf5b;
  --state-danger: #ff667d;
  --focus-ring: #8be6ff;
}
```

`Dialog` uses the native `<dialog>` element with labelled title, initial focus, Escape behavior, and focus restoration. `DataTable` wraps wide tables in a named `tabIndex=0` region and accepts an accessible caption.

- [ ] **Step 4: Implement public/user/admin layouts and route table**

Create exact routes for `/`, `/login`, `/logout`, `/user/panel`, `/user/change-password`, `/user/logout`, and every admin route in spec section 5. `/user/login` and `/admin/daily` use loader redirects. Route guards use `SessionView.role`; page modules are lazy imports.

- [ ] **Step 5: Run accessibility, type, and build tests**

Run: `cd frontend && npm test -- layout-accessibility.test.tsx && npm run typecheck && npm run lint && npm run build`

Expected: all pass.

- [ ] **Step 6: Commit layouts and routing**

```bash
git add frontend/src/app frontend/src/layouts frontend/src/components frontend/src/styles frontend/tests/layout-accessibility.test.tsx
git commit -m "feat(frontend): add control deck layouts and routing"
```

### Task 4: Public authentication and user self-service pages

**Files:**
- Create: `frontend/src/features/public/HomePage.tsx`
- Create: `frontend/src/features/auth/LoginPage.tsx`
- Create: `frontend/src/features/auth/LogoutPage.tsx`
- Create: `frontend/src/features/user/UserPanelPage.tsx`
- Create: `frontend/src/features/user/ChangePasswordPage.tsx`
- Create: `frontend/src/features/user/ProfileLinks.tsx`
- Create: `frontend/src/features/user/LandingEgressSelector.tsx`
- Create: `frontend/tests/auth-user-flows.test.tsx`

**Interfaces:**
- Consumes: API/session/client, layouts, forms/dialogs
- Produces: all public/user workflows except decorative Three.js scene

- [ ] **Step 1: Add failing auth, token-safety, and capability tests**

```tsx
it("never places subscription credentials in browser storage", async () => {
  renderUserPanel({subscriptionUrl: "https://host/sub/alice?token=secret"})
  expect(localStorage.length).toBe(0)
  expect(sessionStorage.length).toBe(0)
})

it("hides password change for bearer-derived sessions", () => {
  renderUserPanel({credentialKind: "subscription_token"})
  expect(screen.queryByRole("link", {name: "修改面板密码"})).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run and confirm feature modules are absent**

Run: `cd frontend && npm test -- auth-user-flows.test.tsx`

Expected: collection fails on missing page modules.

- [ ] **Step 3: Implement login/logout/password forms**

Use React Hook Form and Zod schemas with explicit `admin`/`user` login mode. Generic credential failures do not reveal whether an identity exists. On login, replace session query data and navigate with `{replace: true}`. Password success clears fields and adopts the replacement cookie/session bootstrap.

- [ ] **Step 4: Implement user panel workflows**

Render quota, devices, expiry, profiles, landing egress, status, and accessible traffic summary. Credential rotation requires a consequence-specific dialog and a generated UUID idempotency key; the returned secret links stay only in dialog state and are cleared on close/unmount.

```tsx
const rotationId = crypto.randomUUID()
await rotateMutation.mutateAsync({rotation_id: rotationId})
```

- [ ] **Step 5: Run component and backend user-flow tests**

Run: `cd frontend && npm test -- auth-user-flows.test.tsx && npm run typecheck && npm run lint`

Run: `pytest tests/test_panel_api_auth.py tests/test_panel_api_user_mutations.py tests/test_landing_user_flow.py -q`

Expected: all pass.

- [ ] **Step 6: Commit public and user pages**

```bash
git add frontend/src/features/public frontend/src/features/auth frontend/src/features/user frontend/tests/auth-user-flows.test.tsx
git commit -m "feat(frontend): add authentication and user self-service"
```

### Task 5: Admin overview and complete user management

**Files:**
- Create: `frontend/src/features/admin/overview/AdminOverviewPage.tsx`
- Create: `frontend/src/features/admin/users/UserTable.tsx`
- Create: `frontend/src/features/admin/users/UserDialog.tsx`
- Create: `frontend/src/features/admin/users/UserDetailPage.tsx`
- Create: `frontend/src/features/admin/users/UserActions.tsx`
- Create: `frontend/src/features/admin/users/userSchemas.ts`
- Create: `frontend/tests/admin-users.test.tsx`

**Interfaces:**
- Produces: create/edit/detail/pause/resume/reset/rotate/delete/access workflows and mutation-epoch reconciliation

- [ ] **Step 1: Add failing revision, unlimited, and destructive-action tests**

```tsx
it("preserves zero as unlimited", async () => {
  const user = userEvent.setup()
  renderUserDialog({max_devices: 0, quota_bytes: 0})
  expect(screen.getByLabelText("设备数上限")).toHaveValue(0)
  await user.click(screen.getByRole("button", {name: "保存用户"}))
  expect(lastPatch()).toMatchObject({max_devices: 0, quota_bytes: 0})
})

it("clears passwords after a 409 conflict", async () => {
  renderConflictScenario()
  await fillAndSubmitPassword("temporary-secret")
  expect(await screen.findByRole("dialog", {name: "用户状态已变化"})).toBeVisible()
  expect(screen.queryByDisplayValue("temporary-secret")).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Run and observe missing feature failures**

Run: `cd frontend && npm test -- admin-users.test.tsx`

Expected: missing module imports.

- [ ] **Step 3: Implement overview table and detail route**

Use the lightweight overview query, stable row keys, semantic status text, and route `/admin/user/:id`. A removed user row unmounts immediately after confirmed deletion and can never be re-enabled by a stale request.

- [ ] **Step 4: Implement reusable create/edit dialog and actions**

Schemas reject out-of-range device limits rather than clamping. Every mutation submits `revision`; `409` stores only the allowlist `{guest,max_devices,quota_gb,quota_extra_gb,expires_at,note,tuic_enabled}`. Rotate/delete/pause/reset dialogs describe their actual consequences.

- [ ] **Step 5: Run frontend, API, and concurrency tests**

Run: `cd frontend && npm test -- admin-users.test.tsx && npm run typecheck && npm run lint`

Run: `pytest tests/test_panel_api_admin_users.py tests/test_panel_api_user_mutations.py tests/test_operator_concurrency_regressions.py tests/test_admin_poll_regressions.py -q`

Expected: all pass.

- [ ] **Step 6: Commit admin user management**

```bash
git add frontend/src/features/admin/overview frontend/src/features/admin/users frontend/tests/admin-users.test.tsx
git commit -m "feat(frontend): migrate admin user management"
```

### Task 6: Usage analytics and accessible charts

**Files:**
- Create: `frontend/src/components/charts/ChartFrame.tsx`
- Create: `frontend/src/components/charts/HourlyChart.tsx`
- Create: `frontend/src/components/charts/UsageHeatmap.tsx`
- Create: `frontend/src/components/charts/ProtocolRadar.tsx`
- Create: `frontend/src/features/usage/UsagePage.tsx`
- Create: `frontend/src/features/usage/UserUsagePanel.tsx`
- Create: `frontend/tests/usage-charts.test.tsx`

**Interfaces:**
- Produces: ECharts visualizations with semantic summary/table fallback and tiered 30/90-second queries

- [ ] **Step 1: Add failing chart semantics and refresh tests**

```tsx
it("provides a keyboard-readable heatmap table", () => {
  render(<UsageHeatmap data={heatmapFixture} />)
  expect(screen.getByRole("img", {name: /七日每小时流量热力图/})).toBeVisible()
  expect(screen.getByRole("table", {name: "热力图数据"})).toBeInTheDocument()
})

it("does not replace chart data on an unchanged signature", () => {
  expect(chartSignature(hourlyFixture)).toBe(chartSignature(structuredClone(hourlyFixture)))
})
```

- [ ] **Step 2: Run and observe missing chart modules**

Run: `cd frontend && npm test -- usage-charts.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement lazy ECharts wrappers and semantic alternatives**

Charts initialize only while visible, resize with `ResizeObserver`, dispose on unmount, and use semantic token colors. Each receives a text summary and real HTML table; canvas is `aria-hidden` when the semantic representation supplies the accessible name.

- [ ] **Step 4: Implement usage page query tiers**

Summary query refreshes at 30 seconds. Full hourly/heatmap series refresh at 90 seconds. History loads on first expansion. Visibility and mutation-epoch policies come from Task 2.

- [ ] **Step 5: Run chart, usage, type, and build gates**

Run: `cd frontend && npm test -- usage-charts.test.tsx && npm run typecheck && npm run lint && npm run build`

Run: `pytest tests/test_panel_api_usage_operations.py tests/test_usage_page.py tests/test_hourly.py -q`

Expected: all pass.

- [ ] **Step 6: Commit analytics pages**

```bash
git add frontend/src/components/charts frontend/src/features/usage frontend/tests/usage-charts.test.tsx
git commit -m "feat(frontend): migrate accessible usage analytics"
```

### Task 7: Health, Codex quota, incidents, logs, and updater

**Files:**
- Create: `frontend/src/features/health/HealthPage.tsx`
- Create: `frontend/src/features/codex/CodexQuotaPage.tsx`
- Create: `frontend/src/features/incidents/IncidentsPage.tsx`
- Create: `frontend/src/features/logs/LogsPage.tsx`
- Create: `frontend/src/features/operations/UpdaterPanel.tsx`
- Create: `frontend/src/features/operations/ReloadStatus.tsx`
- Create: `frontend/tests/operations-pages.test.tsx`

**Interfaces:**
- Produces: all read-only operational pages and updater/reload actions

- [ ] **Step 1: Add failing probe, countdown, table, and updater tests**

```tsx
it("renders all stable health probes as labelled rows", () => {
  renderHealth(healthFixture)
  expect(screen.getAllByTestId(/^health-probe-/)).toHaveLength(15)
})

it("stops Codex countdown while hidden", () => {
  documentHidden(true)
  vi.advanceTimersByTime(5_000)
  expect(screen.getByTestId("quota-countdown")).toHaveTextContent("05:00")
})
```

- [ ] **Step 2: Run and confirm modules are absent**

Run: `cd frontend && npm test -- operations-pages.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement operational read pages**

Use stable probe IDs, bounded live regions, keyboard-scrollable evidence/log tables, safe download links, server-provided Codex refresh intervals, and last-known-data stale markers.

- [ ] **Step 4: Implement updater and reload actions**

Check/apply actions require confirmations, show durable pending markers, preserve current history, and keep polling until the backend reports a terminal state. A `401` presents the common login-expired flow.

- [ ] **Step 5: Run frontend and backend operational suites**

Run: `cd frontend && npm test -- operations-pages.test.tsx && npm run typecheck && npm run lint`

Run: `pytest tests/test_panel_api_usage_operations.py tests/test_health_probes.py tests/test_codex_quota.py tests/test_hysteria_update_web.py tests/test_alert_integration.py tests/test_product_ux_regressions.py -q`

Expected: all pass.

- [ ] **Step 6: Commit operational pages**

```bash
git add frontend/src/features/health frontend/src/features/codex frontend/src/features/incidents frontend/src/features/logs frontend/src/features/operations frontend/tests/operations-pages.test.tsx
git commit -m "feat(frontend): migrate operations consoles"
```

### Task 8: Settings, template editor, rules, costs, and landing registry

**Files:**
- Create: `frontend/src/features/settings/SettingsPage.tsx`
- Create: `frontend/src/features/config/TemplatePage.tsx`
- Create: `frontend/src/features/rules/RulesPage.tsx`
- Create: `frontend/src/features/landing/LandingEgressPage.tsx`
- Create: `frontend/src/features/cost/CostCalibration.tsx`
- Create: `frontend/tests/configuration-pages.test.tsx`

**Interfaces:**
- Produces: all remaining administrator configuration workflows

- [ ] **Step 1: Add failing corrupt-state, conflict, and redaction tests**

```tsx
it("locks every template write control for corrupt source", () => {
  renderTemplate({raw: "broken: [", writable: false, error: "invalid YAML"})
  expect(screen.getByLabelText("模板原文")).toHaveValue("broken: [")
  expect(screen.getByRole("button", {name: "保存模板"})).toBeDisabled()
})

it("never renders landing SOCKS credentials", () => {
  renderLandingRegistry(landingFixture)
  expect(document.body.textContent).not.toContain("upstream-password")
})
```

- [ ] **Step 2: Run and observe missing pages**

Run: `cd frontend && npm test -- configuration-pages.test.tsx`

Expected: collection fails.

- [ ] **Step 3: Implement settings, cycle, costs, and password flows**

Use server bounds and revision values. Password rotation clears every password field and refreshes the replacement session. Cost inputs enforce `0.1`–`20.0`; automatic policy status remains explicit.

- [ ] **Step 4: Implement versioned template/rules and landing pages**

Template raw text remains visible when corrupt but every write action is disabled. Rule add/delete/raw/pack actions submit exact revisions and preserve non-secret drafts on `409`. Landing node forms never receive stored upstream credentials from reads; credential replacement fields are write-only.

- [ ] **Step 5: Run configuration, type, and backend tests**

Run: `cd frontend && npm test -- configuration-pages.test.tsx && npm run typecheck && npm run lint`

Run: `pytest tests/test_panel_api_configuration.py tests/test_operator_concurrency_regressions.py tests/test_template_lock.py tests/test_landing_user_flow.py -q`

Expected: all pass.

- [ ] **Step 6: Commit configuration pages**

```bash
git add frontend/src/features/settings frontend/src/features/config frontend/src/features/rules frontend/src/features/landing frontend/src/features/cost frontend/tests/configuration-pages.test.tsx
git commit -m "feat(frontend): migrate panel configuration"
```

### Task 9: Production-build browser parity suite

**Files:**
- Create: `frontend/playwright.config.ts`
- Create: `frontend/e2e/fixtures/panelServer.ts`
- Create: `frontend/e2e/auth.spec.ts`
- Create: `frontend/e2e/user-panel.spec.ts`
- Create: `frontend/e2e/admin-users.spec.ts`
- Create: `frontend/e2e/usage-operations.spec.ts`
- Create: `frontend/e2e/configuration.spec.ts`
- Create: `frontend/e2e/security.spec.ts`
- Create: `frontend/e2e/accessibility.spec.ts`
- Create: `scripts/hy2-panel-test-server.py`
- Modify: `scripts/hy2-preflight.sh`

**Interfaces:**
- Produces: `npm run e2e`, real Python/Vite production-build harness, full functional parity gate

- [ ] **Step 1: Add a failing deep-link and token-scrub E2E test**

```ts
test("token entry is exchanged before React sees a clean URL", async ({page}) => {
  await page.goto("/panel/alice?token=test-token")
  await expect(page).toHaveURL(/\/user\/panel$/)
  expect(await page.evaluate(() => location.search)).toBe("")
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("test-token")
})
```

- [ ] **Step 2: Build the deterministic local harness**

`hy2-panel-test-server.py` creates temporary users/meta/state/template files, starts the real bounded Python handler with injected `PanelPaths`, starts `vite preview` against a production build, and prints one JSON readiness line containing both ports. It never writes to `/root/hysteria`.

- [ ] **Step 3: Implement route-by-route E2E contracts**

Cover password login/logout, user panel, create/edit/pause/resume/reset/rotate/delete, stale revisions, usage/health/Codex/incidents/logs, template/rules/settings/landing, deep-link refresh, mobile navigation, keyboard focus, reduced motion, and `401/409/422/429/503` fixtures.

- [ ] **Step 4: Add credential and storage scan**

```ts
const leakSurface = await page.evaluate(() => ({
  url: location.href,
  html: document.documentElement.outerHTML,
  local: {...localStorage},
  session: {...sessionStorage},
}))
expect(JSON.stringify(leakSurface)).not.toContain("test-token")
```

Capture console and request events and apply the same secret corpus to URLs, headers permitted for inspection, and messages.

- [ ] **Step 5: Run all frontend and Python gates**

Run: `cd frontend && npm run typecheck && npm run lint && npm run test && npm run build && npm run e2e`

Run: `pytest -q`

Expected: all pass.

- [ ] **Step 6: Wire preflight and commit parity phase**

`hy2-preflight.sh` runs frontend install verification, typecheck, unit tests, production build, then pytest. Playwright remains a separately named full gate to avoid downloading browsers during ordinary server deployment.

```bash
git add frontend scripts/hy2-panel-test-server.py scripts/hy2-preflight.sh
git commit -m "test(frontend): prove React feature parity"
```

## Phase Acceptance

- Every spec document route renders in React under the production build.
- Every UI read/mutation calls `/api/v1` or an explicitly backend-owned route.
- Component and Playwright suites cover functional, auth, conflict, timeout, keyboard, mobile, reduced-motion, and leak contracts.
- The legacy renderer is still production-authoritative, so this phase is independently safe to deploy for preview/testing.
- Three.js and final deployment cutover are intentionally handled by the following plans.
