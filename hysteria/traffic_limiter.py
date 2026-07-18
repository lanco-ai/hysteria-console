#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid
import cost_calibrator
import display as display_config
import online_snapshot
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
import cycle as cycle_util
import state_store
from display import DISPLAY_MULTIPLIER
from timeutil import billing_cycle_key, local_now

import alerts as _alerts
import anomaly as _anomaly
import static_access
import tuic_meter
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
PROTOCOL_USAGE_HOURLY_FILE = "/root/hysteria/state/protocol_usage_hourly.json"
COST_CALIBRATION_FILE = "/root/hysteria/state/cost_calibration.json"
DISPLAY_MULTIPLIER_STATE_FILE = "/root/hysteria/state/display_multiplier.json"
MULTIPLIER_AUTO_POLICY_FILE = "/root/hysteria/state/display_multiplier_auto.json"
ONLINE_SNAPSHOT_FILE = "/root/hysteria/state/online.json"
RESET_STATE_FILE = "/root/hysteria/state/auto_reset_state.json"
RESET_LOG_FILE = "/root/hysteria/state/usage_reset.log"
USAGE_LOCK_FILE = "/root/hysteria/state/usage.lock"
META_FILE = "/root/hysteria/subscription_meta.json"
SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX
# Quota enforcement may span up to CYCLE_LENGTH_MAX days. Keep a small buffer
# so delayed ticks around a boundary cannot prune bytes still in-cycle.
DAILY_RETENTION_DAYS = CYCLE_LENGTH_MAX + 2
HOURLY_RETENTION_HOURS = 168
API_BASE = "http://127.0.0.1:25413"
API_SECRET_FILE = "/root/hysteria/api_secret"
API_SECRET_PLACEHOLDER = "__HY_API_SECRET__"
API_SECRET_FALLBACK = "__HY_API_SECRET__"

_LIVE_CORE_STATE_PATHS = (
    "/root/hysteria/users.json",
    "/root/hysteria/subscription_meta.json",
    "/root/hysteria/state/usage.json",
    "/root/hysteria/state/usage_daily.json",
)
_INTEGER_TEXT = re.compile(r"^[0-9]+$")
_CYCLE_KEY = re.compile(r"^[0-9]{4}-[0-9]{2}$")
_DAY_KEY = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")


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


def load_json(path, default, *, required=False):
    return state_store.load_json_strict(path, default, required=required)


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
    Strict readers then distinguish missing initialization state from corrupt
    live state instead of replacing a truncated file with an empty default."""
    state_store.save_json(path, data)


@contextmanager
def usage_lock():
    with state_store.file_lock(USAGE_LOCK_FILE, timeout=30.0):
        yield


def _invalid_state(path, detail):
    raise state_store.InvalidJsonState(
        f"invalid JSON state schema: {path}: {detail}"
    )


def _non_negative_integer(value, *, path, field):
    """Validate a persisted byte/count value without breaking legacy strings."""
    if isinstance(value, bool):
        _invalid_state(path, f"{field} must be a non-negative integer")
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and _INTEGER_TEXT.fullmatch(value.strip()):
        parsed = int(value.strip())
    else:
        _invalid_state(path, f"{field} must be a non-negative integer")
    if parsed < 0:
        _invalid_state(path, f"{field} must be a non-negative integer")
    return parsed


def _validate_usage_entry(entry, *, path, field):
    if isinstance(entry, dict):
        unknown = set(entry) - {"tx", "rx", "total"}
        if unknown:
            _invalid_state(
                path,
                f"{field} has unsupported fields: {', '.join(sorted(map(str, unknown)))}",
            )
        values = {
            direction: _non_negative_integer(
                entry.get(direction, 0),
                path=path,
                field=f"{field}.{direction}",
            )
            for direction in ("tx", "rx", "total")
        }
        if values["total"] != values["tx"] + values["rx"]:
            _invalid_state(path, f"{field}.total must equal tx + rx")
        return
    _non_negative_integer(entry, path=path, field=field)


def _validate_usage_ledger(data, *, path, daily=False):
    if not isinstance(data, dict):
        _invalid_state(path, "top-level value must be an object")
    key_pattern = _DAY_KEY if daily else _CYCLE_KEY
    key_format = "%Y-%m-%d" if daily else "%Y-%m"
    for bucket, rows in data.items():
        if not isinstance(bucket, str) or not key_pattern.fullmatch(bucket):
            _invalid_state(path, f"invalid bucket key {bucket!r}")
        try:
            datetime.strptime(bucket, key_format)
        except ValueError:
            _invalid_state(path, f"invalid bucket key {bucket!r}")
        if not isinstance(rows, dict):
            _invalid_state(path, f"bucket {bucket!r} must be an object")
        for username, entry in rows.items():
            if not isinstance(username, str) or not username:
                _invalid_state(
                    path, f"bucket {bucket!r} has an invalid user key"
                )
            _validate_usage_entry(
                entry,
                path=path,
                field=f"{bucket}.{username}",
            )
    return data


def _validate_users(data, *, path=USERS_FILE):
    if not isinstance(data, dict):
        _invalid_state(path, "top-level value must be an object")
    integer_fields = (
        "monthly_quota_bytes",
        "quota_extra_bytes",
    )
    claimed_vless_uuids = {}
    for username, cfg in data.items():
        if not user_compat.is_valid_username(username):
            _invalid_state(path, f"invalid user key {username!r}")
        if not isinstance(cfg, dict):
            _invalid_state(path, f"user {username!r} must be an object")
        config_error = user_compat.authorization_config_error(cfg)
        if config_error:
            _invalid_state(path, f"user {username!r}: {config_error}")
        raw_vless_uuid = str(cfg.get("vless_uuid") or "").strip()
        try:
            vless_uuid = (
                uuid.UUID(raw_vless_uuid).hex if raw_vless_uuid else ""
            )
        except (ValueError, AttributeError, TypeError):
            _invalid_state(
                path, f"user {username!r}: vless_uuid is invalid"
            )
        if vless_uuid:
            previous = claimed_vless_uuids.get(vless_uuid)
            if previous is not None:
                _invalid_state(
                    path,
                    f"users {previous!r} and {username!r} share vless_uuid",
                )
            claimed_vless_uuids[vless_uuid] = username
        for field in integer_fields:
            if field in cfg:
                _non_negative_integer(
                    cfg[field], path=path, field=f"{username}.{field}"
                )
    return data


def _validate_meta(data, *, path=META_FILE):
    if not isinstance(data, dict):
        _invalid_state(path, "top-level value must be an object")
    if "settlement_day" in data:
        value = _non_negative_integer(
            data["settlement_day"], path=path, field="settlement_day"
        )
        if not 1 <= value <= 28:
            _invalid_state(path, "settlement_day must be between 1 and 28")
    if "cycle_length_days" in data:
        value = _non_negative_integer(
            data["cycle_length_days"], path=path, field="cycle_length_days"
        )
        if not CYCLE_LENGTH_MIN <= value <= CYCLE_LENGTH_MAX:
            _invalid_state(
                path,
                f"cycle_length_days must be between "
                f"{CYCLE_LENGTH_MIN} and {CYCLE_LENGTH_MAX}",
            )
    anchor = data.get("cycle_anchor_date")
    if anchor not in (None, ""):
        if not isinstance(anchor, str):
            _invalid_state(path, "cycle_anchor_date must be an ISO date")
        try:
            datetime.strptime(anchor, "%Y-%m-%d")
        except ValueError:
            _invalid_state(path, "cycle_anchor_date must be an ISO date")
    return data


def _validate_reset_state(data, *, path=RESET_STATE_FILE):
    if not isinstance(data, dict):
        _invalid_state(path, "top-level value must be an object")
    for field in ("last_reset_month", "last_reset_time"):
        if field in data and data[field] is not None and not isinstance(
            data[field], str
        ):
            _invalid_state(path, f"{field} must be a string")
    return data


def _using_live_core_state():
    paths = (USERS_FILE, META_FILE, USAGE_FILE, USAGE_DAILY_FILE)
    return tuple(map(str, paths)) == _LIVE_CORE_STATE_PATHS


def _stop_static_service(service, *, reason):
    """Stop a static-auth proxy if its canonical authorization plan is unsafe."""
    return static_access.stop_fail_closed(
        service,
        reason=reason,
        live=_using_live_core_state(),
    )


def _fail_closed_static_access(reason):
    """Revoke static credentials when canonical core state cannot be trusted.

    Hysteria's auth backend denies requests against bad state, but Xray and
    TUIC authorize from generated static files. Stop them first, then reconcile
    to empty/locked-only configs. A successful reload may safely bring them
    back with no canonical users; a failed rewrite leaves the service stopped.
    """
    import sys
    if not _using_live_core_state():
        return

    _stop_static_service(xray_config.RELOAD_SERVICE, reason=reason)
    _stop_static_service(tuic_config.RELOAD_SERVICE, reason=reason)

    try:
        changed = xray_config.apply_user_plan({}, prune_unknown=True)
        if changed:
            xray_config.reload_async()
    except Exception as exc:
        print(f"xray fail-closed reconciliation failed: {exc}", file=sys.stderr)

    try:
        changed = tuic_config.sync_all(users={})
        if changed:
            tuic_config.reload_async()
    except Exception as exc:
        print(f"tuic fail-closed reconciliation failed: {exc}", file=sys.stderr)


def preflight_persistent_state(now=None):
    """Validate state before any source counter is destructively cleared.

    Hysteria and Xray return-and-clear their counters. Core accounting state
    therefore fails closed. Auxiliary snapshots are reported to the caller so
    only their corresponding feature is skipped; a broken chart cache must not
    freeze quota accounting for every user.
    """
    now = now or local_now()
    users = load_json(USERS_FILE, {}, required=True)
    _validate_users(users, path=USERS_FILE)
    meta = load_json(META_FILE, {}, required=True)
    _validate_meta(meta, path=META_FILE)
    usage = load_json(USAGE_FILE, {}, required=True)
    _validate_usage_ledger(usage, path=USAGE_FILE)
    daily = load_json(USAGE_DAILY_FILE, {}, required=True)
    _validate_usage_ledger(daily, path=USAGE_DAILY_FILE, daily=True)
    try:
        display_config.effective_display_multiplier_strict(
            path=DISPLAY_MULTIPLIER_STATE_FILE,
        )
    except ValueError as exc:
        _invalid_state(DISPLAY_MULTIPLIER_STATE_FILE, str(exc))

    settlement_day = cycle_util.settlement_day_from_meta(meta)
    reset = load_json(
        RESET_STATE_FILE, {}, required=now.day == settlement_day
    )
    _validate_reset_state(reset, path=RESET_STATE_FILE)

    unavailable = set()
    for path in (
        USAGE_HOURLY_FILE,
        PROTOCOL_USAGE_HOURLY_FILE,
        ONLINE_SNAPSHOT_FILE,
        COST_CALIBRATION_FILE,
        MULTIPLIER_AUTO_POLICY_FILE,
        tuic_meter.STATE_FILE,
        _alerts.CONFIG_FILE,
        _alerts.STATE_FILE,
    ):
        try:
            load_json(path, {})
        except state_store.StateStoreError:
            unavailable.add(str(path))
    return unavailable


def _optional_unavailable(unavailable, path):
    return str(path) in unavailable


def _warn_optional_state(feature, exc=None):
    import sys
    suffix = f": {exc}" if exc else ""
    print(f"{feature}: skipped because auxiliary state is unavailable{suffix}",
          file=sys.stderr)


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


def restart_subscription_async():
    try:
        subprocess.Popen(
            ['systemd-run', '--no-block', '--on-active=2s',
             '--unit', f'hy2-subscription-restart-auto-{int(datetime.utcnow().timestamp())}',
             'systemctl', 'restart', 'hysteria-subscription.service'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


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


def cycle_days(now, day=None, length=None, anchor=None, *, meta=None):
    """List of YYYY-MM-DD date keys in the current cycle, oldest first.

    Uses fixed-N-day rolling blocks anchored at `anchor` (defaults to the
    persisted `cycle_anchor_date`, or to the most recent settlement_day on/
    before now if not set)."""
    if meta is None:
        meta = load_json(META_FILE, {}, required=True)
        _validate_meta(meta, path=META_FILE)
    return cycle_util.cycle_days(
        now,
        day=day,
        length=length,
        anchor=anchor,
        meta=meta or {},
    )


def cycle_used_raw_for(uid, daily, *, now, day=None, meta=None):
    """Per-user raw cycle bytes derived from usage_daily.json. Single source
    of truth for both display and quota enforcement, so the kick check and the
    /admin UI agree."""
    total = 0
    for dk in cycle_days(now, day=day, meta=meta):
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
    state = load_json(RESET_STATE_FILE, {}, required=True)
    _validate_reset_state(state, path=RESET_STATE_FILE)
    if state.get("last_reset_month") == month:
        return

    usage.setdefault(month, {})
    before_all = {}
    for uid in users.keys():
        before_all[uid] = normalize_usage_entry(usage[month].get(uid, 0))
        usage[month][uid] = {"tx": 0, "rx": 0, "total": 0}

    save_json(USAGE_FILE, usage)
    save_json(
        RESET_STATE_FILE,
        {
            "last_reset_month": month,
            "last_reset_time": now.isoformat(timespec="seconds"),
        },
    )
    try:
        append_reset_log(
            actor="system",
            action="reset_usage_all_auto_day21",
            target="all_users",
            before=before_all,
            after={
                u: {"tx": 0, "rx": 0, "total": 0}
                for u in users.keys()
            },
            mk=month,
        )
    except Exception as exc:
        _warn_optional_state("usage reset audit log", exc)


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


def get_tuic_traffic():
    return tuic_meter.get_tuic_traffic()


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


def traffic_totals(traffic):
    total = {"tx": 0, "rx": 0, "total": 0}
    for stat in (traffic or {}).values():
        entry = normalize_usage_entry(stat)
        total["tx"] += entry["tx"]
        total["rx"] += entry["rx"]
        total["total"] += entry["total"]
    return total


def accumulate_daily(traffic, now, *, daily=None):
    """Returns the post-write daily dict so the caller can reuse it for
    cycle-quota math without re-reading the file we just persisted."""
    day_key = now.strftime("%Y-%m-%d")
    if daily is None:
        daily = load_json(USAGE_DAILY_FILE, {}, required=True)
        _validate_usage_ledger(daily, path=USAGE_DAILY_FILE, daily=True)
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


def accumulate_protocol_hourly(protocol_traffic, now):
    """Track raw traffic by source protocol for the line-quality radar.

    The main usage files stay user-centric. This sidecar keeps only aggregate
    hourly buckets like {"2026-06-03T15": {"hysteria": {...}, "xray": {...}}}
    so it is compact and independent of quota accounting.
    """
    hour_key = now.strftime("%Y-%m-%dT%H")
    hourly = load_json(PROTOCOL_USAGE_HOURLY_FILE, {})
    hourly.setdefault(hour_key, {})
    for proto, traffic in (protocol_traffic or {}).items():
        if not traffic:
            continue
        cur = normalize_usage_entry(hourly[hour_key].get(proto, 0))
        delta = traffic_totals(traffic)
        cur["tx"] += delta["tx"]
        cur["rx"] += delta["rx"]
        cur["total"] += delta["total"]
        hourly[hour_key][proto] = cur
    prune_hourly(hourly, now)
    save_json(PROTOCOL_USAGE_HOURLY_FILE, hourly)
    return hourly


def resume_expired_temporary_disables(users, now):
    changed = False
    for cfg in (users or {}).values():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("disabled") and user_compat.temporary_disable_expired(cfg, now=now):
            cfg["disabled"] = False
            cfg.pop("disabled_until", None)
            changed = True
    if changed:
        save_json(USERS_FILE, users)
    return changed


def _fmt_bytes(n):
    n = float(max(0, int(n)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{n:.2f} {units[i]}"


def check_alerts(users, now, month_key, *, daily=None, _opener=None):
    """Detect quota crossings and daily anomalies, dispatch claimed alerts.

    `daily` may be passed in to reuse the in-memory dict from accumulate_daily
    and skip a redundant disk read; if None the file is loaded.

    Each event is claimed under alert_state.json.lock, dispatched without that
    lock, then CAS-completed. Concurrent timer invocations therefore cannot
    both deliver the same event, while transport failures release the claim for
    retry. Wrapped in a top-level auxiliary error boundary by the caller.
    """
    cfg = _alerts.load_config()
    if not cfg:
        return  # nothing configured → nothing to send

    if daily is None:
        daily = load_json(USAGE_DAILY_FILE, {})
    today_date = now.date()

    today_key = today_date.strftime('%Y-%m-%d')
    z_threshold = float(cfg.get('anomaly_z_threshold', _alerts.DEFAULT_Z_THRESHOLD))
    min_bytes = int(cfg.get('anomaly_min_bytes', _alerts.DEFAULT_MIN_BYTES))

    for uid, user_cfg in (users or {}).items():
        # ---- quota crossings ----
        quota = user_compat.total_quota_bytes(user_cfg)
        if user_compat.is_metered(user_cfg) and quota > 0:
            raw_total = cycle_used_raw_for(uid, daily, now=now)
            scaled = int(raw_total * _DM)
            pct = scaled * 100.0 / quota
            if pct >= 100:
                _alerts.dispatch_once({
                    'kind': 'quota_100', 'user': uid,
                    'details': {'used_human': _fmt_bytes(scaled),
                                'total_human': _fmt_bytes(quota),
                                'cycle': month_key},
                }, month_key, config=cfg, opener=_opener)
            elif pct >= 80:
                _alerts.dispatch_once({
                    'kind': 'quota_80', 'user': uid,
                    'details': {'used_human': _fmt_bytes(scaled),
                                'total_human': _fmt_bytes(quota),
                                'cycle': month_key},
                }, month_key, config=cfg, opener=_opener)

        # ---- anomaly ----
        hit = _anomaly.detect(uid, daily, today_date,
                              z_threshold=z_threshold, min_bytes=min_bytes)
        if hit is not None:
            _alerts.dispatch_once({
                'kind': 'anomaly', 'user': uid,
                'details': {'today_human': _fmt_bytes(int(hit['today'] * _DM)),
                            'mean_human': _fmt_bytes(int(hit['mean'] * _DM)),
                            'z': hit['z']},
            }, today_key, config=cfg, opener=_opener)

        # ---- expiry reminders ----
        expiry = user_compat.expiry_date(user_cfg)
        if expiry is not None:
            days_left = (expiry - today_date).days
            try:
                warn_days = int(cfg.get('expiry_warn_days', 3) or 3)
            except (TypeError, ValueError):
                warn_days = 3
            warn_days = max(0, min(30, warn_days))
            key = expiry.isoformat()
            if days_left < 0:
                _alerts.dispatch_once({
                    'kind': 'expiry_expired', 'user': uid,
                    'details': {'expires_at': key},
                }, key, config=cfg, opener=_opener)
            elif 0 <= days_left <= warn_days:
                _alerts.dispatch_once({
                    'kind': 'expiry_soon', 'user': uid,
                    'details': {'expires_at': key, 'days_left': days_left},
                }, key, config=cfg, opener=_opener)


def build_static_access_plan(users, daily, *, now, meta):
    """Build the exact proxy credential plan from committed canonical state."""
    plan = {}
    to_kick = []
    for uid, cfg in users.items():
        if user_compat.is_inactive(cfg, today=now.date()):
            plan[uid] = None
            to_kick.append(uid)
            continue
        vless_uuid = str(cfg.get("vless_uuid") or "").strip()
        metered = user_compat.is_metered(cfg)
        quota = user_compat.total_quota_bytes(cfg)
        if metered and quota > 0:
            used = cycle_used_raw_for(
                uid, daily, now=now, meta=meta
            )
            if used * _DM >= quota:
                plan[uid] = None
                to_kick.append(uid)
                continue
        if vless_uuid:
            plan[uid] = vless_uuid
    return plan, to_kick


def _apply_static_access_plan(users, plan):
    """Apply Xray and TUIC independently; stop a proxy whose plan is unsafe."""
    import sys
    xray_changed = False
    tuic_changed = False
    live_state = _using_live_core_state()
    # Alternate state roots are used by validation tools and tests. Keep their
    # generated configs beside that state instead of ever touching live proxy
    # credentials merely because this module was imported on a server.
    xray_path = None if live_state else Path(USERS_FILE).parent / "xray.json"
    tuic_path = None if live_state else Path(USERS_FILE).parent / "tuic.json"
    try:
        xray_kwargs = {"prune_unknown": True}
        if xray_path is not None:
            xray_kwargs["path"] = xray_path
        xray_changed = xray_config.apply_user_plan(plan, **xray_kwargs)
        static_access.recover_if_pending(
            xray_config.RELOAD_SERVICE,
            live=live_state,
        )
    except Exception as exc:
        print(f"xray plan reconciliation failed: {exc}", file=sys.stderr)
        _stop_static_service(xray_config.RELOAD_SERVICE, reason=exc)
    try:
        tuic_kwargs = {}
        if tuic_path is not None:
            tuic_kwargs["path"] = tuic_path
        tuic_changed = tuic_config.sync_user_plan(
            users, plan, **tuic_kwargs
        )
        static_access.recover_if_pending(
            tuic_config.RELOAD_SERVICE,
            live=live_state,
        )
    except Exception as exc:
        print(f"tuic plan reconciliation failed: {exc}", file=sys.stderr)
        _stop_static_service(tuic_config.RELOAD_SERVICE, reason=exc)
    return xray_changed, tuic_changed


def _run_auxiliary(feature, func, *, unavailable=False, default=None):
    """Run one non-accounting feature without coupling it to other features."""
    if unavailable:
        _warn_optional_state(feature)
        return default
    try:
        return func()
    except Exception as exc:
        _warn_optional_state(feature, exc)
        return default


def _validated_source_traffic(value, *, source):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{source} traffic response must be an object")
    for username, entry in value.items():
        if not isinstance(username, str) or not username:
            raise ValueError(f"{source} traffic has an invalid user key")
        if not isinstance(entry, dict):
            raise ValueError(
                f"{source} traffic entry for {username!r} must be an object"
            )
        for direction in ("tx", "rx"):
            _non_negative_integer(
                entry.get(direction, 0),
                path=source,
                field=f"{username}.{direction}",
            )
    return value


def _commit_core_and_enforce_locked(traffic, *, now):
    """Commit core ledgers and authorization while the usage lock is held."""
    users = load_json(USERS_FILE, {}, required=True)
    _validate_users(users, path=USERS_FILE)
    meta = load_json(META_FILE, {}, required=True)
    _validate_meta(meta, path=META_FILE)
    settle_day = cycle_util.settlement_day_from_meta(meta)
    month_key = billing_month_key(now, day=settle_day)
    usage = load_json(USAGE_FILE, {}, required=True)
    _validate_usage_ledger(usage, path=USAGE_FILE)
    daily = load_json(USAGE_DAILY_FILE, {}, required=True)
    _validate_usage_ledger(daily, path=USAGE_DAILY_FILE, daily=True)
    usage.setdefault(month_key, {})
    maybe_reset_all_usage_on_day_21(
        now, users, usage, month_key, day=settle_day
    )
    usage.setdefault(month_key, {})

    for uid, stat in traffic.items():
        cur = normalize_usage_entry(usage[month_key].get(uid, 0))
        tx = int(stat.get("tx", 0))
        rx = int(stat.get("rx", 0))
        cur["tx"] += tx
        cur["rx"] += rx
        cur["total"] += tx + rx
        usage[month_key][uid] = cur

    # Daily is the authoritative quota ledger. Commit it first so a
    # subsequent failure while refreshing the legacy cycle summary cannot
    # permanently omit already-cleared source bytes from enforcement.
    daily = accumulate_daily(traffic, now, daily=daily)
    save_json(USAGE_FILE, usage)

    # Static authorization is enforcement, not reporting. Apply it
    # immediately after both core ledgers commit, before auxiliary work.
    xray_plan, to_kick = build_static_access_plan(
        users, daily, now=now, meta=meta
    )
    xray_changed, tuic_changed = _apply_static_access_plan(
        users, xray_plan
    )
    return (
        users,
        daily,
        month_key,
        to_kick,
        xray_changed,
        tuic_changed,
    )


def _commit_core_and_enforce(traffic, *, now):
    """Standalone entry point for callers that do not already own the lock."""
    with usage_lock():
        return _commit_core_and_enforce_locked(traffic, now=now)


def main():
    now = local_now()
    # Hold one canonical lock from preflight through both destructive source
    # reads, core fsyncs, and static authorization. Releasing it after
    # preflight and reacquiring it after return-and-clear could lose the
    # collected bytes if the second acquisition failed. The external calls
    # here all have bounded timeouts.
    try:
        with usage_lock():
            unavailable_optional = preflight_persistent_state(now)
            users = load_json(USERS_FILE, {}, required=True)
            _validate_users(users, path=USERS_FILE)
            resume_expired_temporary_disables(users, now)

            # Either source returning None / {} is fine — accumulate whatever
            # delta was available and try the other source again next tick.
            hysteria_delta = _run_auxiliary(
                "hysteria traffic collection",
                lambda: _validated_source_traffic(
                    get("/traffic?clear=1"), source="hysteria"
                ),
                default={},
            )
            # Copy nested counters as well as the mapping: merge_traffic
            # mutates its destination, while protocol reporting must retain
            # the Hysteria-only snapshot.
            traffic = {
                uid: {
                    "tx": int(stat.get("tx", 0)),
                    "rx": int(stat.get("rx", 0)),
                }
                for uid, stat in hysteria_delta.items()
            }
            xray_delta = _run_auxiliary(
                "xray traffic collection",
                lambda: _validated_source_traffic(
                    get_xray_traffic(), source="xray"
                ),
                default={},
            )
            if xray_delta:
                merge_traffic(traffic, xray_delta)

            (
                users,
                daily,
                month_key,
                to_kick,
                xray_changed,
                tuic_changed,
            ) = _commit_core_and_enforce_locked(traffic, now=now)
    except state_store.LockTimeout:
        # A concurrent, bounded mutation still owns the canonical snapshot.
        # Skip this tick and let the timer retry; contention is not evidence
        # that existing static credentials are unsafe.
        raise
    except (state_store.StateStoreError, OSError) as exc:
        _fail_closed_static_access(exc)
        raise

    # TUIC counters are non-destructive and are not part of per-user quota
    # accounting, so their auxiliary baseline update stays outside the
    # canonical lock.
    if _optional_unavailable(unavailable_optional, tuic_meter.STATE_FILE):
        _warn_optional_state("tuic metering")
        tuic_delta = {}
    else:
        tuic_delta = _run_auxiliary(
            "tuic metering", get_tuic_traffic, default={}
        )

    if to_kick:
        post("/kick", to_kick)
    if xray_changed and _using_live_core_state():
        xray_config.reload_async()
    if tuic_changed and _using_live_core_state():
        tuic_config.reload_async()

    # Everything below is auxiliary. Each feature gets its own error boundary
    # and writes atomically, so a broken cache remains available for operator
    # repair and cannot block quota/expiry enforcement in another protocol.
    _run_auxiliary(
        "hourly usage",
        lambda: accumulate_hourly(traffic, now),
        unavailable=_optional_unavailable(
            unavailable_optional, USAGE_HOURLY_FILE
        ),
    )

    protocol_traffic = {
        "hysteria": hysteria_delta,
        "xray": xray_delta,
    }
    tuic_entry = normalize_usage_entry(tuic_delta)
    if tuic_entry["total"] > 0:
        protocol_traffic["tuic"] = {"_tuic": tuic_entry}
    _run_auxiliary(
        "protocol usage",
        lambda: accumulate_protocol_hourly(protocol_traffic, now),
        unavailable=_optional_unavailable(
            unavailable_optional, PROTOCOL_USAGE_HOURLY_FILE
        ),
    )

    app_raw_bytes = traffic_totals(traffic)["total"] + tuic_entry["total"]
    calibration_paths = (
        COST_CALIBRATION_FILE,
        DISPLAY_MULTIPLIER_STATE_FILE,
        MULTIPLIER_AUTO_POLICY_FILE,
    )

    def update_calibration():
        cost_calibrator.update_sample(
            COST_CALIBRATION_FILE,
            app_raw_bytes=app_raw_bytes,
            now=now,
        )
        auto_result = cost_calibrator.maybe_auto_adjust(
            COST_CALIBRATION_FILE,
            current_multiplier=DISPLAY_MULTIPLIER,
            policy_path=MULTIPLIER_AUTO_POLICY_FILE,
            runtime_state_path=DISPLAY_MULTIPLIER_STATE_FILE,
            now=now,
        )
        if auto_result.get("applied"):
            restart_subscription_async()
        return auto_result

    if app_raw_bytes > 0:
        _run_auxiliary(
            "cost calibration",
            update_calibration,
            unavailable=any(
                _optional_unavailable(unavailable_optional, path)
                for path in calibration_paths
            ),
        )

    def refresh_online_snapshot():
        online_resp = get("/online")
        if not isinstance(online_resp, dict):
            # Preserve both the last good snapshot and its capture metadata on
            # transient API failures. Authorization applies its own short TTL.
            return load_json(ONLINE_SNAPSHOT_FILE, {})
        for username, count in online_resp.items():
            if not isinstance(username, str) or not username:
                raise ValueError("online snapshot has an invalid user key")
            _non_negative_integer(
                count,
                path=ONLINE_SNAPSHOT_FILE,
                field=username,
            )
        metadata = online_snapshot.build_metadata(
            online_resp, captured_at=time.time()
        )
        save_json(ONLINE_SNAPSHOT_FILE, online_resp)
        save_json(
            online_snapshot.metadata_path(ONLINE_SNAPSHOT_FILE),
            metadata,
        )
        return online_resp

    if _optional_unavailable(unavailable_optional, ONLINE_SNAPSHOT_FILE):
        _warn_optional_state("online snapshot")
        # The API read is non-destructive and remains useful as a liveness
        # probe, but never overwrite an operator-repairable bad snapshot.
        _run_auxiliary(
            "online API", lambda: get("/online"), default={}
        )
    else:
        _run_auxiliary(
            "online snapshot",
            refresh_online_snapshot,
            default={},
        )
    _run_auxiliary(
        "alerts",
        lambda: check_alerts(users, now, month_key, daily=daily),
        unavailable=any(
            _optional_unavailable(unavailable_optional, path)
            for path in (_alerts.CONFIG_FILE, _alerts.STATE_FILE)
        ),
    )


if __name__ == "__main__":
    main()
