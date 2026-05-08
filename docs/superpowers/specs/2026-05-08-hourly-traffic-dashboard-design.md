# Hourly Traffic Dashboard — Design Spec

**Date:** 2026-05-08
**Status:** Approved by operator, ready for implementation plan
**Successor to:** `2026-05-05-observability-and-alerting-design.md` (this spec assumes the alerting/anomaly bundle is shipped)

## 1. Motivation

The existing `/admin/daily` page surfaces a 14×N table of per-day totals. When the operator asks "why did bandwidth spike last night?" or "who's driving the load right now?", a daily resolution buries the answer — a 4 GB hour-long burst at 02:00 averages out across a 24-hour cell.

This spec adds **hour-level resolution** to the existing observability stack and turns the analysis surface from a table into a single dashboard page that answers the operator's three habitual questions:

1. **Trend** — "did anything weird happen this week?" (168-bar timeline)
2. **Pattern** — "is the spike a recurring evening peak or a one-off?" (7×24 heatmap)
3. **Attribution** — "who's responsible right now?" (Top-5 in last 24h, click-through to per-user drill)

The page replaces `/admin/daily` (with a 301 redirect) and stays inside the project's "single-server, JSON-as-database, server-rendered SVG, ~zero JS" envelope.

## 2. Scope

### In scope
1. New `accumulate_hourly()` in `traffic_limiter.py`, mirroring `accumulate_daily()` (separate JSON file, 168-hour retention)
2. New `hysteria/timeutil.py` — single source of truth for `local_now()` returning `datetime.now(ZoneInfo("Asia/Shanghai"))`. Apply to all time-bucketing in `traffic_limiter.py` and `subscription_service.py` (daily key, hour key, cycle key, today). Audit log timestamps stay UTC.
3. New `hysteria/charts.py` — pure SVG generators: `hourly_bars_svg()`, `weekday_hour_heatmap_svg()`, `mini_sparkline_svg()` (factor existing `sparkline_svg` here).
4. New page `/admin/usage` (replaces `/admin/daily`):
    - 4 stat cards (current hour / today / last 7d / current cycle)
    - 168-bar hourly chart with hover tooltips
    - 7×24 heatmap
    - Top-5 list (last-24h ordered) with per-user 24h sparkline
    - Collapsed `<details>` block containing the existing 14/30-day daily table
5. New page `/admin/user/<uid>` — per-user drill-down with hourly bars, personal heatmap, recent alerts
6. New JSON endpoint `/admin/usage.json` — feeds the page's 5-second polling loop
7. New JSON endpoint `/admin/user/<uid>.json` — feeds the drill page's polling loop
8. New `hysteria/static/usage.js` (~60 lines) — vanilla JS for hover tooltips and partial DOM swap on poll
9. ADR-0003 documenting the explicit `Asia/Shanghai` time-bucketing decision
10. New tests `tests/test_hourly.py`, `tests/test_usage_page.py`, plus extensions to `test_smoke.py` and `test_alert_integration.py`

### Explicitly out of scope
- User-facing hourly view on `/panel/<user>` — admin-only this round, mirroring the alerting bundle's choice
- Hourly granularity for the alert dispatcher (anomaly detection still runs on daily z-scores per ADR-0001 era; hourly bars are display-only)
- Hourly retention beyond 7 days — deeper history goes to the existing daily series
- Adjustable window controls on the front end (Top-N count, 24h vs 7d) — fixed values, YAGNI
- Migration of historical UTC-bucketed daily data when switching to Shanghai-tz (best-effort: any pre-existing keys are read as-is, new writes go to Shanghai-bucketed keys; the 7–30 day window naturally rotates to clean state within retention)
- A separate Top-N metered-only / unmetered-only filter (Top-N includes both)
- Hourly data export (CSV/JSON download buttons) — operator can read the JSON file directly if needed

## 3. Architecture

```
hysteria /traffic?clear=1 ──┐
                            ├─► merge_traffic ──► usage.json (cycle)
xray statsquery -reset ─────┘                ──► usage_daily.json   (existing, 30d)
                                              ──► usage_hourly.json (NEW, 168h)
                                                       │
                                                       ▼
                          subscription_service routes
                          ┌─────────────────────────────┬──────────────────────────────┐
                          ▼                             ▼                              ▼
                    /admin/usage              /admin/usage.json              /admin/user/<uid>
                    (HTML render via            (5s poll feed,                (HTML render via
                     charts.py SVG fns)         partial-update DOM)            charts.py SVG fns)
```

**Files touched** (4 new, 2 modified):

| File | Action | Line count rough |
|---|---|---|
| `hysteria/timeutil.py` | new | ~10 |
| `hysteria/charts.py` | new | ~150 |
| `hysteria/static/usage.js` | new | ~60 |
| `docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md` | new | ~40 |
| `hysteria/traffic_limiter.py` | modify | +30, -3 |
| `hysteria/subscription_service.py` | modify | +200, -100 (replaces `render_daily_usage`, adds two routes) |

**Files unchanged**: `alerts.py`, `anomaly.py`, `auth_backend.py`, `xray_config.py`, `display.py`, `user_compat.py`, `deploy.sh`, all systemd units, all nginx config.

## 4. Data Model

### 4.1 New file `usage_hourly.json`

Path: `/root/hysteria/state/usage_hourly.json` (covered by `state/` in `.gitignore`).

Schema (mirrors `usage_daily.json`):

```json
{
  "2026-05-08T14": {
    "alice": {"tx": 1234567, "rx": 8901234, "total": 10135801},
    "bob":   {"tx": 5000000, "rx": 1000000, "total": 6000000}
  },
  "2026-05-08T15": { },
  ...
}
```

- **Hour key format:** `YYYY-MM-DDTHH` (no minutes, ISO-like). Always Shanghai-bucketed.
- **Retention:** 168 hours (7 × 24). `prune_hourly()` mirrors `prune_daily()`, keyed on the cutoff hour.
- **Storage units:** raw bytes (per-user `tx`, `rx`, `total`). The `× DISPLAY_MULTIPLIER` step happens at the latest possible moment in `charts.py` and stat-card aggregation, per CONTEXT.md's iron rule.
- **Concurrency:** writes happen inside `usage_lock()` from `traffic_limiter.py` (existing fcntl flock). Readers (subscription_service in another process) tolerate eventual consistency — already the pattern for `usage_daily.json`.
- **Estimated size:** 50 users × 168 hours × ~80B per JSON entry ≈ 670 KB. Same order of magnitude as the existing daily file.

### 4.2 New module `hysteria/timeutil.py`

```python
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")

def local_now() -> datetime:
    """Project-wide canonical 'now', explicitly in Asia/Shanghai.

    Use for any time-bucket key (daily, hourly, cycle). Audit log timestamps
    keep using datetime.utcnow() — that's a separate concern (industry norm).
    """
    return datetime.now(LOCAL_TZ)
```

### 4.3 Replacement of naive `datetime.now()` calls

Audit (from grep):

| File:Line | Current | Replacement | Rationale |
|---|---|---|---|
| `traffic_limiter.py:282` | `now = datetime.now()` | `now = local_now()` | drives daily/hourly/cycle keys |
| `subscription_service.py:199` | `now = datetime.now()` | `now = local_now()` | admin render `now` |
| `subscription_service.py:767` | `today = today or datetime.now().date()` | `today = today or local_now().date()` | window calc |
| `subscription_service.py:819` | `today = datetime.now().date()` | `today = local_now().date()` | window calc |

`auth_backend.py:89` is **not** changed — it's a session-expiry check on a UTC-stored timestamp; tz-naive comparison is fine for that path.

Audit logs (`*.utcnow().isoformat()`) are unchanged.

### 4.4 Schema for `/admin/usage.json`

```json
{
  "ts": "2026-05-08T14:23:42+08:00",
  "stats": {
    "current_hour_bytes": 1288490188,
    "today_bytes": 15677260595,
    "last_7d_bytes": 105553116160,
    "cycle_bytes": 335007326208,
    "cycle_day": 18,
    "cycle_total_days": 30,
    "yesterday_bytes": 13985320960,
    "online": 12
  },
  "hourly_totals": [
    {"hour": "2026-05-02T00", "bytes": 234567890},
    ... 168 entries, oldest first, padded with 0 for empty hours
  ],
  "heatmap": [
    {"date": "2026-05-02", "hours": [n0, n1, ..., n23]},
    ... 7 entries, oldest first
  ],
  "top_n": [
    {"uid": "alice", "last_24h_bytes": 3435973836, "spark": [n0..n23]},
    ... up to 5 entries
  ]
}
```

All `bytes` values are post-`DISPLAY_MULTIPLIER`. Frontend never multiplies.

For the current day, `heatmap[6].hours` holds zeros for hours that haven't happened yet. The SVG renderer derives `current_hour` from the top-level `ts` field and dashes any `today` cell whose hour-of-day is `> current_hour` (distinguishing "future" zero from "no traffic" zero).

### 4.5 Schema for `/admin/user/<uid>.json`

```json
{
  "ts": "2026-05-08T14:23:42+08:00",
  "uid": "alice",
  "metered": true,
  "online": 2,
  "max_devices": 2,
  "cycle_used_bytes": 13314039480,
  "cycle_quota_bytes": 53687091200,
  "current_hour_bytes": 188743680,
  "today_bytes": 3435973836,
  "hourly_bars": [
    {"hour": "2026-05-02T00", "bytes": ...},
    ... 168
  ],
  "heatmap": [{"date": "...", "hours": [...24]}, ... 7],
  "recent_alerts": [
    {"ts": "2026-05-04T02:13+08:00", "kind": "anomaly", "details": "..."}
  ]
}
```

`recent_alerts` is sourced from `alert_state.json` plus best-effort tailing of any future `alert.log` (out of scope here — empty list if no log exists).

## 5. Components

### 5.1 `hysteria/charts.py`

Pure functions, all return strings, no I/O:

| Function | Args | Returns |
|---|---|---|
| `hourly_bars_svg(hourly_totals, *, peak_hour=None)` | list of 168 ints | SVG, 720×130 viewport, day separators every 24, today's bars `--accent`, peak hour `--alert`, others `--text-muted` |
| `weekday_hour_heatmap_svg(grid)` | list[7][24] | SVG, 540×200 viewport, opacity-mapped intensity, future-cell dashed outline |
| `mini_sparkline_svg(values, *, height=24)` | list of N ints | SVG (replaces existing `sparkline_svg` in `subscription_service`). Default `height=24` matches the legacy 30-day per-user-row sparkline; Top-N rows pass `height=14` for the compact variant. |
| `user_hourly_bars_svg(...)` | per-user 168 | wider, taller variant for `/admin/user/<uid>` |

Refactor note: existing `sparkline_svg(values, height=24)` in `subscription_service.py` (line 776) becomes `mini_sparkline_svg(values, height=...)` and moves here. The 30-day per-user-row sparkline in `/admin` calls the new function — no behavior change.

### 5.2 `hysteria/traffic_limiter.py` changes

```python
USAGE_HOURLY_FILE = "/root/hysteria/state/usage_hourly.json"
HOURLY_RETENTION_HOURS = 168


def accumulate_hourly(traffic, now):
    """Mirror of accumulate_daily, bucketed at hour resolution."""
    hour_key = now.strftime("%Y-%m-%dT%H")
    hourly = load_json(USAGE_HOURLY_FILE, {})
    hourly.setdefault(hour_key, {})
    for uid, stat in traffic.items():
        cur = normalize_usage_entry(hourly[hour_key].get(uid, 0))
        tx, rx = int(stat.get("tx", 0)), int(stat.get("rx", 0))
        cur["tx"] += tx
        cur["rx"] += rx
        cur["total"] += tx + rx
        hourly[hour_key][uid] = cur
    prune_hourly(hourly, now)
    save_json(USAGE_HOURLY_FILE, hourly)


def prune_hourly(hourly, now):
    cutoff = (now - timedelta(hours=HOURLY_RETENTION_HOURS - 1)).strftime("%Y-%m-%dT%H")
    for k in list(hourly.keys()):
        if k < cutoff:
            del hourly[k]
```

`main()` adds one line: `accumulate_hourly(traffic, now)` immediately after the existing `accumulate_daily(traffic, now)`. Same `usage_lock()` guards both.

### 5.3 `hysteria/subscription_service.py` changes

- **Replace** `render_daily_usage()` with `render_usage_page()` mounted at `/admin/usage`. Inside, call:
    - `_aggregate_stats()` → returns the four stat-card numbers
    - `charts.hourly_bars_svg(load_hourly_totals())`
    - `charts.weekday_hour_heatmap_svg(load_heatmap_grid())`
    - `_top_n_users(n=5, window_hours=24)` → returns list with mini-sparkline SVGs
- **Add** `render_user_detail_page(uid)` mounted at `/admin/user/<uid>`. 404 if `uid` not in `users.json`.
- `_top_n_users` ranks **all** users by last-24h `total`, both `metered` and `unmetered` (operator's own account is included — answers "who's actually moving traffic right now").
- **Add** JSON twins: `/admin/usage.json` and `/admin/user/<uid>.json` returning the schemas in §4.4 / §4.5.
- **Keep** `/admin/daily` route, but make it return `301` to `/admin/usage` (preserves any bookmarks).
- **Remove** the `('daily', '/admin/daily', '每日流量', 'chart')` nav entry and replace with `('usage', '/admin/usage', '流量分析', 'chart')`.

### 5.4 `hysteria/static/usage.js` (~60 lines)

Two responsibilities:

1. **Hover tooltips on hourly bars**: delegated `mousemove` listener on the SVG; reads `data-hour` and `data-bytes` from the hovered `<rect>`; positions an absolute-positioned `<div class="hover-tip">` above the bar; hides on `mouseleave`.
2. **5-second poll + partial DOM swap**: `setInterval(fetch('/admin/usage.json'), 5000)`. On response, replace text content of the four stat cards (no DOM reflow), then `<svg id="hourly-bars">` and `<svg id="heatmap">` get their inner `<g>` replaced via `outerHTML`-on-child. Top-N gets a list re-render.

Reuses the project's existing `admin_poll.js` pattern — does **not** import that file (different page, different layout).

### 5.5 ADR-0003

New file `docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md`:

> All time-bucket keys in usage accounting (daily, hourly, cycle, "today") use `Asia/Shanghai` explicitly via `timeutil.local_now()`, regardless of host system timezone. Rationale: keys printed in admin UI are read as Shanghai dates by the operator; relying on host-tz produced silent UTC bucketing on default-config Linux servers, leading to misalignment between the daily column and an hour-of-day analysis. Audit logs continue to use UTC `isoformat() + Z` — separate concern, industry norm.

## 6. Edge Cases

| Case | Behavior |
|---|---|
| Cycle rollover (day 21) | Hourly data unchanged (rolling 7 days, cycle-independent). Stat card "本周期" resets to new cycle. |
| Manual reset (per-user or all) | Hourly data **not** cleared. Per ADR-0001 the alert dedup state clears, hourly is a fact-record not a quota. Cycle-bytes stat card drops on the affected user(s). |
| New user added mid-cycle | `setdefault(uid, ...)` on first traffic tick. Top-N sees them as soon as they have nonzero last-24h. |
| User deleted | Their hour-bucket entries naturally age out within 7 days. No active cleanup. README note. |
| Server clock jumps backward | Accumulation goes back into earlier hour bucket; data is additive so no corruption, just a small over-count for that hour. Logged. |
| Server clock jumps forward | New hour bucket created, gap not backfilled. Acceptable. Logged. |
| cron tick miss / delay | `/traffic?clear=1` is delta-since-last-call; total is preserved. Only the visible "instantaneous rate" is briefly understated for the missed minute. |
| Pre-existing `/admin/daily` bookmark | 301 → `/admin/usage`. |
| Cold start (file missing) | First tick creates the file. `/admin/usage` shows mostly zeros for the first 7 days post-deploy. README note. |
| Server tz was UTC when daily data was written, now switching to Shanghai | Cutover side-effect: existing `usage_daily.json` keys stay readable; new keys land in Shanghai dates. Within 30 days the daily series fully rotates. Operator sees a one-time visible "day boundary shifted" the day of upgrade — call out in commit message. |
| `/panel/<user>` exposure | Hourly views are admin-only. Existing `/panel/<user>` is unchanged. |

## 7. Testing Strategy

New test files:

**`tests/test_hourly.py`** (unit):
- `test_local_now_returns_shanghai_tz`
- `test_hour_key_format_is_iso_compact`
- `test_accumulate_hourly_appends_to_current_bucket`
- `test_accumulate_hourly_rolls_to_new_bucket_at_hour_boundary`
- `test_accumulate_hourly_creates_bucket_for_new_user_mid_cycle`
- `test_prune_hourly_drops_keys_older_than_168h`
- `test_prune_hourly_preserves_exactly_168_keys_at_steady_state`
- `test_top_n_orders_by_last_24h_total_descending`
- `test_top_n_includes_unmetered_users`
- `test_top_n_caps_at_5`
- `test_heatmap_grid_shape_is_7x24`
- `test_stat_cards_aggregation_matches_sum_of_buckets`

**`tests/test_charts.py`** (unit):
- `test_hourly_bars_svg_contains_168_rect_elements`
- `test_hourly_bars_svg_marks_peak_hour`
- `test_hourly_bars_svg_handles_all_zero_input`
- `test_heatmap_svg_dashes_future_cells_in_today_row`
- `test_mini_sparkline_svg_matches_old_sparkline_byte_for_byte` (ensures the move from subscription_service is no-op)

**`tests/test_usage_page.py`** (integration):
- `test_admin_usage_returns_html_with_three_charts`
- `test_admin_usage_json_schema_matches_spec`
- `test_admin_user_detail_returns_html_for_existing_user`
- `test_admin_user_detail_returns_404_for_missing_user`
- `test_admin_user_detail_json_schema_matches_spec`
- `test_old_daily_redirects_to_usage_with_301`
- `test_admin_usage_requires_admin_session`

**Extensions to existing tests**:
- `tests/test_smoke.py`: add `/admin/usage` and `/admin/user/<uid>` to the smoke list
- `tests/test_alert_integration.py`: add `test_manual_reset_does_not_clear_hourly_data`
- `tests/test_sparkline.py`: keep as-is, update its import to point at `charts.mini_sparkline_svg` (legacy 30-day sparkline coverage stays intact)

Test conventions follow the project's existing fixture-driven, fcntl-aware integration style (see `conftest.py`, `test_alert_integration.py`).

## 8. Rollout

Single-server deploy (the only deployment topology):

1. Merge branch — all tests green
2. `deploy.sh` (existing) syncs files, restarts `subscription` and `traffic-limiter` units
3. First cron tick after restart writes initial `usage_hourly.json`
4. `/admin/daily` redirects to `/admin/usage` from second one
5. Operator sees mostly-empty 168-bar chart for the first hour, fills out within 7 days

No data migration needed. No config file changes. No new ports. No new dependencies.

---

**Approvals**: brainstorm validated 2026-05-08. Spec generated by Claude (Opus 4.7) under `/superpowers:brainstorming`.
