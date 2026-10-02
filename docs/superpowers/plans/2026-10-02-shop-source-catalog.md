# Retail catalogue from a private supplier feed

## Accepted scope

The user supplied qiangyunai.com as the first GPT supplier and requested real
products on the existing home page. They clarified that they are a reseller:
costs are administrator-only, retail prices are entered individually, and a
customer must stay on this website to inspect purchase information before
contacting the reseller's own Telegram for manual payment and delivery.
The reseller handle is pending and must be configurable; supplier contacts are
never substitutes. No commit, push, merge or production deployment is authorized
for this new task. The paused TUIC task is separate and unchanged.

## Baseline and verified source

- Worktree: `/root/hy2/.worktrees/shop-source-catalog`.
- Branch: `codex/shop-source-catalog`, base `2c390079d58a0d8d1af07ecb61f32e8e5e511297`.
- Current production UI baseline is newer than origin/main; preserve it.
- Existing ShopPage is an empty category/search/table shell, with no product API.
- Real source discovery on 2026-10-02: `/api/v1/public/config` reports CNY;
  `/api/v1/public/products` has status_code=0, five products, seven active SKUs,
  localized fields, pagination, price_amount decimal strings, sold-out flags,
  and hidden stock quantities. Public JavaScript calls these endpoints.
- Product paths are `/products/{slug}`; these are for private provenance only.
- Original public responses are temporary evidence in
  `/dev/shm/catalog-source-20261002`; do not ship their product data as live data.
- Baseline: source-adapter tests 18 passed. Full CI of another task revealed
  pre-existing test fixture isolation issues; do not claim them fixed or all green.

## Design

### Private acquisition and merchant configuration

Use fixed HTTPS requests to the supplier's public config and products endpoints.
No user-supplied URL, authentication, checkout, Telegram scraping or contact.
Bound pages, body size, connect/read/overall time; reject redirects and 403/429,
use a retry interval rather than busy retries. Normalize active SKUs independently
so new activation and renewal variants cannot silently share a retail price.
Use Decimal/integer cents, reject ambiguous prices/currency and duplicate IDs.
Treat external names/descriptions as text; never render upstream HTML.
Hidden inventory remains quantity=null, even if the API has sentinel stock=1.
No sales field means sales=null, never fabricated zero.

Keep a limited private JSON source snapshot and merchant settings separate from
all proxy/billing/credential state. Reuse state_store locks and atomic writes.
Default new SKUs to unpublished with no selling price. Merchant settings contain
reseller Telegram username plus per-SKU integer retail price and publication
flag; updates use revision comparison under a file lock. Source refresh must not
overwrite configured selling prices. Removed/disabled supplier SKUs cannot sell.
Validate the persisted state before any public projection.

Use a lifecycle-owned refresh task (15-minute source freshness, failure backoff,
multi-process exclusion). Startup and public reads must remain usable if supplier
is unavailable. Failed refresh retains the last successful snapshot; show a safe
stale/error status. No public request may bypass cooldown or fetch arbitrary URLs.

### Routes and permissions

- Public `GET /api/v1/shop/catalog`: allowlisted retail projection only. Never
  expose cost, profit, supplier names/URLs/contact, raw upstream fields or HTML.
- Administrator `GET /api/v1/shop/admin`: private normalized source, costs,
  merchant settings, revision and last success/failure status.
- Administrator `PUT /api/v1/shop/admin`: bounded validated settings update;
  require existing administrator session and existing same-origin CSRF checks.
- Administrator `POST /api/v1/shop/refresh`: bounded controlled refresh using
  the same lock/cooldown as automatic refresh.
- `GET /admin/shop`: explicit existing document/router whitelist entry, same
  admin guard. Add 商品管理 in the management navigation, preserve unknown 404s.

### Existing home and merchant interface

Keep the portal header, theme, search and category/list layout. Display real
published products grouped by product, selectable priced SKU variants, retail
price/range, truthful availability and unknown sales as an em dash. Responsive
rows become compact cards on phones. Keep third-party logos/assets out of the
public browser; use the existing icon style or a local GPT text badge.

The purchase dialog shows product, precise SKU, quantity and retail total only.
Label it as purchase information awaiting manual confirmation: no invented order
number, payment success, reservation, automatic delivery or saved-order claim.
Provide copyable purchase information and a link only to the configured reseller
Telegram username. Missing contact disables contacting, with a clear message.
Never silently link to a supplier. Reopening uses the current catalogue. Enforce
sold-out/removed states, bounded integer quantities and exact price arithmetic.

The new admin page offers source refresh, private costs, per-SKU selling price,
publication controls and the reseller Telegram field. Draft products are visible
to the admin even while the anonymous catalogue is empty. Preserve unsaved edits
on failures/conflicts; do not mark them saved until confirmed by the server.

## Implementation units and checks

1. `web_api/shop_source.py` plus offline fixtures/tests: actual API contract,
   CNY, SKU separation, exact decimals, hidden inventory, malformed/duplicate
   data, bounded HTTP, denials/timeouts/redirects and pagination.
2. `shop_store.py`, `shop_routes.py`, factory/lifecycle wiring and route tests:
   private/public projection, no-cost leakage, drafts, revision/CSRF/auth,
   refreshed cost preserving retail, stale cache, failures and concurrent refresh.
3. ShopPage/product components, `/admin/shop`, existing CSS and browser tests:
   real API display, draft publication, price editing, SKU/quantity totals,
   customer Telegram flow, no supplier link, 390/768/1440px screenshots,
   loading/empty/failure states, keyboard/focus and retained AI navigation.
4. Run targeted backend tests, typecheck, lint, build, browser acceptance and
   appropriate full quality gates without deleting or weakening existing gates.
   Use fixed fixture data only for tests. Run one separate live source smoke.
5. Fresh GPT-6 Sol/high independent review of money/data boundaries, lifecycle,
   routes, layout, source failure and regression risks. Repair within 2-round
   budget. Report actual commands/results, screenshot paths, branch/status and
   remaining merchant configuration. Do not publish without task authorization.

## Resource and ownership constraints

Root disk has about 127 MiB free: reuse existing node_modules/test tools, avoid
installing browsers or large dependencies. Use only task-owned temporary output
and monitor free disk before builds/tests; never delete another task or production
data. No source/runtime/service changes under `/root/hysteria`, no service restart.
Only one writer; controller maintains checkpoint, independent reviewer read-only.
