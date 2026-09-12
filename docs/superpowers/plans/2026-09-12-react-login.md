# React administrator login implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Migrate the current administrator login page into the controlled React preview using the accepted login JSON transport, with unchanged visual design and ordinary URLs.

**Architecture:** Exact `/__react/login` entry renders the existing login DOM in React. A feature-scoped request helper posts the current form fields to `/api/v1/login`; successful cookies authorize subsequent requests and the browser follows the existing allowlisted destination. Preview forwards only that exact POST into temporary fixture state; every other mutation remains rejected.

**Tech Stack:** Existing React/TypeScript/Vite, current shared CSS/fonts, guarded preview and Playwright. No new packages or visual redesign.

**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md

## Global Constraints

- Preserve existing CSS cascade and design tokens initially; replace DOM ownership with components, not a default component-library theme.
- During coexistence, one frontend owns each page's DOM. Do not run legacy polling and React polling against the same rendered page.
- Keep existing URLs, subscription formats, dedicated-user link exchanges, cookie separation, redirects, downloads, QR responses and status semantics.
- Preserve confirmation, loading, disabled, empty, validation-error, conflict and retry states. Canceling a destructive dialog sends no mutation.
- Stale requests must not overwrite a newer mutation or draft. Network/API errors cannot be interpreted as successful empty data.
- No production routing/deployment, runtime data reads, new user-login form, OAuth, account registration, dependency or CSS changes.

### Task 1: Current login UI backed by fixture-tested JSON authentication

**Prerequisite:** FastAPI login task reviewed and accepted, including bounded
body/cancellation/security header tests. Do not implement concurrently in its
backend files. Its POST contract is the only new API consumed here.

**Files:** Create `frontend/src/features/auth/LoginPage.tsx`,
`frontend/src/features/auth/loginRequest.ts`,
`frontend/src/shared/useInitialFragmentNavigation.ts`,
`tests/react_login_browser.cjs`. Modify `frontend/src/main.tsx`,
`frontend/src/features/public/HomePage.tsx` only to reuse its existing fragment
logic, `tests/react_preview_server.py`, `tests/test_react_preview.py`,
`tests/run_react_browser.py`, `frontend/README.md`. No backend production edits.

**Page interface:** `LoginPage({ passwordMaxLength }: {passwordMaxLength: number})`.
Use the authoritative `auth_views.render_login` markup and `static/login.js`
behavior; title `管理员登录 · Hysteria`, body `page-auth page-admin-login`.
Preserve the outer skip link and `main-content` wrapper from existing html_page,
all wording/classes/SVG geometry, sole administrator form, labels/IDs/names,
required/autocomplete/autocapitalize/spellcheck/maxlength, and ordinary `/` links.
No session read on load and no query-derived login message or username; legacy
GET `/login` ignores those query values. Keep form method/action `post`/`/login`
but prevent its native submit when the React handler processes the request.

Extract HomePage's already-reviewed one-time initial fragment effect into
`useInitialFragmentNavigation(targets: ReadonlySet<string>)`, retaining its
explicit-tabindex focus behavior, no hashchange listener, and exact allowlist.
Home keeps its existing six targets; Login uses a stable module-level Set
containing `main-content`. Re-run home tests; do not duplicate the effect.

**Request interface:** `submitLogin(credentials, signal)` in loginRequest.ts
accepts `{username: string, password: string}` and returns the validated public
union below. It does exactly one fetch with POST, same-origin credentials,
no-store, Accept JSON, URLSearchParams body using `admin_username` and
`admin_password`, and the supplied AbortSignal. It does not own retries or
persist credentials. Validate unknown JSON before returning; reject unexpected
status/shape/HTML as a request failure, never a successful empty response.

```ts
type LoginResponse =
  | { ok: true; redirect_to: '/admin?msg=login+success' | '/user/panel' | '/user/change-password' }
  | { ok: false; message: string };
```

Accept failure feedback from HTTP 200 or 429; only accept success on HTTP 200.
On success use `window.location.assign` with the exact validated destination.
No session id is read from JSON, copied into local storage or accessed in JS.

**Interaction:** Password toggle keeps type/text/aria-label/aria-pressed aligned
with legacy (`显示`/`隐藏`, `显示密码`/`隐藏密码`). Only the submit button is disabled
while pending, with aria-busy and `正在验证…` / live `正在验证登录信息`. Use a
synchronous ref guard as well as state so rapid submit events issue one request.
Fields remain editable as before. On definitive feedback clear submitted
password/restore masked toggle and trim submitted username only if those current
draft values still equal the submitted values; do not overwrite edits made while
waiting. Render feedback as escaped React text in `.err[role=alert]` with
aria-live assertive and aria-atomic true, matching shared_views.render_alert.

Use one 15-second client timeout and abort controller per submit; there is no
automatic replay. Transport/timeout failure shows `登录结果未确认，请检查网络后重试。`,
restores submit controls, and retains the draft without exposing credentials in
logs/storage. pageshow resets busy/masked-toggle/live status like the old script;
pagehide and unmount cancel the current request and invalidate its completion.
Ensure late results cannot overwrite reset state. Never delete a session merely
because a response was lost or cancelled.

**Preview fixture:** Add exact `/__react/login` to REACT_PAGES. Its root bootstrap
contains `data-password-max-length` taken from the fixture service's current
PASSWORD_MAX_LENGTH; main.tsx validates a positive integer before rendering.
Keep other entry bootstrap behavior intact, no shell preference initialization
on login, unknown React/API/assets still have no fallback. HEAD stays bodyless.

Inside the guarded temporary preview lifetime, seed fictional administrator
password `preview-only-password` with the real hash_secret helper into temporary
metadata before creating fixture admin cookies, and bind those cookies to that
new hash. Preserve user fixtures. Snapshot/replace the three in-memory login
failure/inflight dictionaries for the preview lifetime and restore them on exit
so preview tests do not contaminate each other. All file guards remain active.
Expose the fictional password to browser tests via `REACT_PREVIEW_LOGIN_PASSWORD`,
not any real account information. Never patch the verifier to accept arbitrary
passwords or disable rate limits.

Add ReactPreview.do_POST permitting only path `/api/v1/login`. Validate incoming
headers/length with shared helpers, bound the loopback stream read and socket
receipt timeout, then forward raw repeated header pairs and exact bytes to the
real TestClient endpoint. Construct TestClient with loopback peer for this local
proxy. Preserve response status/body/headers including Set-Cookie. Do not call a
legacy mutation handler. Proxy-level form/receipt rejection uses the same JSON
error/status contract as the accepted API instead of an HTML error page.
All other paths, including `/login`, `/logout`, admin
actions and unknown API POSTs, remain 405; the base legacy preview stays read-only.

The shared TestClient acquires a cookie jar after successful login. Every
forwarded API request, GET and POST, must explicitly forward the incoming Cookie
header or an empty Cookie header when absent, so its internal jar never supplies
another browser's session. Do not rely on clearing a shared jar around requests
(that races between threads). Preserve actual incoming headers otherwise.

- [ ] Write real-build browser RED for `/__react/login` (currently 404), page
structure and one real failed-login feedback interaction. Record expected
failure before component/preview changes; use fictional data only.

```js
await page.goto(baseUrl + '/__react/login');
await page.locator('#admin-username').fill('admin');
await page.locator('#admin-password').fill('wrong-fixture-password');
await page.locator('.auth-submit').click();
await page.getByRole('alert').filter({hasText: '用户名或密码错误'}).waitFor();
assert.equal(await page.locator('#admin-password').inputValue(), '');
```

- [ ] Implement JSX, feature request helper, controlled interaction and shared
fragment hook exactly above; retain legacy scripts only on legacy comparison
documents. No login.js, shell.js or ui-core script in the React document.

- [ ] Add preview bootstrap and the single temporary-state POST forwarding seam.
Python preview tests must show wrong credentials produce no cookie, correct
fixture credentials set sid and authorize `/api/v1/session`, all other POSTs
remain 405, legacy base preview remains read-only, bad body headers cannot call
authentication, and unknown/missing GET/HEAD routes remain 404/bodyless. Extend
the preview auth isolation test to assert an anonymous request
after somebody else's successful login is still 401, while the returned cookie
alone authorizes its owner. Include distinct real browser contexts too. Rename
the old all-POST-read-only test to describe the exact new exception, retaining
its old cases rather than deleting the assertions. Keep synthetic build fixtures
so these Python tests run without Node on a clean test checkout.

- [ ] Real browser tests cover required/max-length validation, no initial API,
toggle/keyboard/Enter, pending text and one request under repeated submit,
real incorrect and correct credentials, cookie HttpOnly plus subsequent session
authorization and exact legacy destination. For fault scenarios use narrowly
allowlisted 429/503/malformed JSON/HTML/network/timeout fixtures; assert no
automatic retry, editable draft retention, feedback escaping, and stale response
suppression after pageshow/pagehide/unmount. Use a controlled delayed response
to prove editing a field during submission is not erased by the old response.

- [ ] Compare legacy `/login` and React at 1920/1024/390 with the same fonts and
motion preferences. Assert text/link/field/SVG attributes and layout bounds
(outer layout, story, panel, fields, submit), background, no horizontal overflow;
capture paired full-page screenshots. Compare initial `#main-content` focus
before normalizing scroll. Check error markup/accessibility and masked toggle
states. Every browser scenario collects unexpected page/network errors; allow
only exact deliberate fault route/status pairs. Following a successful navigation
to a legacy page may load its scripts; do not confuse that with scripts loaded
by the React login document.

- [ ] Register the login browser test in run_react_browser.py with fixture
password and screenshot directory. Update README: three controlled entries;
only login mutates temporary preview sessions, every other POST including logout
is still blocked. Run all frontend gates (including home/logs regressions),
focused Python preview/isolation/auth-design tests and diff hygiene.

```bash
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_react_preview.py tests/test_preview_isolation.py tests/test_admin_login_design.py tests/test_auth_views.py
git diff --check
```

- [ ] Self-review and commit only owned files locally, with exact red/green and
covering command outputs and screenshot paths in the ignored task report. No
full backend rerun for this frontend/fixture slice; API task already owns that gate.

## Scope remainder

This migrates the administrator login document and fixture-tested login action,
not logout confirmations, password-change documents or the whole user panel.
No production entry changes, and legacy rendering remains available until full
parity and the separate approved cutover/rollback checkpoint.
