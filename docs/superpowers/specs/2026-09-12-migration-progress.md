# Personal-site migration progress

Source branch: `refactor/personal-site-foundation`. Production is unchanged.
This is an acceptance index, not a claim that the full migration is complete.

| Surface | Current state | Acceptance reference |
| --- | --- | --- |
| Baseline fixtures and route inventory | Complete for migration baseline | preview-parity-foundation plan |
| FastAPI read boundary | Session, admin overview and reset logs JSON; no production mount | fastapi-read-boundary and react-logs-slice plans |
| React administrator shell and reset logs | Controlled `/__react/admin/logs`, real fixture cookies/API, desktop/mobile and error recovery verified | `27be0c3`, react-logs-slice plan |
| React public home | Controlled `/__react/`, content/layout/tabs/motion/initial anchors verified | `7b69a77`, `177dd29`, `82406a6`, react-public-home plan |
| Shared-module release packaging | Reset log reader registered; source import-closure regression added | `54114f4`, shared-module-deploy-contract plan |
| Login service | Shared decision and exception-safe reservation cleanup verified; existing HTML route remains consumer | `4734582`, login-service-boundary plan |
| Shared strict form decoding | Header and byte helpers extracted; legacy consumer and rejection behavior verified | `e7b14b2`, shared-form-decoding plan |
| FastAPI login transport | POST `/api/v1/login`, shared login decision, bounded receipt/admission and full security headers; no production mount | `d596ab7`, `cff57c9`, fastapi-login plan |
| React login | Controlled `/__react/login`, real fixture auth/cookies, pending/error/stale handling and desktop/mobile parity verified; preview permits exact login POST only | `a645d9e`, `7cad057`, react-login plan |
| Preview request lifetime | Shared socket shutdown/handler drain encloses React and legacy fixture guards; partial requests and active auth verified | `7cad057`, react-login acceptance |
| FastAPI logout | Two fixed-realm POST endpoints; real current-device revocation, failures and concurrency verified; no production mount or preview forwarding | `f82653e`, fastapi-logout plan |
| Preview quality adoption | Shared helper included in the adopted import/format gate; exact repository lint wrapper green | `9bed63e`, react-login acceptance |
| Remaining administrator pages and mutations | Legacy implementation remains authoritative | full spec parity register |
| React user panel | Not yet implemented; legacy panel unchanged | full spec parity register |
| Staging, production switch, cleanup | Not started; explicit deployment checkpoint required | full spec cutover and completion gates |

## Verification boundaries

Every task has its own reviewed diff and covering tests. Full backend checks
are reserved for cross-cutting changes; focused tests cover source-inventory
and frontend-only fixes. Passing preview tests does not establish production
asset caching, routing, runtime drift, rollback or deployment readiness.

Known upstream TestClient/AnyIO deprecations and pre-existing naive-UTC warnings
remain visible and tracked for final integration review. No warnings were
blanket-suppressed. No live settings, credentials, certificates, domain mappings,
proxy configuration or user balances were copied into previews.

The login browser's full-document navigation checks are not a direct React-root
unmount exercise; retain this coverage limitation for final integration review.

Do not retire legacy rendering or page scripts until the entire parity register
is accepted and the separately approved cutover/rollback has been verified.
