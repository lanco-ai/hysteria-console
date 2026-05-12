#!/usr/bin/env python3
import json
import base64
import hashlib
import hmac
import sys
import urllib.request
from datetime import datetime

import user_compat

USERS_FILE = "/root/hysteria/users.json"
USAGE_FILE = "/root/hysteria/state/usage.json"
USAGE_DAILY_FILE = "/root/hysteria/state/usage_daily.json"
ONLINE_SNAPSHOT_FILE = "/root/hysteria/state/online.json"
META_FILE = "/root/hysteria/subscription_meta.json"
SETTLEMENT_DAY_DEFAULT = 12
CYCLE_LENGTH_DAYS_DEFAULT = 30
CYCLE_LENGTH_MIN = 1
CYCLE_LENGTH_MAX = 90
API_BASE = "http://127.0.0.1:25413"
API_SECRET_FILE = "/root/hysteria/api_secret"
API_SECRET_PLACEHOLDER = "__HY_API_SECRET__"
API_SECRET_FALLBACK = "__HY_API_SECRET__"


def get_api_secret():
    """Same contract as traffic_limiter.get_api_secret. auth_backend is invoked
    by hysteria-server as an external CLI hook on every connection, so this
    intentionally has no non-stdlib imports."""
    try:
        with open(API_SECRET_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v and v != API_SECRET_PLACEHOLDER:
            return v
    except OSError:
        pass
    return API_SECRET_FALLBACK


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def usage_total(entry):
    if isinstance(entry, dict):
        tx = int(entry.get("tx", 0))
        rx = int(entry.get("rx", 0))
        return int(entry.get("total", tx + rx))
    return int(entry or 0)


def _b64url_decode_nopad(s):
    raw = (s or "").encode("ascii")
    pad = b"=" * ((4 - (len(raw) % 4)) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def verify_password_hash(password, encoded):
    try:
        algo, rounds_s, salt_b64, digest_b64 = str(encoded).split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        rounds = int(rounds_s)
        salt = _b64url_decode_nopad(salt_b64)
        expected = _b64url_decode_nopad(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def get_online_counts():
    req = urllib.request.Request(
        f"{API_BASE}/online",
        headers={"Authorization": get_api_secret()},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return load_json(ONLINE_SNAPSHOT_FILE, {})


def main():
    if len(sys.argv) < 3:
        sys.exit(1)

    auth_payload = sys.argv[2] or ""
    if ":" not in auth_payload:
        sys.exit(1)

    username, password = auth_payload.split(":", 1)
    users = load_json(USERS_FILE, {})
    u = users.get(username)
    if not u:
        sys.exit(1)
    token = str(u.get("sub_token") or "")
    ok = bool(token) and hmac.compare_digest(password, token)
    if not ok and u.get("password_hash"):
        ok = verify_password_hash(password, str(u.get("password_hash") or ""))
    if not ok:
        sys.exit(1)

    if user_compat.is_metered(u):
        now = datetime.now()
        meta = load_json(META_FILE, {}) or {}
        try:
            settle_day = int(meta.get("settlement_day", SETTLEMENT_DAY_DEFAULT))
        except (TypeError, ValueError):
            settle_day = SETTLEMENT_DAY_DEFAULT
        settle_day = max(1, min(28, settle_day))
        try:
            cycle_len = int(meta.get("cycle_length_days", CYCLE_LENGTH_DAYS_DEFAULT))
        except (TypeError, ValueError):
            cycle_len = CYCLE_LENGTH_DAYS_DEFAULT
        cycle_len = max(CYCLE_LENGTH_MIN, min(CYCLE_LENGTH_MAX, cycle_len))
        from datetime import timedelta
        raw_anchor = meta.get("cycle_anchor_date")
        anchor = None
        if raw_anchor:
            try:
                anchor = datetime.strptime(str(raw_anchor), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                anchor = None
        if anchor is None:
            if now.day >= settle_day:
                anchor = now.replace(day=settle_day).date()
            else:
                prev_month_end = now.replace(day=1) - timedelta(days=1)
                anchor = prev_month_end.replace(day=settle_day).date()
        today = now.date()
        if today < anchor:
            cycle_start_date = anchor
        else:
            offset_days = (today - anchor).days
            cycle_start_date = anchor + timedelta(days=(offset_days // cycle_len) * cycle_len)
        daily = load_json(USAGE_DAILY_FILE, {})
        used = 0
        d = cycle_start_date
        cycle_end_date = min(cycle_start_date + timedelta(days=cycle_len - 1), today)
        while d <= cycle_end_date:
            entry = (daily.get(d.strftime("%Y-%m-%d")) or {}).get(username)
            used += usage_total(entry)
            d += timedelta(days=1)
        quota = int(u.get("monthly_quota_bytes", 0))
        if quota > 0 and used >= quota:
            sys.exit(1)

        max_devices = int(u.get("max_devices", 0))
        if max_devices > 0:
            online = get_online_counts()
            if int(online.get(username, 0)) >= max_devices:
                sys.exit(1)

    sys.stdout.write(username)
    sys.exit(0)


if __name__ == "__main__":
    main()
