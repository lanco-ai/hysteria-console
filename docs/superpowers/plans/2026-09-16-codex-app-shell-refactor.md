# Codex App Shell Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the marketing root and standalone login documents with one authenticated Hysteria workbench that hosts chat and all existing administrator pages.

**Architecture:** `CodexShell` owns the single responsive sidebar, toolbar frame, mobile drawer, and auth overlay boundary. `AdminShell` remains a compatibility adapter, while `ChatPage` supplies its existing conversation state to a shell conversation-sidebar slot. The self-router maps `/`, `/auth`, `/login`, `/user/login`, and `/admin/chat` to the workbench; guarded data APIs and the existing FastAPI chat proxy remain the security boundary.

**Tech Stack:** React 19.3.0, React DOM 19.3.0, TypeScript 7.0.2, Vite 8.3.0, `@vitejs/plugin-react`, ordinary CSS, FastAPI 0.141.1, Uvicorn, existing pushState/popstate routing.

**Spec:** `docs/superpowers/specs/2026-09-16-codex-app-shell-refactor-design.md`

## Global Constraints

- Keep React 19.3.0, React DOM 19.3.0, TypeScript 7.0.2, Vite 8.3.0, `@vitejs/plugin-react`, and ordinary CSS.
- Keep the custom `pushState`/`popstate` router; do not add React Router, Tailwind, Next.js, Vue, Electron, Tauri, or a Node backend.
- Keep FastAPI/Uvicorn `react_server:app` on `127.0.0.1:8083`.
- The browser may call `/api/chat/completions` only; the server keeps the API key and calls the configured upstream.
- Keep `/api/chat/*` administrator-session, CSRF, and same-origin checks; never add public `/v1/chat/completions`.
- Preserve `docs/superpowers/specs/2026-09-12-http-cutover-notes.md`, `.codex-guard/`, and all existing uncommitted user changes.
- Do not run `git reset --hard`, `git clean -fd`, or force push.
- Use TDD for behavior changes: write one focused failing test, run it to observe the expected failure, implement the smallest change, rerun, then refactor only while green.

## File Map

- Create `frontend/src/shared/CodexShell.tsx`: the only shared workbench frame, responsive sidebar, toolbar, and navigation slots.
- Create `frontend/src/shared/session.ts`: typed `/api/v1/session` hook and refresh contract.
- Create `frontend/src/features/auth/LoginModal.tsx`: modal form that reuses `submitLogin` and reports authentication to the shell.
- Create `frontend/src/features/chat/ChatSidebar.tsx`: controlled recent-conversation UI extracted from `ChatPage`.
- Modify `frontend/src/shared/AdminShell.tsx`: preserve its public props and delegate rendering to `CodexShell`.
- Modify `frontend/src/shared/navigation.ts`: add shell-level chat actions and the existing admin groups without duplicating route definitions.
- Modify `frontend/src/features/chat/ChatPage.tsx`: expose conversation state/actions to `ChatSidebar`, gate private reads by session, and render only thread/composer content inside `CodexShell`.
- Modify `frontend/src/main.tsx`: centralize route metadata, auth modal state, `/auth` aliases, and protected-route gating; remove `HomePage`/`LoginPage` route rendering.
- Modify `hysteria/web_api/document_routes.py`: add `/auth`, change workbench bootstrap metadata, and preserve safe return paths for guarded documents.
- Modify `frontend/src/styles/sections/06-shell.css`, `18-chat.css`, and a new `19-workbench.css` if needed: one visual system and responsive behavior, with no marketing root selectors required for the workbench.
- Modify browser and Python contracts: `tests/react_home_browser.cjs`, `tests/react_login_browser.cjs`, `tests/react_chat_browser.cjs`, `tests/react_preview_server.py`, `tests/test_react_preview.py`, `tests/test_react_route_parity.py`, `tests/test_web_api_documents.py`, and focused new tests under `tests/`.
- Keep existing concurrent edits in `frontend/src/features/chat/chatApi.ts`, `frontend/src/features/network-admin/logs/LogsPage.tsx`, `tests/test_chat_feature.py`, `tests/react_chat_browser.cjs`, `tests/react_logs_browser.cjs`, `tests/test_config_react_contract.py`, and `tests/test_rules_react_contract.py`; reconcile only when a failing test requires a compatible change.

### Task 1: Lock route and document contracts before implementation

**Files:**
- Modify: `tests/test_react_preview.py`
- Modify: `tests/test_react_route_parity.py`
- Modify: `tests/test_web_api_documents.py`
- Modify: `tests/react_preview_server.py`
- Test: `tests/test_workbench_routes.py` (create)
- Modify: `hysteria/web_api/document_routes.py`

**Interfaces:**
- Produces the exact route set consumed by `main.tsx`: `/`, `/auth`, `/login`, `/user/login`, `/admin/chat`, and the existing admin/user documents.
- Keeps `POST /auth` in the separate authentication service unchanged; only the panel's React GET/HEAD document gains `/auth`.

- [ ] **Step 1: Write failing route assertions.** Add tests that request the React document router and assert `/auth` returns the workbench bootstrap, `/login` and `/user/login` use the same root marker, and anonymous protected documents redirect to `/login?next=<safe-path>` rather than exposing business data. Assert a malicious absolute `next` value is discarded.

```python
def test_auth_alias_uses_workbench_bootstrap(client):
    response = client.get('/auth')
    assert response.status_code == 200
    assert 'CodexShell' in response.text or 'id="root"' in response.text
    assert 'page-auth page-admin-login' not in response.text

def test_anonymous_admin_document_preserves_safe_return_path(client):
    response = client.get('/admin/health', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'] == '/login?next=%2Fadmin%2Fhealth'

def test_guard_rejects_external_return_path(client):
    response = client.get('/admin?next=https://evil.example', follow_redirects=False)
    assert response.status_code == 303
    assert response.headers['location'].startswith('/login?next=%2Fadmin')
    assert 'evil.example' not in response.headers['location']
```

- [ ] **Step 2: Run only the new route tests and confirm the expected failures.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_routes.py`

Expected: FAIL because `/auth` is not in the React document allowlist and the current guard returns the standalone `/login` location.

- [ ] **Step 3: Implement the minimal document metadata and guard change.** Add `/auth` to `REACT_DOCUMENTS`; give root/login aliases workbench title/body classes and password-length bootstrap. In `_guard`, build a relative `next` from `request.url.path` plus non-sensitive query parameters, and redirect anonymous users to `/login?next=...`. Keep token exchange, user-password guards, and 503 handling unchanged.

- [ ] **Step 4: Update preview fixtures and route parity assertions.** Add `/__react/auth` and `/auth` to the explicit preview list and update expected workbench title/body classes. Do not add a wildcard SPA fallback.

- [ ] **Step 5: Run route tests and the existing document suite.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_routes.py tests/test_react_preview.py tests/test_react_route_parity.py tests/test_web_api_documents.py`

Expected: PASS, with any old standalone-login expectations changed to the approved compatibility behavior.

- [ ] **Step 6: Commit only route-contract changes.**

```bash
git add hysteria/web_api/document_routes.py tests/test_workbench_routes.py tests/test_react_preview.py tests/test_react_route_parity.py tests/test_web_api_documents.py tests/react_preview_server.py
git commit -m "feat: add workbench document route contracts"
```

### Task 2: Add typed session state and the LoginModal

**Files:**
- Create: `frontend/src/shared/session.ts`
- Create: `frontend/src/features/auth/LoginModal.tsx`
- Modify: `frontend/src/features/auth/loginRequest.ts` only if the modal needs an existing typed response extension
- Test: `tests/test_workbench_frontend_contract.py` (create)
- Modify: `tests/react_login_browser.cjs`
- Modify: `tests/react_home_browser.cjs`

**Interfaces:**
- `useSession(requiredRole?: 'admin' | 'user'): { status: 'loading' | 'anonymous' | 'authenticated' | 'unavailable'; role?: 'admin' | 'user'; refresh: () => Promise<void> }`.
- `LoginModal` props: `{ open: boolean; realm: LoginRealm; passwordMaxLength: number; returnTo?: string; onAuthenticated: (returnTo?: string) => Promise<void> | void; onClose?: () => void }`.
- `LoginModal` calls `submitLogin({ username, password }, signal, realm)` and never stores credentials or response fields in localStorage.

- [ ] **Step 1: Write failing session and modal contract tests.** Add a Python source contract that requires the typed status union, same-origin session fetch options, abort handling, `LoginModal` dialog semantics, reuse of `submitLogin`, and absence of localStorage writes for credentials. Browser assertions in `react_login_browser.cjs` cover the actual interaction.

- [ ] **Step 2: Run the focused tests and observe the expected missing-module failures.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'session or modal'`

Expected: FAIL because the new modules and required source markers do not exist.

- [ ] **Step 3: Implement `session.ts`.** Use `fetch('/api/v1/session', { credentials: 'same-origin', cache: 'no-store', headers: { Accept: 'application/json' } })`, map 401/`login_required` to `anonymous`, network/5xx to `unavailable`, validate the role, and ignore stale/aborted results. Keep refresh stable with `useCallback`.

- [ ] **Step 4: Implement `LoginModal.tsx`.** Reuse `useFormAction` conventions and `submitLogin`; set `aria-modal="true"`, `role="dialog"`, labelled heading, focus the first field on open, trap Escape/close safely, and expose busy/error states. Resolve a same-origin `returnTo` path before invoking the success callback. Do not render or echo API keys, upstream URLs, cookies, or raw server exceptions.

- [ ] **Step 5: Prepare browser auth helpers for the new modal.** Add selectors and helper branching that can address either the current legacy fixture or the new modal while Task 5 is not mounted. The full scenarios must be rewritten and run in Task 5: `/`, `/auth`, `/login`, and `/user/login` show the same workbench and modal; wrong credentials stay on the same URL; valid credentials close the modal; refresh with the session cookie does not reopen it.

- [ ] **Step 6: Run focused source and browser auth tests.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'session or modal'`
Run: `REACT_BROWSER_TEST=react_login_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`
Run: `REACT_BROWSER_TEST=react_home_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`

Expected: the unit test passes in this task; browser tests are rerun in Task 5 after route integration, where their full pass is recorded.

- [ ] **Step 7: Commit the session/modal slice after its focused tests are green.**

```bash
git add frontend/src/shared/session.ts frontend/src/features/auth/LoginModal.tsx tests/test_workbench_frontend_contract.py tests/react_login_browser.cjs tests/react_home_browser.cjs
git commit -m "feat: add workbench session gate and login modal"
```

### Task 3: Build the single CodexShell and navigation model

**Files:**
- Create: `frontend/src/shared/CodexShell.tsx`
- Modify: `frontend/src/shared/AdminShell.tsx`
- Modify: `frontend/src/shared/navigation.ts`
- Create: `tests/test_workbench_frontend_contract.py` (extend)
- Modify: `tests/react_logs_browser.cjs`

**Interfaces:**
- `CodexShellProps = { active: string; pageTitle: string; badge?: string; children: ReactNode; subtitle?: ReactNode; topbarExtra?: ReactNode; sidebarTop?: ReactNode; sidebarBottom?: ReactNode; authStatus?: SessionStatus }`.
- `AdminShell` keeps its current `{active, badge, pageTitle, children, subtitle?, topbarExtra?}` props and calls `CodexShell` with the administrator navigation.
- `navigationGroups` remains the single source for administrator routes; chat actions are rendered by `sidebarTop` so ChatPage owns only conversation data.

- [ ] **Step 1: Write failing Shell contract tests.** Extend the Python source contract to require one `.app`/`.sidebar`/`.main` tree, the required “新对话” and search controls, all existing admin links, exactly one active link, mobile drawer `inert`/scrim behavior, and no nested `.app` wrapper in the adapter.

- [ ] **Step 2: Run the contract tests and observe failure against the current AdminShell.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'shell'`

Expected: FAIL because `CodexShell` is absent and the current shell has no chat slot.

- [ ] **Step 3: Implement `CodexShell.tsx` by moving the existing responsive behavior.** Preserve `applyInitialShellPreferences`, the 880px breakpoint, focus restoration, keyboard trap, logout feedback, and sidebar preference keys. Render a top slot for chat new/search/recent content, the existing grouped admin navigation, and a single content main.

- [ ] **Step 4: Convert `AdminShell.tsx` into a compatibility adapter.** Remove duplicated markup and return `<CodexShell ...>{children}</CodexShell>`, leaving all current page imports and logout behavior intact through props.

- [ ] **Step 5: Update navigation labels and accessibility metadata.** Ensure the groups read “概览与用量”, “运行维护”, and “网络配置”; add “设置” in the bottom area and keep existing hrefs. Add `aria-current="page"` only to the active route.

- [ ] **Step 6: Run Shell tests and existing admin navigation browser checks.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'shell'`
Run: `REACT_BROWSER_TEST=react_logs_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`
Run: `REACT_BROWSER_TEST=react_overview_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`

Expected: PASS, with one Shell tree on every authenticated admin route.

- [ ] **Step 7: Commit the shared Shell slice.**

```bash
git add frontend/src/shared/CodexShell.tsx frontend/src/shared/AdminShell.tsx frontend/src/shared/navigation.ts tests/test_workbench_frontend_contract.py tests/react_logs_browser.cjs
git commit -m "feat: unify admin pages under codex shell"
```

### Task 4: Extract ChatSidebar and gate ChatPage private state

**Files:**
- Create: `frontend/src/features/chat/ChatSidebar.tsx`
- Modify: `frontend/src/features/chat/ChatPage.tsx`
- Modify: `frontend/src/features/chat/chatApi.ts` only for compatible error typing already required by the focused tests
- Modify: `tests/react_chat_browser.cjs`
- Modify: `tests/test_workbench_frontend_contract.py`

**Interfaces:**
- `ChatSidebarProps = { sessions: ChatSession[]; activeId: string; search: string; usage: ChatUsage; onSearch(value: string): void; onNew(): void; onSelect(id: string): void; onRename(session: ChatSession): void; onDelete(session: ChatSession): void; onOpenSettings(): void; onOpenUsage(): void }`.
- `ChatPageProps = { publicHost: string; authenticated?: boolean; onUnauthenticated?: () => void }`.
- `ChatPage` passes `sidebarTop={<ChatSidebar .../>}` to `CodexShell` and returns the thread/composer as its children; it no longer nests `AdminShell` or `.chat-sidebar`.

- [ ] **Step 1: Write failing chat access tests.** Extend the Python source contract for the `authenticated` gate, disabled composer, effect guards, and the absence of a second `AdminShell`/`.chat-sidebar`. The browser chat suite asserts no private requests for an anonymous page and preserves the current model/reasoning/usage behavior for an authenticated page.

- [ ] **Step 2: Run the focused tests and observe the current unauthorized API calls/nested sidebar failure.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'chat'`

Expected: FAIL because ChatPage currently loads local sessions and settings on mount and renders its own sidebar inside AdminShell.

- [ ] **Step 3: Extract the session row/group markup into `ChatSidebar.tsx`.** Keep the current grouping, search filtering, rename/delete actions, usage/settings buttons, and mobile close controls. Pass all mutations through typed callbacks rather than duplicating state.

- [ ] **Step 4: Gate ChatPage effects and local storage.** Initialize sessions/usage only when `authenticated` is true; skip settings/models effects, clear transient requests when access is lost, disable composer/actions while anonymous, and call `onUnauthenticated` on a 401 from the proxy. On the first authenticated transition, load the existing local sessions and preserve the active conversation.

- [ ] **Step 5: Preserve model/reasoning/context semantics.** Keep no hard-coded model; auto-select only when one model exists; omit `reasoning_effort` for `auto`; retain the existing graceful `reasoning_unsupported` notice; show `未知` when usage/context max is absent. Do not save model/API key values to localStorage.

- [ ] **Step 6: Run focused chat tests and the existing chat browser test with the current concurrent changes intact.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'chat'`
Run: `REACT_BROWSER_TEST=react_chat_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`

Expected: PASS with one application sidebar, preserved conversation history for authenticated users, and no anonymous private API requests.

- [ ] **Step 7: Commit only the chat extraction/gating files.**

```bash
git add frontend/src/features/chat/ChatSidebar.tsx frontend/src/features/chat/ChatPage.tsx tests/test_workbench_frontend_contract.py tests/react_chat_browser.cjs
git commit -m "feat: move chat history into unified shell"
```

### Task 5: Integrate the router, aliases, and auth return flow

**Files:**
- Modify: `frontend/src/main.tsx`
- Modify: `frontend/src/features/public/HomePage.tsx` only to remove it from production entry usage; retain the file if old preview imports require it
- Modify: `frontend/src/features/auth/LoginPage.tsx` to become an explicit compatibility wrapper or remove its route-only export
- Modify: `tests/react_preview_server.py`
- Modify: `tests/react_home_browser.cjs`
- Modify: `tests/react_login_browser.cjs`
- Modify: `tests/test_workbench_frontend_contract.py`

**Interfaces:**
- `normalizeRoute` recognizes `/auth` and preview aliases.
- `RouteContent` renders `ChatPage` for `/` and `/admin/chat`; renders `LoginModal` through the shell for `/auth`, `/login`, and `/user/login`; renders the existing admin page components only after the matching session role is authenticated.
- A validated relative `next` value is consumed once after login with `history.pushState`, then removed from the query string.

- [ ] **Step 1: Write failing router tests.** Extend the Python source contract to assert root no longer renders marketing selectors or `HomePage`, login aliases never render standalone `LoginPage`, `/auth` opens the same modal, `/admin/chat` and `/` share the ChatPage, and an authenticated admin route renders exactly one Shell.

- [ ] **Step 2: Run the router tests and observe failures against `main.tsx`.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'router'`

Expected: FAIL because root maps to `HomePage`, login maps to `LoginPage`, and `/auth` is not recognized.

- [ ] **Step 3: Implement route metadata and shell composition.** Replace duplicated title/body branches with a route metadata table. Mark workbench documents with `has-shell page-workbench`; leave logout/password/user routes on their current semantics. Add the authentication gate before protected page components.

- [ ] **Step 4: Implement modal open/close and safe return navigation.** `/`, `/login`, `/auth`, and `/user/login` all render the workbench; aliases set `loginOpen` and realm. Clicking an internal legacy login link uses pushState and opens the modal without a full document navigation. After successful login, refresh the session, close the modal, and navigate to the safe `next` path or remain on `/`.

- [ ] **Step 5: Keep preview and direct-document behavior aligned.** Ensure `/__react/`, `/__react/auth`, `/__react/login`, `/__react/user/login`, and `/__react/admin/chat` have the same route semantics and bootstrap attributes as production paths.

- [ ] **Step 6: Run router, typecheck, and focused browser tests.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_frontend_contract.py -k 'router'`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run typecheck:react`
Run: `REACT_BROWSER_TEST=react_home_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`
Run: `REACT_BROWSER_TEST=react_login_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`
Run: `REACT_BROWSER_TEST=react_chat_browser.cjs PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python tests/run_react_browser.py`

Expected: PASS; anonymous root shows Shell + LoginModal with no private API traffic, wrong credentials stay in place, and valid login restores chat or the requested admin route.

- [ ] **Step 7: Commit router integration.**

```bash
git add frontend/src/main.tsx frontend/src/features/public/HomePage.tsx frontend/src/features/auth/LoginPage.tsx tests/test_workbench_frontend_contract.py tests/react_preview_server.py tests/react_home_browser.cjs tests/react_login_browser.cjs
git commit -m "feat: route root and auth aliases to workbench"
```

### Task 6: Apply the workbench visual system and responsive behavior

**Files:**
- Modify: `frontend/src/styles/sections/06-shell.css`
- Modify: `frontend/src/styles/sections/18-chat.css`
- Create or modify: `frontend/src/styles/sections/19-workbench.css`
- Modify: `frontend/src/styles/index.css` and `frontend/src/styles/manifest.json` when a new section is added
- Modify: `tests/workspace_visual.cjs` or create `tests/workbench_visual.cjs`

**Interfaces:**
- CSS classes consumed by `CodexShell`: `.app`, `.sidebar`, `.main`, `.topbar`, `.content`, `.shell-auth-layer`, `.sidebar-drawer`, `.workbench`.
- CSS classes consumed by ChatSidebar/ChatPage: `.chat-sidebar`, `.chat-thread`, `.chat-composer`, `.chat-context-status`, `.chat-notice`.

- [ ] **Step 1: Write failing visual contract assertions.** Add Playwright checks at 1440px, 1024px, and 390px for no horizontal overflow, one visible sidebar on desktop, drawer/scrim on mobile, fixed composer bounds, modal dim/blur, and no marketing text on `/`.

- [ ] **Step 2: Run the visual contract against the current UI and confirm failures.**

Run: `PREVIEW_BASE_URL=http://127.0.0.1:18765 node tests/workbench_visual.cjs`

Expected: FAIL on marketing root, modal absence, and nested chat sidebar.

- [ ] **Step 3: Implement the CSS changes.** Use system font stacks, pale background, thin borders, restrained shadow, 240px desktop rail, constrained thread width, bottom composer, mobile drawer at 880px, safe-area padding, and `prefers-reduced-motion`. Remove only selectors that are unreachable after root routing; leave legacy styles harmlessly scoped.

- [ ] **Step 4: Run style lint and visual checks, save screenshots.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run lint:css`
Run: `REACT_SCREENSHOT_DIR=/tmp/hy2-workbench-shots PREVIEW_BASE_URL=http://127.0.0.1:18765 node tests/workbench_visual.cjs`

Expected: PASS and screenshots for desktop, tablet, and mobile showing one sidebar, centered empty state, fixed composer, and usable drawer/modal.

- [ ] **Step 5: Commit CSS and visual coverage.**

```bash
git add frontend/src/styles/sections/06-shell.css frontend/src/styles/sections/18-chat.css frontend/src/styles/sections/19-workbench.css frontend/src/styles/index.css frontend/src/styles/manifest.json tests/workbench_visual.cjs
git commit -m "style: tune responsive workbench shell"
```

### Task 7: Verify API security, backend regressions, and build

**Files:**
- Modify: `tests/test_chat_feature.py` only if a new regression test is required and it does not conflict with the existing uncommitted retry test
- Create: `tests/test_workbench_security.py`
- Modify: `tests/react_chat_browser.cjs` for browser-side secret assertions, preserving its uncommitted reasoning/model changes
- No production backend changes unless a failing test demonstrates a missing existing guard

**Interfaces:**
- Browser network capture must show `/api/chat/completions` as the only chat completion endpoint.
- Browser-visible settings responses contain only `api_key_configured`/masked metadata; upstream API keys never cross the FastAPI boundary.

- [ ] **Step 1: Write failing security assertions.** Test anonymous `/api/chat/settings`, `/api/chat/models`, `/api/chat/test`, and `/api/chat/completions` responses are rejected; test browser HTML, response bodies, localStorage, and built assets do not contain a sentinel API key. Test an Auto request omits `reasoning_effort`.

- [ ] **Step 2: Run the focused security tests and observe any missing guard or leak.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_security.py tests/test_chat_feature.py`

Expected: FAIL only for newly specified workbench assertions; existing chat tests must remain green.

- [ ] **Step 3: Implement only the minimal boundary fix.** Keep API keys in the existing server-side settings store and sanitize all browser payloads; keep CSRF/same-origin checks and do not add a public upstream route. If a 401 reaches the browser, route it through `onUnauthenticated` to reopen the modal.

- [ ] **Step 4: Run the complete validation set.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run typecheck:react`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run build:react`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run test:react-assets`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run test:react-unit`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH /tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_workbench_routes.py tests/test_workbench_security.py tests/test_chat_feature.py tests/test_react_navigation.py tests/test_react_route_parity.py tests/test_react_server_entrypoint.py tests/test_web_api_documents.py`
Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run test:react-browser`

Expected: all commands exit 0; browser screenshots and network captures provide the acceptance evidence.

- [ ] **Step 5: Run the repository quality gate and inspect the final diff.**

Run: `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend`
Run: `git diff --check`
Run: `git status --short`

Expected: quality checks pass; only intended workbench commits plus the pre-existing concurrent files remain; protected paths are unchanged.

- [ ] **Step 6: Commit any final test-only changes separately.**

```bash
git add tests/test_workbench_security.py tests/react_chat_browser.cjs tests/test_workbench_routes.py
git commit -m "test: verify workbench auth and secret boundaries"
```

### Task 8: Final acceptance report and completion audit

**Files:**
- No new production files
- Inspect: `docs/superpowers/specs/2026-09-16-codex-app-shell-refactor-design.md`, all workbench commits, screenshots, and `git status`

- [ ] **Step 1: Exercise the ten acceptance cases manually in the running preview.** Record evidence for anonymous root, wrong credentials, correct login, chat proxy round trip, refresh/session persistence, all eight admin links, `/auth`, secret absence, build, and backend regressions.

- [ ] **Step 2: Review screenshots at desktop/tablet/mobile sizes.** Confirm no marketing hero/capability cards, no standalone login page, no nested sidebars, centered empty state, composer at the bottom, usable toolbar, and mobile drawer/modal focus behavior.

- [ ] **Step 3: Audit every spec requirement against command output or rendered behavior.** Treat missing or indirect evidence as incomplete; rerun the smallest relevant test until each requirement has direct evidence.

- [ ] **Step 4: Report the requested sections exactly:** UI architecture; Routes changed; Login flow; Chat API flow; Files changed; Existing features preserved; Security verification; Build/tests; Screenshots / visual verification; Git status.
