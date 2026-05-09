# ADR-0003: Explicit Asia/Shanghai for time-bucket keys

**Status:** Accepted
**Date:** 2026-05-08

## Context

Daily and (newly added) hourly traffic accounting use string keys derived from `datetime.now()`. On a host configured to UTC (the Linux default), keys land on UTC day/hour boundaries; on a host configured to `Asia/Shanghai`, they land on local boundaries. The admin operator reads keys as Shanghai dates regardless, so the wrong host config silently produced 8h-shifted buckets that no longer aligned with the displayed dates. The shift is a real operational hazard once hourly granularity surfaces "the daily total ≠ sum of that day's 24 hours".

## Decision

Introduce `hysteria/timeutil.py::local_now()` returning `datetime.now(ZoneInfo("Asia/Shanghai"))` and route all time-bucket key generation through it:

- Daily key (`YYYY-MM-DD`) — `accumulate_daily`, `daily_window_for_user`, the historical 14-day table inside `render_usage_page`
- Hourly key (`YYYY-MM-DDTHH`) — `accumulate_hourly`
- Cycle key (`YYYY-MM`) — `month_key()` (now accepts optional `now`)
- "Today" derivations across `subscription_service.py`

Audit log timestamps continue to use `datetime.utcnow().isoformat() + "Z"` — that's a separate concern, industry norm, and audit-log consumers (`usage_reset.log`, future `alert.log`) already parse UTC.

## Consequences

- Host-tz-independent: keys are always Shanghai-bucketed.
- One-time visible bucket shift on existing UTC-host deployments at upgrade — daily-table dates appear to advance ~8h once. The 30-day window naturally rotates within retention.
- New code must use `local_now()`; reviewers should reject new `datetime.now()` for time-bucket purposes.
- `auth_backend.py:89` is intentionally exempt (UTC-naive session expiry, not a bucket key).
- `zoneinfo` falls back to the `tzdata` PyPI package on hosts without a system tz database (Alpine, distroless containers, vanilla Windows). Production target is a stock Linux server — `/usr/share/zoneinfo` is present, no extra install needed. If the deployment topology ever expands to minimal containers, install `tzdata` via pip.
