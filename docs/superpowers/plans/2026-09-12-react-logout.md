# React logout implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Migrate both logout confirmations and the React administrator shell's
direct logout to accepted JSON transports, retaining layout and realm isolation.

**Architecture:** Fixed-realm logout components share a bounded form-action
lifecycle with login. A small shared POST transport leaves response validation
inside each authentication request module. Controlled preview permissions
expand only to the two already-reviewed logout APIs.

**Tech Stack:** Existing React, TypeScript, Vite, Playwright, Python preview.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve same-origin mutation protection, credential-generation invalidation,
  revision conflicts, strict-state failure behavior, request size limits,
  request concurrency bounds and cache/security headers.
- Preserve confirmation, loading, disabled, empty, validation-error, conflict
  and retry states. Canceling a destructive dialog sends no mutation.
- Preserve existing CSS cascade and design tokens initially; replace DOM
  ownership with components, not a default component-library theme.
- Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444,
  runtime secrets, proxy configuration and all persistent user data.
- During coexistence, one frontend owns each page's DOM. Do not run legacy
  polling and React polling against the same rendered page.
- No dependencies, CSS, production routing, deployment, push, AI features,
  storage schema, legacy renderers or backend service changes in this task.

### Task 1: Confirmation and direct logout vertical slice

**Files:**
- Create `frontend/src/shared/postForm.ts`, `useFormAction.ts` in that directory.
- Create `frontend/src/features/auth/logoutRequest.ts`, `useLogout.ts`,
  `LogoutPage.tsx` in that directory.
- Modify `frontend/src/features/auth/loginRequest.ts`, `LoginPage.tsx`,
  `frontend/src/shared/AdminShell.tsx`, `frontend/src/main.tsx`.
- Create `tests/react_logout_browser.cjs`, `tests/react_form_action_harness.tsx`.
- Modify `tests/react_preview_server.py`, `tests/test_react_preview.py`,
  `tests/run_react_browser.py`, `tsconfig.json`, `frontend/README.md`.

**Interfaces and exact behavior:**

```ts
type LogoutRealm = 'admin' | 'user';
type LogoutResponse = { ok: true; redirect_to: '/login' };
// logoutRequest exports submitLogout(realm, signal): Promise<LogoutResponse>.
// POST /api/v1/logout for admin, /api/v1/user/logout for user.
// Only status 200 and exactly these two response keys are accepted.
// Shared postForm exports postFormJson(path, fields, signal), returning
// Promise<{ value: unknown; status: number }> and hasExactKeys(record, keys).
// fields is Record<string, string>; empty object for logout.
```

Shared transport retains the current login fetch options: POST, same-origin
credentials, no-store, Accept application/json, URLSearchParams body, supplied
AbortSignal and strict JSON MIME check. No automatic retries, cookie access,
credential persistence, arbitrary redirect or new dependency. Keep domain
response validation and login's accepted destinations/statuses unchanged.

`useFormAction` owns busy state, synchronous duplicate-submit guard, a single
AbortController, generation invalidation and 15-second timer. Its `run` accepts
an operation and start/result/error callbacks. Callbacks run only for the current
generation. Pagehide/unmount abort and invalidate; pageshow resets busy and
allows a fresh attempt. Clear timers on all completion/invalidation paths.
Timeout gives current-operation error feedback, never a success from an operation
that resolves after abort. Use a committed callback ref for optional pageshow
behavior. No general query cache, state machine package or custom event bus.

Move login onto this shared lifecycle without changing its markup, messages,
password mask reset, draft-sensitive failure cleanup or allowlisted redirect.
New logout feedback is exactly `退出结果未确认，请检查网络后重试。`; busy button
text is `正在退出…`. `useLogout(realm)` composes the shared lifecycle, fixed
request and feedback. It supplies submit handling to both consumers.

`LogoutPage({realm, publicHost})` reproduces
`console_shell_views.render_logout_confirmation` including the skip link and
focusable main-content wrapper. Title `确认退出`, empty body class. Keep brand,
subtitle, auth classes, button and link ordering. Fixed mappings:

| Preview entry | Heading | Native form action | Cancel href |
| --- | --- | --- | --- |
| `/__react/logout` | 退出管理后台？ | `/logout` | `/admin` |
| `/__react/user/logout` | 退出用户面板？ | `/user/logout` | `/user/panel` |

Confirmation is rendered without making a private data request. These are
fictional preview documents, like the existing visual reference; actual legacy
GET/HEAD authentication redirects stay authoritative and must be retained at
the separate production cutover. Do not claim preview HTTP status parity proves
production document authorization. Cancel remains navigation with no mutation.

AdminShell keeps direct logout, not a new confirmation step. Keep its native
action and normal button/icon/classes. Disable while pending. On failure place
an existing `.err` alert at the top of main content, close the mobile drawer and
focus the alert once. It must remain visible with collapsed desktop navigation;
do not cram feedback into the narrow sidebar. Later normal open/close actions
must retain current focus restoration and must not refocus old feedback.

The preview accepts exactly three POST paths: login and both logout APIs.
Rename `_login_api` to a shared form forwarding method; reuse bounded receipt,
raw headers, explicit incoming-or-empty Cookie forwarding and handler drain.
Every other POST (including legacy logout) stays 405. Unknown React/API paths
and missing assets remain 404, not SPA fallback. Do not touch base-preview POST
policy. Add fictional second-device cookies only if required by real-state tests.

- [ ] Add a focused browser RED before source changes: controlled logout page
  currently returns 404, or shell submits a blocked legacy POST. For example:

```js
const response = await page.goto(`${base}/__react/logout`);
assert.equal(response.status(), 200);
await page.getByRole('button', {name: '确认退出', exact: true}).click();
await page.waitForURL(`${base}/login`);
```

- [ ] Implement the transport/lifecycle and wire both confirmation pages and
  shell as described. A fixed mapping prevents request data selecting a realm:

```ts
const paths = { admin: '/api/v1/logout', user: '/api/v1/user/logout' } as const;
// submitLogout calls postFormJson(paths[realm], {}, signal), then validates
// status === 200, record.ok === true, record.redirect_to === '/login',
// hasExactKeys(record, ['ok', 'redirect_to']); otherwise throw.
```

- [ ] Test real temporary sessions in browser/preview: both cookies together,
  admin logout leaves user cookie/session usable, user logout removes only that
  user session, second devices remain usable, repeated/anonymous logout succeeds.
  Use explicit cookie headers/context isolation, not TestClient's shared jar.
  Existing service tests own detailed revocation internals; do not duplicate
  their whole matrix. Verify cancel sends zero POST and shell sends one exact
  API POST without displaying a confirmation page.
- [ ] Browser fault tests hold responses for duplicate clicks, allow manual
  retry after network/503/wrong MIME/malformed JSON/extra keys/unsafe destination,
  reject any non-200 success-shaped payload, test timeout, pagehide/pageshow
  stale settlement and no unsolicited retries/navigation. Verify visible and
  focused mobile/desktop-collapsed errors plus normal subsequent drawer focus.
- [ ] Close the ledger's real-root-unmount coverage gap with a test-only harness
  importing the actual shared hook (and LoginPage if needed). Create/unmount a
  real React root with an operation pending, then settle its promise; assert
  abort and no result/error/navigation callback. Exercise remount freshness.
  Use existing Vite programmatic build with `write:false`, `configFile:false`,
  React plugin and IIFE library output injected into a Playwright-only blank
  route. No runtime debug globals, production bundle imports or new packages.
  Include the harness in root tsconfig strict checks. Test an operation that
  ignores abort and settles late, not merely the fetch mock's abort exception.
- [ ] Capture distinct legacy/React confirmation pages at 1920, 1024 and 390
  widths for both realms using fictional host/state. Save under this task's
  ignored `task-1-screenshots/`; assert bounds/semantics and compare pairs.
  Preserve all existing home/login/logs tests and screenshots.
- [ ] Update preview Python tests for the five exact documents and three exact
  writes, direct legacy POST rejection, cookie isolation and body-limit/drain
  invariants. Wire the new browser suite into the existing runner and document
  preview-only permissions and remaining production authentication gate.
- [ ] Run focused tests while iterating, then the following covering gates once
  on the final tree. Capture output and RED/GREEN evidence in task report:

```bash
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_preview.py tests/test_preview_isolation.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
git diff --check
```

- [ ] Self-review and commit only owned source/test/README/config files locally.
  Record full results, screenshot paths, concerns and commit IDs in the ignored
  task report. Independent review follows; no push, deployment or legacy removal.

## Scope remainder

Password changes, overview and other administrator/user pages remain separate
parity slices. Production document guards, matched frontend/backend staging,
runtime packaging and rollback remain mandatory before the approved cutover.
