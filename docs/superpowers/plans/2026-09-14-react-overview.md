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
Seed deterministic nonzero fictional usage in the React overview fixture so
browser reset-versus-refresh assertions demonstrate real accounting changes,
not two indistinguishable zero values. Keep existing page fixtures compatible
and use the same seed for paired legacy/React captures. CSV acceptance combines
the exact download link in the browser with the existing authenticated legacy
CSV HTTP contract tests; do not invent a new CSV API merely for preview support.
Reuse `useInitialFragmentNavigation` for initial hash/skip-link focus once the
content exists, retaining keyboard navigation behavior of the accepted pages.

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

- [x] Browser TDD missing controlled page first; use deterministic fictional
  preview/cookies and no external URLs. Then implement feature iteratively.
- [x] Browser checks desktop1920/tablet1024/mobile390 against legacy screenshots
  at same fixed state; compare bounding boxes/computed styles and actually inspect
  paired images (not screenshot creation alone). Cover empty/unlimited/disabled/
  expired rows and create/edit dialog. No pageerrors or unexpected failed resources.
  Parent legacy reference images/metrics are available at
  `.superpowers/sdd/2026-09-14-react-overview/baseline/` (single-user legacy fixture).
  Final paired acceptance must use identical fixture data on both implementations,
  not compare that single-user image to a differently seeded React fixture.
- [x] Real API create/edit/cycle/reset/refresh/toggle/rotate/delete from browser,
  temporary fixture state only; assert refreshed text/row/links, changed revision,
  unchanged server total for refresh vs reset, dialog/draft/focus, clipboard andCSV.
- [x] Cancellation sends zero POSTs for each destructive control; duplicate clicks
  across different controls send one; validation/conflict/missing/auth/state failures
  preserve correct feedback and safe draft, pending rotation/deletion code behavior.
- [x] Controlled delayed poll across mutation start/end cannot win; poll metadata
  omissions don't clear links/spark/edit fields; membership/revision change warns;
  timers prove30s cadence, backoff bounds, hiddenpause/resume, manualretry, timeouts,
  malformed replies, post-success readfailure and unknownwrite failure recovery.
  Include invalid link protocol and prototype/markup-shaped feedback payloads.
- [x] Pagehide/BFCache/unmount late responses do not set stale busy/data or replay
  a write. Reload watcher bounded/read-only; no legacy admin-poll.js loaded or
  /admin/overview.json requested by React. Other React pages remain intact.
- [x] Wire tests into existing run_react_browser/check:frontend; allow environment
  selection of this test only for iteration if needed, default still runs all.
  Final `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend`, relevant
  preview-isolation/preview-page/API tests, lint-only, bash-n and diffcheck.
- [x] Local owned commit, report TDD, exact gates, screenshot paths and visual
  inspection findings. Independent review and goal-wide audit before completion.

## Acceptance evidence — 2026-09-14

Scope: this approved source-only goal is the operation APIs and complete React
overview with real-time refresh, not the later full-site migration/cutover.
Production `/admin` remains legacy; `/__react/admin` is the controlled entry.
No dependencies, push, deployment, TLS/port/domain or live-data changes occurred.

### Behavior, isolation and visual acceptance

`tests/react_overview_browser.cjs` performs real create/edit/cycle/reset/refresh/
global-reset/toggle/rotation/deletion against fictional temporary account,
accounting and revocation-WAL state. Reset subtracts the selected user's actual
bytes from server total; refresh preserves that total. Revision, membership,
link rotation, independent drafts, all destructive cancellations, CSV href,
search/chips/count and dialog focus are asserted.

`tests/react_overview_lifecycle_browser.cjs` covers initial HTML/malformed/state/
auth errors, strict booleans/numbers/link protocols, prototype/markup feedback,
field errors, conflicts, missing users, pending codes and unknown writes; 30-second
polling, 10-second timeout, bounded backoff, visibility, manual retry, stale replies
across mutations, single-flight writes, post-success read failure, reload watcher,
pagehide/BFCache/unmount, clipboard fallback and native dialog focus trapping.

Observed RED then GREEN fixes: confirmed POST feedback during pagehide (real-hook
unit control plus browser), stale actionable BFCache snapshot during recovery,
and hidden-during-bootstrap recovery deadlock. Ordinary visibility cannot clear
an external configuration conflict. Recovery never replays a POST. Unknown browser
selector false-green was also reproduced and fixed (exit64 regression).

Final complete overview browser invocation, exit0 at22:11:52 local time:

```sh
/usr/local/sbin/hy2-codex-safe-run /usr/bin/env PATH=/tmp/hy2-quality-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin REACT_BROWSER_TEST=react_overview_browser.cjs npm run test:react-browser
```

All eighteen final paired images were actually opened and visually inspected:
`.superpowers/sdd/2026-09-14-react-overview/screenshots/` contains
`legacy-WIDTH.png`, `react-WIDTH.png`, `legacy-create-WIDTH.png`,
`react-create-WIDTH.png`, `legacy-edit-WIDTH.png`, `react-edit-WIDTH.png`,
for WIDTH1920/1024/390. Both sides use the same three-user, fixed-date, nonzero
fixture, including unlimited, disabled/expired and over-limit rows. Five columns,
tablet wrapping, mobile cards, badges, forms and sparks match; geometry.json has
21 exact-equal region boxes and the browser compares selected computed styles
and SVG markup/title. Only intentional visible delta: legacy timestamp feedback
versus React's 30-second status label. No CSS redesign was necessary.

Isolation is proven by `test_preview_isolation.py`, `test_preview_page_parity.py`
and `test_react_preview.py`: scoped POST allowlist, real temporary WAL/accounting,
production-path/network/service-command guards and no blanket mutation access.
React loads no legacy admin polling owner or `/admin/overview.json` request.
The CSV browser link is paired with the six authenticated legacy CSV contracts
in `test_admin_read_routes.py`; no new client-generated CSV/secret transport.

### Complete gate coverage with bounded sequential execution

The aggregate `PATH=/tmp/hy2-quality-venv/bin:$PATH npm run check:frontend` was
attempted but did not finish as a single command under the 180-second resource
guard. It is **not** claimed as a single aggregate pass. All constituent programs
were run sequentially with their existing assertions:

- CSS freshness, JS/CSS lint, TypeScript and Vite build; hook tests2/2 and JS
  quality tests7/7; `scripts/check-quality.sh --lint-only`.
- Legacy `npm run test:browser`: all three programs (layout/operations,
  metrics/history ordering, user metrics/subscriptions/visibility/retry).
- `test:react-browser`: logs, home, login, logout, password pages and overview,
  each selected explicitly by the runner; default selection still runs all six.
  Unknown selectors now fail rather than silently skipping every program.
- Shell syntax `bash -n deploy.sh scripts/check-quality.sh` and `git diff --check`.

Final static recheck reproduced the default Node test runner exhausting all32
tasks while its CSS fixture spawned a child Node process. The guarded run timed
out before completion; it is not counted as passing. `test:gates` now explicitly
uses `--test-concurrency=1`, retaining all seven test cases and their assertions,
and running the three
files serially under the existing32 cap rather than changing machine limits.
The npm wrapper's own Node threads still left insufficient capacity for that
child process, so that second owned transient test unit was explicitly stopped;
its wrapper exit0 is not a test pass. Direct serial Node execution then passed
all7. The exact npm gate is verified with per-command
`NODE_OPTIONS=--v8-pool-size=1` to reduce Node helper threads; this changes neither
machine configuration nor test assertions. Final gate results below identify
that environment rather than claiming an unqualified default-command pass.

Browser acceptance used explicitly authorized temporary TasksMax96. Afterwards,
TasksMax was restored to32. MemoryMax/SwapMax256M, CPUQuota50%, timeout180seconds,
disk/memory guard and single-command policy stayed intact. No live service was
stopped. Static/backend gates run with the restored32 cap.

Relevant backend tests, each direct guarded invocation:

```sh
/usr/local/sbin/hy2-codex-safe-run /tmp/hy2-quality-venv/bin/pytest -q tests/EXACT_FILE.py
```

| Test file | Passed |
| --- | ---: |
| test_react_preview.py |30|
| test_preview_isolation.py |6|
| test_preview_page_parity.py |28|
| test_web_api_overview_page.py |13|
| test_web_api_accounts.py |52|
| test_web_api_operations.py |83|
| test_web_api_credential_operations.py |42|
| test_admin_read_routes.py |40|
| test_public_read_routes.py |3|
| test_account_mutation_service.py |38|
| test_web_api_login.py |44|
| test_web_api_logout.py |57|
| test_web_api_password_changes.py |58|
| test_web_api_reads.py |49|
| test_overview_operation_services.py |37|
| test_admin_credential_services.py |28|
| test_operator_concurrency_regressions.py |7|
| test_admin_user_routes.py |3|
| test_new_features.py |94|
| test_form_recovery.py |6|
| test_reliability_regressions.py |31|
| test_deploy_durable_recovery.py |102|
| test_share_panel_and_landing.py |22|
| test_credential_routes.py |1|
| test_credential_service.py |2|
| test_revocation_service.py |2|
| test_rotation_recovery.py |13|
| test_meta_token_concurrency.py |6|
| Total distinct relevant tests |897|

An earlier form-recovery run exited143 with no proven cause; after terminal
process verification, the exact node and the complete six-test file passed.
The interrupted run is not counted as green. Existing deprecation warnings were
not hidden. This is covering verification for this goal, not a claim that every
test in the repository or every pending migration page has been accepted.

### Independent review and requirement audit

Reviewer `/root/overview_code_review` checked `75f9305..21dbd34` and this React
worktree: account/operation/bootstrap/poll/reload integration, accounting service
reuse, revision/credential semantics, five-column UI, controlled routing and
isolation. Both Important BFCache findings and the selector Minor were closed;
final verdict has no remaining Critical/Important/Minor findings for this goal.
Parent inspected the actual files, final images/geometry and covering tests.

Detailed TDD and goal requirement matrix are retained in
`.superpowers/sdd/2026-09-14-react-overview/task-1-report.md` and `goal-audit.md`.
Preexisting health-cutover notes and user-owned `.codex-guard` are preserved and
excluded from the owned source commit. Other migration pages and production
cutover/legacy retirement remain separate, unfinished scope.

### Local delivery receipt

Owned source commit: `1d0070f2d87afcce5a221144094c355378b29ea7`
(`feat: complete React admin overview and safe live refresh`),18 files.
The tested application source is identical to that commit; no browser or
production work followed the final acceptance.

Final restored32-cap verification:

```sh
# The first six steps passed; the following default test:gates exhausted32 tasks
# and the aggregate exited124. Passing constituent output is retained, but this
# invocation as a whole is not green.
/usr/local/sbin/hy2-codex-safe-run /usr/bin/env PATH=/tmp/hy2-quality-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -c 'npm run lint:js && npm run typecheck:react && npm run build:react && npm run test:react-unit && npm run check:css-build && npm run lint:css && npm run test:gates && PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only'

# After serializing test:gates, direct Node passed all7; exact npm gate plus
# lint-only passed with reduced helper-thread creation (exit0).
/usr/local/sbin/hy2-codex-safe-run /usr/bin/node --test --test-concurrency=1 tests/frontend-quality.test.cjs tests/css-build.test.cjs tests/ui-core.test.cjs
/usr/local/sbin/hy2-codex-safe-run /usr/bin/env NODE_OPTIONS=--v8-pool-size=1 PATH=/tmp/hy2-quality-venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin bash -c 'npm run test:gates && PYTHON=/tmp/hy2-quality-venv/bin/python bash scripts/check-quality.sh --lint-only'

# Executed after the guarded commands had ended; all exit0.
bash -n deploy.sh scripts/check-quality.sh /usr/local/sbin/hy2-codex-safe-run .codex-guard/test-hy2-codex-safe-run.sh
bash .codex-guard/test-hy2-codex-safe-run.sh
git diff --check
git diff --cached --check
```

Build output:41 modules, `index-B7YimKKE.js`,310.14kB (gzip93.32kB).
Unit output:2 passed/0 failed; quality output:7 passed/0 failed;
lint-only: all checks passed,111 adopted files and1 composition-root file already
formatted. Guard self-test confirms actual cgroup32/256M/256M/50% plus singleton,
timeouts and disk/memory refusal. No unsafe cleanup or live-service termination.

Branch `refactor/personal-site-foundation` and workspace `/root/hy2` are kept
as-is. No push, merge, PR or deployment. Remaining unrelated health notes and
user-owned guard file are deliberately outside this delivery.
