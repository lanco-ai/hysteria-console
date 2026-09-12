# Personal-site modular migration

Status: operator approved with “是”; implementation in progress, production cutover not complete.

## Approved intent and baseline

Preserve the existing public, administrator and user experiences while moving
to React + TypeScript + Vite and a FastAPI HTTP boundary around the existing
Python business services. Hysteria becomes the network module of a future
personal website. This phase adds no AI gateway, reminders, personal dashboard,
registration, payments, placeholder navigation or database migration.

The reference revision is `f7ec53ff630ddbabc0a15dd2ea87542765b55f89`.
It includes removal of Codex quota and the settlement-day test fixture fix.
Source branch: `refactor/personal-site-foundation`, based on that revision.
The runtime is separate from the source checkout. Do not deploy by copying the
entire repository or replacing live configuration with source defaults.

## Architecture

- `frontend/`: React application with public, authentication, network-admin and
  user-panel feature directories; shared shell, forms, tables, dialogs and
  request handling. TypeScript for new application code.
- `hysteria/web_api/`: FastAPI application factory, route groups, explicit input
  and output models, exception translation and authentication dependencies.
  New panel JSON endpoints use `/api/v1`; existing documented URLs remain
  compatible throughout migration.
- Existing billing, identity, session, credential, user-state, template and
  operational modules remain the authoritative business implementation.
  HTTP adapters do not reproduce accounting or proxy mutation rules.
- Existing strict JSON storage, file locks and recovery protocols remain
  authoritative. Do not introduce an independently maintained second copy of
  users, session state or usage balances.
- Nginx remains the public entry. Build artifacts are local and versioned;
  no production Vite development server, public API development port, external
  font/CDN dependency or additional Node server is required.
- Blocking file locks, subprocesses and synchronous probes must not execute
  directly on an async event loop. Keep their existing bounds and cancellation
  semantics. Do not increase worker count without a shared-state review.

Frontend state ownership: the request/cache layer owns fetched data; component
state owns unsaved drafts and dialog state. Successful mutations invalidate
affected queries. Stale requests must not overwrite a newer mutation or draft.
Network/API errors cannot be interpreted as successful empty data.

## Compatibility and safety invariants

1. Keep existing URLs, subscription formats, dedicated-user link exchanges,
   cookie separation, redirects, downloads, QR responses and status semantics.
   Missing API/assets/subscription resources never become a successful SPA page.
2. Administrator access and user-panel access stay separate. A user session
   cannot authorize administrator actions or another user's information.
3. Preserve same-origin mutation protection, credential-generation invalidation,
   revision conflicts, strict-state failure behavior, request size limits,
   request concurrency bounds and cache/security headers.
4. API responses use explicit field allowlists: never serialize an entire runtime
   object containing password hashes, proxy secrets or account credentials.
   Existing authorized subscription delivery is distinct from summary endpoints.
5. Preserve usage units, timezone, billing anchors, multiplier snapshots,
   resets, online counts and per-user action semantics.
6. Preserve confirmation, loading, disabled, empty, validation-error, conflict
   and retry states. Canceling a destructive dialog sends no mutation.
7. Preserve existing CSS cascade and design tokens initially; replace DOM
   ownership with components, not a default component-library theme.
8. Preserve host-specific settings, TLS, nginx routing, TCP/UDP 443, panel 9444,
   runtime secrets, proxy configuration and all persistent user data.
9. During coexistence, one frontend owns each page's DOM. Do not run legacy
   polling and React polling against the same rendered page.
10. Frontend/backend compatibility and runtime deployment are tested as a pair;
    rollback switches the matched pair, not an arbitrary subset of assets.

## Feature parity register

Each row requires HTTP contract tests, relevant interactions and desktop/mobile
visual comparison before the old implementation can be retired.

| Area | Existing surface | Required parity |
| --- | --- | --- |
| Public | `/` | Existing content, animation, links and responsive layout |
| Authentication | `/login`, compatibility login routes, logout confirmations | Existing login choices, password display, failed login, expiry and revocation |
| Admin shell | All administrator documents | Grouped sidebar, active item, collapse/mobile navigation and sign-out |
| Overview | `/admin` | Five-column user table; sparkline, usage bar, online state; search/filter; add/edit user; reset/refresh usage; reset subscription; pause/resume/delete; dedicated links; cycle settings |
| Usage | `/admin/usage`, user detail, daily compatibility route | Current filters, charts, hourly expansion, daily history, ranking, CSV and refresh ordering |
| Health | `/admin/health` | Existing health sections, fragments, update check/apply status and policy-disabled state |
| Incidents | `/admin/incidents`, evidence download | Existing evidence, incident actions, pending/failure states and download behavior |
| Templates | `/admin/config` | Load/edit/format/validate/save/overwrite, draft retention, existing server-configuration effects |
| Rules | `/admin/rules` | Existing listing, edits/import/export/delete and cancellation behavior |
| Residential egress | `/admin/landing-egresses` | Existing configuration, status, validation and mutation behavior |
| Logs/settings | `/admin/logs`, `/admin/settings` | Existing records, controls, feedback and account/security operations |
| User panel | `/user/panel`, password and logout routes | Authorized user's quota/devices/subscriptions, password controls, disabled/expired states and periodic refresh |
| Backend-only | Subscription/token exchange/QR/CSV/evidence/health routes | Keep content type, authorization, status, headers and body semantics; never SPA fallback |
| Retired quota | `/admin/codex`, `/admin/codex.json`, old script | Remain unavailable and absent from navigation |

The register is an audit index, not a substitute for a complete method/path and
form-action inventory. Inventory extraction and missing preview fixtures are
the first implementation deliverable.

## Donor branch assessment

`codex/react-vite-panel` at `0216830` contains `hysteria/panel_api/` and associated
tests. Its common-ancestor diff is approximately 20,000 added lines across 48
files. It is not a ready-to-merge frontend or a FastAPI implementation.

Selectively review pure payload builders, validation, authorization contracts
and service seams. Port behavior through the new adapters with tests. Do not
blindly merge its custom router, old composition-root changes, storage changes,
Codex quota references or visual design specification.

Its August design explicitly excludes FastAPI and calls for a redesign with
3D scenes. This September design supersedes those choices for this task only;
leave the other worktree and branch untouched.

## Incremental delivery and acceptance gates

1. Baseline: inventory all page/API/form routes and side effects; expand isolated
   preview coverage to all parity rows. Capture fixed-clock fictional fixtures
   at widths 1920, 1024 and 390. No live credentials/data in screenshots.
2. API foundation: introduce a side-effect-free FastAPI factory and session/error
   boundary; compare authenticated, unauthenticated, forbidden, malformed and
   conflict responses with legacy behavior. No production routing switch.
3. First vertical slice: a read-only network page using React, current styles
   and the new API, with a controlled preview entry. Demonstrate layout parity,
   refresh correctness and safe navigation before expanding.
4. Complete frontend migration: overview mutations, configuration/operations,
   public/auth pages and user panel, retaining per-feature behavior tests.
5. Cutover: validate immutable assets, deep links, absent-resource behavior,
   authorization and failure recovery in a staging copy; inspect live drift and
   back up exact changed artifacts. Production switch is a separate explicit
   deployment checkpoint, not implied by scaffolding the framework.
6. Cleanup: only after complete parity and cutover, remove old rendering and
   page scripts. Keep required external compatibility handlers and shared
   business helpers. End state is not a permanently duplicated UI.

Verification commands retain `scripts/check-quality.sh`, `npm run
check:frontend`, and `bash -n deploy.sh`; new type/component/API/visual tests
join the same quality entry points. Existing tests may be adapted to new
boundaries, but security/accounting assertions must not simply disappear.

## Completion definition

The full goal is complete only when the existing pages run through React,
the panel HTTP boundary runs through FastAPI, parity is verified for every row,
approved cutover and rollback are verified, and obsolete rendering is removed.
A design, API skeleton, preview-only page or green subset of tests is progress,
not completion of the full migration.
