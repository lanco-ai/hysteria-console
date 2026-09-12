# React password pages implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox tracking.

**Goal:** Migrate existing administrator settings and user password pages,
including real password submissions, while retaining design and access rules.

**Architecture:** Two minimal authenticated reads provide names and password
limits. React pages reuse the existing shell, form transport and action lifecycle;
the pending password-change backend task owns all credential mutations. Preview
fixtures remain isolated, with fresh state for each browser suite.

**Tech Stack:** Existing React/TypeScript/Vite, FastAPI, pytest and Playwright.

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
- No dependencies, CSS redesign, runtime/deployment/push, proxy configuration,
  schema changes or new password policy. Backend mutation service stays unchanged.

### Task 1: Two complete authentication-related page slices

**Prerequisite:** The password-change-boundary plan must be independently
accepted before dispatch. Verify its final interfaces against this plan first.

**Files:**
- Create `frontend/src/features/network-admin/settings/SettingsPage.tsx`.
- Create `frontend/src/features/auth/UserPasswordPage.tsx`, `passwordRequest.ts`,
  `usePasswordChange.ts`, `passwordPageTypes.ts` in that auth directory.
- Modify `frontend/src/main.tsx`, `frontend/src/shared/readResource.ts`.
- Modify `hysteria/web_api/services.py`, `app.py`, `models.py` in that directory;
  create `tests/test_web_api_password_page_reads.py`.
- Create `tests/react_password_pages_browser.cjs`; modify
  `tests/react_preview_server.py`, `tests/test_react_preview.py`,
  `tests/run_react_browser.py`, `frontend/README.md`, `scripts/check-quality.sh`.

**Reads:** Exact GET/HEAD `/api/v1/admin/settings` and
`/api/v1/user/password`, via existing read admission/state/error boundaries.
The administrator endpoint checks administrator identity before metadata read.
The user endpoint checks password-kind user identity before user-state read;
it must permit `password_change_required`, unlike the general session endpoint.
Do not weaken `/api/v1/session`. Preserve current user password GET lifecycle
priority, including the existing must-change priority. Disabled/expired return
403 with their exact code; missing/wrong-kind identity401. If user configuration
disappears after identity validation,403 forbidden clears only the user cookie.

```python
# Explicit success allowlist, same keys for either endpoint:
{'username': 'admin', 'password_min_length': 8, 'password_max_length': 256}
# Values come from authoritative username and PASSWORD_* constants, not these
# illustrative literals. Never return metadata/config/hash/token/session ID.
```

Use one explicit model with strict string/integer fields and valid positive
length ordering. Add narrow `read_admin_settings`/`read_user_password` methods.
For forbidden-cookie forwarding, `UserAccessDenied` may gain an optional hidden
cookie parameter, default None; `_read_error_response` forwards it only when
present. Keep existing call sites and error JSON unchanged. No write on ordinary
successful reads. GET/HEAD headers and bodyless HEAD, POST405 and trailing404
must match the existing API boundary.

**Frontend:** Controlled entries `/__react/admin/settings` and
`/__react/user/change-password`. Settings uses AdminShell active=settings,
title设置 and the existing host badge. Reproduce `operations_views.render_settings`
classes/content, max-width560 sections, field IDs/labels/attributes and order.
User page reproduces `user_views.render_user_change_password`, title修改面板密码,
body page-auth, header/illustration markup, escaped username/host, native action,
autocomplete/min/max lengths, autofocus and return links. Keep skip-link/main
focus behavior. Do not substitute a generic form-library theme.

Settings motion checkbox reads the existing root class, toggles it immediately,
sets `hy2.sidebar-motion=enabled` when checked and removes that key when unchecked.
Storage errors cannot prevent the current visible change. No HTTP write for this
preference. Existing desktop/mobile shell semantics remain authoritative.

Each page loads only its own minimal API; do not gate the user page through the
general session endpoint. While loading, show an accessible status, not a usable
form with guessed limits. Authentication failures navigate to /login; user403
disabled/expired navigates to /user/panel, forbidden to /login. Unknown errors
show an accessible retry state. Extend ResourceError with an optional validated
access code and parse only known error codes from failing JSON; never display
arbitrary server text. Preserve all existing reader messages and stale handling.

`passwordRequest` uses postFormJson and fixed realm paths from the accepted API
plan; exact success destination and validation-code allowlists, no extra keys.
`usePasswordChange` uses useFormAction, never a second lifecycle copy. Keep field
names current/new/confirm. Fields remain editable while pending, button disabled,
immediate duplicate guard, no automatic mutation retry. Only current-generation
callbacks update UI. Definitive validation failure clears unchanged submitted
password drafts but must not erase a newer edit. Transport uncertainty preserves
drafts and shows `修改结果未确认，请检查网络或重新登录后再试。`.

Map accepted validation codes to the exact existing Chinese messages from
`_SETTINGS_FLASH` and `render_user_change_password`. Dynamic maximum uses fetched
limit. Keep known initial query-message rendering and escape unknown text as
legacy does; no raw HTML. Success assigns the accepted legacy destination.
Authentication/lifecycle mutation responses navigate using the same fixed
destinations as reads. Never put passwords in URL, storage, console or alerts.

**Preview:** Add only these two controlled documents and two password API POSTs,
now five exact permitted POSTs. Every legacy POST and other POST remains405.
Create fictional password-kind user sessions with actual password hashing and
credential generation, separate from existing token-kind cookies. Preserve
unknown-path404, temporary state guards, explicit incoming-or-empty Cookie and
shared draining server. Change browser runner to a fresh preview_server context
per suite so real password mutations cannot poison another suite's fixture.
Never introduce a test-only mutation/reset endpoint.

- [ ] Add RED for the missing minimal read endpoints and controlled documents;
  record expected404 before source changes. Test real-session read authorization
  including must-change user allowed and token-kind forbidden from password page:

```python
response = client.get('/api/v1/user/password', headers={'Cookie': forced_user_cookie})
assert response.status_code == 200
assert set(response.json()) == {'username', 'password_min_length', 'password_max_length'}
```

- [ ] Implement the narrow reads, models, code-aware reader and the two pages.
  Browser tests assert exact native form actions/field attributes, copy, return
  links, local preference toggle/on/off/reload/storage-denied behavior and focus.
- [ ] Wire password requests and shared mutation hook. Real browser flows must
  change an administrator and user password, prove old credentials/session(s)
  invalid, new credentials accepted and other users/realm unaffected. Use the
  isolated real API; do not satisfy success tests solely with route interception.
  Test required initial password change remains reachable and succeeds.
- [ ] Hold requests to verify one POST for double-submit, current draft safety,
  validation mapping, network/503/malformed/unsafe/extra-key rejection, timeout,
  stale page transitions and manual retry only. Test access expiry after initial
  page read. Verify no passwords appear in storage or outbound request URLs.
- [ ] Compare distinct legacy/React pages at1920/1024/390 with fictional data;
  save six paired screenshots under this task's task-1-screenshots. Assert content,
  element bounds and no mobile overflow. Preserve existing home/login/logout/
  logs suites, not weakened assertions or screenshots of the same page twice.
- [ ] Test minimal reads' state failure/headers/HEAD/unknown paths and forbidden
  cookie. Preview tests verify exact five-write allowlist, wrong methods, receipt
  bounds, fixture restoration and no cross-suite session contamination. Register
  the new Python test in the adopted quality list and new browser in runner.
- [ ] Run focused tests while iterating, then final covering commands:

```bash
PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend
/tmp/hy2-quality-venv/bin/python -m pytest -q tests/test_web_api_password_page_reads.py tests/test_web_api_reads.py tests/test_react_preview.py tests/test_preview_isolation.py
PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only
git diff --check
```

- [ ] Self-review, locally commit owned files, and record exact RED/GREEN,
  covering output, screenshots and concerns. Independent review before acceptance;
  do not deploy, push or remove legacy UI.

## Remaining scope

Overview, usage, health, incidents, templates, rules, egress and user panel remain
parity tasks. Production document guards, asset packaging, staging and separately
approved cutover/rollback are not proved by these preview pages.
