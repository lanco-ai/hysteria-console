# hy2

A self-hosted Hysteria2 + Xray (VLESS Reality) deployment with a built-in subscription panel and admin dashboard. Single Linux server, run by a solo operator, JSON-as-database.

## Language

### Time and accounting

**Billing cycle**:
A fixed-length usage-accounting period. By default it is a 30-day cycle anchored on day 12; operators can change `settlement_day`, `cycle_length_days`, and the persisted `cycle_anchor_date` from the admin panel. Quotas are enforced per cycle; cycle resets are automatic on the configured settlement boundary.
_Canonical key:_ `cycle_key`, format `YYYY-MM`, kept for legacy state and alert dedup. The displayed cycle range itself is derived from `cycle_anchor_date` + `cycle_length_days`, not from the key name.
_Aliases in legacy code (do not introduce new uses):_ `month_key`, `billing_month_key`, `month`, `周期`, `统计月份`, `计费月份`. New code, new alert payload fields, new UI strings must say **cycle**.

### Bytes accounting

**Raw bytes** (`raw_bytes`):
The byte counts returned by hysteria's `/traffic?clear=1` and xray `statsquery -reset`. These are the *application-level* bytes the user actually exchanged — what their proxy client downloaded/uploaded. Stored in `usage.json` and `usage_daily.json` verbatim.

**Displayed bytes** (`displayed_bytes`):
`raw_bytes × DISPLAY_MULTIPLIER`. This is what gets shown to users and admins, and what counts against `monthly_quota_bytes` for kick decisions and quota alerts. The multiplier (currently **2.28**) reflects the operator's *real* outbound bandwidth cost: when a client transfers 1 GB through the proxy, the server actually moves about 2.28 GB across its egress link (relay/encryption/transit overhead). Users are billed for that real cost, not their application-level perception.

_Aliases in legacy code:_ `scaled` (in function names like `scaled_usage_for_user`). Acceptable in existing code; new code says `displayed_bytes`.

**Iron rule**: never compare or arithmetically combine raw and displayed values. Code that does is a bug. Z-score anomaly math is in raw; quota threshold checks and all UI strings are in displayed; the boundary is a one-way `× DISPLAY_MULTIPLIER` step, applied at the *latest* possible moment. If the relay topology ever changes, `DISPLAY_MULTIPLIER` must be re-measured — it is not arbitrary.

### Time-zone for bucket keys

All time-bucket keys (daily `YYYY-MM-DD`, hourly `YYYY-MM-DDTHH`, cycle `YYYY-MM`) are generated from `timeutil.local_now()` — explicit `Asia/Shanghai`. See [ADR-0003](docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md). Audit log timestamps keep using `datetime.utcnow().isoformat()+"Z"` — separate concern.

### Cycle and reset operations

**Cycle rollover** (`周期翻页`):
The automatic, system-driven transition into a new billing cycle on the configured settlement day. `traffic_limiter.maybe_reset_all_usage_on_day_21` zeroes every user's per-cycle counters; `auto_reset_state.last_reset_month` guards against double-firing. Idempotent across multiple cron ticks on the same day. Cycle rollover is *time*, not an action — operators take no part in it.

**Manual reset** (`手动重置`):
The admin actively zeroing a user's (or all users') current-cycle usage *before* the cycle ends. Used to refund traffic, fix metering errors, or grant promotions mid-cycle. Two flavors: `reset_usage_user` (one user) and `reset_usage_all` (every user). Logged to `usage_reset.log` along with cycle rollover events.

_Aliases in legacy code (do not introduce new uses):_ "auto reset day21", "reset" used without a qualifier. Reset logs already use distinct `action` values per row — that distinction is canonical, the names below it are not.

### User types

**Metered user** (`metered` flag, `按量`):
A user whose **displayed bytes** are checked against `monthly_quota_bytes` every cron tick; over-quota means kicked from active sessions and quota alerts fire. The "customers" of the service in the operator's mental model.
_UI badge:_ "按量" (yellow/info color).

**Unmetered user** (no `metered` flag set):
A user whose traffic is recorded but never enforced — typically the operator's own account or trusted accounts. No `monthly_quota_bytes` check, no kicks, no quota alerts. (Anomaly alerts still fire — those are about behavior, not quota.)

**Admin**:
The single backend-panel session, authenticated by a separate password hash in `subscription_meta.json`. Lives entirely outside `users.json`. There is no in-band concept of "admin user" mixed with proxy users.

_Aliases in legacy code (do not introduce new uses):_ `guest` field name, `cfg.get('guest')` checks, "访客" UI label. The Chinese "访客" was misleading — it usually means "anonymous free" in SaaS, whereas here it actually means "billed, quota-limited". New code writes `metered` and reads `cfg.get('metered', cfg.get('guest', False))` for backward compatibility with existing `users.json` data.

### Connectivity

**Inbound port pair**:
xray runs two VLESS Reality inbounds, on **port 443** (primary) and **port 8443** (backup). Both serve the same protocol with the same UUIDs; 8443 exists so a client whose 443 path is blocked can still reach the server. See [ADR-0002](docs/adr/0002-xray-dual-inbound-backup-suffix.md).

**Backup-suffixed client** (`<username>@hy2-backup.invalid`):
The xray-config-level entry that represents a user on the 8443 inbound. Generated by `_xray_email_for(8443, username)`. The reserved-domain suffix is stripped during traffic aggregation, so users themselves never see it — it lives entirely inside `xray/config.json` and the stats pipeline.

**Online sessions** (`online`):
A point-in-time snapshot of active hysteria2 sessions per user, published by hysteria's `/online` API and refreshed on the 90-second traffic-limiter tick into `online.json`. Each successful capture also writes `online.meta.json`, which binds the snapshot digest to its Unix capture time. Treated as synonymous with "active devices" because each device typically holds one session. Used by `auth_backend.py` to enforce `max_devices` at *login* time only — sessions already established are never killed for exceeding `max_devices`, only new logins are rejected. Authentication normally queries the live API; if that query fails, it accepts the cached snapshot only while its matching metadata is at most 20 seconds old. Because the collector cadence is longer than that safety window, the fallback is intentionally available only just after a successful tick; otherwise capped-user logins fail closed rather than trusting stale counts. Missing, mismatched, future-dated, or stale metadata also fails closed. Users without `max_devices` do not depend on this cache. This snapshot can flicker between ticks and is not authoritative for billing.

**Max devices** (`max_devices`):
Per-user soft cap on concurrent sessions, enforced at login. Default 2. Value 0 means "no cap" (any number of concurrent sessions allowed). Independent from `monthly_quota_bytes` — a user can hit the device cap without using any bytes.

### Identity and access

**Subscription token** (`sub_token`):
A per-user random secret issued at user-creation time. The same token authenticates **two** URLs:
- `/sub/<user>?token=...` — returns a Clash YAML subscription file (consumed by client apps via "import URL")
- `/panel/<user>?token=...` — returns the user's HTML panel page (their personal dashboard with usage and quota)

There is no separate password for these; the token *is* the authentication. Rotating the token invalidates both URLs simultaneously.

**Hysteria auth password**:
Optional separate secret used by clients that authenticate against Hysteria directly (legacy non-subscription flow). Stored as a PBKDF2 hash in `users.json` under `password_hash`. Most users never set this — they authenticate through the subscription URL flow only. The persistent loopback HTTP service checks this hash under its shared rate and CPU budgets. The emergency process-per-request command CLI is deliberately token-only and will not evaluate this hash.

**Hysteria authentication path**:
`hysteria-auth.service` is a persistent, loopback-only HTTP bridge on `127.0.0.1:8082`. Hysteria posts `{addr, auth, tx}` to `/auth`; the bridge strictly validates the envelope, canonicalizes the official IP/port address, and calls `auth_backend.authenticate_payload`, which owns the canonical credential, lifecycle, quota, and device-admission policy. Structurally valid denials—including PBKDF2 rate/concurrency throttles—return HTTP 200 with `{"ok":false}` because that is Hysteria's protocol contract. Tokens bypass the bounded PBKDF2 work gate. Before success, the backend re-reads `users.json` and compares a fixed-size digest of the target user's full authorization generation, rejecting a credential rotation, disable/expiry, or policy edit that raced the request. A single absolute deadline covers headers, body, policy work, and every loopback online-API read/write; accepted connections are watched from accept time, including executor queue time. The service has fixed worker/pending bounds, a separate bounded overload responder, and never logs the auth payload. `/livez` is a shallow process probe. `/readyz` (and `/healthz`) requires the live online API plus admission-ledger operability without consuming a reservation; only individual capped-user authentication may use a matching snapshot no older than 20 seconds. `hysteria-server.service` remains lifecycle-bound to auth and runs the deep probe before marking a root-only runtime intent. An auth-caused stop preserves that marker; a deliberate server stop while auth is active clears it. Auth recovery starts only an enabled server with preserved intent, so it repairs an outage without undoing an operator stop. Deployment requires three consecutive live deep observations. `auth_backend.py` retains a two-second, token-only command CLI contract for emergency compatibility; PBKDF2 is rejected there because independent processes cannot share the HTTP service's global/concurrent budget.

**Admin password**:
Single password for the `/admin` panel. Stored as pbkdf2 hash in `subscription_meta.json`. Sessions live in `state/panel_sessions.json`. Login failures are rate-limited at 3/hour per IP.

**User panel password**:
Optional password used with the proxy username at `/user/login`. Stored as a PBKDF2 hash in `users.json` under `panel_pass_hash`; it is deliberately separate from both `sub_token` and the Hysteria auth password. Successful login creates a 24-hour HTTP-only `usid` session in `state/user_panel_sessions.json` and opens the clean `/user/panel` URL. Passwords set or reset by an admin carry `panel_password_must_change`, forcing the user through `/user/change-password` before the panel opens. A successful self-service change clears that flag, revokes the user's other panel sessions, and keeps the current device signed in with a fresh session. A token-authenticated panel link is also exchanged immediately for a token-bound `usid` session and redirected to `/user/panel`; rotating the token invalidates every session minted from the old link.

Self-service token rotation requires that valid `usid` and a hidden idempotency key. Before the canonical save it persists a mode-0600 receipt in `state/credential_rotation_receipts.json`, bound to a digest of the original session for five minutes and capped at 256 entries. A replay first compares the receipt's old/new generation and UUID with `users.json`: a prepared-but-uncommitted request resumes with the same generated token, a committed request re-delivers that token, and a superseding admin/self rotation invalidates the receipt without exposing either credential. Replacement session IDs may be bound to the same receipt, so loss at the session-save or redirect boundary is also recoverable. Receipt state is never logged, is excluded from backups, and is actively pruned by the background worker even when no further rotation request arrives.

`state_store.AtomicReplaceDurabilityUncertain` distinguishes failures before `os.replace` from a directory-fsync failure after the new file became visible. Rotation re-reads and compares both generated credentials; an exact match is treated as committed-but-durability-uncertain, delivered to the authorized browser, and static auth stops fail-closed. A replay rewrites the matching canonical generation to establish a fresh durability point before reconciliation. Session mint occurs under the user-state lock after a generation recheck, closing the self/admin interleave between commit and mint; a later generation returns a secret-free 409 instead of an instantly invalid cookie. A completed recovery page is HTTP 200, `no-store`, and `no-referrer`.

Credential side effects are explicit structured outcomes. `state/credential_revocations.json` is a secret-free, mode-0600 durable queue prepared before the canonical commit and capped at 512 unfinished tasks. Pending work is not silently pruned by wall-clock age: committed rotations and revision-bound account deletions survive long outages, while an expired pre-commit rotation intent is discarded only when its previous generation remains canonical. It requires two spaced successful Hysteria kicks, covering an auth request already in flight during the first kick, and retries failed static reconciliation/stops. The kick client uses a direct `http.client` connection to `127.0.0.1:25413`, ignores proxy environment variables, and bounds total time and response bytes so the API secret cannot be forwarded to a configured proxy. Xray/TUIC reload scheduling failures immediately attempt a structured fail-closed stop; durable reload/fail-closed markers remain the recovery authority. UI copy says “confirmed paused” only when the stop effect was observed, otherwise it warns that revocation is unconfirmed and still retrying. Worker errors log only their type with bounded exponential suppression and reset after recovery. Both ephemeral rotation files are excluded from backups.

## Relationships

- **Cycle rollover** zeroes **raw bytes** for every user; **displayed bytes** therefore reset too (they're a function of raw).
- A **manual reset** zeroes **raw bytes** for the affected user(s) within the current **cycle**; the cycle key itself does not change.
- A **manual reset** clears the affected users' alert dedup state (see [ADR-0001](docs/adr/0001-manual-reset-clears-alert-dedup.md)).
- Only **metered users** are subject to quota enforcement, kick, and quota alerts. **Unmetered users** still receive anomaly alerts and still appear in usage tables.
- Each user has exactly one **subscription token** that authenticates both their `/sub/` YAML and their `/panel/` page.
- Each user has exactly two xray client entries (one per **inbound port**), keyed `<username>` and `<username>@hy2-backup.invalid`.
- **Online sessions** count is enforced against `max_devices` at *login*, not continuously — already-connected sessions are not killed when the cap is lowered.

## Example dialogue

> **Engineer:** "alice was at 12 GB last week and now her panel shows 5 GB — what happened?"
> **Operator:** "She crossed the configured **cycle rollover** boundary. She's now in a new cycle instead of the previous one."
>
> **Engineer:** "But the alert dedup state still has her flagged for `quota_80` in the old cycle..."
> **Operator:** "That's fine — the dedup key is the cycle. New cycle, new dedup row, alerts re-arm automatically. **Manual reset** is the only case that needs explicit dedup cleanup."
>
> **Engineer:** "OK. Her hysteria session count keeps bouncing between 1 and 2 every minute though — bug?"
> **Operator:** "No, **online** is a point-in-time snapshot refreshed by the 90-second collector. It can flicker. We only enforce `max_devices` at login, not in real time."

## Flagged ambiguities

- "month" / "月份" historically conflated **billing cycle** with the natural calendar month. They are not the same — the cycle is configurable and may cross natural month boundaries. Resolved 2026-05-05: **billing cycle** is canonical.
- "guest" field name and "访客" UI label inverted the usual SaaS meaning ("guest" usually = anonymous free; here it = quota-enforced paid customer). Resolved 2026-05-05: canonical name is **metered** / "按量"; legacy `guest` field stays readable via fallback.
- "reset" without a qualifier was used for both **cycle rollover** (system-driven, time-triggered) and **manual reset** (operator-driven, ad-hoc). These have different correctness implications for alert dedup state. Resolved 2026-05-05 — see [ADR-0001](docs/adr/0001-manual-reset-clears-alert-dedup.md).

## Flagged ambiguities

- "month" / "月份" historically conflated **billing cycle** with the natural calendar month. They are not the same — the cycle is configurable and may cross natural month boundaries. Resolved 2026-05-05: **billing cycle** is the canonical concept; existing `month_key()` / `billing_month_key()` functions stay (rename cost > value), but their docstrings and any new caller name the parameter `cycle_key`.
