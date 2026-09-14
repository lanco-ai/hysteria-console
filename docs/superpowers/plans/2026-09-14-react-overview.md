# React overview and real-time refresh implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Track checkboxes.

**Goal:** Complete the administrator overview in React with its real operation
APIs and verified refresh behavior, preserving the current five-column design.
**Architecture:** A focused overview feature owns fetched page/poll data and a
single-flight operation controller; create/edit/cycle drafts remain separate
component state. Existing shell/styles are reused. No legacy script owns its DOM.
**Tech Stack:** Existing React/TS/Vite/Playwright and isolated FastAPI preview.
**Spec:** docs/superpowers/specs/2026-09-12-personal-site-refactor.md
**Prerequisites:** Accepted account API, basic operations and credential operations.

## Global Constraints

- Keep existing URLs, subscription formats, dedicated-user link exchanges,
  cookie separation, redirects, downloads, QR responses and status semantics.
- Administrator access and user-panel access stay separate. A user session
  cannot authorize administrator actions or another user's information.
- Preserve usage units, timezone, billing anchors, multiplier snapshots,
  resets, online counts and per-user action semantics.
- Preserve confirmation, loading, disabled, empty, validation-error, conflict
  and retry states. Canceling a destructive dialog sends no mutation.
- Preserve existing CSS cascade and design tokens initially; replace DOM
  ownership with components, not a default component-library theme.
- During coexistence, one frontend owns each page's DOM. Do not run legacy
  polling and React polling against the same rendered page.
- No dependencies, production routing switch, live data, deployment or push.

### Task 1: Complete overview feature with browser acceptance

**Files:** create within `frontend/src/features/network-admin/overview/`:
`OverviewPage.tsx`, `OverviewTable.tsx`, `UserForms.tsx`, `useOverview.ts`,
`types.ts`, `requests.ts`, `presentation.tsx` (format/spark/feedback only).
Modify `frontend/src/main.tsx`, `frontend/src/shared/AdminShell.tsx`,
`frontend/src/shared/icons.tsx`, `tests/react_preview_server.py`,
`tests/run_react_browser.py`, `tests/test_react_preview.py`,
`scripts/check-quality.sh`. Create `tests/react_overview_browser.cjs`;
split large browser scenarios into an explicit sibling helper only if useful.
No CSS redesign or new theme; narrowly necessary parity fixes must be documented.

Controlled entry `/__react/admin` renders document title总览, bodyhas-shell,
active dashboard, public-host/cycle subtitle, cycle form/poll status/user count in
topbar. Extend AdminShell with optional subtitle/topbarExtra ReactNode props,
preserving existing callers' exact output when absent. Keep production `/admin`
legacy until separately authorized cutover. Add preview entry/allowlisted POSTs
for all accepted overview APIs, not blanket API write access. Preserve isolated
state and externally guarded sync/reload/kick behavior of preview server.
The current preview mainly supports reads/password writes: extend only the React
preview's scoped MonkeyPatch context with safe external sync/kick/reload doubles
and redirected module state needed by these operations. Retain production-path,
network and process guards unchanged. Exercise real account/usage/WAL writes in
the temporary directory; never disable guards to make a browser scenario pass.
Use the existing action-result contracts (not a truthy arbitrary dictionary).

Data contracts:
- Bootstrap `/api/v1/admin/overview-page`: exact cycle/users/landing_options from
  accepted models. Runtime validate every consumed field and finite numeric
  value, plain records/arrays, strict strings/bools; no `any` or casting untrusted
  JSON as valid. Only React text/SVG nodes, no dangerous HTML insertion.
  Validate authorized link protocols as HTTP(S), never javascript/data URLs;
  retain configured public host support rather than forcing same-origin links.
- Poll `/api/v1/admin/overview`: lightweight ts/total_used/user counters and
  revision fields. Merge counters into bootstrap rows without replacing fields
  absent from poll (spark, edit metadata, authorized links).
- Account create/update and eight operation endpoints use accepted explicit
  response models. Validate tags/status correspondence before reporting success.
  Do not interpret malformed/HTML/unexpected 2xx as success.
- Read reload-status separately after successful operations. No read failure may
  relabel an already confirmed write as failed. All POSTs use shared postFormJson
  transport with existing form size/CSRF/server bounds; checkboxfalse is omitted,
  never sent as `'false'` because legacy booleans are presence-based.

Layout and all controls:
- Reproduce current `admin_views.render_admin` / `_render_user_row` structure,
  class hooks, spacing, typography, three stats cards, five-column user table,
  notes/badges/expiry/unlimited quota/devices, usage bars, dedicated links, initial
  30-day sparklines, CSV link and collapsed create form. Use proper matching
  header IDs/headers attributes and retain accessible names/field IDs.
- Spark SVG reproduces `charts.mini_sparkline_svg` geometry/classes/title: width
  n*3+(n-1), height24, floor23, draw22, line/area/current dot; consume already
  scaled dated values without multiplying again. Format bytes with1024 units,
  nonnegative integer truncation and two decimals through TB as display.fmt_bytes.
- Search case-insensitive username substring; all/online(count>0)/over(percent>=90)
  chips, aria-pressed, filtered count, distinct no-users and no-match messages.
- Shared edit dialog populated only from selected bootstrap row, passwordsblank,
  all existing fields/limits, disabled buttons while submitting, Escape/cancel,
  focus trap via native showModal and focus return to trigger. Preserve submitted
  revision even if counters refresh; stale conflict never silently overwrites.
- Create defaults quota150/extra0/meteredtrue/TUICfalse, optional passwords,
  date/note/landing choice, no-node disabled state/link. Invalid code/field focuses
  correct input and retains non-secret draft; never put credentials in URL/storage.
  Clear passwords after submission settles, preserve non-secret drafts on errors.
- Cycle day1..28, length bounds from bootstrap; preserve current layout and exact
  confirmation text from legacy form. Cycle drafts not reset by background reads.
- Account successful save closes/reset only its form, then refreshes bootstrap;
  other unsaved forms stay intact. A failed refresh reports saved-but-stale and
  offers a read retry, not an automatic repeat write.
- Row reset/refresh/rotate/toggle/delete and global reset call correct new APIs
  with exact selected username/revision. Keep current Chinese confirmation text
  from admin_poll.confirmAdminAction, no mutation when canceled. Existing row
  暂停 means toggle disabled (not invented 60minute pause); enable no destructive
  confirmation. Pause API exists for compatibility but add no new row button.
- Copy panel/subscription links uses clipboard then safe fallback, visible success/
  failure, meaningful aria labels. Keep original hrefs/targetblank/relnoopener,
  CSV `/admin/usage.csv?window=cycle`, no client-generated subscription credentials.
- Initial query feedback uses explicit Map/own-property checks and escaped text;
  `__proto__`,constructor,toString and markup-like values cannot crash/inject.

Refresh/mutation lifecycle:
- Bootstrap loading, retryable failure, malformed/state503, login-required are
  explicit states, never successful empty data. Do not render sensitive rows
  after an authentication failure.
- Poll every30seconds, timeout10seconds, capped exponential backoff240seconds
  with bounded0..4second jitter; button supports immediate retry/status. Use
  recursive timer, at most one in-flight poll; stop hidden and resume visible.
- On external membership/config-revision mismatch, stop patching and show reload
  required; do not overwrite draft/config. Explicit refresh may load new data but
  must keep a dirty draft's original revision and warn it may conflict, or ask
  before discarding. No automatic browser reload that destroys draft.
- A shared mutation epoch increments at start AND finish; invalidate/abort old
  polls and bootstrap requests so late responses cannot overwrite new results.
  One synchronous pendingRef gate covers every write, including create/edit/cycle;
  disable all mutation controls while pending, not just the clicked button.
- Successful operation refreshes authoritative bootstrap before normal polling
  resumes. Preserve success/pending message if refetch fails; disable further
  writes and stale link-copy/open controls until explicit successful read recovery.
- Network timeout/abort/5xx may mean committed/unknown, not rollback. Show
  “操作结果尚未确认，请刷新核对后再操作”, keep draft, no automatic POST retry.
  Do not claim abort cancels server work. A manually recovered snapshot cannot
  be used to automatically replay a destructive action.
- Rotation/delete pending codes show exact distinct recovery message from legacy
  flash mappings. Do not call pending a failed mutation; do not issue repeats.
  Canonical refetch owns membership/links; if pending deletion remains visible,
  retain pending warning rather than falsely claiming background cleanup finished.
- Reload watcher read-only first600ms/next850ms/up to9000ms as legacy; pending or
  timeout message not save failure. It never reloads services. Cancel watcher,
  timers/fetches and ignore late results on unmount/pagehide; BFCache pageshow
  resets busy/read state safely without replaying POST. Avoid duplicate watchers.

- [ ] Browser TDD missing controlled page first; use deterministic fictional
  preview/cookies and no external URLs. Then implement feature iteratively.
- [ ] Browser checks desktop1920/tablet1024/mobile390 against legacy screenshots
  at same fixed state; compare bounding boxes/computed styles and actually inspect
  paired images (not screenshot creation alone). Cover empty/unlimited/disabled/
  expired rows and create/edit dialog. No pageerrors or unexpected failed resources.
- [ ] Real API create/edit/cycle/reset/refresh/toggle/rotate/delete from browser,
  temporary fixture state only; assert refreshed text/row/links, changed revision,
  unchanged server total for refresh vs reset, dialog/draft/focus, clipboard andCSV.
- [ ] Cancellation sends zero POSTs for each destructive control; duplicate clicks
  across different controls send one; validation/conflict/missing/auth/state failures
  preserve correct feedback and safe draft, pending rotation/deletion code behavior.
- [ ] Controlled delayed poll across mutation start/end cannot win; poll metadata
  omissions don't clear links/spark/edit fields; membership/revision change warns;
  timers prove30s cadence, backoff bounds, hiddenpause/resume, manualretry, timeouts,
  malformed replies, post-success readfailure and unknownwrite failure recovery.
  Include invalid link protocol and prototype/markup-shaped feedback payloads.
- [ ] Pagehide/BFCache/unmount late responses do not set stale busy/data or replay
  a write. Reload watcher bounded/read-only; no legacy admin-poll.js loaded or
  /admin/overview.json requested by React. Other React pages remain intact.
- [ ] Wire tests into existing run_react_browser/check:frontend; allow environment
  selection of this test only for iteration if needed, default still runs all.
  Final `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend`, relevant
  preview-isolation/preview-page/API tests, lint-only, bash-n and diffcheck.
- [ ] Local owned commit, report TDD, exact gates, screenshot paths and visual
  inspection findings. Independent review and goal-wide audit before completion.
