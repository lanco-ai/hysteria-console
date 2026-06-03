#!/usr/bin/env python3
import json
import os
import subprocess
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta
import cycle as cycle_util
import state_store
from display import DISPLAY_MULTIPLIER
from timeutil import billing_cycle_key, local_now

import alerts as _alerts
import anomaly as _anomaly
import tuic_config
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
SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX
DAILY_RETENTION_DAYS = 30
HOURLY_RETENTION_HOURS = 168
API_BASE = "http://127.0.0.1:25413"
API_SECRET_FILE = "/root/hysteria/api_secret"
API_SECRET_PLACEHOLDER = "__HY_API_SECRET__"
API_SECRET_FALLBACK = "__HY_API_SECRET__"


def get_api_secret():
    """Read the hysteria API auth secret from a file (single-line, mode 600).
    Falls back to the module-level placeholder for legacy installs that still
    rely on deploy.sh's sed substitution. The file-based path means `git pull`
    of just the .py sources can't accidentally overwrite a deployed secret
    with the literal placeholder string (which is what triggered HTTP 401
    crashes in the cron tick — see PR #11)."""
    try:
        with open(API_SECRET_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v and v != API_SECRET_PLACEHOLDER:
            return v
    except OSError:
        pass
    return API_SECRET_FALLBACK


def load_json(path, default):
    return state_store.load_json(path, default)


def get_settlement_day():
    """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
    return cycle_util.settlement_day_from_meta(load_json(META_FILE, {}) or {})


def get_cycle_length_days():
    """Length of one billing cycle, in days. Mirrors subscription_service.get_cycle_length_days."""
    return cycle_util.cycle_length_from_meta(load_json(META_FILE, {}) or {})


def _settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now."""
    return cycle_util.settlement_anchor_date(now, settlement_day)


def get_cycle_anchor_date(now):
    """Persisted anchor date (the cycle calendar's origin). Falls back to the
    most recent settlement_day on/before now if not yet stored."""
    return cycle_util.cycle_anchor_date(now, load_json(META_FILE, {}) or {})


def save_json(path, data):
    """Atomic write: serialize to a sibling temp file, fsync, then rename.
    Prevents truncated state files — load_json silently returns `{}` on parse
    errors, which has caused cycle/reset state to be lost across the boundary
    of an interrupted oneshot tick."""
    state_store.save_json(path, data)


@contextmanager
def usage_lock():
    with state_store.file_lock(USAGE_LOCK_FILE):
        yield


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
    """GET a hysteria API endpoint. Returns parsed JSON, or None on any failure
    (timeout, refused, parse error, etc.). The previous behavior of letting the
    exception propagate would crash the whole oneshot tick on any transient API
    hiccup, freezing usage stats until hysteria recovered AND someone noticed
    that systemd had been silently re-running a failing service."""
    try:
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            headers={"Authorization": get_api_secret()},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        import sys
        print(f"hysteria GET {path} failed: {e}", file=sys.stderr)
        return None


def post(path, obj):
    """POST to a hysteria API endpoint. Returns True on success, False on any
    failure. Same rationale as `get()`."""
    try:
        body = json.dumps(obj).encode("utf-8")
        req = urllib.request.Request(
            f"{API_BASE}{path}",
            data=body,
            headers={"Authorization": get_api_secret(), "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3):
            return True
    except Exception as e:
        import sys
        print(f"hysteria POST {path} failed: {e}", file=sys.stderr)
        return False


def billing_month_key(now, day=None):
    """Thin wrapper around timeutil.billing_cycle_key — shared with
    subscription_service.month_key so the two can never drift apart."""
    d = int(day if day is not None else get_settlement_day())
    return billing_cycle_key(now, d)


def cycle_days(now, day=None, length=None, anchor=None):
    """List of YYYY-MM-DD date keys in the current cycle, oldest first.

    Uses fixed-N-day rolling blocks anchored at `anchor` (defaults to the
    persisted `cycle_anchor_date`, or to the most recent settlement_day on/
    before now if not set)."""
    return cycle_util.cycle_days(
        now,
        day=day,
        length=length,
        anchor=anchor,
        meta=load_json(META_FILE, {}) or {},
    )


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
    """Returns the post-write daily dict so the caller can reuse it for
    cycle-quota math without re-reading the file we just persisted."""
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
    return daily


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


def check_alerts(users, now, month_key, *, daily=None, _opener=None):
    """Detect quota crossings and daily anomalies, dispatch alerts, persist dedup state.

    `daily` may be passed in to reuse the in-memory dict from accumulate_daily
    and skip a redundant disk read; if None the file is loaded.

    Wrapped in a top-level try/except by the caller; this function may itself
    raise on filesystem errors but those should never break the kick path.
    """
    cfg = _alerts.load_config()
    if not cfg:
        return  # nothing configured → nothing to send

    state = _alerts.load_state()
    if daily is None:
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
    # Either source returning None / {} is fine — we accumulate whatever
    # delta we got and try the other source again next tick. The previous
    # implementation let exceptions propagate, which silently froze stats
    # for the entire window of any hysteria/xray hiccup.
    traffic = get("/traffic?clear=1") or {}
    xray_delta = get_xray_traffic()
    if xray_delta:
        merge_traffic(traffic, xray_delta)
    with usage_lock():
        usage = load_json(USAGE_FILE, {})
        usage.setdefault(month_key, {})
        # maybe_reset writes through save_json then we continue mutating the
        # same in-memory dict — no need to reload from disk.
        maybe_reset_all_usage_on_day_21(now, users, usage, month_key, day=settle_day)
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
        daily = accumulate_daily(traffic, now)
        accumulate_hourly(traffic, now)

    online_resp = get("/online")
    if online_resp is None:
        # Transient API failure: keep the previous snapshot rather than wiping
        # it to {}. Overwriting on every hiccup made every UI surface (admin
        # row, user panel card, health card, /admin/usage.json poll) flash
        # "0 online" until the next successful tick — the "online device
        # display is often incorrect" symptom operators were seeing.
        online = load_json(ONLINE_SNAPSHOT_FILE, {})
    else:
        online = online_resp
        save_json(ONLINE_SNAPSHOT_FILE, online)

    try:
        check_alerts(users, now, month_key, daily=daily)
    except Exception as e:
        import sys
        print(f"alerts: skipped due to error: {e}", file=sys.stderr)

    # Build one plan covering every user, then apply it with a single
    # read+write of xray/config.json. The previous loop called sync_user /
    # remove_user once per user, each of which re-parsed the full config —
    # O(N) full-file reads every 5 s.
    to_kick = []
    xray_plan = {}
    for uid, cfg in users.items():
        if (cfg or {}).get("disabled"):
            # Durable suspend: every tick force-removes the user from xray and
            # re-kicks any live hysteria session, so suspension survives across
            # cron ticks (hy_kick from the admin handler is one-shot/best-effort).
            xray_plan[uid] = None  # ensure removed from both inbounds
            if int(online.get(uid, 0) or 0) > 0:
                to_kick.append(uid)
            continue
        vless_uuid = str((cfg or {}).get("vless_uuid") or "").strip()
        metered = user_compat.is_metered(cfg)
        quota = int((cfg or {}).get("monthly_quota_bytes", 0))
        if not metered or quota <= 0:
            if vless_uuid:
                xray_plan[uid] = vless_uuid
            continue
        used = cycle_used_raw_for(uid, daily, now=now)
        if used * _DM >= quota:
            if int(online.get(uid, 0)) > 0:
                to_kick.append(uid)
            xray_plan[uid] = None  # remove from both inbounds
        elif vless_uuid:
            xray_plan[uid] = vless_uuid

    if to_kick:
        post("/kick", to_kick)
    if xray_config.apply_user_plan(xray_plan):
        xray_config.reload_async()
    if tuic_config.sync_user_plan(users, xray_plan):
        tuic_config.reload_async()


if __name__ == "__main__":
    main()
