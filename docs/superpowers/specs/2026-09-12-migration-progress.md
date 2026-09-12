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
| React login and write transport | Not yet implemented | HTTP cutover notes list boundary requirements |
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

Do not retire legacy rendering or page scripts until the entire parity register
is accepted and the separately approved cutover/rollback has been verified.
