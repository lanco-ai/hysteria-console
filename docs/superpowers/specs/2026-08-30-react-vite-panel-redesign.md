# React + Vite Panel Redesign — Design Spec

**Date:** 2026-08-30
**Status:** Approved by operator, ready for implementation plan
**Scope:** Complete replacement of the server-rendered panel frontend; Python business core retained and reorganized behind a versioned JSON API

## 1. Motivation

The panel has grown beyond the shape that its original server-rendered implementation can sustain. The primary HTTP module is roughly 9,500 lines, the shared stylesheet is roughly 5,700 lines, and interactive behavior is spread across four standalone JavaScript files plus multiple inline scripts. New features currently require coordinated HTML-string, CSS, JavaScript, routing, deployment, and source-assertion test changes.

This project replaces every user-visible panel page with a React + Vite application, reorganizes the Python panel service into a business/API backend, and uses Three.js only where it materially improves the visual experience. The result must feel like a modern network control plane without weakening the security, accessibility, concurrency, recovery, or one-command deployment properties of the current system.

The operator accepts the full migration workload and has approved continuous progress through the remaining design and implementation gates.

## 2. Goals

1. Replace all server-generated panel HTML, the legacy shared CSS, and the four legacy JavaScript files with a typed React application.
2. Preserve the existing Python implementation of authentication, user policy, quota accounting, credential rotation, proxy configuration, state durability, and operational recovery.
3. Expose all panel data and mutations through an explicit `/api/v1` JSON boundary.
4. Preserve every URL explicitly listed in this specification, subscription behavior, clean token-to-cookie exchanges, and existing bookmarks for those routes.
5. Deliver a pronounced but usable technology aesthetic through a coherent design system, data visualization, motion, and two bounded Three.js scenes.
6. Make frontend behavior testable with unit, component, integration, browser, accessibility, security, and deployment fault-injection coverage.
7. Deploy Vite releases atomically and roll back the complete frontend generation rather than individual files.
8. Remove the legacy frontend after parity and cutover; the final architecture must not remain a permanent hybrid.

## 3. Non-goals

- Rewriting the Python business core in Node.js, TypeScript, FastAPI, Django, or another backend framework.
- Changing Hysteria, Xray, TUIC, nginx, systemd, traffic accounting, JSON state schemas, backup contents, or proxy configuration semantics unless required to expose the same behavior safely through the API.
- Adding a database, Redis, external CDN, hosted analytics, third-party fonts, or remote visual assets.
- Turning forms, tables, settings, credential actions, or subscription workflows into 3D interfaces.
- Introducing a permanent `/legacy` product surface after cutover.
- Adding a light theme or a general-purpose internationalization subsystem in this migration. Simplified Chinese remains the default product language.

## 4. Target Architecture

```text
Browser
  |
  v
nginx
  |-- known UI documents ----------> React SPA entry (no-store)
  |-- /assets/<hash>.* ------------> immutable Vite release assets
  |-- /api/v1/* -------------------> Python 127.0.0.1:8081
  |-- /panel/* token exchange -----> Python 127.0.0.1:8081
  |-- /sub/*, QR, CSV, evidence ----> Python 127.0.0.1:8081
  `-- /healthz and operational API -> Python 127.0.0.1:8081

Python panel process
  |-- api/          request parsing, authentication, schemas, responses
  |-- services/     user, usage, config, health, incidents, credentials
  |-- repositories/ strict JSON reads, locks, atomic durable writes
  `-- integrations/ Hysteria, Xray, TUIC, systemd, alerts, Codex quota
```

The Python service remains loopback-only and keeps its current systemd sandbox, process limits, request backpressure, and `no-store` behavior. nginx remains the only public HTTP server.

The React application is built in a dedicated `frontend/` workspace. Production nginx serves a single active Vite release from `/usr/local/share/hy2/panel/current`, which points atomically to a validated release directory.

## 5. URL and Routing Contract

### 5.1 React document routes

The following URLs return the SPA entry after cutover:

| Area | Routes |
|---|---|
| Public | `/`, `/login`, `/logout` |
| User | `/user/panel`, `/user/change-password`, `/user/logout` |
| Admin | `/admin`, `/admin/user/:id`, `/admin/usage`, `/admin/health`, `/admin/codex`, `/admin/incidents`, `/admin/config`, `/admin/rules`, `/admin/settings`, `/admin/landing-egresses`, `/admin/logs` |

`/user/login` remains a `303` compatibility redirect to `/login`.
`/admin/daily` remains a permanent redirect to `/admin/usage`.

### 5.2 Backend-owned routes

The following never fall through to the SPA:

- `/api/v1/*`
- `/sub/*`
- `/panel/:user` token exchange and `/panel/:user/qr.svg`
- `/admin/usage.csv`
- `/admin/incidents/evidence.json`
- `/healthz`, `/livez`, `/readyz` where applicable
- `/assets/*` and missing asset paths

The legacy `/panel/:user?token=...` request remains backend-owned. It validates the bearer, creates the revocable `usid` cookie, and returns a `303` to `/user/panel` with no credential in the target URL. Invalid, disabled, expired, and unknown users remain indistinguishable where the current security contract requires it.

nginx uses explicit known-document locations or an exact internal named fallback. It must not turn missing API, subscription, download, QR, or asset requests into `index.html`.

## 6. Python Backend Redesign

### 6.1 Boundary, not rewrite

The backend changes structurally but retains the proven business core. Pure policy and durability functions are moved out of `subscription_service.py` without changing behavior. HTTP handlers become thin adapters that authenticate, parse, validate, invoke a service, and serialize a result.

Proposed package layout:

```text
hysteria/
├── panel_api/
│   ├── router.py
│   ├── responses.py
│   ├── schemas.py
│   ├── auth.py
│   ├── admin.py
│   ├── user.py
│   ├── usage.py
│   ├── configuration.py
│   ├── health.py
│   └── incidents.py
├── panel_services/
│   ├── users.py
│   ├── sessions.py
│   ├── credentials.py
│   ├── usage.py
│   ├── configuration.py
│   ├── landing_egress.py
│   └── operations.py
└── panel_repositories/
    ├── users.py
    ├── metadata.py
    ├── usage.py
    └── templates.py
```

Existing modules such as `state_store.py`, `xray_config.py`, `tuic_config.py`, `rotation_recovery.py`, `revocation_queue.py`, `health.py`, `codex_quota.py`, and `landing_egress.py` remain authoritative. The new packages call them rather than duplicate them.

### 6.2 API response conventions

Successful responses use a stable envelope:

```json
{
  "data": {},
  "meta": {
    "request_id": "opaque-id",
    "generated_at": "2026-08-30T12:34:56Z"
  }
}
```

Errors use:

```json
{
  "error": {
    "code": "user_revision_conflict",
    "message": "该用户已被其他管理员更新",
    "fields": {},
    "retryable": false
  },
  "meta": {"request_id": "opaque-id"}
}
```

Status meanings are consistent:

| Status | Meaning |
|---|---|
| `400` | malformed request |
| `401` | login required or session expired |
| `403` | authenticated but not permitted |
| `404` | safe resource-not-found response |
| `409` | optimistic concurrency or committed-side-effect conflict |
| `422` | field validation failure |
| `429` | bounded rate limit or overload policy |
| `503` | operational dependency unavailable or durability uncertain |

No error response includes a password, token, UUID, upstream credential, raw request body, internal filesystem path, or subprocess output containing secrets.

### 6.3 API surface

The API is grouped by product capability rather than by old page fragments:

```text
GET    /api/v1/session
POST   /api/v1/auth/login
POST   /api/v1/auth/logout
POST   /api/v1/user/password

GET    /api/v1/user/panel
POST   /api/v1/user/credentials/rotate
PUT    /api/v1/user/landing-egress

GET    /api/v1/admin/overview
GET    /api/v1/admin/users/:user
POST   /api/v1/admin/users
PATCH  /api/v1/admin/users/:user
DELETE /api/v1/admin/users/:user
POST   /api/v1/admin/users/:user/pause
POST   /api/v1/admin/users/:user/resume
POST   /api/v1/admin/users/:user/credentials/rotate
POST   /api/v1/admin/users/:user/usage/reset
PUT    /api/v1/admin/users/:user/landing-egress-access

GET    /api/v1/admin/usage
GET    /api/v1/admin/usage/history
POST   /api/v1/admin/usage/refresh
POST   /api/v1/admin/usage/reset

GET    /api/v1/admin/health
GET    /api/v1/admin/codex-quota
GET    /api/v1/admin/incidents
GET    /api/v1/admin/logs

GET    /api/v1/admin/settings
PATCH  /api/v1/admin/settings
GET    /api/v1/admin/cycle
PATCH  /api/v1/admin/cycle
POST   /api/v1/admin/password
POST   /api/v1/admin/alerts/test
GET    /api/v1/admin/cost-calibration
POST   /api/v1/admin/cost-calibration/apply
POST   /api/v1/admin/cost-calibration/auto
GET    /api/v1/admin/landing-egresses
POST   /api/v1/admin/landing-egresses
PATCH  /api/v1/admin/landing-egresses/:id
DELETE /api/v1/admin/landing-egresses/:id
POST   /api/v1/admin/landing-egresses/:id/check

GET    /api/v1/admin/template
PUT    /api/v1/admin/template
GET    /api/v1/admin/rules
POST   /api/v1/admin/rules
PUT    /api/v1/admin/rules/raw
DELETE /api/v1/admin/rules/:id
POST   /api/v1/admin/rule-packs/:id/apply

GET    /api/v1/admin/hysteria-update
POST   /api/v1/admin/hysteria-update/check
POST   /api/v1/admin/hysteria-update/apply
GET    /api/v1/admin/reload-status
```

Exact payload schemas are derived from existing behavior contracts and covered by schema tests. Sensitive credential results are returned only from the mutation that generated them, with `Cache-Control: no-store`, and are not obtainable later from a general read endpoint.

### 6.4 Authentication and CSRF

- Existing HttpOnly, `SameSite=Lax`, path-wide, conditionally `Secure` `sid` and `usid` cookies remain.
- Sessions remain server-side and credential-generation-bound.
- `/api/v1/session` returns role, safe identity, feature flags, and a per-authenticated-session CSRF token; it never returns the session cookie value.
- All authenticated mutating `/api/v1` requests require both the CSRF header and the existing same-origin `Origin`/`Referer`/Fetch Metadata policy.
- The login mutation has no authenticated session yet, so it uses the existing strict same-origin checks, bounded body parser, generic credential failure, and login rate limiter rather than the session CSRF header.
- Token-bearing URLs are exchanged by the backend before React loads.
- Authentication expiration clears client query state and navigates to `/login` without persisting the rejected target if it contains sensitive query data.
- Password-authenticated and bearer-derived user sessions retain their current capability differences.

### 6.5 Concurrency and durability

All user mutations carry `user_revision`; template, rule, and landing-registry mutations carry their corresponding revision. A `409` never overwrites newer state. The frontend receives the current safe revision and can reload while retaining a non-sensitive draft.

Credential rotation remains atomic and crash recoverable. The API preserves idempotency keys, one-time recovery receipts, static-access fail-closed behavior, kick/reload queues, and durability-uncertain outcomes. React is only a caller of these semantics.

## 7. React Application Design

### 7.1 Stack

- React 19-compatible stable release
- TypeScript in strict mode
- Vite with deterministic production build settings
- React Router for document routing
- TanStack Query for server state, polling, invalidation, and cancellation
- React Hook Form plus schema validation for complex forms
- Motion for React for page, layout, and micro-interactions
- Apache ECharts for quantitative charts
- Three.js through React Three Fiber for the two bounded 3D surfaces
- Vitest, Testing Library, MSW, Playwright, and axe-core for verification

The lockfile is committed. No runtime package is loaded from a CDN.

### 7.2 Source layout

```text
frontend/
├── package.json
├── package-lock.json
├── vite.config.ts
├── tsconfig.json
├── index.html
├── public/
└── src/
    ├── app/             providers, router, guards, error boundaries
    ├── layouts/         public, user, and admin shells
    ├── features/        capability-oriented page modules
    ├── components/      reusable accessible primitives
    ├── lib/api/         generated/manual request and response types
    ├── lib/query/       keys, polling, retries, mutation epochs
    ├── lib/security/    CSRF memory store and secret-handling helpers
    ├── styles/          tokens, reset, utilities, component layers
    └── visuals/         lazy Three.js scenes and motion utilities
```

Feature modules include authentication, users, usage, health, Codex quota, incidents, subscriptions, configuration, rules, landing egress, and operations.

### 7.3 State rules

- TanStack Query owns server state.
- Component state owns dialogs, drafts, filters, expanded rows, and transient selection.
- The URL owns navigable filters and selected resource identifiers when they contain no secret.
- Passwords, tokens, UUIDs, recovery receipts, and subscription URLs remain in memory only.
- `localStorage` is limited to non-sensitive presentation preferences: sidebar state, reduced-motion override, and optional chart density.
- Query persistence, service workers, offline caches, external analytics, and session replay are prohibited.

### 7.4 Polling

Reusable query policies preserve current operational behavior:

- overview and user summary: 30 seconds
- expensive chart series: 90 seconds
- Codex quota: server-provided interval, normally 180 seconds
- health: 30 seconds
- 10-second request timeout unless an endpoint requires a stricter existing bound
- exponential backoff up to 240 seconds with jitter
- no overlapping requests
- cancellation and pause while the document is hidden
- stale responses begun before a mutation are discarded with a mutation epoch
- polling does not re-enable a deleted or invalidated control

### 7.5 Forms and conflicts

Every form has a server-authoritative validation path. Client validation improves feedback but never replaces backend validation. Field errors focus the first invalid control and connect messages with `aria-describedby`.

On `409`, React opens a conflict surface that:

1. states which resource changed;
2. preserves only allowlisted, non-sensitive draft fields;
3. offers reload-and-review rather than blind overwrite;
4. clears password/token fields;
5. never displays a secret from either version.

Destructive actions use consequence-specific confirmations. Generic `confirm("确定吗")` prompts are not sufficient for delete, pause, reset, rotation, update, or configuration replacement.

## 8. Visual System

### 8.1 Direction

The product adopts a **midnight network control deck** aesthetic rather than a generic glass dashboard:

- near-black navy background with a restrained blue radial field;
- cyan for active network state, violet for secondary analytical state, amber for warnings, red for destructive/failing state, green for confirmed healthy state;
- thin luminous hairlines, inset borders, and subtle grid/noise layers instead of heavy drop shadows;
- Inter for interface copy and JetBrains Mono for identifiers, ports, bandwidth, timestamps, and revisions;
- dense but breathable information hierarchy suited to operations work;
- data cards that appear instrument-like without sacrificing conventional labels and controls.

Design tokens live in CSS custom properties and cover color, type, spacing, radius, border, shadow, motion, chart palettes, focus rings, and z-index. Components consume semantic tokens rather than raw color literals.

### 8.2 Motion

Motion reinforces state changes:

- short opacity/translate page entrances;
- shared-layout underline and sidebar transitions;
- spring-based dialog and drawer transitions;
- animated numeric deltas only when data changes;
- restrained status pulses for genuinely live states;
- chart transitions that do not replay on unchanged polling payloads.

There are no continuous decorative animations inside tables, forms, settings, or incident evidence. `prefers-reduced-motion: reduce` disables non-essential movement, animated counting, parallax, and Three.js rendering.

### 8.3 Three.js surfaces

Only two surfaces use React Three Fiber:

1. **Public/login network field:** a low-poly node-and-link field behind the hero/login card. It contains no real hosts, IPs, users, traffic, or credentials. Pointer movement changes the camera by a bounded amount; the form remains ordinary DOM above it.
2. **Admin overview protocol constellation:** an optional header visualization with three protocol clusters for Hysteria, Xray, and TUIC. It may consume aggregate, non-sensitive health/traffic proportions already visible to the administrator. It is not the only representation of those values.

Both scenes are dynamically imported after primary content is interactive. They pause when hidden, render at no more than 30 frames per second, cap device pixel ratio at 1.5 on desktop and 1.0 below 768 CSS pixels, dispose WebGL resources on unmount, and avoid post-processing pipelines. A CSS/SVG fallback is used when WebGL is missing, motion is reduced, the viewport is below 768 CSS pixels, or the scene is not ready within 1.5 seconds.

No critical action, status, chart label, or navigation affordance exists only in canvas.

### 8.4 Accessibility

- Normal text meets WCAG AA 4.5:1; large text and meaningful graphics meet 3:1.
- Every page has one focusable main landmark and a working skip link.
- Navigation exposes `aria-current`; mobile navigation traps and restores focus correctly.
- Dialogs use semantic modal behavior, Escape handling, initial focus, and focus return.
- Live refresh messages use bounded, non-chattering live regions.
- Heatmaps and charts have text summaries and keyboard-accessible semantic data tables.
- Wide tables remain keyboard-scrollable; mobile headers remain available to assistive technology.
- Color is never the only status indicator.

## 9. Error and Degradation Design

| Failure | Required behavior |
|---|---|
| SPA asset missing or mixed release | deployment/readiness fails and active release rolls back |
| API `401` | clear private query state, announce expiration, route to login |
| API `403` | render safe permission state without revealing target details |
| API `409` | preserve allowlisted draft and offer reload/review |
| API `422` | attach server errors to exact fields |
| API `429` | show bounded retry guidance and honor `Retry-After` |
| API `503` | retain non-sensitive input, show operational state, permit safe retry |
| Poll timeout/network loss | keep last known data marked stale; back off without overlap |
| Corrupt critical JSON/template | preserve fail-closed backend behavior; disable unsafe writes |
| Three.js import/WebGL failure | render static CSS/SVG fallback with no page failure |
| ECharts failure | retain numeric summaries and accessible tables |
| Unexpected React render error | route-level error boundary; no secret dump; retry or safe navigation |

The frontend never substitutes empty editable state for corrupt server state.

## 10. Security Design

- Remove all inline scripts and inline style attributes that are not unavoidable SVG presentation attributes.
- Final CSP removes `'unsafe-inline'` from `script-src`; styles are bundled and served same-origin. Any remaining required inline bootstrap uses a nonce rather than a broad policy exception.
- Preserve `default-src 'self'`, `base-uri 'none'`, `object-src 'none'`, `frame-ancestors 'none'`, `form-action 'self'`, `connect-src 'self'`, `Referrer-Policy: no-referrer`, anti-framing, and MIME sniffing protection.
- Never log or persist query-bearing subscription URLs. nginx access logs continue omitting query strings.
- Static asset serving is confined to the validated active release and cannot traverse into `/root/hysteria`.
- Vite source maps are excluded from production releases.
- Dependency installation uses a committed lockfile, integrity verification, disabled lifecycle scripts unless explicitly audited, and a non-root temporary build user/environment.
- Browser tests scan URL, history, DOM, storage, console, and network metadata for credentials.
- User-generated strings are rendered through React text interpolation; raw HTML insertion is forbidden except for reviewed, sanitized, static content.

## 11. Build, Deployment, and Rollback

### 11.1 Reproducible build

`frontend/package-lock.json` is committed. Deployment builds before stopping any service:

1. prepare an isolated temporary build directory;
2. install Node `24.20.0` LTS when absent using an architecture-specific checksum pinned in the repository, or use an already-installed exact match after verifying its path and version;
3. run dependency installation from the lockfile with lifecycle scripts disabled;
4. run typecheck, unit tests, and `vite build` as an unprivileged build identity;
5. validate the Vite manifest, allowed file types, regular-file ownership/modes, absence of symlinks, maximum file and total sizes, and absence of source maps;
6. compute a release identifier from the validated manifest and file hashes.

No npm development server runs in production.

### 11.2 Atomic release

Validated assets are installed root-owned under:

```text
/usr/local/share/hy2/panel/releases/<release-id>/
```

The deployment transaction registers the complete validated manifest and the active-release pointer in its durable recovery model. It installs the new immutable directory first, then atomically switches `current`. nginx never observes a partially populated release.

The recovery helper is extended to understand a manifest-validated static release as a single logical generation while still rejecting symlinks, unexpected paths, dynamic runtime files, and unbounded trees. Fault injection must prove recovery before this replaces the current exact-file frontend allowlist.

Old releases are pruned only after the deployment commits and at least the active and immediately previous release remain available. Rollback switches the pointer to the previous complete generation.

### 11.3 nginx and readiness

- `/assets/` uses `try_files`, `nosniff`, immutable caching, and no SPA fallback.
- SPA entry responses are `no-store`.
- `/api/v1`, backend-owned routes, downloads, subscriptions, and health probes retain proxy timeouts, rate limits, host validation, and query-redacted logging.
- Readiness validates Python `/healthz`, the SPA entry, manifest identity, representative JS/CSS assets, CSP, and three consecutive full-stack observations.

## 12. Migration Sequence

1. **Contract freeze:** capture existing API, authentication, concurrency, security, accessibility, and deployment behavior in durable tests.
2. **Backend extraction:** move policy/data functions into services and repositories without changing public behavior.
3. **API v1:** add typed JSON endpoints alongside the old renderer; run both against the same business services.
4. **Frontend foundation:** establish tokens, layouts, routing, session bootstrap, API client, query policies, and test harness.
5. **Feature migration:** implement every public, user, and administrator route to parity.
6. **Visual layer:** add ECharts, Motion, and the two lazy Three.js surfaces after functional parity.
7. **Parallel verification:** expose the new application only through a restricted preview path or test server; compare it against legacy behavior without exposing two production authorities.
8. **Atomic cutover:** deploy the complete Vite release and route known UI documents to it.
9. **Legacy removal:** remove Python render functions, inline scripts, `admin.css`, `admin_poll.js`, `usage.js`, `codex_quota.js`, `static/home.js`, legacy asset routes, and obsolete source-assertion tests.
10. **Final hardening:** remove CSP inline exceptions, run full recovery fault injection, browser security scans, accessibility checks, and production-like readiness tests.

The migration is complete only after step 10. A dual-renderer state is intermediate, never the completion condition.

## 13. Testing Strategy

### 13.1 Python

- Existing policy, state, quota, credentials, concurrency, recovery, proxy configuration, and operational tests remain.
- New API schema tests cover every endpoint, status code, safe error shape, auth role, revision, and no-store header.
- Mutation tests cover success, unauthenticated, forbidden, missing, stale revision, validation, durability uncertainty, and retry/idempotency.
- Old form/SSR tests remain during dual-run and are removed only with the corresponding legacy code.

### 13.2 Frontend unit and component

- API client and schema decoding
- auth guards and session expiration
- polling timeout/backoff/visibility/no-overlap behavior with fake clocks
- mutation epoch and stale-response rejection
- `409` draft recovery and secret clearing
- forms, unlimited values, field errors, destructive confirmations
- table/chart accessibility fallbacks
- WebGL/reduced-motion lazy fallback behavior
- route and error boundaries

### 13.3 Browser end-to-end

Playwright runs against a real Python test server and production Vite build:

- administrator and user password login/logout
- token-query exchange, URL scrubbing, cookie flags, revocation
- disabled/expired/unknown user indistinguishability
- create/edit/pause/resume/delete/reset/rotate flows
- concurrent stale edit and template/rule conflict recovery
- user self-service rotation recovery and landing-egress permissions
- usage, health, incidents, Codex quota, configuration, rules, settings, logs
- deep links and page refresh under every React route
- timeout, offline, `401`, `409`, `422`, `429`, and `503` states
- responsive mobile navigation, keyboard flow, focus restoration, reduced motion
- credential scans across DOM, URL, storage, logs, console, and request metadata

### 13.4 Accessibility and visual verification

- axe-core automated checks on every route and key dialog state
- keyboard-only smoke suite
- contrast token tests and heatmap palette checks
- semantic heading, landmark, live-region, and data-table assertions
- screenshots at desktop, tablet, and mobile widths for stable critical pages
- manual NVDA or VoiceOver spot checks before final cutover

### 13.5 Deployment and recovery

- deterministic build and manifest validation tests
- nginx route containment and cache/header tests
- fault injection before, during, and after release installation and pointer switch
- rollback proves the old entry and all referenced hashed assets return together
- readiness rejects missing assets, mixed manifest identity, wrong CSP, or unhealthy Python state

## 14. Performance Budgets

- Primary login/admin shell becomes interactive before any Three.js chunk is requested.
- Initial shared JavaScript budget: 220 KiB gzip, excluding route-lazy chart and Three.js chunks.
- Shared CSS budget: 80 KiB gzip.
- Three.js/R3F is isolated in a lazy chunk and never blocks authentication or primary dashboard data.
- Production source maps are not shipped.
- Admin overview payload remains lightweight; chart arrays stay on slower, separate queries.
- Unchanged polling payloads do not trigger full chart or page rerenders.
- Three.js scenes cap DPR/frame rate and stop while hidden.

Budgets are build/test gates. A budget increase requires an explicit design update rather than silent drift.

## 15. Documentation

Update both README languages and operational docs with:

- frontend development commands;
- supported Node/npm versions and lockfile policy;
- production build and one-command deployment behavior;
- nginx route ownership and asset paths;
- API v1 overview;
- atomic frontend release and rollback model;
- CSP and no-CDN policy;
- browser/E2E test commands;
- troubleshooting for missing WebGL, failed build, asset mismatch, and rollback.

## 16. Completion Criteria

The project is complete only when all of the following are proven in the current worktree:

1. Every listed public, user, and admin route is implemented in React and verified in a production build.
2. Every panel read and mutation uses `/api/v1` or an explicitly backend-owned download/token/subscription route.
3. Python no longer generates panel HTML or serves legacy CSS/JavaScript.
4. Legacy frontend files and obsolete tests are removed.
5. Authentication, URL token scrubbing, session invalidation, capability distinctions, concurrency revisions, credential recovery, fail-closed behavior, and subscription generation pass their preservation tests.
6. The visual design is consistently applied, and both Three.js surfaces degrade safely without blocking controls or information.
7. Unit, component, Python integration, Playwright, axe, build, security, deployment, and recovery suites pass.
8. nginx serves one complete immutable Vite generation, dynamic responses remain uncached, and CSP no longer permits arbitrary inline scripts.
9. Deployment fault injection proves atomic cutover and rollback without mixed assets.
10. README and operational documentation describe the new architecture accurately.
11. Existing unrelated worktree changes in `hysteria/clash-default.yaml.tpl` and `tests/test_clash_template.py` remain intact.

## 17. Open Questions

None. The operator approved continuous progress, and this specification pins the remaining architectural, visual, deployment, security, and verification decisions.
