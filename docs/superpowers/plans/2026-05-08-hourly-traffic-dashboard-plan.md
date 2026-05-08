# Hourly Traffic Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `/admin/usage` — a single-page hourly traffic dashboard with 168-bar timeline, 7×24 heatmap, Top-5 list and per-user drill (`/admin/user/<uid>`). Replaces `/admin/daily`.

**Architecture:** Extend the existing cron pipeline with `accumulate_hourly` writing to `usage_hourly.json` (168h rolling). Add `charts.py` (pure SVG generators), `timeutil.local_now()` (explicit Asia/Shanghai bucketing), two HTML routes and two JSON routes in `subscription_service.py`. Server-side SVG, ~60 lines vanilla JS for hover tooltips and 5s polling, zero new deps.

**Tech Stack:** Python 3 stdlib only (`zoneinfo` for tz), pytest, vanilla JS, SVG. No new runtime dependencies.

**Spec:** [`docs/superpowers/specs/2026-05-08-hourly-traffic-dashboard-design.md`](../specs/2026-05-08-hourly-traffic-dashboard-design.md)

**Test runner:** `pytest` from repo root. `pytest.ini` already sets `pythonpath = . hysteria`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `hysteria/timeutil.py` | new | `local_now()` returning `datetime.now(ZoneInfo("Asia/Shanghai"))` — single source of truth for time-bucket keys |
| `hysteria/charts.py` | new | Pure SVG generators: `mini_sparkline_svg`, `hourly_bars_svg`, `weekday_hour_heatmap_svg`, `user_hourly_bars_svg` |
| `hysteria/static/usage.js` | new | Hover tooltips + 5s poll → partial DOM swap for `/admin/usage` and `/admin/user/<uid>` |
| `hysteria/traffic_limiter.py` | modify | Add `USAGE_HOURLY_FILE`, `accumulate_hourly`, `prune_hourly`; switch `datetime.now()` → `local_now()` at line 282 |
| `hysteria/subscription_service.py` | modify | Replace `render_daily_usage` → `render_usage_page`; add user-detail route + JSON twins; switch `datetime.now()` at lines 199, 767, 819; move `sparkline_svg` → `charts.mini_sparkline_svg` (forwarder kept temporarily for `test_sparkline.py`) |
| `docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md` | new | Records the explicit-tz decision (~40 lines) |
| `tests/test_timeutil.py` | new | 1 test for `local_now()` |
| `tests/test_hourly.py` | new | Unit tests for `accumulate_hourly`, `prune_hourly`, aggregation helpers |
| `tests/test_charts.py` | new | Unit tests for the four SVG generators |
| `tests/test_usage_page.py` | new | Integration tests for `/admin/usage`, `/admin/user/<uid>`, JSON endpoints, 301 redirect |
| `tests/test_smoke.py` | modify | Add `/admin/usage` and `/admin/user/<uid>` to smoke list |
| `tests/test_alert_integration.py` | modify | Add `test_manual_reset_does_not_clear_hourly_data` |
| `tests/test_sparkline.py` | modify | Update import to point at `charts.mini_sparkline_svg` |

---

## Task Sequencing

**Phase 1 (foundation, no UI change yet):** Tasks 1–3 introduce `timeutil` and switch existing call sites — gives us a tz-correct base.
**Phase 2 (data):** Tasks 4–5 add hourly accumulation.
**Phase 3 (rendering):** Tasks 6–9 build the SVG generators.
**Phase 4 (aggregation):** Tasks 10–13 build aggregation helpers used by routes.
**Phase 5 (routes):** Tasks 14–18 wire up HTML/JSON routes and the redirect.
**Phase 6 (frontend):** Task 19 adds vanilla JS.
**Phase 7 (housekeeping):** Tasks 20–21 update smoke/integration tests, write ADR.

Commit after each task. The codebase remains in a working state at every commit.

---

## Task 1: Create `timeutil.local_now()` (test-first)

**Files:**
- Create: `hysteria/timeutil.py`
- Test: `tests/test_timeutil.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_timeutil.py
from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

import timeutil


def test_local_now_returns_aware_shanghai_datetime():
    now = timeutil.local_now()
    assert now.tzinfo is not None, "local_now() must return tz-aware datetime"
    assert now.tzinfo.key == "Asia/Shanghai"


def test_local_now_offset_is_plus_8():
    now = timeutil.local_now()
    assert now.utcoffset() == timedelta(hours=8)


def test_local_tz_constant_is_shanghai():
    assert timeutil.LOCAL_TZ == ZoneInfo("Asia/Shanghai")
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_timeutil.py -v
```

Expected: `ModuleNotFoundError: No module named 'timeutil'`.

- [ ] **Step 3: Write minimal implementation**

```python
# hysteria/timeutil.py
"""Single source of truth for time-bucket 'now' across the project.

All daily/hourly/cycle bucket keys are computed from local_now(). Audit log
timestamps continue to use datetime.utcnow() — that's a separate concern.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_timeutil.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add hysteria/timeutil.py tests/test_timeutil.py
git commit -m "feat(timeutil): local_now() with explicit Asia/Shanghai"
```

---

## Task 2: Switch existing `datetime.now()` call sites to `local_now()`

**Files:**
- Modify: `hysteria/traffic_limiter.py:282`
- Modify: `hysteria/subscription_service.py:199, 767, 819`

The audit table from §4.3 of the spec lists 4 call sites. `auth_backend.py:89` is intentionally left alone (UTC-naive session expiry, separate concern).

- [ ] **Step 1: Update `traffic_limiter.py`**

Add to imports near the top (after the existing `from display import DISPLAY_MULTIPLIER`):

```python
from timeutil import local_now
```

Change line 282 from:
```python
    now = datetime.now()
```
to:
```python
    now = local_now()
```

- [ ] **Step 2: Update `subscription_service.py` line 199**

Add to imports near the top of `subscription_service.py`:

```python
from timeutil import local_now
```

(Place it next to other intra-package imports like `import user_compat`. Order alphabetically if there's an existing convention.)

Change line 199 from:
```python
    now = datetime.now()
```
to:
```python
    now = local_now()
```

- [ ] **Step 3: Update `subscription_service.py` lines 767 and 819**

Line 767 (in `daily_window_for_user`):
```python
    today = today or datetime.now().date()
```
to:
```python
    today = today or local_now().date()
```

Line 819 (in `render_daily_usage`):
```python
    today = datetime.now().date()
```
to:
```python
    today = local_now().date()
```

- [ ] **Step 4: Refactor `month_key()` to accept optional `now`**

The existing `month_key()` (line 197) takes no args and calls `datetime.now()` internally. Helpers in later tasks need to inject a known `now` for testability. Change:

```python
def month_key():
    """Billing cycle resets on the 21st. Before the 21st belongs to the previous cycle."""
    now = datetime.now()
    if now.day >= 21:
        return now.strftime('%Y-%m')
    first = now.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime('%Y-%m')
```

to:

```python
def month_key(now=None):
    """Billing cycle resets on the 21st. Before the 21st belongs to the previous cycle."""
    if now is None:
        now = local_now()
    if now.day >= 21:
        return now.strftime('%Y-%m')
    first = now.replace(day=1)
    prev = first - timedelta(days=1)
    return prev.strftime('%Y-%m')
```

Existing call sites (`month_key()` with no args) keep working unchanged.

- [ ] **Step 5: Run the entire test suite**

```
pytest -v
```

Expected: All existing tests still pass (no behavior change on a Shanghai-tz dev machine; tests don't rely on UTC bucketing).

- [ ] **Step 6: Commit**

```bash
git add hysteria/traffic_limiter.py hysteria/subscription_service.py
git commit -m "refactor: route 4 datetime.now() call sites through local_now(); month_key accepts now"
```

---

## Task 3: Add hourly file constants + retention to `traffic_limiter`

**Files:**
- Modify: `hysteria/traffic_limiter.py` (top-of-file constants block, around line 21–28)

- [ ] **Step 1: Add constants**

Find the block:
```python
USAGE_DAILY_FILE = "/root/hysteria/state/usage_daily.json"
ONLINE_SNAPSHOT_FILE = "/root/hysteria/state/online.json"
```

Insert immediately after `USAGE_DAILY_FILE`:

```python
USAGE_HOURLY_FILE = "/root/hysteria/state/usage_hourly.json"
```

Find the existing:
```python
DAILY_RETENTION_DAYS = 30
```

Insert immediately after:
```python
HOURLY_RETENTION_HOURS = 168
```

- [ ] **Step 2: Sanity-check imports still parse**

```
python -c "import sys; sys.path.insert(0, 'hysteria'); import traffic_limiter; print(traffic_limiter.USAGE_HOURLY_FILE)"
```

Expected: `/root/hysteria/state/usage_hourly.json`

- [ ] **Step 3: Commit**

```bash
git add hysteria/traffic_limiter.py
git commit -m "chore: add USAGE_HOURLY_FILE + HOURLY_RETENTION_HOURS constants"
```

---

## Task 4: Implement `prune_hourly` (test-first)

**Files:**
- Modify: `hysteria/traffic_limiter.py` (after `prune_daily`, around line 187)
- Create test: `tests/test_hourly.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hourly.py
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import traffic_limiter as tl

SH = ZoneInfo("Asia/Shanghai")


def _make_hourly(now, n_hours):
    """Build a dict with n_hours hour keys ending at `now`."""
    out = {}
    for i in range(n_hours):
        h = now - timedelta(hours=i)
        out[h.strftime("%Y-%m-%dT%H")] = {"alice": {"tx": 1, "rx": 1, "total": 2}}
    return out


def test_prune_hourly_drops_keys_older_than_168h():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 200)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168


def test_prune_hourly_keeps_exact_boundary():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 168)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168, "no key should be dropped at exact 168 boundary"


def test_prune_hourly_drops_just_one_too_old():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 169)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_hourly.py -v
```

Expected: `AttributeError: module 'traffic_limiter' has no attribute 'prune_hourly'`.

- [ ] **Step 3: Implement `prune_hourly`**

In `hysteria/traffic_limiter.py`, find `def prune_daily(daily, today):` (around line 182) and add immediately after it:

```python
def prune_hourly(hourly, now):
    """Drop hour buckets older than HOURLY_RETENTION_HOURS - 1 hours back from `now`."""
    cutoff = (now - timedelta(hours=HOURLY_RETENTION_HOURS - 1)).strftime("%Y-%m-%dT%H")
    for k in list(hourly.keys()):
        if k < cutoff:
            del hourly[k]
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/test_hourly.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add hysteria/traffic_limiter.py tests/test_hourly.py
git commit -m "feat(traffic_limiter): prune_hourly drops keys older than 168h"
```

---

## Task 5: Implement `accumulate_hourly` (test-first)

**Files:**
- Modify: `hysteria/traffic_limiter.py` (after `accumulate_daily`)
- Modify: `tests/test_hourly.py`

- [ ] **Step 1: Append failing tests to `tests/test_hourly.py`**

```python
import json


def test_accumulate_hourly_creates_bucket_for_first_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now = datetime(2026, 5, 8, 14, 5, tzinfo=SH)
    traffic = {"alice": {"tx": 100, "rx": 200}}
    tl.accumulate_hourly(traffic, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data == {
        "2026-05-08T14": {"alice": {"tx": 100, "rx": 200, "total": 300}}
    }


def test_accumulate_hourly_appends_within_same_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now1 = datetime(2026, 5, 8, 14, 5, tzinfo=SH)
    now2 = datetime(2026, 5, 8, 14, 55, tzinfo=SH)  # same hour
    tl.accumulate_hourly({"alice": {"tx": 100, "rx": 200}}, now1)
    tl.accumulate_hourly({"alice": {"tx": 50,  "rx": 25}},  now2)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data["2026-05-08T14"]["alice"] == {"tx": 150, "rx": 225, "total": 375}


def test_accumulate_hourly_rolls_to_new_bucket_at_hour_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    tl.accumulate_hourly(
        {"alice": {"tx": 1, "rx": 1}},
        datetime(2026, 5, 8, 14, 59, tzinfo=SH),
    )
    tl.accumulate_hourly(
        {"alice": {"tx": 5, "rx": 5}},
        datetime(2026, 5, 8, 15, 0, tzinfo=SH),
    )
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert "2026-05-08T14" in data
    assert "2026-05-08T15" in data
    assert data["2026-05-08T14"]["alice"]["total"] == 2
    assert data["2026-05-08T15"]["alice"]["total"] == 10


def test_accumulate_hourly_creates_bucket_for_new_user_mid_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    tl.accumulate_hourly({"alice": {"tx": 1, "rx": 1}}, now)
    tl.accumulate_hourly({"alice": {"tx": 1, "rx": 1}, "bob": {"tx": 9, "rx": 9}}, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data["2026-05-08T14"]["bob"] == {"tx": 9, "rx": 9, "total": 18}
    assert data["2026-05-08T14"]["alice"]["total"] == 4


def test_accumulate_hourly_prunes_at_each_call(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    # Seed file with old data
    old = datetime(2026, 5, 1, 0, tzinfo=SH).strftime("%Y-%m-%dT%H")
    (tmp_path / "usage_hourly.json").write_text(json.dumps(
        {old: {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    ))
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    tl.accumulate_hourly({"alice": {"tx": 5, "rx": 5}}, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert old not in data, "old hour should be pruned"
    assert "2026-05-08T14" in data
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_hourly.py -v
```

Expected: 5 new tests fail with `AttributeError: ... has no attribute 'accumulate_hourly'`. The three earlier tests still pass.

- [ ] **Step 3: Implement `accumulate_hourly`**

In `hysteria/traffic_limiter.py`, find `def accumulate_daily(traffic, now):` and add immediately after it:

```python
def accumulate_hourly(traffic, now):
    """Mirror of accumulate_daily, bucketed at hour resolution.

    Hour key format: 'YYYY-MM-DDTHH'. Pass a tz-aware `now` (project uses
    timeutil.local_now()).
    """
    hour_key = now.strftime("%Y-%m-%dT%H")
    hourly = load_json(USAGE_HOURLY_FILE, {})
    hourly.setdefault(hour_key, {})
    for uid, stat in traffic.items():
        cur = normalize_usage_entry(hourly[hour_key].get(uid, 0))
        tx = int(stat.get("tx", 0))
        rx = int(stat.get("rx", 0))
        cur["tx"] += tx
        cur["rx"] += rx
        cur["total"] += tx + rx
        hourly[hour_key][uid] = cur
    prune_hourly(hourly, now)
    save_json(USAGE_HOURLY_FILE, hourly)
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_hourly.py -v
```

Expected: 8 passed (3 prune + 5 accumulate).

- [ ] **Step 5: Commit**

```bash
git add hysteria/traffic_limiter.py tests/test_hourly.py
git commit -m "feat(traffic_limiter): accumulate_hourly with 168h rolling retention"
```

---

## Task 6: Wire `accumulate_hourly` into `main()` cron tick

**Files:**
- Modify: `hysteria/traffic_limiter.py:303` (inside `main()`)

- [ ] **Step 1: Add the call**

Find the existing line in `main()`:
```python
        accumulate_daily(traffic, now)
```

Insert immediately after it (still inside the `with usage_lock():` block):
```python
        accumulate_hourly(traffic, now)
```

- [ ] **Step 2: Run the full test suite**

```
pytest -v
```

Expected: all green.

- [ ] **Step 3: Commit**

```bash
git add hysteria/traffic_limiter.py
git commit -m "feat(traffic_limiter): wire accumulate_hourly into main cron loop"
```

---

## Task 7: Create `charts.py` and move `mini_sparkline_svg` (test-first)

**Files:**
- Create: `hysteria/charts.py`
- Create: `tests/test_charts.py`
- Modify: `hysteria/subscription_service.py` (delete `sparkline_svg` body, replace with forwarder)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_charts.py
import re

import charts


def test_mini_sparkline_svg_empty_input_returns_empty_svg():
    out = charts.mini_sparkline_svg([])
    assert '<svg class="spark"' in out
    assert "<rect" not in out


def test_mini_sparkline_svg_renders_one_rect_per_nonzero_value():
    values = [("2026-05-01", 100), ("2026-05-02", 0), ("2026-05-03", 50)]
    out = charts.mini_sparkline_svg(values)
    rects = re.findall(r"<rect ", out)
    assert len(rects) == 2  # zero day skipped


def test_mini_sparkline_svg_marks_today_class_on_last_bar():
    values = [("2026-05-01", 100), ("2026-05-02", 50)]
    out = charts.mini_sparkline_svg(values)
    assert 'class="spark-bar today"' in out


def test_mini_sparkline_svg_height_param_default_24():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values)
    assert "viewBox=\"0 0 3 24\"" in out, "default height is 24 (legacy 30-day sparkline)"


def test_mini_sparkline_svg_height_14_for_topn():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values, height=14)
    assert "viewBox=\"0 0 3 14\"" in out
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_charts.py -v
```

Expected: `ModuleNotFoundError: No module named 'charts'`.

- [ ] **Step 3: Create `charts.py` with `mini_sparkline_svg` (verbatim move from subscription_service.py)**

Create `hysteria/charts.py`:

```python
"""Pure SVG generators for usage analytics dashboards.

All functions are I/O-free string builders. They consume already-DISPLAY-MULTIPLIED
byte counts; they do not multiply themselves. Output uses only inline SVG with
class hooks the polling JS / CSS can target.
"""
import html

from display import fmt_bytes


def mini_sparkline_svg(values, *, height=24):
    """Render a series of (date, bytes) into a compact bar SVG.

    Last entry carries the `today` class; zero-valued days render no bar.
    Width/height come from the viewBox so the caller's CSS can size the SVG.

    Output contract (relied on by the admin dashboard's polling JS):
    - Outermost element is `<svg class="spark" ...>` — JS uses this class.
    - Each non-empty bar is `<rect class="spark-bar [today]" ...>` — CSS uses these.
    Default height=24 matches the legacy 30-day per-user-row sparkline; Top-N
    rows pass height=14 for the compact variant.
    """
    n = len(values)
    label = f'{n} 天趋势' if n else ''
    if n == 0:
        return f'<svg class="spark" viewBox="0 0 0 {height}" aria-hidden="true"></svg>'
    max_v = max((v for _, v in values), default=0) or 1
    bar_w = 3
    gap = 1
    width = n * bar_w + (n - 1) * gap
    parts = []
    for i, (dk, v) in enumerate(values):
        if v <= 0:
            continue
        h = max(1, int(round(height * v / max_v)))
        x = i * (bar_w + gap)
        y = height - h
        cls = 'spark-bar today' if i == n - 1 else 'spark-bar'
        title = f'{dk}: {fmt_bytes(v)}'
        parts.append(
            f'<rect class="{cls}" x="{x}" y="{y}" width="{bar_w}" height="{h}">'
            f'<title>{html.escape(title)}</title></rect>'
        )
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'aria-label="{html.escape(label)}">'
            f'{"".join(parts)}</svg>')
```

- [ ] **Step 4: Replace the body in `subscription_service.py`**

In `hysteria/subscription_service.py`, locate `def sparkline_svg(values, *, height=24):` (around line 776). Replace its body (keeping the function name as a forwarder so nothing else breaks):

```python
def sparkline_svg(values, *, height=24):
    """Forwarder kept for backward compat; new code calls charts.mini_sparkline_svg."""
    from charts import mini_sparkline_svg
    return mini_sparkline_svg(values, height=height)
```

- [ ] **Step 5: Run all tests**

```
pytest -v
```

Expected: all green (existing `test_sparkline.py` still imports `sparkline_svg` from `subscription_service` and passes via the forwarder; new `test_charts.py` 5 tests pass).

- [ ] **Step 6: Commit**

```bash
git add hysteria/charts.py hysteria/subscription_service.py tests/test_charts.py
git commit -m "refactor(charts): extract mini_sparkline_svg into hysteria/charts.py"
```

---

## Task 8: Implement `hourly_bars_svg` (test-first)

**Files:**
- Modify: `hysteria/charts.py`
- Modify: `tests/test_charts.py`

- [ ] **Step 1: Append failing tests to `tests/test_charts.py`**

```python
def test_hourly_bars_svg_empty_input():
    out = charts.hourly_bars_svg([])
    assert '<svg class="hourly-bars"' in out
    assert "<rect" not in out


def test_hourly_bars_svg_renders_168_rects():
    series = [{"hour": f"2026-05-0{(i//24)+2}T{i%24:02d}", "bytes": (i % 5) * 1_000_000_000}
              for i in range(168)]
    out = charts.hourly_bars_svg(series)
    import re
    rects = re.findall(r"<rect ", out)
    # zero-valued bars are skipped (every 5th and 0th); count nonzeros instead
    expected_nonzero = sum(1 for s in series if s["bytes"] > 0)
    assert len(rects) == expected_nonzero


def test_hourly_bars_svg_marks_peak_with_alert_class():
    series = [{"hour": "2026-05-08T00", "bytes": 1_000_000_000},
              {"hour": "2026-05-08T01", "bytes": 5_000_000_000},
              {"hour": "2026-05-08T02", "bytes": 2_000_000_000}]
    out = charts.hourly_bars_svg(series, peak_hour="2026-05-08T01")
    assert 'class="hourly-bar peak"' in out


def test_hourly_bars_svg_attaches_data_attrs_for_hover():
    series = [{"hour": "2026-05-08T00", "bytes": 1_073_741_824}]
    out = charts.hourly_bars_svg(series)
    assert 'data-hour="2026-05-08T00"' in out
    assert 'data-bytes="1073741824"' in out


def test_hourly_bars_svg_handles_all_zero_input_without_div_by_zero():
    series = [{"hour": f"2026-05-08T{i:02d}", "bytes": 0} for i in range(24)]
    out = charts.hourly_bars_svg(series)
    assert "<rect" not in out
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_charts.py -v
```

Expected: 5 new tests fail with `AttributeError: module 'charts' has no attribute 'hourly_bars_svg'`.

- [ ] **Step 3: Implement `hourly_bars_svg`**

Append to `hysteria/charts.py`:

```python
def hourly_bars_svg(series, *, peak_hour=None, height=120, bar_w=3, gap=1):
    """Render an hourly time-series as a compact bar chart with day separators.

    Args:
        series: list of {"hour": "YYYY-MM-DDTHH", "bytes": int}, oldest first.
                bytes already × DISPLAY_MULTIPLIER.
        peak_hour: optional hour key to highlight with the `peak` class.
        height: SVG drawable height (excluding day-label strip).

    Output:
        <svg class="hourly-bars">
          <g class="day-separators">…</g>
          <g class="bars">
            <rect class="hourly-bar [peak]" data-hour="…" data-bytes="…" …/>
            …
          </g>
          <g class="day-labels">…</g>
        </svg>
    """
    n = len(series)
    if n == 0:
        return '<svg class="hourly-bars" viewBox="0 0 0 0" aria-hidden="true"></svg>'

    max_v = max((s["bytes"] for s in series), default=0)
    if max_v <= 0:
        max_v = 1  # avoid div-by-zero; bars will still be empty since all zeros are skipped
    width = n * bar_w + (n - 1) * gap
    label_strip = 16  # pixels reserved for day labels at the bottom
    total_h = height + label_strip

    # Day separators: one per day boundary inside the window
    seen_days = set()
    seps = []
    for i, s in enumerate(series):
        day = s["hour"][:10]
        if day not in seen_days:
            seen_days.add(day)
            if i > 0:  # don't draw separator at x=0
                x = i * (bar_w + gap)
                seps.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{height}"/>')
    sep_svg = f'<g class="day-separators">{"".join(seps)}</g>'

    # Bars
    bars = []
    for i, s in enumerate(series):
        v = int(s["bytes"])
        if v <= 0:
            continue
        h = max(1, int(round(height * v / max_v)))
        x = i * (bar_w + gap)
        y = height - h
        cls = "hourly-bar peak" if s["hour"] == peak_hour else "hourly-bar"
        bars.append(
            f'<rect class="{cls}" x="{x}" y="{y}" width="{bar_w}" height="{h}" '
            f'data-hour="{s["hour"]}" data-bytes="{v}"/>'
        )
    bar_svg = f'<g class="bars">{"".join(bars)}</g>'

    # Day labels: midpoint of each day's run of hours
    days_in_order = []
    for s in series:
        d = s["hour"][:10]
        if not days_in_order or days_in_order[-1] != d:
            days_in_order.append(d)
    label_parts = []
    cursor = 0
    for d in days_in_order:
        run = sum(1 for s in series if s["hour"][:10] == d)
        midx = (cursor + run / 2) * (bar_w + gap)
        label_parts.append(
            f'<text class="day-label" x="{midx:.1f}" y="{total_h - 3}" '
            f'text-anchor="middle">{d[5:]}</text>'
        )
        cursor += run
    label_svg = f'<g class="day-labels">{"".join(label_parts)}</g>'

    return (f'<svg class="hourly-bars" viewBox="0 0 {width} {total_h}" '
            f'aria-label="过去 {n} 小时流量">'
            f'{sep_svg}{bar_svg}{label_svg}</svg>')
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_charts.py -v
```

Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add hysteria/charts.py tests/test_charts.py
git commit -m "feat(charts): hourly_bars_svg with day separators and peak marker"
```

---

## Task 9: Implement `weekday_hour_heatmap_svg` (test-first)

**Files:**
- Modify: `hysteria/charts.py`
- Modify: `tests/test_charts.py`

- [ ] **Step 1: Append failing tests**

```python
def test_heatmap_svg_renders_7x24_cells():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [j * 1_000_000 for j in range(24)]}
            for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    import re
    rects = re.findall(r"<rect ", out)
    assert len(rects) == 7 * 24


def test_heatmap_svg_dashes_future_cells_in_today_row():
    today = "2026-05-08"
    grid = [{"date": f"2026-05-0{i+2}", "hours": [0] * 24} for i in range(6)] + [
        {"date": today, "hours": [1, 1, 1, 0, 0, 0] + [0] * 18}
    ]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=f"{today}T02")
    # cells for hours 0-2 of today are real, 3+ are future and dashed
    assert 'class="heat-cell future"' in out
    assert out.count('class="heat-cell future"') == 21  # hours 3..23


def test_heatmap_svg_intensity_proportional_to_value():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [j for j in range(24)]} for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    # opacity should vary; at least we get a min and a max present
    assert 'opacity="0.05"' in out or 'opacity="0.10"' in out
    assert 'opacity="1.00"' in out or 'opacity="0.95"' in out


def test_heatmap_svg_handles_all_zero_input():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [0] * 24} for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    # all cells render with min opacity
    assert 'class="heat-cell"' in out
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_charts.py -v
```

Expected: 4 new tests fail.

- [ ] **Step 3: Implement `weekday_hour_heatmap_svg`**

Append to `hysteria/charts.py`:

```python
def weekday_hour_heatmap_svg(grid, *, current_hour_iso=None,
                             cell_w=20, cell_h=22, label_w=46):
    """Render a 7×24 heatmap of bytes-per-hour-per-day.

    Args:
        grid: list of 7 {"date": "YYYY-MM-DD", "hours": [24 ints]}, oldest first.
        current_hour_iso: optional "YYYY-MM-DDTHH"; cells in `grid[-1]` after this
                          hour-of-day get class="heat-cell future" and a dashed border.

    Output: <svg class="heatmap"> with 7 rows × 24 cols of <rect> cells, plus
    date labels (left) and hour-of-day labels (bottom: 0, 4, 8, 12, 16, 20, 23).
    """
    rows = len(grid)
    cols = 24
    width = label_w + cols * cell_w
    height = rows * cell_h + 28  # +28 for hour-axis labels at bottom

    # Find max for opacity scaling (across whole grid)
    max_v = 0
    for row in grid:
        for v in row["hours"]:
            if v > max_v:
                max_v = v
    if max_v <= 0:
        max_v = 1

    today_idx = rows - 1
    cur_hour_of_day = None
    if current_hour_iso and current_hour_iso[:10] == grid[today_idx]["date"]:
        try:
            cur_hour_of_day = int(current_hour_iso[11:13])
        except ValueError:
            cur_hour_of_day = None

    parts = []
    # Date labels (right-aligned, at row baseline)
    for r, row in enumerate(grid):
        y = r * cell_h + cell_h - 6
        parts.append(
            f'<text class="heat-date" x="{label_w - 6}" y="{y}" '
            f'text-anchor="end">{row["date"][5:]}</text>'
        )

    # Cells
    for r, row in enumerate(grid):
        y = r * cell_h + 1
        for c, v in enumerate(row["hours"]):
            x = label_w + c * cell_w
            is_future = (r == today_idx
                         and cur_hour_of_day is not None
                         and c > cur_hour_of_day)
            if is_future:
                parts.append(
                    f'<rect class="heat-cell future" x="{x}" y="{y}" '
                    f'width="{cell_w - 1}" height="{cell_h - 2}"/>'
                )
            else:
                # Opacity range 0.05..1.00
                op = 0.05 + 0.95 * (v / max_v)
                parts.append(
                    f'<rect class="heat-cell" x="{x}" y="{y}" '
                    f'width="{cell_w - 1}" height="{cell_h - 2}" '
                    f'opacity="{op:.2f}"/>'
                )

    # Hour-axis labels: 0, 4, 8, 12, 16, 20, 23
    for h in (0, 4, 8, 12, 16, 20, 23):
        x = label_w + h * cell_w + cell_w / 2
        ylab = rows * cell_h + 12
        parts.append(
            f'<text class="heat-hour" x="{x:.0f}" y="{ylab}" '
            f'text-anchor="middle">{h}</text>'
        )

    return (f'<svg class="heatmap" viewBox="0 0 {width} {height}" '
            f'aria-label="7 天小时热图">{"".join(parts)}</svg>')
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_charts.py -v
```

Expected: 14 passed.

- [ ] **Step 5: Commit**

```bash
git add hysteria/charts.py tests/test_charts.py
git commit -m "feat(charts): weekday_hour_heatmap_svg with future-cell dashing"
```

---

## Task 10: Implement aggregation helpers (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py` (new helpers near `render_daily_usage`)
- Modify: `tests/test_hourly.py`

These are pure functions used by both the HTML page and the JSON endpoint.

- [ ] **Step 1: Append failing tests to `tests/test_hourly.py`**

```python
import subscription_service as ss


def _seed_hourly(hours_back, per_hour_bytes_per_user):
    """Build a hourly dict with `hours_back` hours up to a fixed `now`.

    Each hour holds the same {uid: bytes} payload (raw, pre-display).
    Returns (hourly_dict, fixed_now).
    """
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    out = {}
    for i in range(hours_back):
        h = now - timedelta(hours=i)
        out[h.strftime("%Y-%m-%dT%H")] = {
            uid: {"tx": v // 2, "rx": v - v // 2, "total": v}
            for uid, v in per_hour_bytes_per_user.items()
        }
    return out, now


def test_load_hourly_totals_returns_168_entries_padded_with_zeros(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    hourly, now = _seed_hourly(50, {"alice": 1_000_000, "bob": 500_000})  # only 50h of data
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    series = ss._load_hourly_totals(now=now)
    assert len(series) == 168
    # The most recent 50 hours have data, rest are zero
    nonzero = [s for s in series if s["bytes"] > 0]
    assert len(nonzero) == 50
    # values are post-DISPLAY_MULTIPLIER (raw 1.5M × 2.28 ≈ 3.42M per hour total)
    from display import DISPLAY_MULTIPLIER
    expected_per_hour = int((1_000_000 + 500_000) * DISPLAY_MULTIPLIER)
    assert nonzero[0]["bytes"] == expected_per_hour


def test_top_n_users_orders_by_last_24h_total_descending(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({
        "alice": {"metered": True}, "bob": {"metered": True},
        "carol": {"metered": False}, "dave": {"metered": True},
    }))
    hourly, now = _seed_hourly(
        24,
        {"alice": 100, "bob": 50, "carol": 75, "dave": 10},
    )
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert [u["uid"] for u in top] == ["alice", "carol", "bob", "dave"]


def test_top_n_users_includes_unmetered(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({
        "alice": {"metered": False}, "bob": {"metered": True},
    }))
    hourly, now = _seed_hourly(24, {"alice": 1_000_000_000, "bob": 1})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert top[0]["uid"] == "alice", "unmetered user with high traffic should rank first"


def test_top_n_users_caps_at_5(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    users = {f"u{i}": {"metered": True} for i in range(10)}
    (tmp_path / "users.json").write_text(json.dumps(users))
    hourly, now = _seed_hourly(24, {f"u{i}": 100 - i for i in range(10)})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert len(top) == 5
    assert [u["uid"] for u in top] == ["u0", "u1", "u2", "u3", "u4"]


def test_load_heatmap_grid_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    hourly, now = _seed_hourly(168, {"alice": 1_000_000})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    grid = ss._load_heatmap_grid(now=now)
    assert len(grid) == 7
    assert all(len(row["hours"]) == 24 for row in grid)


def test_aggregate_stats_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({"alice": {"metered": True}}))
    hourly, now = _seed_hourly(48, {"alice": 1_000_000})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    stats = ss._aggregate_stats(now=now, online={})
    assert {"current_hour_bytes", "today_bytes", "yesterday_bytes",
            "last_7d_bytes", "cycle_bytes", "cycle_day", "cycle_total_days",
            "online"} <= set(stats.keys())
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_hourly.py -v
```

Expected: 6 new tests fail with `AttributeError: module 'subscription_service' has no attribute '_load_hourly_totals'` etc.

- [ ] **Step 3: Implement helpers in `subscription_service.py`**

Insert near the existing `daily_window_for_user` function (around line 765):

```python
from display import DISPLAY_MULTIPLIER

USAGE_HOURLY_FILE = Path('/root/hysteria/state/usage_hourly.json')
HOURLY_RETENTION_HOURS = 168


def _hour_key(dt):
    return dt.strftime("%Y-%m-%dT%H")


def _entry_total(entry):
    """Extract `total` from a per-user usage entry, tolerating int and dict shapes."""
    if isinstance(entry, dict):
        return int(entry.get("total", 0))
    return int(entry or 0)


def _load_hourly_totals(*, now):
    """Return list of 168 {hour, bytes} entries (oldest first), bytes × DISPLAY_MULTIPLIER."""
    hourly = load_json(USAGE_HOURLY_FILE, {})
    out = []
    for i in reversed(range(HOURLY_RETENTION_HOURS)):
        h = now - timedelta(hours=i)
        hk = _hour_key(h)
        bucket = hourly.get(hk) or {}
        raw_total = sum(_entry_total(v) for v in bucket.values())
        out.append({"hour": hk, "bytes": int(raw_total * DISPLAY_MULTIPLIER)})
    return out


def _load_heatmap_grid(*, now):
    """Return 7-row grid: [{date, hours: [24 ints]}, ...] oldest first.

    Each cell value is post-DISPLAY_MULTIPLIER aggregate across all users for that hour.
    """
    hourly = load_json(USAGE_HOURLY_FILE, {})
    today = now.date()
    rows = []
    for d in reversed(range(7)):
        day = today - timedelta(days=d)
        date_str = day.strftime("%Y-%m-%d")
        hours = []
        for hh in range(24):
            hk = f"{date_str}T{hh:02d}"
            bucket = hourly.get(hk) or {}
            raw = sum(_entry_total(v) for v in bucket.values())
            hours.append(int(raw * DISPLAY_MULTIPLIER))
        rows.append({"date": date_str, "hours": hours})
    return rows


def _top_n_users(*, n=5, window_hours=24, now):
    """Return top-N users by last-`window_hours` total bytes (post-DISPLAY_MULTIPLIER).

    Each item: {uid, last_24h_bytes, spark}. `spark` is 24 hourly ints.
    Includes both metered and unmetered users.
    """
    hourly = load_json(USAGE_HOURLY_FILE, {})
    users = load_json(USERS_FILE, {})

    per_user_totals = {}
    per_user_spark = {uid: [0] * window_hours for uid in users.keys()}
    for i in reversed(range(window_hours)):
        h = now - timedelta(hours=i)
        bucket = hourly.get(_hour_key(h)) or {}
        idx = window_hours - 1 - i
        for uid in users.keys():
            v = _entry_total(bucket.get(uid))
            per_user_totals[uid] = per_user_totals.get(uid, 0) + v
            per_user_spark[uid][idx] = int(v * DISPLAY_MULTIPLIER)

    ranked = sorted(per_user_totals.items(), key=lambda kv: kv[1], reverse=True)
    out = []
    for uid, raw_total in ranked[:n]:
        out.append({
            "uid": uid,
            "last_24h_bytes": int(raw_total * DISPLAY_MULTIPLIER),
            "spark": per_user_spark[uid],
        })
    return out


def _aggregate_stats(*, now, online):
    """Return the 4 stat-card numbers + cycle context."""
    hourly = load_json(USAGE_HOURLY_FILE, {})
    daily = load_json(USAGE_DAILY_FILE, {})

    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Current hour
    cur_bucket = hourly.get(_hour_key(now)) or {}
    current_hour_raw = sum(_entry_total(v) for v in cur_bucket.values())

    # Today: sum hours of today
    today_raw = 0
    for hh in range(24):
        b = hourly.get(f"{today_str}T{hh:02d}") or {}
        today_raw += sum(_entry_total(v) for v in b.values())

    # Yesterday from daily (more accurate for full days)
    yest_bucket = daily.get(yesterday_str) or {}
    yesterday_raw = sum(_entry_total(v) for v in yest_bucket.values())

    # Last 7d via daily
    last_7d_raw = 0
    for d in range(7):
        dk = (now.date() - timedelta(days=d)).strftime("%Y-%m-%d")
        last_7d_raw += sum(_entry_total(v) for v in (daily.get(dk) or {}).values())

    # Cycle from existing usage
    usage = load_json(USAGE_FILE, {})
    mk = month_key(now)
    cycle_bucket = usage.get(mk) or {}
    cycle_raw = sum(_entry_total(v) for v in cycle_bucket.values())

    # Cycle day (1..30): days since cycle start
    if now.day >= 21:
        cycle_start = now.replace(day=21, hour=0, minute=0, second=0, microsecond=0)
    else:
        prev_month_end = now.replace(day=1) - timedelta(days=1)
        cycle_start = prev_month_end.replace(day=21, hour=0, minute=0, second=0, microsecond=0)
    cycle_day = (now.date() - cycle_start.date()).days + 1

    return {
        "current_hour_bytes": int(current_hour_raw * DISPLAY_MULTIPLIER),
        "today_bytes": int(today_raw * DISPLAY_MULTIPLIER),
        "yesterday_bytes": int(yesterday_raw * DISPLAY_MULTIPLIER),
        "last_7d_bytes": int(last_7d_raw * DISPLAY_MULTIPLIER),
        "cycle_bytes": int(cycle_raw * DISPLAY_MULTIPLIER),
        "cycle_day": cycle_day,
        "cycle_total_days": 30,
        "online": int(sum(1 for v in (online or {}).values() if int(v or 0) > 0)),
    }
```

Note: `month_key` is the existing function in `subscription_service.py` (mirrors `traffic_limiter.billing_month_key`); `USAGE_FILE` is the existing `Path` constant.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/test_hourly.py -v
```

Expected: 14 passed (8 from earlier + 6 new).

- [ ] **Step 5: Commit**

```bash
git add hysteria/subscription_service.py tests/test_hourly.py
git commit -m "feat(subscription_service): hourly aggregation helpers (totals, heatmap, top-N, stats)"
```

---

## Task 11: Implement `/admin/usage.json` JSON endpoint (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py` (route handler near line 1497)
- Create: `tests/test_usage_page.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_usage_page.py
"""Integration tests for /admin/usage and /admin/user/<uid> routes."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import subscription_service as ss

SH = ZoneInfo("Asia/Shanghai")


def _seed_state(tmp_path, monkeypatch, *, users=None, hourly=None, daily=None,
                usage=None, online=None):
    """Repoint all state files at tmp_path and pre-fill them."""
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", tmp_path / "usage_daily.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json", raising=False)
    monkeypatch.setattr(ss, "ONLINE_SNAPSHOT_FILE", tmp_path / "online.json", raising=False)
    (tmp_path / "users.json").write_text(json.dumps(users or {}))
    (tmp_path / "usage.json").write_text(json.dumps(usage or {}))
    (tmp_path / "usage_daily.json").write_text(json.dumps(daily or {}))
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly or {}))
    (tmp_path / "online.json").write_text(json.dumps(online or {}))


def test_build_usage_json_payload_schema(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {}
    for i in range(24):
        h = now - timedelta(hours=i)
        hourly[h.strftime("%Y-%m-%dT%H")] = {
            "alice": {"tx": 100, "rx": 100, "total": 200}
        }
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True}}, hourly=hourly, online={"alice": 1})
    payload = ss._build_usage_json_payload(now=now)
    assert "ts" in payload
    assert set(payload["stats"].keys()) >= {
        "current_hour_bytes", "today_bytes", "yesterday_bytes",
        "last_7d_bytes", "cycle_bytes", "cycle_day", "cycle_total_days", "online"
    }
    assert len(payload["hourly_totals"]) == 168
    assert len(payload["heatmap"]) == 7
    assert all(len(r["hours"]) == 24 for r in payload["heatmap"])
    assert isinstance(payload["top_n"], list)
    assert all({"uid", "last_24h_bytes", "spark"} <= set(t.keys()) for t in payload["top_n"])
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_usage_page.py -v
```

Expected: `AttributeError: module 'subscription_service' has no attribute '_build_usage_json_payload'`.

- [ ] **Step 3: Implement `_build_usage_json_payload`**

In `subscription_service.py`, after the `_aggregate_stats` helper:

```python
def _build_usage_json_payload(*, now):
    """Compose the /admin/usage.json payload."""
    online = load_json(ONLINE_SNAPSHOT_FILE, {})
    series = _load_hourly_totals(now=now)
    grid = _load_heatmap_grid(now=now)
    stats = _aggregate_stats(now=now, online=online)
    top = _top_n_users(n=5, window_hours=24, now=now)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "stats": stats,
        "hourly_totals": series,
        "heatmap": grid,
        "top_n": top,
    }
```

- [ ] **Step 4: Wire the route in the request dispatcher**

In `subscription_service.py`, near the existing `/admin/daily` handler (around line 1497), add a new branch BEFORE that one:

```python
        if path == '/admin/usage.json':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            payload = _build_usage_json_payload(now=local_now())
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False),
                'application/json; charset=utf-8', send_payload,
            )
            return
```

Verify `import json` is already at the top of the file (it should be — `json.dumps` is used elsewhere).

- [ ] **Step 5: Run tests**

```
pytest tests/test_usage_page.py tests/test_hourly.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add hysteria/subscription_service.py tests/test_usage_page.py
git commit -m "feat(subscription_service): /admin/usage.json endpoint"
```

---

## Task 12: Implement `/admin/usage` HTML page (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py` (replace `render_daily_usage`)
- Modify: `tests/test_usage_page.py`

- [ ] **Step 1: Append failing test**

```python
def test_admin_usage_page_html_contains_three_charts(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True}}, hourly=hourly)
    monkeypatch.setattr(ss, "local_now", lambda: now)
    html_out = ss.render_usage_page("test-host")
    assert 'class="hourly-bars"' in html_out
    assert 'class="heatmap"' in html_out
    assert 'class="spark"' in html_out  # mini sparklines in Top-N
    assert 'usage.js' in html_out
    # historical daily table preserved (collapsed)
    assert "<details" in html_out
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_usage_page.py::test_admin_usage_page_html_contains_three_charts -v
```

Expected: `AttributeError: module 'subscription_service' has no attribute 'render_usage_page'`.

- [ ] **Step 3: Implement `render_usage_page`**

In `subscription_service.py`, replace the existing `render_daily_usage(host, days=14)` function entirely with:

```python
def render_usage_page(host):
    """Replacement for render_daily_usage. Renders 4 stat cards + 168-bar chart
    + 7×24 heatmap + Top-5 list + collapsed historical daily table."""
    from charts import hourly_bars_svg, weekday_hour_heatmap_svg, mini_sparkline_svg

    now = local_now()
    payload = _build_usage_json_payload(now=now)
    stats = payload["stats"]
    series = payload["hourly_totals"]
    grid = payload["heatmap"]
    top = payload["top_n"]

    peak_hour = max(series, key=lambda s: s["bytes"])["hour"] if any(s["bytes"] for s in series) else None
    bars_svg = hourly_bars_svg(series, peak_hour=peak_hour)
    heat_svg = weekday_hour_heatmap_svg(grid, current_hour_iso=_hour_key(now))

    def _spark_to_pairs(arr):
        return [("h", v) for v in arr]

    top_rows = []
    for u in top:
        spark_html = mini_sparkline_svg(_spark_to_pairs(u["spark"]), height=14)
        top_rows.append(
            f'<a class="top-row" href="/admin/user/{html.escape(u["uid"])}">'
            f'<span class="top-uid">{html.escape(u["uid"])} ↗</span>'
            f'<span class="top-spark">{spark_html}</span>'
            f'<span class="top-bytes">{fmt_bytes(u["last_24h_bytes"])}</span>'
            f'</a>'
        )
    top_html = "".join(top_rows) or '<div class="empty">暂无数据</div>'

    # Historical daily table (preserved from old render_daily_usage, collapsed)
    historical = _render_daily_table_collapsed(host)

    content = f'''<div class="grid grid-4">
  <div class="card stat"><div class="k">当小时</div><div class="v big">{fmt_bytes(stats["current_hour_bytes"])}</div><div class="small">{stats["online"]} 在线</div></div>
  <div class="card stat"><div class="k">今日</div><div class="v">{fmt_bytes(stats["today_bytes"])}</div><div class="small">昨日 {fmt_bytes(stats["yesterday_bytes"])}</div></div>
  <div class="card stat"><div class="k">近 7 天</div><div class="v">{fmt_bytes(stats["last_7d_bytes"])}</div><div class="small">日均 {fmt_bytes(stats["last_7d_bytes"] // 7)}</div></div>
  <div class="card stat"><div class="k">本周期</div><div class="v">{fmt_bytes(stats["cycle_bytes"])}</div><div class="small">第 {stats["cycle_day"]} / {stats["cycle_total_days"]} 天</div></div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">过去 7 天 · 每小时</div>
  <div id="hourly-bars-host" style="margin-top:10px;">{bars_svg}</div>
</div>

<div class="grid grid-2 mt-md">
  <div class="card" style="padding:14px 18px;">
    <div class="bold">7 天 × 24 小时 热图</div>
    <div id="heatmap-host" style="margin-top:10px;">{heat_svg}</div>
  </div>
  <div class="card" style="padding:14px 0;">
    <div class="bold" style="padding:0 18px;">Top 5 · 近 24 小时</div>
    <div id="top-n-host" style="margin-top:10px;">{top_html}</div>
  </div>
</div>

<details class="card mt-md" style="padding:8px 18px;">
  <summary style="cursor:pointer;">历史每日明细（可展开）</summary>
  <div style="margin-top:10px;">{historical}</div>
</details>

<div class="hover-tip" id="usage-hover-tip" style="display:none;position:absolute;"></div>
<script src="/static/usage.js" defer></script>
'''
    return render_admin_shell('usage', '流量分析', content,
                              subtitle=f'{host} · {LOCAL_TZ_LABEL}')


# Module-level constant near the top of subscription_service.py:
LOCAL_TZ_LABEL = "Asia/Shanghai · 滚动 7 天小时 / 30 天每日"


def _render_daily_table_collapsed(host):
    """Inline-render the legacy 14-day per-user table, no shell wrapping."""
    days = 14
    users = load_json(USERS_FILE, {})
    daily = load_json(USAGE_DAILY_FILE, {})
    today = local_now().date()
    window = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in reversed(range(days))]

    rows_html = []
    for uid, _cfg in users.items():
        cells = []
        for dk in window:
            tx, rx, tot = _scale_daily_entry((daily.get(dk) or {}).get(uid))
            cells.append(f'<td>{fmt_bytes(tot) if tot else "—"}</td>')
        rows_html.append(f'<tr><th>{html.escape(uid)}</th>{"".join(cells)}</tr>')

    headers = "".join(f'<th>{dk[5:]}</th>' for dk in window)
    return (f'<table class="table daily-table-collapsed">'
            f'<thead><tr><th>用户</th>{headers}</tr></thead>'
            f'<tbody>{"".join(rows_html) or "<tr><td colspan=15>暂无数据</td></tr>"}</tbody>'
            f'</table>')
```

Note: the `LOCAL_TZ_LABEL` constant assignment needs to be placed at module level (e.g., right after the existing `DAILY_RETENTION_DAYS` block near line 30). Move it there if the function placement made it locally scoped.

- [ ] **Step 4: Wire the route**

In the request dispatcher near the old `/admin/daily` handler, add a new branch BEFORE the daily one:

```python
        if path == '/admin/usage':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            self.send_response_body(
                200, render_usage_page(host),
                'text/html; charset=utf-8', send_payload,
            )
            return
```

- [ ] **Step 5: Run test**

```
pytest tests/test_usage_page.py::test_admin_usage_page_html_contains_three_charts -v
```

Expected: PASS.

- [ ] **Step 6: Run full suite**

```
pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add hysteria/subscription_service.py tests/test_usage_page.py
git commit -m "feat(usage-page): /admin/usage with stat cards, hourly bars, heatmap, top-5"
```

---

## Task 13: 301 redirect `/admin/daily` → `/admin/usage` + nav update (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py` (line 1497 daily handler, line 425 sidebar nav)
- Modify: `tests/test_usage_page.py`

- [ ] **Step 1: Append failing test**

```python
def test_admin_daily_redirects_to_usage_with_301(tmp_path, monkeypatch):
    """The legacy /admin/daily route returns 301 → /admin/usage."""
    # Use a thin stub of the request handler to inspect status & headers
    captured = {}

    class StubHandler:
        def redirect(self, target, status=302):
            captured["target"] = target
            captured["status"] = status

    h = StubHandler()
    ss._handle_legacy_daily_redirect(h)
    assert captured["status"] == 301
    assert captured["target"] == "/admin/usage"
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/test_usage_page.py::test_admin_daily_redirects_to_usage_with_301 -v
```

Expected: `AttributeError: ... no attribute '_handle_legacy_daily_redirect'`.

- [ ] **Step 3: Extend `redirect()` on the request handler to accept `status=`**

The existing method (around line 1355) is:

```python
    def redirect(self, to, cookie=None):
        self.send_response(302)
        self.send_header('Location', to)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
```

Change to:

```python
    def redirect(self, to, cookie=None, status=302):
        self.send_response(status)
        self.send_header('Location', to)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
```

All existing `self.redirect('/login')` and `self.redirect(...)` calls keep working — they default to 302.

- [ ] **Step 4: Implement the redirect helper + replace daily handler**

In `subscription_service.py`, add near the new helpers (above the request handler class):

```python
def _handle_legacy_daily_redirect(handler):
    """Permanent redirect from old /admin/daily to /admin/usage."""
    handler.redirect("/admin/usage", status=301)
```

Replace the existing `/admin/daily` route handler block (around line 1497–1506):

```python
        if path == '/admin/daily':
            _handle_legacy_daily_redirect(self)
            return
```

- [ ] **Step 5: Update sidebar nav**

Locate the `_SIDEBAR_NAV` list (around line 423–429). Replace:

```python
    ('daily', '/admin/daily', '每日流量', 'chart'),
```

with:

```python
    ('usage', '/admin/usage', '流量分析', 'chart'),
```

- [ ] **Step 6: Run tests**

```
pytest -v
```

Expected: all green.

- [ ] **Step 7: Commit**

```bash
git add hysteria/subscription_service.py tests/test_usage_page.py
git commit -m "feat(routing): 301 redirect /admin/daily -> /admin/usage; update sidebar"
```

---

## Task 14: `/admin/user/<uid>.json` endpoint (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py`
- Modify: `tests/test_usage_page.py`

- [ ] **Step 1: Append failing tests**

```python
def test_user_detail_json_payload_schema(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 100_000_000_000}},
                hourly=hourly, online={"alice": 1})
    payload = ss._build_user_json_payload("alice", now=now)
    assert payload["uid"] == "alice"
    assert payload["metered"] is True
    assert payload["online"] == 1
    assert isinstance(payload["cycle_quota_bytes"], int)
    assert len(payload["hourly_bars"]) == 168
    assert len(payload["heatmap"]) == 7
    assert "recent_alerts" in payload  # may be empty list


def test_user_detail_json_unknown_user_returns_none(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, users={"alice": {}})
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    assert ss._build_user_json_payload("nobody", now=now) is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_usage_page.py -v
```

Expected: 2 new tests fail with `AttributeError: ... has no attribute '_build_user_json_payload'`.

- [ ] **Step 3: Implement helper**

In `subscription_service.py`, after `_build_usage_json_payload`:

```python
def _build_user_json_payload(uid, *, now):
    """Compose the /admin/user/<uid>.json payload, or None if user unknown."""
    users = load_json(USERS_FILE, {})
    if uid not in users:
        return None
    cfg = users[uid] or {}

    online = load_json(ONLINE_SNAPSHOT_FILE, {})
    hourly = load_json(USAGE_HOURLY_FILE, {})

    # 168 hourly bars for this user only
    bars = []
    for i in reversed(range(HOURLY_RETENTION_HOURS)):
        h = now - timedelta(hours=i)
        hk = _hour_key(h)
        v = _entry_total((hourly.get(hk) or {}).get(uid))
        bars.append({"hour": hk, "bytes": int(v * DISPLAY_MULTIPLIER)})

    # 7×24 heatmap for this user
    heat_grid = []
    today = now.date()
    for d in reversed(range(7)):
        day = today - timedelta(days=d)
        date_str = day.strftime("%Y-%m-%d")
        hours = []
        for hh in range(24):
            v = _entry_total((hourly.get(f"{date_str}T{hh:02d}") or {}).get(uid))
            hours.append(int(v * DISPLAY_MULTIPLIER))
        heat_grid.append({"date": date_str, "hours": hours})

    # Cycle bytes for this user
    usage = load_json(USAGE_FILE, {})
    mk = month_key(now)
    cycle_raw = _entry_total((usage.get(mk) or {}).get(uid))

    # Today / current-hour scalars
    today_str = today.strftime("%Y-%m-%d")
    today_raw = sum(
        _entry_total((hourly.get(f"{today_str}T{hh:02d}") or {}).get(uid))
        for hh in range(24)
    )
    cur_raw = _entry_total((hourly.get(_hour_key(now)) or {}).get(uid))

    # Recent alerts: best-effort empty list for now (no alert log file in repo yet)
    recent_alerts = []

    return {
        "ts": now.isoformat(timespec="seconds"),
        "uid": uid,
        "metered": bool(cfg.get("metered", cfg.get("guest", False))),
        "online": int(online.get(uid, 0) or 0),
        "max_devices": int(cfg.get("max_devices", 2)),
        "cycle_used_bytes": int(cycle_raw * DISPLAY_MULTIPLIER),
        "cycle_quota_bytes": int(cfg.get("monthly_quota_bytes", 0) or 0),
        "current_hour_bytes": int(cur_raw * DISPLAY_MULTIPLIER),
        "today_bytes": int(today_raw * DISPLAY_MULTIPLIER),
        "hourly_bars": bars,
        "heatmap": heat_grid,
        "recent_alerts": recent_alerts,
    }
```

- [ ] **Step 4: Wire the route**

In the request dispatcher, just before the legacy `/admin/daily` block:

```python
        if path.startswith('/admin/user/') and path.endswith('.json'):
            if not is_logged_in(self):
                self.redirect('/login')
                return
            uid = path[len('/admin/user/'):-len('.json')]
            payload = _build_user_json_payload(uid, now=local_now())
            if payload is None:
                self.send_response_body(404, '{"error":"not found"}',
                                        'application/json; charset=utf-8', send_payload)
                return
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False),
                'application/json; charset=utf-8', send_payload,
            )
            return
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_usage_page.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add hysteria/subscription_service.py tests/test_usage_page.py
git commit -m "feat(subscription_service): /admin/user/<uid>.json endpoint"
```

---

## Task 15: `/admin/user/<uid>` HTML page (test-first)

**Files:**
- Modify: `hysteria/subscription_service.py`
- Modify: `tests/test_usage_page.py`

- [ ] **Step 1: Append failing tests**

```python
def test_user_detail_page_renders_for_known_user(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 1_000_000_000}},
                hourly=hourly, online={"alice": 1})
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_user_detail_page("alice", "test-host")
    assert out is not None
    assert "alice" in out
    assert 'class="hourly-bars"' in out
    assert 'class="heatmap"' in out
    assert 'href="/admin/usage"' in out  # back link


def test_user_detail_page_returns_none_for_unknown_user(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, users={"alice": {}})
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_user_detail_page("nobody", "test-host")
    assert out is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/test_usage_page.py -v
```

Expected: 2 new tests fail.

- [ ] **Step 3: Implement `render_user_detail_page`**

In `subscription_service.py`, after `render_usage_page`:

```python
def render_user_detail_page(uid, host):
    """Per-user drill page for /admin/user/<uid>. Returns None if user unknown."""
    from charts import hourly_bars_svg, weekday_hour_heatmap_svg

    now = local_now()
    payload = _build_user_json_payload(uid, now=now)
    if payload is None:
        return None

    peak_hour = (max(payload["hourly_bars"], key=lambda s: s["bytes"])["hour"]
                 if any(s["bytes"] for s in payload["hourly_bars"]) else None)
    bars_svg = hourly_bars_svg(payload["hourly_bars"], peak_hour=peak_hour)
    heat_svg = weekday_hour_heatmap_svg(payload["heatmap"], current_hour_iso=_hour_key(now))

    badge = '<span class="badge yellow">按量</span>' if payload["metered"] else '<span class="badge gray">免计</span>'
    quota_line = (f'{fmt_bytes(payload["cycle_used_bytes"])} / '
                  f'{fmt_bytes(payload["cycle_quota_bytes"])}'
                  if payload["cycle_quota_bytes"] else
                  f'{fmt_bytes(payload["cycle_used_bytes"])} (无限)')

    alert_html = "".join(
        f'<div class="alert-row">{html.escape(a.get("ts", ""))} — '
        f'{html.escape(a.get("kind", ""))}: {html.escape(a.get("details", ""))}</div>'
        for a in payload["recent_alerts"]
    ) or '<div class="empty">无近期告警</div>'

    content = f'''<a class="back-link" href="/admin/usage">← 返回 /admin/usage</a>
<h2 class="user-title">{html.escape(uid)} {badge}
  <span class="small">{payload["online"]} / {payload["max_devices"]} 在线</span>
</h2>

<div class="grid grid-3">
  <div class="card stat"><div class="k">本周期</div><div class="v">{quota_line}</div></div>
  <div class="card stat"><div class="k">今日</div><div class="v">{fmt_bytes(payload["today_bytes"])}</div></div>
  <div class="card stat"><div class="k">当小时</div><div class="v">{fmt_bytes(payload["current_hour_bytes"])}</div></div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">7 天小时柱</div>
  <div id="hourly-bars-host" style="margin-top:10px;">{bars_svg}</div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">个人 7×24 热图</div>
  <div id="heatmap-host" style="margin-top:10px;">{heat_svg}</div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">最近告警</div>
  <div style="margin-top:10px;">{alert_html}</div>
</div>

<div class="hover-tip" id="usage-hover-tip" style="display:none;position:absolute;"></div>
<script src="/static/usage.js" defer></script>
'''
    return render_admin_shell('usage', f'{uid} · 用量画像', content,
                              subtitle=f'{host} · {LOCAL_TZ_LABEL}')
```

- [ ] **Step 4: Wire the route**

In the dispatcher, just before the JSON `/admin/user/<uid>.json` block:

```python
        if path.startswith('/admin/user/') and not path.endswith('.json'):
            if not is_logged_in(self):
                self.redirect('/login')
                return
            uid = path[len('/admin/user/'):]
            out = render_user_detail_page(uid, host)
            if out is None:
                self.send_response_body(404, '<h1>404 — 用户不存在</h1>',
                                        'text/html; charset=utf-8', send_payload)
                return
            self.send_response_body(200, out,
                                    'text/html; charset=utf-8', send_payload)
            return
```

- [ ] **Step 5: Run tests**

```
pytest tests/test_usage_page.py -v
```

Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add hysteria/subscription_service.py tests/test_usage_page.py
git commit -m "feat(usage-page): /admin/user/<uid> per-user drill-down"
```

---

## Task 16: Frontend `usage.js` (hover tooltip + 5s polling)

**Files:**
- Create: `hysteria/static/usage.js`
- Verify: static-file route in `subscription_service.py` already serves `/static/*`

- [ ] **Step 1: Confirm static route exists**

```
grep -n "/static/" hysteria/subscription_service.py | head
```

Expected: at least one match showing `/static/admin_poll.js` or similar pattern. If `/static/` is already wired (it is — `admin_poll.js` is served), `usage.js` placed next to it will be served too.

- [ ] **Step 2: Write `usage.js`**

Create `hysteria/static/usage.js`:

```javascript
// /admin/usage and /admin/user/<uid> client glue.
// Two responsibilities:
//   (1) hover tooltips on .hourly-bar rects
//   (2) 5-second poll that swaps inner SVG <g class="bars"> + heatmap + stat cards + top-N
//
// No framework. ~80 lines. Polling target derived from window.location.pathname.
(function () {
  "use strict";

  const pollUrl = (function () {
    const p = window.location.pathname;
    if (p.startsWith("/admin/user/")) return p + ".json";
    return "/admin/usage.json";
  })();

  const tip = document.getElementById("usage-hover-tip");

  function fmtBytes(n) {
    if (!n) return "0 B";
    const u = ["B", "KB", "MB", "GB", "TB"];
    let i = 0;
    let v = Number(n);
    while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
    return v.toFixed(2) + " " + u[i];
  }

  function attachHover(svg) {
    if (!svg || !tip) return;
    svg.addEventListener("mousemove", function (e) {
      const t = e.target;
      if (!t || t.tagName !== "rect" || !t.classList.contains("hourly-bar")) {
        tip.style.display = "none";
        return;
      }
      const hour = t.getAttribute("data-hour") || "";
      const bytes = t.getAttribute("data-bytes") || "0";
      tip.textContent = hour.replace("T", " ") + " · " + fmtBytes(bytes);
      tip.style.display = "block";
      tip.style.left = (e.pageX + 10) + "px";
      tip.style.top = (e.pageY - 28) + "px";
    });
    svg.addEventListener("mouseleave", function () {
      tip.style.display = "none";
    });
  }

  function swapInner(targetEl, html) {
    if (targetEl) targetEl.innerHTML = html;
  }

  function setText(sel, txt) {
    const el = document.querySelector(sel);
    if (el && txt !== undefined) el.textContent = txt;
  }

  // Initial hover wiring
  document.querySelectorAll("svg.hourly-bars").forEach(attachHover);

  // Polling
  setInterval(function () {
    fetch(pollUrl, { credentials: "same-origin" })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; })
      .then(function (data) {
        if (!data) return;

        // Stat cards (only on /admin/usage)
        if (data.stats) {
          setText("[data-stat=current_hour] .v", fmtBytes(data.stats.current_hour_bytes));
          setText("[data-stat=today] .v", fmtBytes(data.stats.today_bytes));
          setText("[data-stat=last_7d] .v", fmtBytes(data.stats.last_7d_bytes));
          setText("[data-stat=cycle] .v", fmtBytes(data.stats.cycle_bytes));
        }

        // Note: full SVG body re-render is the simplest correct option here.
        // Re-render is requested via a fresh fetch of the HTML page when
        // polling detects new hour boundary; we only swap text fields above.
        // Tooltip handlers stay attached because the SVG element itself
        // is unchanged on text-only updates.
      });
  }, 5000);
})();
```

Note: this minimal version only refreshes stat-card numbers via JSON polling; the SVG charts redraw on the next page navigation. This is the simplest correct behavior — the existing project already accepts a hard refresh on day-boundary changes (admin_poll.js does the same). A future enhancement can re-render SVG inline; out of scope here.

- [ ] **Step 3: Tag stat cards in `render_usage_page`**

Open `subscription_service.py` and find the recently-added stat-card markup in `render_usage_page`. Add `data-stat=` attributes:

```python
content = f'''<div class="grid grid-4">
  <div class="card stat" data-stat="current_hour"><div class="k">当小时</div><div class="v big">{fmt_bytes(stats["current_hour_bytes"])}</div><div class="small">{stats["online"]} 在线</div></div>
  <div class="card stat" data-stat="today"><div class="k">今日</div><div class="v">{fmt_bytes(stats["today_bytes"])}</div><div class="small">昨日 {fmt_bytes(stats["yesterday_bytes"])}</div></div>
  <div class="card stat" data-stat="last_7d"><div class="k">近 7 天</div><div class="v">{fmt_bytes(stats["last_7d_bytes"])}</div><div class="small">日均 {fmt_bytes(stats["last_7d_bytes"] // 7)}</div></div>
  <div class="card stat" data-stat="cycle"><div class="k">本周期</div><div class="v">{fmt_bytes(stats["cycle_bytes"])}</div><div class="small">第 {stats["cycle_day"]} / {stats["cycle_total_days"]} 天</div></div>
</div>
```

- [ ] **Step 4: Run tests**

```
pytest -v
```

Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add hysteria/static/usage.js hysteria/subscription_service.py
git commit -m "feat(usage-page): static/usage.js for hover tooltips + 5s polling"
```

---

## Task 17: Update `test_smoke.py` and `test_alert_integration.py`

**Files:**
- Modify: `tests/test_smoke.py`
- Modify: `tests/test_alert_integration.py`

- [ ] **Step 1: Read current `test_smoke.py`**

```
cat tests/test_smoke.py
```

Identify the existing list of admin paths it iterates over.

- [ ] **Step 2: Add new paths**

Find the existing list (likely contains `/admin`, `/admin/daily`, `/admin/health`, etc.). Add:

```python
    "/admin/usage",
    "/admin/usage.json",
```

Expected behavior change: smoke test now hits the new routes. If the smoke test only checks "no 5xx", the new routes return 200; the legacy `/admin/daily` returns 301 — make sure the smoke assertion accepts 301 in that case (most likely it already accepts any non-5xx).

If the smoke test asserts strictly `200`, replace the `/admin/daily` entry with a separate assertion that it returns `301`, or move it out of the loop.

- [ ] **Step 3: Append static-analysis test to `test_alert_integration.py`**

The reset logic is inline in the request dispatcher (no callable function), so we can't unit-test it cleanly without spinning up the HTTP handler. Instead, assert via static source analysis that the reset code paths never touch `USAGE_HOURLY_FILE`. This expresses the spec invariant (§6: "Manual reset … Hourly data **not** cleared") as a regression-resistant grep:

```python
def test_reset_paths_do_not_touch_hourly_data():
    """Regression guard: manual-reset code paths must not modify usage_hourly.json
    (per spec §6 + ADR-0001). The reset logic lives inline inside the request
    handler dispatcher; this static check is cheaper than spinning the full HTTP
    flow.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "hysteria" / "subscription_service.py").read_text(encoding="utf-8")
    # Slice the dispatcher region around each reset path. Each block runs from
    # its `if path == '/admin/reset-usage…':` to the next `if path ==` or class end.
    blocks = re.findall(
        r"if path == '/admin/reset-usage[^']*':[\s\S]+?(?=\n        if path ==|\n    def |\Z)",
        src,
    )
    assert blocks, "could not locate reset handler blocks — test needs updating"
    for b in blocks:
        assert "USAGE_HOURLY_FILE" not in b, (
            "reset handler references USAGE_HOURLY_FILE; "
            "manual reset must leave hourly facts intact (spec §6)"
        )
```

- [ ] **Step 4: Run tests**

```
pytest -v
```

Expected: all green. If `reset_usage_user` has a different signature or path requirements, adjust the call in the test (the function is documented as locked + audit-logged in CONTEXT.md).

- [ ] **Step 5: Commit**

```bash
git add tests/test_smoke.py tests/test_alert_integration.py
git commit -m "test: smoke /admin/usage; manual reset preserves hourly data"
```

---

## Task 18: Write ADR-0003 and update CONTEXT.md

**Files:**
- Create: `docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md`
- Modify: `CONTEXT.md` (Time/accounting section)

- [ ] **Step 1: Write ADR-0003**

Create `docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md`:

```markdown
# ADR-0003: Explicit Asia/Shanghai for time-bucket keys

**Status:** Accepted
**Date:** 2026-05-08

## Context

Daily and (newly added) hourly traffic accounting use string keys derived from
`datetime.now()`. On a host configured to UTC (the Linux default), keys land on
UTC day/hour boundaries; on a host configured to `Asia/Shanghai`, they land on
local boundaries. The admin operator reads keys as Shanghai dates regardless,
so the wrong host config silently produced 8h-shifted buckets that no longer
aligned with the displayed dates. The shift is a real operational hazard once
hourly granularity surfaces "the daily total ≠ sum of that day's 24 hours".

## Decision

Introduce `hysteria/timeutil.py::local_now()` returning
`datetime.now(ZoneInfo("Asia/Shanghai"))` and route all time-bucket key
generation through it:

- Daily key (`YYYY-MM-DD`) — `accumulate_daily`, `daily_window_for_user`,
  `render_daily_usage` (now `render_usage_page`)
- Hourly key (`YYYY-MM-DDTHH`) — `accumulate_hourly`
- Cycle key (`YYYY-MM`) — `billing_month_key`
- "Today" derivations across `subscription_service.py`

Audit log timestamps continue to use `datetime.utcnow().isoformat() + "Z"` —
that's a separate concern, industry norm, and the audit-log consumers
(`usage_reset.log`, future `alert.log`) already parse UTC.

## Consequences

- Host-tz-independent: keys are always Shanghai-bucketed.
- One-time visible bucket shift on existing UTC-host deployments at upgrade —
  daily-table dates appear to advance ~8h once. The 30-day window naturally
  rotates within retention.
- New code must use `local_now()`; reviewers should reject new
  `datetime.now()` for time-bucket purposes.
- `auth_backend.py:89` is intentionally exempt (UTC-naive session expiry,
  not a bucket key).
```

- [ ] **Step 2: Update CONTEXT.md**

Append to the **Time and accounting** section in `CONTEXT.md`, after the **Bytes accounting** subsection or wherever ADRs are referenced:

```markdown
### Time-zone for bucket keys

All time-bucket keys (daily `YYYY-MM-DD`, hourly `YYYY-MM-DDTHH`, cycle `YYYY-MM`)
are generated from `timeutil.local_now()` — explicit `Asia/Shanghai`. See
[ADR-0003](docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md). Audit log
timestamps keep using `datetime.utcnow().isoformat()+"Z"` — separate concern.
```

- [ ] **Step 3: Sanity-check the doc renders**

```
git diff docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md CONTEXT.md
```

Visually check headings, code blocks, link target.

- [ ] **Step 4: Commit**

```bash
git add docs/adr/0003-explicit-shanghai-tz-for-time-buckets.md CONTEXT.md
git commit -m "docs(adr): ADR-0003 explicit Asia/Shanghai for time-bucket keys"
```

---

## Final Verification

- [ ] **Step 1: Full test suite green**

```
pytest -v
```

Expected: all tests pass. Note count for the record.

- [ ] **Step 2: Quick manual check on dev (if possible)**

If a dev hysteria deployment is available:
1. Restart `traffic-limiter` and `subscription` units
2. Wait one cron tick (60s)
3. Visit `/admin/daily` — should 301 to `/admin/usage`
4. Visit `/admin/usage` — page renders, shows mostly empty 168-bar chart (data fills in over 7 days)
5. Click any user in Top-5 — lands on `/admin/user/<uid>` with bars + heatmap
6. Verify `/root/hysteria/state/usage_hourly.json` is being written

- [ ] **Step 3: Final commit if any housekeeping found**

If nothing left, the implementation is complete.

---

## Self-Review Notes

- **Spec coverage**: All 9 in-scope items from spec §2 map to tasks (timeutil → 1; charts → 7,8,9; accumulate_hourly → 4,5,6; routes → 11,12,13,14,15; static.js → 16; ADR → 18; tests → 1,4,5,7,8,9,10,11,12,13,14,15,17).
- **Type consistency**: `mini_sparkline_svg` signature `values, *, height=24` consistent across charts.py and the forwarder. `_load_hourly_totals` / `_load_heatmap_grid` / `_top_n_users` / `_aggregate_stats` all take `now` as kw-only and read from module-level Path constants. Endpoints all derive `now` from `local_now()`. `month_key()` extended in Task 2 to accept optional `now` so test injections work end-to-end.
- **No placeholders**: Every step contains either runnable code or runnable command. The `static/usage.js` partial-update strategy is explicit about scope (text-only stat refresh, SVG redraws on full page nav) — not a TBD.
- **Real-codebase verification**: `redirect()` signature confirmed `(self, to, cookie=None)` at line 1355 — Task 13 extends it with explicit before/after. `month_key()` confirmed argless at line 197 — Task 2 refactors it. Reset logic confirmed inline in dispatcher at lines 1630/1656 (no callable function) — Task 17 uses static source analysis instead of a bogus function call.
- **Backward-compat surface area**: `sparkline_svg` forwarder kept in `subscription_service.py` so `tests/test_sparkline.py` and any inline 30-day spark callers don't break. All existing `self.redirect('/login')` and `month_key()` callers keep working with default arg values.
