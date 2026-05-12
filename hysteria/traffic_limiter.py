#!/usr/bin/env python3
import json
import os
import fcntl
import subprocess
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
from display import DISPLAY_MULTIPLIER
from timeutil import local_now

import alerts as _alerts
import anomaly as _anomaly
import user_compat
import xray_config

_DM = DISPLAY_MULTIPLIER

XRAY_BIN = "/usr/local/bin/xray"
XRAY_API = "127.0.0.1:10085"

USERS_FILE = "/root/hysteria/users.json"
USAGE_FILE = "/root/hysteria/state/usage.json"
USAGE_DAILY_FILE = "/root/hysteria/state/usage_daily.json"
USAGE_HOURLY_FILE = "/root/hysteria/state/usage_hourly.json"
ONLINE_SNAPSHOT_FILE = "/root/hysteria/state/online.json"
RESET_STATE_FILE = "/root/hysteria/state/auto_reset_state.json"
RESET_LOG_FILE = "/root/hysteria/state/usage_reset.log"
USAGE_LOCK_FILE = "/root/hysteria/state/usage.lock"
META_FILE = "/root/hysteria/subscription_meta.json"
SETTLEMENT_DAY_DEFAULT = 12
CYCLE_LENGTH_DAYS_DEFAULT = 30
CYCLE_LENGTH_MIN = 1
CYCLE_LENGTH_MAX = 90
DAILY_RETENTION_DAYS = 30
HOURLY_RETENTION_HOURS = 168
API_BASE = "http://127.0.0.1:25413"
API_SECRET = "__HY_API_SECRET__"


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def get_settlement_day():
    """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
    try:
        v = int((load_json(META_FILE, {}) or {}).get("settlement_day", SETTLEMENT_DAY_DEFAULT))
    except (TypeError, ValueError):
        return SETTLEMENT_DAY_DEFAULT
    return max(1, min(28, v))


def get_cycle_length_days():
    """Length of one billing cycle, in days. Mirrors subscription_service.get_cycle_length_days."""
    try:
        v = int((load_json(META_FILE, {}) or {}).get("cycle_length_days", CYCLE_LENGTH_DAYS_DEFAULT))
    except (TypeError, ValueError):
        return CYCLE_LENGTH_DAYS_DEFAULT
    return max(CYCLE_LENGTH_MIN, min(CYCLE_LENGTH_MAX, v))


def _settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now."""
    if now.day >= settlement_day:
        return now.date().replace(day=settlement_day)
    prev_month_end = now.replace(day=1) - timedelta(days=1)
    return prev_month_end.date().replace(day=settlement_day)


def get_cycle_anchor_date(now):
    """Persisted anchor date (the cycle calendar's origin). Falls back to the
    most recent settlement_day on/before now if not yet stored."""
    meta = load_json(META_FILE, {}) or {}
    raw = meta.get("cycle_anchor_date")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            pass
    return _settlement_anchor_date(now, get_settlement_day())


def save_json(path, data):
    """Atomic write: serialize to a sibling temp file, fsync, then rename.
    Prevents truncated state files — load_json silently returns `{}` on parse
    errors, which has caused cycle/reset state to be lost across the boundary
    of an interrupted oneshot tick."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=True, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def usage_lock():
    os.makedirs(os.path.dirname(USAGE_LOCK_FILE), exist_ok=True)
    with open(USAGE_LOCK_FILE, "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def append_reset_log(actor, action, target, before, after, mk):
    line = {
        "time": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "actor": actor,
        "ip": "",
        "action": action,
        "target": target,
        "month": mk,
        "before": before,
        "after": after,
    }
    os.makedirs(os.path.dirname(RESET_LOG_FILE), exist_ok=True)
    with open(RESET_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=True) + "\n")


def get(path):
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        headers={"Authorization": API_SECRET},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post(path, obj):
    body = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(
        f"{API_BASE}{path}",
        data=body,
        headers={"Authorization": API_SECRET, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=3):
        return


def billing_month_key(now, day=None):
    """Cycle key (YYYY-MM) keyed on the settlement day. Before the settlement day,
    traffic belongs to the previous cycle. Must match subscription_service.month_key()
    / auth_backend month logic."""
    d = int(day if day is not None else get_settlement_day())
    if now.day >= d:
        return now.strftime("%Y-%m")
    prev = now.replace(day=1) - timedelta(days=1)
    return prev.strftime("%Y-%m")


def cycle_days(now, day=None, length=None, anchor=None):
    """List of YYYY-MM-DD date keys in the current cycle, oldest first.

    Uses fixed-N-day rolling blocks anchored at `anchor` (defaults to the
    persisted `cycle_anchor_date`, or to the most recent settlement_day on/
    before now if not set)."""
    if anchor is None:
        if day is None:
            anchor = get_cycle_anchor_date(now)
        else:
            anchor = _settlement_anchor_date(now, int(day))
    N = int(length) if length is not None else get_cycle_length_days()
    today = now.date()
    if today < anchor:
        start = anchor
    else:
        offset_days = (today - anchor).days
        start = anchor + timedelta(days=(offset_days // N) * N)
    end = min(start + timedelta(days=N - 1), today)
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def cycle_used_raw_for(uid, daily, *, now, day=None):
    """Per-user raw cycle bytes derived from usage_daily.json. Single source
    of truth for both display and quota enforcement, so the kick check and the
    /admin UI agree."""
    total = 0
    for dk in cycle_days(now, day=day):
        entry = (daily.get(dk) or {}).get(uid)
        if isinstance(entry, dict):
            total += int(entry.get("total", 0))
        else:
            total += int(entry or 0)
    return total


def normalize_usage_entry(entry):
    if isinstance(entry, dict):
        tx = int(entry.get("tx", 0))
        rx = int(entry.get("rx", 0))
        total = int(entry.get("total", tx + rx))
        return {"tx": tx, "rx": rx, "total": total}
    total = int(entry or 0)
    return {"tx": 0, "rx": total, "total": total}


def maybe_reset_all_usage_on_day_21(now, users, usage, month, day=None):
    """Once-per-cycle zeroing of usage on the settlement day. Idempotent across
    multiple cron ticks via auto_reset_state.last_reset_month."""
    d = int(day if day is not None else get_settlement_day())
    if now.day != d:
        return
    state = load_json(RESET_STATE_FILE, {})
    if state.get("last_reset_month") == month:
        return

    usage.setdefault(month, {})
    before_all = {}
    for uid in users.keys():
        before_all[uid] = normalize_usage_entry(usage[month].get(uid, 0))
        usage[month][uid] = {"tx": 0, "rx": 0, "total": 0}

    save_json(USAGE_FILE, usage)
    append_reset_log(
        actor="system",
        action="reset_usage_all_auto_day21",
        target="all_users",
        before=before_all,
        after={u: {"tx": 0, "rx": 0, "total": 0} for u in users.keys()},
        mk=month,
    )
    save_json(
        RESET_STATE_FILE,
        {
            "last_reset_month": month,
            "last_reset_time": now.isoformat(timespec="seconds"),
        },
    )


def get_xray_traffic():
    try:
        out = subprocess.check_output(
            [XRAY_BIN, "api", "statsquery", f"--server={XRAY_API}",
             "-pattern", "user>>>", "-reset"],
            timeout=3,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(out.decode("utf-8"))
    except Exception:
        return {}
    result = {}
    for stat in data.get("stat") or []:
        name = stat.get("name", "")
        parts = name.split(">>>")
        if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
            continue
        email = parts[1]
        direction = parts[3]
        uid = xray_config.strip_backup_suffix(email)
        val = int(stat.get("value", 0) or 0)
        entry = result.setdefault(uid, {"tx": 0, "rx": 0})
        if direction == "downlink":
            entry["tx"] += val
        elif direction == "uplink":
            entry["rx"] += val
    return result


def merge_traffic(dst, src):
    for uid, stat in src.items():
        cur = dst.setdefault(uid, {"tx": 0, "rx": 0})
        cur["tx"] = int(cur.get("tx", 0)) + int(stat.get("tx", 0))
        cur["rx"] = int(cur.get("rx", 0)) + int(stat.get("rx", 0))


def prune_daily(daily, today):
    cutoff = (today - timedelta(days=DAILY_RETENTION_DAYS - 1)).strftime("%Y-%m-%d")
    for k in list(daily.keys()):
        if k < cutoff:
            del daily[k]


def prune_hourly(hourly, now):
    """Drop hour buckets older than HOURLY_RETENTION_HOURS - 1 hours back from `now`."""
    cutoff = (now - timedelta(hours=HOURLY_RETENTION_HOURS - 1)).strftime("%Y-%m-%dT%H")
    for k in list(hourly.keys()):
        if k < cutoff:
            del hourly[k]


def accumulate_daily(traffic, now):
    day_key = now.strftime("%Y-%m-%d")
    daily = load_json(USAGE_DAILY_FILE, {})
    daily.setdefault(day_key, {})
    for uid, stat in traffic.items():
        cur = normalize_usage_entry(daily[day_key].get(uid, 0))
        tx = int(stat.get("tx", 0))
        rx = int(stat.get("rx", 0))
        cur["tx"] += tx
        cur["rx"] += rx
        cur["total"] += tx + rx
        daily[day_key][uid] = cur
    prune_daily(daily, now.date())
    save_json(USAGE_DAILY_FILE, daily)


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


def _fmt_bytes(n):
    n = float(max(0, int(n)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.2f} {units[i]}"


def check_alerts(usage, users, online, now, month_key, *, _opener=None):
    """Detect quota crossings and daily anomalies, dispatch alerts, persist dedup state.

    Wrapped in a top-level try/except by the caller; this function may itself
    raise on filesystem errors but those should never break the kick path.
    """
    cfg = _alerts.load_config()
    if not cfg:
        return  # nothing configured → nothing to send

    state = _alerts.load_state()
    daily = load_json(USAGE_DAILY_FILE, {})
    today_date = now.date()

    today_key = today_date.strftime('%Y-%m-%d')
    z_threshold = float(cfg.get('anomaly_z_threshold', _alerts.DEFAULT_Z_THRESHOLD))
    min_bytes = int(cfg.get('anomaly_min_bytes', _alerts.DEFAULT_MIN_BYTES))

    for uid, user_cfg in (users or {}).items():
        # ---- quota crossings ----
        quota = int((user_cfg or {}).get('monthly_quota_bytes', 0) or 0)
        if user_compat.is_metered(user_cfg) and quota > 0:
            raw_total = cycle_used_raw_for(uid, daily, now=now)
            scaled = int(raw_total * _DM)
            pct = scaled * 100.0 / quota
            if pct >= 100 and not _alerts.already_alerted(state, 'quota_100', uid, month_key):
                _alerts.dispatch({
                    'kind': 'quota_100', 'user': uid,
                    'details': {'used_human': _fmt_bytes(scaled),
                                'total_human': _fmt_bytes(quota),
                                'cycle': month_key},
                }, config=cfg, opener=_opener)
                _alerts.mark_alerted(state, 'quota_100', uid, month_key)
            elif pct >= 80 and not _alerts.already_alerted(state, 'quota_80', uid, month_key):
                _alerts.dispatch({
                    'kind': 'quota_80', 'user': uid,
                    'details': {'used_human': _fmt_bytes(scaled),
                                'total_human': _fmt_bytes(quota),
                                'cycle': month_key},
                }, config=cfg, opener=_opener)
                _alerts.mark_alerted(state, 'quota_80', uid, month_key)

        # ---- anomaly ----
        if _alerts.already_alerted(state, 'anomaly', uid, today_key):
            continue
        hit = _anomaly.detect(uid, daily, today_date,
                              z_threshold=z_threshold, min_bytes=min_bytes)
        if hit is not None:
            _alerts.dispatch({
                'kind': 'anomaly', 'user': uid,
                'details': {'today_human': _fmt_bytes(int(hit['today'] * _DM)),
                            'mean_human': _fmt_bytes(int(hit['mean'] * _DM)),
                            'z': hit['z']},
            }, config=cfg, opener=_opener)
            _alerts.mark_alerted(state, 'anomaly', uid, today_key)

    _alerts.save_state(state)


def main():
    users = load_json(USERS_FILE, {})
    now = local_now()
    settle_day = get_settlement_day()
    month_key = billing_month_key(now, day=settle_day)
    traffic = get("/traffic?clear=1") or {}
    merge_traffic(traffic, get_xray_traffic())
    with usage_lock():
        usage = load_json(USAGE_FILE, {})
        usage.setdefault(month_key, {})
        maybe_reset_all_usage_on_day_21(now, users, usage, month_key, day=settle_day)
        usage = load_json(USAGE_FILE, {})
        usage.setdefault(month_key, {})

        for uid, stat in traffic.items():
            cur = normalize_usage_entry(usage[month_key].get(uid, 0))
            tx = int(stat.get("tx", 0))
            rx = int(stat.get("rx", 0))
            cur["tx"] += tx
            cur["rx"] += rx
            cur["total"] += tx + rx
            usage[month_key][uid] = cur

        save_json(USAGE_FILE, usage)
        accumulate_daily(traffic, now)
        accumulate_hourly(traffic, now)

    online = get("/online")
    save_json(ONLINE_SNAPSHOT_FILE, online)

    try:
        check_alerts(usage, users, online, now, month_key)
    except Exception as e:
        import sys
        print(f"alerts: skipped due to error: {e}", file=sys.stderr)

    daily_for_kick = load_json(USAGE_DAILY_FILE, {})
    to_kick = []
    for uid, cfg in users.items():
        if not user_compat.is_metered(cfg):
            continue
        quota = int(cfg.get("monthly_quota_bytes", 0))
        if quota <= 0:
            continue
        used = cycle_used_raw_for(uid, daily_for_kick, now=now)
        if used * DISPLAY_MULTIPLIER >= quota and int(online.get(uid, 0)) > 0:
            to_kick.append(uid)

    if to_kick:
        post("/kick", to_kick)


if __name__ == "__main__":
    main()
