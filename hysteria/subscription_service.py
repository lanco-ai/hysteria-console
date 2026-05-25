#!/usr/bin/env python3
import csv
import html
import base64
import hashlib
import hmac
import io
import json
import fcntl
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
import urllib.request

import alerts
import tuic_config
import user_compat
import xray_config
from display import DISPLAY_MULTIPLIER, fmt_bytes
from timeutil import billing_cycle_key, local_now
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

USERS_FILE = Path('/root/hysteria/users.json')
USAGE_FILE = Path('/root/hysteria/state/usage.json')
USAGE_DAILY_FILE = Path('/root/hysteria/state/usage_daily.json')
USAGE_HOURLY_FILE = Path('/root/hysteria/state/usage_hourly.json')
USAGE_PRESERVED_FILE = Path('/root/hysteria/state/usage_preserved.json')
HOURLY_RETENTION_HOURS = 168
ONLINE_FILE = Path('/root/hysteria/state/online.json')
META_FILE = Path('/root/hysteria/subscription_meta.json')
TEMPLATE_FILE = Path('/root/hysteria/template.yaml')
SESSIONS_FILE = Path('/root/hysteria/state/panel_sessions.json')
RESET_LOG_FILE = Path('/root/hysteria/state/usage_reset.log')
USAGE_LOCK_FILE = Path('/root/hysteria/state/usage.lock')
HY_API_BASE = 'http://127.0.0.1:25413'
HY_API_SECRET_FILE = '/root/hysteria/api_secret'
HY_API_SECRET_PLACEHOLDER = '__HY_API_SECRET__'
HY_API_SECRET_FALLBACK = '__HY_API_SECRET__'


def get_hy_api_secret():
    """Read the hysteria API auth secret at runtime from /root/hysteria/api_secret.
    Falls back to the (possibly sed-substituted) module-level constant so existing
    deploys keep working without re-rendering. Reading at request time means
    a deploy that updates only the secret file takes effect immediately, and
    a `git pull` that resets the source file to the literal placeholder no
    longer causes 401s in the cron tick."""
    try:
        with open(HY_API_SECRET_FILE, 'r', encoding='utf-8') as f:
            v = f.read().strip()
        if v and v != HY_API_SECRET_PLACEHOLDER:
            return v
    except OSError:
        pass
    return HY_API_SECRET_FALLBACK


def hy_kick(usernames):
    """Force-disconnect active hysteria sessions for the given usernames."""
    if not usernames:
        return
    try:
        body = json.dumps(list(usernames)).encode('utf-8')
        req = urllib.request.Request(
            f'{HY_API_BASE}/kick',
            data=body,
            headers={'Authorization': get_hy_api_secret(), 'Content-Type': 'application/json'},
            method='POST',
        )
        with urllib.request.urlopen(req, timeout=3):
            return
    except Exception:
        pass
LISTEN = ('127.0.0.1', 8081)
SESSION_TTL = 86400

_STATIC_DIR = Path(__file__).resolve().parent
BASE_CSS_BYTES = (_STATIC_DIR / 'admin.css').read_bytes()
BASE_CSS_ETAG = '"' + hashlib.sha1(BASE_CSS_BYTES).hexdigest()[:16] + '"'
ADMIN_POLL_JS_BYTES = (_STATIC_DIR / 'admin_poll.js').read_bytes()
ADMIN_POLL_JS_ETAG = '"' + hashlib.sha1(ADMIN_POLL_JS_BYTES).hexdigest()[:16] + '"'
USAGE_JS_BYTES = (_STATIC_DIR / 'usage.js').read_bytes()
USAGE_JS_ETAG = '"' + hashlib.sha1(USAGE_JS_BYTES).hexdigest()[:16] + '"'


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    """Atomic write: serialize to a sibling temp file, fsync, then rename. Prevents
    truncated state files (which the readers fall back to `{}` on, silently losing
    the cycle/state tracking)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=True, indent=2) + "\n"
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


@contextmanager
def usage_lock():
    USAGE_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE_LOCK_FILE.open('a+', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


_USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


def is_valid_username(name):
    """A creatable username: 1-64 chars of [A-Za-z0-9_.-], not ending in
    `.json`. The `.json` exclusion avoids route-extraction ambiguity with
    `/panel/<user>.json`; the charset blocks path/HTML-injection sinks."""
    if not name or not _USERNAME_RE.match(name):
        return False
    if name.endswith('.json'):
        return False
    return True


def parse_int_field(raw, default, min_value, max_value):
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(min_value, min(max_value, value))


def sanitize_host(raw_host):
    h = (raw_host or '').strip()
    if not h:
        return '127.0.0.1'
    if ',' in h:
        h = h.split(',', 1)[0].strip()
    if '/' in h or '\\' in h or '@' in h:
        return '127.0.0.1'
    if h.count(':') <= 1 and ':' in h:
        name, port = h.rsplit(':', 1)
        if name and port.isdigit() and 1 <= int(port) <= 65535:
            h = name
    allowed = set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-[]:')
    if any(ch not in allowed for ch in h):
        return '127.0.0.1'
    return h or '127.0.0.1'


def safe_base_url(host, forwarded_proto):
    scheme = (forwarded_proto or 'http').split(',')[0].strip().lower()
    if scheme not in ('http', 'https'):
        scheme = 'http'
    return f'{scheme}://{host}'


def _b64url_nopad(data):
    return base64.urlsafe_b64encode(data).decode('ascii').rstrip('=')


def hash_secret(secret):
    salt = secrets.token_bytes(16)
    rounds = 200000
    digest = hashlib.pbkdf2_hmac('sha256', secret.encode('utf-8'), salt, rounds)
    return f'pbkdf2_sha256${rounds}${_b64url_nopad(salt)}${_b64url_nopad(digest)}'


def migrate_plaintext_passwords():
    users = load_json(USERS_FILE, {})
    changed = False
    for _, cfg in users.items():
        plain = str(cfg.get('password') or '')
        if plain:
            cfg['password_hash'] = hash_secret(plain)
            cfg.pop('password', None)
            changed = True
        if cfg.get('password') is not None:
            cfg.pop('password', None)
            changed = True
    if changed:
        save_json(USERS_FILE, users)


def _write_initial_admin_password(user, password):
    """Persist an auto-generated initial admin password to a root-only file so
    the operator can retrieve it on a fresh deploy, log in, then rotate it via
    /admin/settings. Best-effort: a write failure must not block meta init.
    Path follows META_FILE so tests (which repoint META_FILE) stay isolated."""
    try:
        path = Path(META_FILE).parent / 'admin_initial_password.txt'
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, (
                f"# hy2 auto-generated initial admin password.\n"
                f"# Log in at /admin (user: {user}), rotate it at /admin/settings, then delete this file.\n"
                f"{user}:{password}\n"
            ).encode('utf-8'))
        finally:
            os.close(fd)
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
    except OSError:
        pass


def ensure_meta():
    meta = load_json(META_FILE, {})
    changed = False
    if not meta.get('admin_token'):
        meta['admin_token'] = secrets.token_urlsafe(24)
        changed = True
    if not meta.get('admin_user'):
        meta['admin_user'] = 'admin'
        changed = True
    if not meta.get('admin_pass') and not meta.get('admin_pass_hash'):
        initial = secrets.token_urlsafe(12)
        meta['admin_pass_hash'] = hash_secret(initial)
        _write_initial_admin_password(meta.get('admin_user', 'admin'), initial)
        changed = True
    if changed:
        save_json(META_FILE, meta)
    return meta


def migrate_admin_password():
    meta = load_json(META_FILE, {})
    plain = str(meta.get('admin_pass') or '')
    if plain:
        meta['admin_pass_hash'] = hash_secret(plain)
        del meta['admin_pass']
        save_json(META_FILE, meta)


SETTLEMENT_DAY_DEFAULT = 12
CYCLE_LENGTH_DAYS_DEFAULT = 30
CYCLE_LENGTH_MIN = 1
CYCLE_LENGTH_MAX = 90


def get_settlement_day():
    """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
    try:
        v = int((load_json(META_FILE, {}) or {}).get('settlement_day', SETTLEMENT_DAY_DEFAULT))
    except (TypeError, ValueError):
        return SETTLEMENT_DAY_DEFAULT
    return max(1, min(28, v))


def get_cycle_length_days():
    """Length of one billing cycle, in days. Editable via /admin/cycle-config.
    Cycles roll exactly every N days from `cycle_anchor_date` (or, if absent,
    from the most recent settlement_day on/before today)."""
    try:
        v = int((load_json(META_FILE, {}) or {}).get('cycle_length_days', CYCLE_LENGTH_DAYS_DEFAULT))
    except (TypeError, ValueError):
        return CYCLE_LENGTH_DAYS_DEFAULT
    return max(CYCLE_LENGTH_MIN, min(CYCLE_LENGTH_MAX, v))


def _settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now.date().
    Falls back through prev month / Feb edge cases."""
    if now.day >= settlement_day:
        return now.date().replace(day=settlement_day)
    prev_month_end = now.replace(day=1) - timedelta(days=1)
    return prev_month_end.date().replace(day=settlement_day)


def get_cycle_anchor_date(now=None):
    """The anchor date (a settlement day in the past or today) that all N-day
    cycle blocks count from. Read from META_FILE if persisted, else derive
    from the current settlement_day. Storing the anchor keeps cycle boundaries
    stable across the inevitable jump that would otherwise happen each month
    when settlement_day recurs (e.g. with cycle_length=15, the most-recent-
    settlement-day-of-month anchor would skip cycles)."""
    if now is None:
        now = local_now()
    meta = load_json(META_FILE, {}) or {}
    raw = meta.get('cycle_anchor_date')
    if raw:
        try:
            return datetime.strptime(str(raw), '%Y-%m-%d').date()
        except (TypeError, ValueError):
            pass
    return _settlement_anchor_date(now, get_settlement_day())


def cycle_start_for(now, day=None, length=None, anchor=None):
    """Datetime at 00:00 local of the current cycle's start.

    For cycle_length_days==30 (default) the result matches the pre-existing
    calendar-month behaviour as long as the anchor is the most recent
    settlement_day. For shorter/longer N, cycles roll exactly every N days
    from the anchor — they intentionally do not re-align to calendar months."""
    if anchor is None:
        if day is None:
            anchor = get_cycle_anchor_date(now)
        else:
            anchor = _settlement_anchor_date(now, int(day))
    N = int(length) if length is not None else get_cycle_length_days()
    today = now.date()
    if today < anchor:
        # `now` is before the anchor (operator just changed settings forward in
        # time); treat the anchor as the current cycle's start.
        start_date = anchor
    else:
        offset_days = (today - anchor).days
        start_date = anchor + timedelta(days=(offset_days // N) * N)
    return datetime.combine(start_date, datetime.min.time(), tzinfo=now.tzinfo)


def month_key(now=None):
    """Legacy cycle key (YYYY-MM) used as a dict key in usage.json. Cycle reads
    are now derived from usage_daily.json (see _cycle_days), so this key only
    needs to round-trip with traffic_limiter.billing_month_key; it does not
    drive the displayed cycle range."""
    if now is None:
        now = local_now()
    return billing_cycle_key(now, get_settlement_day())


def _cycle_days(now):
    """List of YYYY-MM-DD date keys covered by the current cycle, oldest first.
    Capped at today (future days in a cycle aren't displayed/summed)."""
    start = cycle_start_for(now).date()
    end = start + timedelta(days=get_cycle_length_days() - 1)
    today = now.date()
    last = min(end, today)
    out = []
    d = start
    while d <= last:
        out.append(d.strftime('%Y-%m-%d'))
        d += timedelta(days=1)
    return out


def _zero_cycle_daily_hourly_for(uids, *, now):
    """Zero each user's daily/hourly entries within the current cycle. Caller
    must hold usage_lock. Keeps the cycle-bucket reset in usage.json consistent
    with usage_daily.json/usage_hourly.json, so post-reset displays read 0
    instead of the pre-reset accumulated values."""
    uids = list(uids)
    if not uids:
        return
    days = set(_cycle_days(now))
    cycle_start = cycle_start_for(now)
    hour_cutoff = cycle_start.strftime('%Y-%m-%dT%H')

    daily = load_json(USAGE_DAILY_FILE, {})
    changed_daily = False
    for dk in list(daily.keys()):
        if dk not in days:
            continue
        bucket = daily.get(dk) or {}
        for uid in uids:
            if uid in bucket:
                bucket[uid] = {'tx': 0, 'rx': 0, 'total': 0}
                changed_daily = True
    if changed_daily:
        save_json(USAGE_DAILY_FILE, daily)

    hourly = load_json(USAGE_HOURLY_FILE, {})
    changed_hourly = False
    for hk in list(hourly.keys()):
        if hk < hour_cutoff:
            continue
        bucket = hourly.get(hk) or {}
        for uid in uids:
            if uid in bucket:
                bucket[uid] = {'tx': 0, 'rx': 0, 'total': 0}
                changed_hourly = True
    if changed_hourly:
        save_json(USAGE_HOURLY_FILE, hourly)


def _cycle_preserve_key(now):
    return cycle_start_for(now).date().isoformat()


def preserved_raw_for_cycle(*, now):
    """Sum of raw bytes preserved (refreshed-not-cleared) for the current cycle.
    Used so 'refresh traffic' can zero a user's counter without shrinking the
    server's '本周期总流量' display."""
    data = load_json(USAGE_PRESERVED_FILE, {})
    bucket = data.get(_cycle_preserve_key(now)) or {}
    total = 0
    for v in bucket.values():
        if isinstance(v, dict):
            total += int(v.get('total', 0))
        else:
            total += int(v or 0)
    return total


def add_preserved_for_user(username, tx, rx, total, *, now):
    """Record `total` raw bytes against `username` under the current cycle's
    preserved bucket, additive across repeated refreshes. Caller holds usage_lock."""
    if total <= 0:
        return
    data = load_json(USAGE_PRESERVED_FILE, {})
    key = _cycle_preserve_key(now)
    bucket = data.setdefault(key, {})
    cur = bucket.get(username) or {}
    if not isinstance(cur, dict):
        cur = {'tx': 0, 'rx': 0, 'total': int(cur or 0)}
    bucket[username] = {
        'tx': int(cur.get('tx', 0)) + int(tx),
        'rx': int(cur.get('rx', 0)) + int(rx),
        'total': int(cur.get('total', 0)) + int(total),
    }
    # GC: drop cycle keys older than the current one. Preserved bytes are a
    # display-only adjustment scoped to "this cycle" — past-cycle entries would
    # otherwise grow without bound across months.
    for k in list(data.keys()):
        if k < key:
            data.pop(k, None)
    save_json(USAGE_PRESERVED_FILE, data)


def _cycle_raw_for_user(uid, daily, *, now):
    """Per-user raw cycle bytes derived from usage_daily.json. Returns (tx, rx, total).

    Daily is the canonical fine-grained source: `today`/`current hour` cards already
    read from daily/hourly, so deriving `cycle` from daily guarantees
    `cycle >= today >= current_hour` and avoids drift against the cycle bucket
    in `usage.json`, which is a separately-accumulated counter that can fall
    behind on file corruption, partial writes, or stale state."""
    tx = rx = total = 0
    for dk in _cycle_days(now):
        entry = (daily.get(dk) or {}).get(uid)
        if isinstance(entry, dict):
            etx = int(entry.get('tx', 0))
            erx = int(entry.get('rx', 0))
            tx += etx
            rx += erx
            total += int(entry.get('total', etx + erx))
        else:
            total += int(entry or 0)
    return tx, rx, total


def usage_for_user(username, usage_month=None, *, daily=None, now=None):
    """Per-user cycle raw bytes (tx, rx, total).

    The `usage_month` positional argument is kept for backward compat with
    legacy call sites that read the cycle bucket from usage.json; it is now
    ignored. Cycle value is always derived from usage_daily.json summed across
    days in the current cycle — see _cycle_raw_for_user for why."""
    if daily is None:
        daily = load_json(USAGE_DAILY_FILE, {})
    return _cycle_raw_for_user(username, daily, now=now or local_now())


def scaled_usage_for_user(username, usage_month=None, *, daily=None, now=None):
    tx, rx, total = usage_for_user(username, usage_month, daily=daily, now=now)
    m = DISPLAY_MULTIPLIER
    return int(tx * m), int(rx * m), int(total * m)


def user_total_quota(user_cfg):
    return int(user_cfg.get('monthly_quota_bytes', 0) or 0)


def build_yaml(username, auth_secret):
    if not TEMPLATE_FILE.exists():
        return ''
    text = TEMPLATE_FILE.read_text(encoding='utf-8')
    text = re.sub(
        r'(?m)^(\s*password:\s*).*$',
        lambda m: f'{m.group(1)}{username}:{auth_secret}',
        text,
        count=1,
    )
    users = load_json(USERS_FILE, {})
    vless_uuid = str((users.get(username) or {}).get('vless_uuid') or '').strip()
    if vless_uuid:
        text = re.sub(
            r'(?m)^(\s*uuid:\s*).*$',
            lambda m: f'{m.group(1)}{vless_uuid}',
            text,
        )
        text = re.sub(
            r'(?m)^(\s*password:\s*)TUIC_PASSWORD_PLACEHOLDER\s*$',
            lambda m: f'{m.group(1)}{username}:{auth_secret}',
            text,
        )
    return text


def pct(used, total):
    if total <= 0:
        return 0.0
    return min(100.0, max(0.0, used * 100.0 / total))


def verify_secret(plain, stored_hash):
    """Verify a plaintext value against a pbkdf2 hash."""
    try:
        _, rounds_s, salt_b64, digest_b64 = stored_hash.split('$')
        rounds = int(rounds_s)
        salt = base64.urlsafe_b64decode(salt_b64 + '==')
        expected = base64.urlsafe_b64decode(digest_b64 + '==')
        candidate = hashlib.pbkdf2_hmac('sha256', plain.encode('utf-8'), salt, rounds)
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


# In-memory login failure tracker: {ip: [timestamp, ...]}
# Bounded so an attacker rotating through many source IPs can't grow this
# dict without limit; entries are also dropped when their timestamp list
# decays to empty so cleanly-decayed IPs don't linger as zero-cost ghosts.
_login_failures: dict = {}
_LOGIN_MAX = 3        # max failures
_LOGIN_WINDOW = 3600  # seconds (1 hour)
_LOGIN_FAILURES_MAX_IPS = 1024


def _is_rate_limited(ip):
    now = time.time()
    times = [t for t in _login_failures.get(ip, []) if now - t < _LOGIN_WINDOW]
    if times:
        _login_failures[ip] = times
    else:
        _login_failures.pop(ip, None)
    return len(times) >= _LOGIN_MAX


def _record_failure(ip):
    if ip not in _login_failures and len(_login_failures) >= _LOGIN_FAILURES_MAX_IPS:
        # Dicts preserve insertion order; evict the oldest tracked IP.
        oldest = next(iter(_login_failures))
        _login_failures.pop(oldest, None)
    _login_failures.setdefault(ip, []).append(time.time())


def _clear_failures(ip):
    _login_failures.pop(ip, None)


def check_user_token(user, token):
    users = load_json(USERS_FILE, {})
    cfg = users.get(user)
    if not cfg:
        return None
    expected = str(cfg.get('sub_token') or '')
    if not token or not hmac.compare_digest(token, expected):
        return None
    return cfg


def parse_cookies(handler):
    raw = handler.headers.get('Cookie', '')
    ck = SimpleCookie()
    try:
        ck.load(raw)
    except Exception:
        return {}
    return {k: v.value for k, v in ck.items()}


def get_sessions():
    sessions = load_json(SESSIONS_FILE, {})
    now = int(time.time())
    alive = {}
    for sid, info in sessions.items():
        exp = int(info.get('exp', 0))
        if exp > now:
            alive[sid] = info
    if alive != sessions:
        save_json(SESSIONS_FILE, alive)
    return alive


def create_session(username='admin'):
    sessions = get_sessions()
    sid = secrets.token_urlsafe(24)
    sessions[sid] = {'user': username, 'exp': int(time.time()) + SESSION_TTL}
    save_json(SESSIONS_FILE, sessions)
    return sid


def delete_session(sid):
    if not sid:
        return
    sessions = get_sessions()
    if sid in sessions:
        del sessions[sid]
        save_json(SESSIONS_FILE, sessions)


def is_logged_in(handler):
    q = parse_qs(urlparse(handler.path).query)
    token = (q.get('token') or [''])[0]
    meta = ensure_meta()
    admin_token = str(meta.get('admin_token') or '')
    if token and hmac.compare_digest(token, admin_token):
        return True
    sid = parse_cookies(handler).get('sid', '')
    sessions = get_sessions()
    return sid in sessions


def html_page(title, body, body_class=''):
    cls = f' class="{body_class}"' if body_class else ''
    return (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="dark">'
        f'<title>{html.escape(title)}</title>'
        f'<link rel="stylesheet" href="/static/style.css">'
        f'</head><body{cls}>{body}</body></html>'
    )


# Inline SVG icons (24×24 stroke icons, sized down via .sidebar-link svg).
_ICONS = {
    'dashboard': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/></svg>',
    'config': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>',
    'rules': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    'logs': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="9" y1="13" x2="15" y2="13"/><line x1="9" y1="17" x2="15" y2="17"/></svg>',
    'logout': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>',
    'menu': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>',
    'copy': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>',
    'open': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/></svg>',
    'back': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="19" y1="12" x2="5" y2="12"/><polyline points="12 19 5 12 12 5"/></svg>',
    'chart': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="6" y1="20" x2="6" y2="14"/><line x1="12" y1="20" x2="12" y2="8"/><line x1="18" y1="20" x2="18" y2="11"/><line x1="3" y1="20" x2="21" y2="20"/></svg>',
    'pulse': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>',
    'lock': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
}


def icon(name):
    return _ICONS.get(name, '')


def render_nav(brand, badge):
    return (
        f'<div class="nav"><div class="brand">{html.escape(brand)}</div>'
        f'<span class="badge">{html.escape(badge)}</span></div>'
    )


def render_alert(msg, kind='flash'):
    if not msg:
        return ''
    return f'<div class="{kind}">{html.escape(msg)}</div>'


def render_prefixed_alert(flash, msg_map):
    """Resolve a flash code that may carry an 'err:' prefix and render the alert."""
    if not flash:
        return ''
    is_err = flash.startswith('err:')
    key = flash.removeprefix('err:')
    msg = msg_map.get(key, key)
    return render_alert(msg, 'err' if is_err else 'flash')


def back_to_admin(label='返回管理后台'):
    return f'<a class="btn secondary" href="/admin">{icon("back")}<span>{html.escape(label)}</span></a>'


_SIDEBAR_NAV = [
    ('dashboard', '/admin', '总览', 'dashboard'),
    ('usage', '/admin/usage', '流量分析', 'chart'),
    ('health', '/admin/health', '健康状态', 'pulse'),
    ('config', '/admin/config', '模板配置', 'config'),
    ('rules', '/admin/rules', '路由规则', 'rules'),
    ('logs', '/admin/logs', '清零日志', 'logs'),
    ('settings', '/admin/settings', '设置', 'lock'),
]


def render_admin_shell(active, page_title, content, *, badge='', subtitle='', topbar_extra=''):
    """Wrap admin page content in the sidebar + topbar app shell."""
    nav_items = ''.join(
        f'<a href="{href}" class="sidebar-link {"active" if key == active else ""}">'
        f'{icon(icon_name)}<span>{html.escape(label)}</span></a>'
        for key, href, label, icon_name in _SIDEBAR_NAV
    )
    badge_html = f'<span class="badge">{html.escape(badge)}</span>' if badge else ''
    sub_html = f'<small>{html.escape(subtitle)}</small>' if subtitle else ''
    body = f'''<div class="app">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-brand"><span class="logo">H</span><span>Hysteria</span></div>
  <nav class="sidebar-nav">
    <div class="sidebar-section">管理</div>
    {nav_items}
  </nav>
  <div class="sidebar-footer">
    <a href="/logout" class="sidebar-link">{icon("logout")}<span>退出登录</span></a>
  </div>
</aside>
<div class="scrim" id="scrim"></div>
<div class="main">
  <header class="topbar">
    <div class="topbar-inner">
      <div class="row gap-sm">
        <button class="sidebar-toggle" id="sidebar-toggle" type="button" aria-label="切换侧边栏">{icon("menu")}</button>
        <h1 class="page-title">{html.escape(page_title)}{sub_html}</h1>
      </div>
      <div class="topbar-actions">{topbar_extra}{badge_html}</div>
    </div>
  </header>
  <div class="content">{content}</div>
</div>
</div>
<script>
(function() {{
  var sb = document.getElementById('sidebar');
  var sc = document.getElementById('scrim');
  var bt = document.getElementById('sidebar-toggle');
  function close() {{ sb.classList.remove('open'); document.body.classList.remove('sidebar-open'); }}
  bt.addEventListener('click', function() {{ sb.classList.toggle('open'); document.body.classList.toggle('sidebar-open'); }});
  sc.addEventListener('click', close);
  window.addEventListener('resize', function() {{ if (window.innerWidth > 880) close(); }});
}})();
</script>'''
    return html_page(page_title, body, body_class='has-shell')


def flash_text(msg):
    if not msg:
        return ''
    if msg.startswith('err:'):
        msg = msg[4:]
    if msg == 'login success':
        return '登录成功'
    if msg.startswith('updated '):
        return f'已更新用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('created '):
        return f'已创建用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('reset usage '):
        return f'已清除用户本月已用流量：{msg.split(" ", 2)[2]}'
    if msg == 'reset usage all':
        return '已清除全部用户本月已用流量'
    if msg.startswith('refresh usage '):
        return f'已刷新用户本月已用流量（服务器总流量不变）：{msg.split(" ", 2)[2]}'
    if msg.startswith('deleted '):
        return f'已删除用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('rotated '):
        return f'已重置订阅令牌（旧链接已失效）：{msg.split(" ", 1)[1]}'
    if msg.startswith('disabled '):
        return f'已停用用户（已断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('enabled '):
        return f'已启用用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('settlement '):
        return f'已更新结算日：每月 {msg.split(" ", 1)[1]} 日'
    maps = {
        'user not found': '用户不存在',
        'user empty': '用户名不能为空',
        'user_exists_use_reset_token': '用户已存在，请勾选”若用户已存在则重置订阅令牌”后再创建',
        'username_invalid': '用户名只能包含字母、数字、点、下划线、连字符，且不能以 .json 结尾',
        'settlement_invalid': '结算日无效（请输入 1–28 之间的整数）',
        'cycle_length_invalid': f'周期长度无效（请输入 {CYCLE_LENGTH_MIN}–{CYCLE_LENGTH_MAX} 之间的整数）',
    }
    return maps.get(msg, msg)


def render_home(host):
    body = f'''<div class="wrap home-wrap">
<div class="card elev inline-form auth-card" style="text-align:center;">
  <div class="auth-head" style="justify-content:center;border-bottom:0;padding-bottom:6px;margin-bottom:8px;">
    <span class="app-logo lg">H</span>
    <div style="text-align:left;">
      <div class="title">Hysteria</div>
      <div class="sub">管理与订阅控制台</div>
    </div>
  </div>
  <a class="btn full mt-md" href="/login">{icon("logout")}<span>管理员登录</span></a>
</div></div>'''
    return html_page('Hysteria', body)


def render_login(host, msg=''):
    body = f'''<div class="wrap home-wrap">
{render_alert(msg, 'err')}
<div class="card elev inline-form auth-card">
  <div class="auth-head">
    <span class="app-logo">H</span>
    <div>
      <div class="title">管理员登录</div>
      <div class="sub">登录到 <code style="padding:2px 6px;font-size:11.5px;">{html.escape(host)}</code></div>
    </div>
  </div>
  <form method="post" action="/login">
    <label>用户名</label><input name="username" required autofocus autocomplete="username">
    <label class="mt-sm">密码</label><input name="password" type="password" required autocomplete="current-password">
    <div class="row mt-md">
      <button class="btn" type="submit" style="flex:1;justify-content:center;">登录</button>
      <a class="btn secondary" href="/">返回</a>
    </div>
  </form>
</div></div>'''
    return html_page('管理员登录', body)


def render_qr_svg(text, *, _runner=None):
    """Return an inline SVG QR code for `text`, or '' if qrencode is unavailable.

    Shells out to the qrencode CLI (libqrencode), installed via apt by
    deploy.sh. The SVG is sized via CSS in the caller, not the SVG attrs,
    so it scales cleanly on phone vs. laptop screens.

    Failures are silent: a missing binary or non-zero exit yields '' and the
    panel just doesn't show the QR card. We don't want a render bug to take
    down the whole panel.
    """
    if not text:
        return ''
    runner = _runner if _runner is not None else subprocess.check_output
    try:
        out = runner(
            ['qrencode', '-t', 'SVG', '-o', '-', '-l', 'L', '-m', '1', '--', text],
            timeout=2,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return ''
    svg = out.decode('utf-8', errors='replace')
    # Strip the XML prolog and DOCTYPE so the SVG inlines cleanly into HTML.
    svg = re.sub(r'<\?xml[^>]*\?>\s*', '', svg)
    svg = re.sub(r'<!DOCTYPE[^>]*>\s*', '', svg)
    return svg


def _cycle_reset_info(now=None):
    """Return (next_reset_date_str, days_left, cycle_length_days) for the panel
    quota-reset countdown. days_left is at least 1 — today is always strictly
    before the next cycle boundary."""
    if now is None:
        now = local_now()
    cycle_len = get_cycle_length_days()
    next_reset = (cycle_start_for(now) + timedelta(days=cycle_len)).date()
    days_left = max((next_reset - now.date()).days, 0)
    return next_reset.strftime('%Y-%m-%d'), days_left, cycle_len


def _build_panel_json_payload(user, cfg, *, now=None):
    """Live-refresh payload for the end-user panel (/panel/<user>.json).
    Mirrors the at-load values render_user_panel computes, in displayed bytes."""
    if now is None:
        now = local_now()
    daily = load_json(USAGE_DAILY_FILE, {})
    tx, rx, used = scaled_usage_for_user(user, daily=daily, now=now)
    total = user_total_quota(cfg)
    remain = max(total - used, 0) if total > 0 else -1
    online = int(load_json(ONLINE_FILE, {}).get(user, 0) or 0)
    return {
        'ts': now.isoformat(timespec='seconds'),
        'used_bytes': int(used),
        'total_bytes': int(total),
        'remain_bytes': int(remain),
        'tx_bytes': int(tx),
        'rx_bytes': int(rx),
        'online': online,
        'percent': round(pct(used, total), 2),
    }


def render_user_panel(host, base_url, user, token, cfg):
    now = local_now()
    daily = load_json(USAGE_DAILY_FILE, {})
    tx, rx, used = scaled_usage_for_user(user, daily=daily, now=now)
    total = user_total_quota(cfg)
    remain = max(total - used, 0) if total > 0 else -1
    online = int(load_json(ONLINE_FILE, {}).get(user, 0) or 0)
    percent = pct(used, total)
    cls = 'danger' if percent >= 90 else ''
    reset_date, days_left, cycle_len = _cycle_reset_info(now)
    spark = sparkline_svg(daily_window_for_user(user, daily, days=30, today=now.date()))
    sub_path = f'/sub/{user}?token={token}'
    panel_path = f'/panel/{user}?token={token}'
    json_path = f'/panel/{user}.json?token={token}'
    sub_http = f'{base_url}{sub_path}'
    panel_http = f'{base_url}{panel_path}'
    max_devices_n = int(cfg.get('max_devices', 0) or 0)
    is_disabled = bool(cfg.get('disabled'))
    disabled_banner = '<div class="err">账号已停用，请联系管理员</div>' if is_disabled else ''
    qr_svg = render_qr_svg(sub_http)
    qr_block = ''
    if qr_svg:
        qr_block = f'''<div class="card mt-md qr-card">
  <div class="k">订阅二维码</div>
  <div class="small">用客户端 App 扫码即可导入（Clash / 小火箭 等）</div>
  <div class="qr-wrap">{qr_svg}</div>
</div>'''
    # Suspended accounts get a 403 from /panel/<user>.json, so don't emit the
    # live-refresh loop (it would just spam '刷新失败'). The embedded URL escapes
    # '<' so a malicious username can't break out of the <script> element.
    poll_url_js = json.dumps(json_path).replace('<', '\\u003c')
    poll_js = '' if is_disabled else f'''  var pollUrl = {poll_url_js};
  var statusEl = document.querySelector('[data-role="poll-status"]');
  function fmtBytes(n) {{
    var v = Math.max(0, Number(n) || 0);
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (v >= 1024 && i < u.length - 1) {{ v /= 1024; i++; }}
    return v.toFixed(2) + ' ' + u[i];
  }}
  function setRole(role, txt) {{
    var el = document.querySelector('[data-role="' + role + '"]');
    if (el && txt !== undefined) el.textContent = txt;
  }}
  function stamp() {{
    return new Date().toLocaleTimeString([], {{ hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }});
  }}
  var timer = null, inflight = false;
  function tick() {{
    if (inflight) return;
    inflight = true;
    fetch(pollUrl, {{ credentials: 'same-origin' }})
      .then(function(r) {{ return r.ok ? r.json() : null; }})
      .catch(function() {{ return null; }})
      .then(function(d) {{
        if (!d) {{ if (statusEl) statusEl.textContent = '刷新失败'; return; }}
        setRole('used', fmtBytes(d.used_bytes));
        setRole('remain', fmtBytes(d.remain_bytes));
        setRole('online', d.online);
        var p = Number(d.percent);
        setRole('percent', p.toFixed(2) + '%');
        setRole('txrx', '上传 ' + fmtBytes(d.tx_bytes) + ' · 下载 ' + fmtBytes(d.rx_bytes));
        var bar = document.querySelector('[data-role="bar"]');
        if (bar) {{ bar.style.width = p.toFixed(2) + '%'; bar.classList.toggle('danger', p >= 90); }}
        if (statusEl) statusEl.textContent = '更新于 ' + stamp();
      }})
      .finally(function() {{ inflight = false; }});
  }}
  function start() {{ if (!timer) {{ tick(); timer = setInterval(tick, 10000); }} }}
  function stop() {{ if (timer) {{ clearInterval(timer); timer = null; }} }}
  document.addEventListener('visibilitychange', function() {{ if (document.hidden) stop(); else start(); }});
  window.addEventListener('pagehide', stop);
  start();'''
    body = f'''<div class="wrap">
{disabled_banner}
<div class="nav">
  <div class="row gap-sm">
    <span class="app-logo">H</span>
    <div>
      <div class="brand" style="font-size:16px;">用户面板</div>
      <div class="small">{html.escape(user)}</div>
    </div>
  </div>
  <div style="text-align:right;">
    <span class="badge">{html.escape(host)}</span>
    <div class="small faint" data-role="poll-status" style="margin-top:4px;">实时刷新中…</div>
  </div>
</div>
<div class="grid grid-4">
  <div class="card stat"><div class="k">本月已用</div><div class="v big" data-role="used">{fmt_bytes(used)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">总流量</div><div class="v">{fmt_bytes(total)}</div></div>
  <div class="card stat"><div class="k">剩余流量</div><div class="v" data-role="remain">{fmt_bytes(remain)}</div></div>
  <div class="card stat"><div class="k">在线设备</div><div class="v"><span data-role="online">{online}</span> <span class="faint" style="font-size:14px;font-weight:500;">/ {max_devices_n}</span></div></div>
</div>
<div class="card mt-md">
  <div class="row" style="justify-content:space-between;margin-bottom:10px;">
    <div class="k" style="margin:0;">流量进度</div>
    <div class="bold" style="font-variant-numeric:tabular-nums;" data-role="percent">{percent:.2f}%</div>
  </div>
  <div class="bar"><div class="fill {cls}" data-role="bar" style="width:{percent:.2f}%"></div></div>
  <div class="small mt-sm" data-role="txrx">上传 {fmt_bytes(tx)} · 下载 {fmt_bytes(rx)}</div>
  <div class="small mt-sm faint">本周期 {cycle_len} 天 · 重置于 {reset_date} · 还剩 {days_left} 天</div>
</div>
<div class="card mt-md">
  <div class="k">近 30 天用量趋势</div>
  <div class="panel-trend">{spark}</div>
</div>
<div class="grid grid-2 mt-md">
  <div class="card">
    <div class="k">订阅链接</div>
    <div class="copy-mono"><code id="sub">{html.escape(sub_http)}</code></div>
    <div class="row mt-md">
      <button class="btn" type="button" data-copy="{html.escape(sub_http)}">{icon("copy")}<span>复制链接</span></button>
      <a class="btn secondary" href="{html.escape(sub_path)}">{icon("open")}<span>打开订阅</span></a>
    </div>
  </div>
  <div class="card">
    <div class="k">当前面板链接</div>
    <div class="copy-mono"><code>{html.escape(panel_http)}</code></div>
    <div class="row mt-md">
      <button class="btn secondary" type="button" data-copy="{html.escape(panel_http)}">{icon("copy")}<span>复制链接</span></button>
      <a class="btn ghost btn-sm" href="/">{icon("back")}<span>返回首页</span></a>
      <form method="post" action="/panel/{html.escape(user)}/rotate-token" data-action="rotate-token" class="inline-form-row">
        <input type="hidden" name="token" value="{html.escape(token)}">
        <button class="btn danger-btn btn-sm" type="submit">重置 Token</button>
      </form>
    </div>
    <div class="small mt-sm faint">重置后旧链接立即失效，需用新链接重新订阅。</div>
  </div>
</div>
{qr_block}
</div>
<script>
(function() {{
  function flashCopied(btn) {{
    var label = btn.querySelector('span');
    var prev = label ? label.textContent : '';
    if (label) label.textContent = '已复制 ✓';
    btn.disabled = true;
    setTimeout(function() {{ if (label) label.textContent = prev; btn.disabled = false; }}, 1400);
  }}
  document.addEventListener('click', function(ev) {{
    var btn = ev.target.closest ? ev.target.closest('[data-copy]') : null;
    if (!btn) return;
    var text = btn.getAttribute('data-copy');
    if (!navigator.clipboard) {{ alert('当前环境不支持自动复制，请手动选中链接复制'); return; }}
    navigator.clipboard.writeText(text).then(function() {{ flashCopied(btn); }})
      .catch(function() {{ alert('复制失败，请手动复制'); }});
  }});
  document.addEventListener('submit', function(ev) {{
    var f = ev.target;
    if (f && f.dataset && f.dataset.action === 'rotate-token') {{
      if (!confirm('确认重置订阅 Token？旧链接将立即失效。')) ev.preventDefault();
    }}
  }});
{poll_js}
}})();
</script>'''
    return html_page(f'{user} 用户面板', body)


def row_form(user, cfg, online, host, base_url, usage_month=None, daily=None, now=None):
    tx, rx, used = scaled_usage_for_user(user, daily=daily, now=now)
    spark_cell = ''
    # NOTE: 30-day sparkline is rendered in 3 places — keep them in sync:
    # (1) here in row_form (initial page); (2) sparkline_svg() emits class="spark";
    # (3) /admin/usage.json sends spark_html, JS finds [data-role="spark"] and sets innerHTML.
    if daily is not None:
        spark_cell = f'<td class="spark-cell" data-role="spark">{sparkline_svg(daily_window_for_user(user, daily, days=30))}</td>'
    total = user_total_quota(cfg)
    max_devices = int(cfg.get('max_devices', 0) or 0)
    quota_gb = int(round(total / 1024 / 1024 / 1024)) if total > 0 else 0
    panel = f'{base_url}/panel/{user}?token={cfg.get("sub_token", "")}'
    sub_http = f'{base_url}/sub/{user}?token={cfg.get("sub_token", "")}'
    metered = user_compat.is_metered(cfg)
    guest_checked = 'checked' if metered else ''
    percent = pct(used, total)
    bar_cls = 'danger' if percent >= 90 else ''
    bar_w = f'{percent:.1f}'
    user_esc = html.escape(user)
    guest_badge = '<span class="badge badge-info">访客</span>' if metered else ''
    disabled = bool(cfg.get('disabled'))
    disabled_badge = '<span class="badge badge-danger">已停用</span>' if disabled else ''
    guest_preview = ' · 访客' if metered else ''
    summary_preview = f'<span class="summary-preview">{quota_gb or 150} GB · {max_devices or 2} 设备{guest_preview}</span>'
    if disabled:
        toggle_form = (
            '<form method="post" action="/admin/toggle-user" class="inline-form-row">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            '<button class="btn ghost btn-sm" type="submit" title="恢复该用户的连接权限">启用</button></form>'
        )
    else:
        toggle_form = (
            '<form method="post" action="/admin/toggle-user" class="inline-form-row" data-action="disable-user">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            '<button class="btn ghost btn-sm" type="submit" title="临时停用：拒绝新连接并断开现有会话，不删除用户">暂停</button></form>'
        )
    return f'''<tr data-user="{user_esc}">
<td>
  <div class="row gap-sm" style="flex-wrap:nowrap;">
    <div class="user-avatar">{html.escape(user[:1].upper())}</div>
    <div style="min-width:0;">
      <div class="bold">{user_esc} {guest_badge}{disabled_badge}</div>
      <div class="small">在线 <span data-role="online">{online.get(user, 0)}</span> / {max_devices} 设备</div>
    </div>
  </div>
</td>
{spark_cell}
<td>
  <div class="row" style="justify-content:space-between;margin-bottom:4px;">
    <span class="bold" data-role="used">{fmt_bytes(used)}</span>
    <span class="small">/ {fmt_bytes(total)}</span>
  </div>
  <div class="mini-bar"><div class="mini-fill {bar_cls}" data-role="bar" style="width:{bar_w}%"></div></div>
  <div class="small mt-sm" data-role="detail">{percent:.1f}% · ↑{fmt_bytes(tx)} ↓{fmt_bytes(rx)}</div>
</td>
<td>
<details>
<summary>编辑套餐{summary_preview}</summary>
<form method="post" action="/admin/update" class="inline-form">
<input type="hidden" name="user" value="{user_esc}">
<label>兼容连接密码（可选）</label><input name="password" type="password" placeholder="留空则不修改">
<label class="mt-sm">设备数上限</label><input name="max_devices" type="number" min="1" value="{max_devices or 2}">
<label class="mt-sm">月流量上限 (GB)</label><input name="quota_gb" type="number" min="1" value="{quota_gb or 150}">
<label class="switch mt-sm"><input type="checkbox" name="guest" {guest_checked}>客人用户（仅做标记，不影响认证）</label>
<button class="btn mt-md" type="submit">保存</button>
</form>
</details>
<div class="row gap-sm mt-sm">
  <form method="post" action="/admin/reset-usage" class="inline-form-row">
    <input type="hidden" name="user" value="{user_esc}">
    <button class="btn ghost btn-sm" type="submit" title="清空该用户已用流量，且从服务器总流量中扣除">清流量</button>
  </form>
  <form method="post" action="/admin/refresh-usage" class="inline-form-row">
    <input type="hidden" name="user" value="{user_esc}">
    <button class="btn ghost btn-sm" type="submit" title="清空该用户已用流量，但保留在服务器总流量中">刷新流量</button>
  </form>
  <form method="post" action="/admin/rotate-token" class="inline-form-row" data-action="rotate-user-token">
    <input type="hidden" name="user" value="{user_esc}">
    <button class="btn ghost btn-sm" type="submit" title="重置该用户订阅令牌，旧订阅/面板链接立即失效">重置订阅</button>
  </form>
  {toggle_form}
  <form method="post" action="/admin/delete" class="inline-form-row" data-action="delete-user">
    <input type="hidden" name="user" value="{user_esc}">
    <button class="btn danger-btn btn-sm" type="submit">删除</button>
  </form>
</div>
</td>
<td class="link-cell">
  <div class="link-row">
    <a href="{html.escape(panel)}" target="_blank" rel="noopener">{icon("dashboard")}<span>面板</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(panel)}" title="复制面板链接">{icon("copy")}</button>
  </div>
  <div class="link-row">
    <a href="{html.escape(sub_http)}" target="_blank" rel="noopener">{icon("open")}<span>订阅</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(sub_http)}" title="复制订阅链接">{icon("copy")}</button>
  </div>
</td>
</tr>'''


def render_admin(host, base_url, flash=''):
    users = load_json(USERS_FILE, {})
    online = load_json(ONLINE_FILE, {})
    now = local_now()
    mk = month_key(now)
    daily = load_json(USAGE_DAILY_FILE, {})
    total_used = sum(scaled_usage_for_user(u, daily=daily, now=now)[2] for u in users)
    total_used += int(preserved_raw_for_cycle(now=now) * DISPLAY_MULTIPLIER)
    settlement_day = get_settlement_day()
    cycle_length = get_cycle_length_days()
    cycle_start = cycle_start_for(now)
    cycle_end = cycle_start + timedelta(days=cycle_length - 1)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_range = f'{cycle_start.strftime("%m/%d")} → {cycle_end.strftime("%m/%d")} · 第 {cycle_day}/{cycle_length} 天'
    settle_form = (
        f'<form method="post" action="/admin/cycle-config" class="inline-form-row" style="margin:0;">'
        f'<label class="small" style="margin-right:6px;">结算日</label>'
        f'<input name="day" type="number" min="1" max="28" value="{settlement_day}" '
        f'style="width:60px;margin-right:6px;" required>'
        f'<label class="small" style="margin-right:6px;">周期</label>'
        f'<input name="length" type="number" min="{CYCLE_LENGTH_MIN}" max="{CYCLE_LENGTH_MAX}" '
        f'value="{cycle_length}" style="width:60px;margin-right:2px;" required>'
        f'<span class="small" style="margin-right:6px;">天</span>'
        f'<button class="btn ghost btn-sm" type="submit">保存</button>'
        f'</form>'
    )
    alert = render_alert(flash_text(flash))
    rows = ''.join(row_form(u, cfg, online, host, base_url, daily=daily, now=now) for u, cfg in users.items()) \
        or '<tr><td colspan="5" class="empty">暂无用户，使用下方表单创建第一个用户</td></tr>'
    content = f'''{alert}
<div class="grid grid-3">
  <div class="card stat"><div class="k">本周期总流量</div><div class="v big" id="total-used">{fmt_bytes(total_used)}</div><div class="small">{html.escape(cycle_range)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">计费周期</div><div class="v">{mk}</div><div class="small">每 {cycle_length} 天结算 · 第 {settlement_day} 日</div></div>
  <div class="card stat">
    <div class="k">快速操作</div>
    <form method="post" action="/admin/reset-usage-all" data-action="reset-all" style="margin:6px 0 0;">
      <button class="btn secondary btn-sm" type="submit">一键清空本周期已用</button>
    </form>
    <div class="row gap-sm mt-sm">
      <a class="btn ghost btn-sm" href="/admin/usage.csv?window=cycle">导出本周期 CSV</a>
      <a class="btn ghost btn-sm" href="/admin/usage.csv?window=30d">导出 30 天 CSV</a>
    </div>
  </div>
</div>
<div class="card mt-md scroll-x" style="padding:0;overflow:hidden;">
  <div class="row" style="padding:14px 18px;justify-content:space-between;border-bottom:1px solid var(--line);gap:12px;flex-wrap:wrap;">
    <div class="bold">用户列表</div>
    <div class="row gap-sm" style="flex:1;justify-content:flex-end;flex-wrap:wrap;">
      <input id="user-filter" type="search" placeholder="搜索用户名…" autocomplete="off"
             class="user-filter-input" style="min-width:180px;max-width:260px;">
      <div class="row gap-sm filter-chips" role="group" aria-label="状态筛选">
        <button type="button" class="chip active" data-filter="all">全部</button>
        <button type="button" class="chip" data-filter="online">在线</button>
        <button type="button" class="chip" data-filter="over">超 90%</button>
      </div>
      <div class="small" id="filter-count" style="min-width:64px;text-align:right;">{len(users)} 个用户</div>
      <div class="small faint">实时 · 5 s</div>
    </div>
  </div>
  <table class="table"><thead><tr><th style="padding-left:18px;">用户</th><th>30 天趋势</th><th>本月用量</th><th>操作</th><th style="padding-right:18px;">链接</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<div class="card mt-md">
  <details class="summary-muted">
    <summary>新增用户</summary>
    <form method="post" action="/admin/add" class="inline-form mt-md">
      <div class="grid grid-3">
        <div><label>用户名</label><input name="user" required></div>
        <div><label>兼容连接密码（可选）</label><input name="password" type="password" placeholder="默认仅用订阅 token 认证"></div>
        <div><label>月流量上限 (GB)</label><input name="quota_gb" type="number" value="150" min="1"></div>
      </div>
      <div class="row mt-md">
        <label class="switch"><input type="checkbox" name="guest" checked>客人用户</label>
        <label class="switch"><input type="checkbox" name="reset_token">已存在则重置订阅令牌</label>
      </div>
      <button class="btn mt-md" type="submit">创建用户</button>
    </form>
  </details>
</div>
<script src="/static/admin-poll.js" defer></script>
'''
    return render_admin_shell('dashboard', '总览', content,
                              badge=f'{len(users)} 个用户',
                              subtitle=f'{host} · 计费周期 {mk}',
                              topbar_extra=settle_form)


def _action_label(action):
    return {
        'reset_usage_user': '清除用户流量',
        'reset_usage_all': '清空全部流量',
        'refresh_usage_user': '刷新用户流量（保留总计）',
        'rotate_token': '重置订阅令牌',
        'disable_user': '停用用户',
        'enable_user': '启用用户',
    }.get(action, action)


DAILY_RETENTION_DAYS = 30
LOCAL_TZ_LABEL = "Asia/Shanghai · 滚动 7 天小时 / 30 天每日"


def _scale_daily_entry(entry):
    """Scale a raw daily usage entry by DISPLAY_MULTIPLIER, returning (tx, rx, total)."""
    if not entry:
        return 0, 0, 0
    if isinstance(entry, dict):
        tx = int(entry.get('tx', 0))
        rx = int(entry.get('rx', 0))
        total = int(entry.get('total', tx + rx))
    else:
        total = int(entry or 0)
        tx, rx = 0, total
    m = DISPLAY_MULTIPLIER
    return int(tx * m), int(rx * m), int(total * m)


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

    Each item: {uid, last_24h_bytes, spark}. `spark` is window_hours hourly ints.
    Includes both metered and unmetered users.

    Allocation note: the previous implementation pre-allocated a window_hours-int
    spark list for *every* user in users.json on every call (called from the
    5-second admin/usage.json poll). This version computes totals first, picks
    the winners, and only then builds spark arrays for the N selected users —
    cuts the transient allocation to ~5% of the previous version when there
    are many users.
    """
    hourly = load_json(USAGE_HOURLY_FILE, {})
    users = load_json(USERS_FILE, {})
    known_users = set(users.keys())

    buckets = []
    for i in reversed(range(window_hours)):
        h = now - timedelta(hours=i)
        buckets.append(hourly.get(_hour_key(h)) or {})

    per_user_totals = {}
    for bucket in buckets:
        for uid, entry in bucket.items():
            if uid not in known_users:
                continue  # skip ghost entries from deleted users
            per_user_totals[uid] = per_user_totals.get(uid, 0) + _entry_total(entry)
    # Ensure users with zero traffic in the window can still appear as
    # zero-total entries when there are fewer than n users with traffic.
    for uid in known_users:
        per_user_totals.setdefault(uid, 0)

    ranked = sorted(per_user_totals.items(), key=lambda kv: kv[1], reverse=True)
    selected = ranked[:n]
    top_uids = [uid for uid, _ in selected]
    top_set = set(top_uids)

    spark = {uid: [0] * window_hours for uid in top_uids}
    for idx, bucket in enumerate(buckets):
        for uid, entry in bucket.items():
            if uid in top_set:
                spark[uid][idx] = int(_entry_total(entry) * DISPLAY_MULTIPLIER)

    out = []
    for uid, raw_total in selected:
        out.append({
            "uid": uid,
            "last_24h_bytes": int(raw_total * DISPLAY_MULTIPLIER),
            "spark": spark[uid],
        })
    return out


def _aggregate_stats(*, now, online):
    """Return the 4 stat-card numbers + cycle context."""
    hourly = load_json(USAGE_HOURLY_FILE, {})
    daily = load_json(USAGE_DAILY_FILE, {})

    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    cur_bucket = hourly.get(_hour_key(now)) or {}
    current_hour_raw = sum(_entry_total(v) for v in cur_bucket.values())

    today_raw = 0
    for hh in range(24):
        b = hourly.get(f"{today_str}T{hh:02d}") or {}
        today_raw += sum(_entry_total(v) for v in b.values())

    yest_bucket = daily.get(yesterday_str) or {}
    yesterday_raw = sum(_entry_total(v) for v in yest_bucket.values())

    last_7d_raw = 0
    for d in range(7):
        dk = (now.date() - timedelta(days=d)).strftime("%Y-%m-%d")
        last_7d_raw += sum(_entry_total(v) for v in (daily.get(dk) or {}).values())

    cycle_raw = sum(
        _entry_total(v)
        for dk in _cycle_days(now)
        for v in (daily.get(dk) or {}).values()
    )
    cycle_raw += preserved_raw_for_cycle(now=now)

    cycle_start = cycle_start_for(now)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_total_days = get_cycle_length_days()

    return {
        "current_hour_bytes": int(current_hour_raw * DISPLAY_MULTIPLIER),
        "today_bytes": int(today_raw * DISPLAY_MULTIPLIER),
        "yesterday_bytes": int(yesterday_raw * DISPLAY_MULTIPLIER),
        "last_7d_bytes": int(last_7d_raw * DISPLAY_MULTIPLIER),
        "cycle_bytes": int(cycle_raw * DISPLAY_MULTIPLIER),
        "cycle_day": cycle_day,
        "cycle_total_days": cycle_total_days,
        "online": int(sum(1 for v in (online or {}).values() if int(v or 0) > 0)),
    }


def _build_usage_csv(*, now, window='cycle'):
    """Return CSV body: per-user per-day usage rows for the requested window.

    Columns: date, user, tx_bytes, rx_bytes, total_bytes, displayed_bytes.
    `tx/rx/total_bytes` are raw (application-level) bytes from usage_daily.json;
    `displayed_bytes` is total * DISPLAY_MULTIPLIER (what the user is billed for).
    Window: 'cycle' = current billing cycle days; '30d' = last 30 calendar days.
    """
    daily = load_json(USAGE_DAILY_FILE, {})
    if window == '30d':
        today = now.date()
        days = [(today - timedelta(days=i)).strftime('%Y-%m-%d')
                for i in range(DAILY_RETENTION_DAYS - 1, -1, -1)]
    else:
        days = _cycle_days(now)

    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\n')
    w.writerow(['date', 'user', 'tx_bytes', 'rx_bytes', 'total_bytes', 'displayed_bytes'])
    for dk in days:
        bucket = daily.get(dk) or {}
        for uid, entry in sorted(bucket.items()):
            if isinstance(entry, dict):
                tx = int(entry.get('tx', 0))
                rx = int(entry.get('rx', 0))
                total = int(entry.get('total', tx + rx))
            else:
                tx = 0
                rx = int(entry or 0)
                total = rx
            displayed = int(total * DISPLAY_MULTIPLIER)
            w.writerow([dk, uid, tx, rx, total, displayed])
    return buf.getvalue()


def _build_usage_json_payload(*, now):
    """Compose the /admin/usage.json payload."""
    users = load_json(USERS_FILE, {})
    online = load_json(ONLINE_FILE, {})
    daily = load_json(USAGE_DAILY_FILE, {})
    series = _load_hourly_totals(now=now)
    grid = _load_heatmap_grid(now=now)
    stats = _aggregate_stats(now=now, online=online)
    top = _top_n_users(n=5, window_hours=24, now=now)
    user_list = []
    total_used = 0
    for u, cfg in users.items():
        tx, rx, used = scaled_usage_for_user(u, daily=daily, now=now)
        total = user_total_quota(cfg)
        total_used += used
        user_list.append({
            'user': u,
            'tx': tx,
            'rx': rx,
            'used': used,
            'total': total,
            'percent': pct(used, total),
            'online': int(online.get(u, 0)),
            # NOTE: spark_html mirrors row_form's spark cell — see row_form for the 3-place coupling note.
            'spark_html': sparkline_svg(daily_window_for_user(u, daily, days=30)),
        })
    total_used += int(preserved_raw_for_cycle(now=now) * DISPLAY_MULTIPLIER)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "stats": stats,
        "total_used": total_used,
        "users": user_list,
        "hourly_totals": series,
        "heatmap": grid,
        "top_n": top,
    }


def _build_user_json_payload(uid, *, now):
    """Compose the /admin/user/<uid>.json payload, or None if user unknown."""
    users = load_json(USERS_FILE, {})
    if uid not in users:
        return None
    cfg = users[uid] or {}

    online = load_json(ONLINE_FILE, {})
    hourly = load_json(USAGE_HOURLY_FILE, {})

    bars = []
    for i in reversed(range(HOURLY_RETENTION_HOURS)):
        h = now - timedelta(hours=i)
        hk = _hour_key(h)
        v = _entry_total((hourly.get(hk) or {}).get(uid))
        bars.append({"hour": hk, "bytes": int(v * DISPLAY_MULTIPLIER)})

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

    daily = load_json(USAGE_DAILY_FILE, {})
    _tx, _rx, cycle_raw = _cycle_raw_for_user(uid, daily, now=now)

    today_str = today.strftime("%Y-%m-%d")
    today_raw = sum(
        _entry_total((hourly.get(f"{today_str}T{hh:02d}") or {}).get(uid))
        for hh in range(24)
    )
    cur_raw = _entry_total((hourly.get(_hour_key(now)) or {}).get(uid))

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


def daily_window_for_user(uid, daily, *, days=30, today=None):
    """Return [(YYYY-MM-DD, scaled_total_bytes), ...] oldest-first for `days`."""
    today = today or local_now().date()
    out = []
    for i in reversed(range(days)):
        dk = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        _tx, _rx, total = _scale_daily_entry((daily.get(dk) or {}).get(uid))
        out.append((dk, total))
    return out


def sparkline_svg(values, *, height=24):
    """Forwarder kept for backward compat; new code calls charts.mini_sparkline_svg."""
    from charts import mini_sparkline_svg
    return mini_sparkline_svg(values, height=height)


def render_daily_usage(host, days=14):
    days = max(1, min(DAILY_RETENTION_DAYS, int(days)))
    users = load_json(USERS_FILE, {})
    daily = load_json(USAGE_DAILY_FILE, {})

    today = local_now().date()
    today_key = today.strftime('%Y-%m-%d')
    window = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in reversed(range(days))]
    weekday_labels = ['一', '二', '三', '四', '五', '六', '日']

    per_user = {}
    user_window_total = {}
    day_totals = {dk: 0 for dk in window}
    overall_total = 0
    for uid in users.keys():
        per_user[uid] = {}
        utot = 0
        for dk in window:
            tx, rx, tot = _scale_daily_entry((daily.get(dk) or {}).get(uid))
            per_user[uid][dk] = (tx, rx, tot)
            utot += tot
            day_totals[dk] += tot
            overall_total += tot
        user_window_total[uid] = utot

    sorted_uids = sorted(users.keys(), key=lambda u: user_window_total[u], reverse=True)

    col_headers = []
    for dk in window:
        wd = weekday_labels[datetime.strptime(dk, '%Y-%m-%d').weekday()]
        cls = ' day-today' if dk == today_key else ''
        col_headers.append(
            f'<th class="day-col{cls}" title="{dk}">'
            f'<div class="day-mmdd">{dk[5:]}</div>'
            f'<div class="day-weekday">周{wd}</div></th>'
        )

    rows = []
    for uid in sorted_uids:
        cells = []
        for dk in window:
            tx, rx, tot = per_user[uid][dk]
            today_cls = ' day-today' if dk == today_key else ''
            if tot <= 0:
                cells.append(f'<td class="day-cell empty-day{today_cls}">—</td>')
            else:
                title = f'{dk} · ↑ {fmt_bytes(tx)} · ↓ {fmt_bytes(rx)}'
                cells.append(
                    f'<td class="day-cell{today_cls}" title="{html.escape(title)}">{fmt_bytes(tot)}</td>'
                )
        utot = user_window_total[uid]
        utot_disp = fmt_bytes(utot) if utot > 0 else '—'
        rows.append(
            f'<tr><th class="user-col" scope="row">{html.escape(uid)}</th>'
            f'<td class="num user-total">{utot_disp}</td>'
            f'{"".join(cells)}</tr>'
        )

    if not rows:
        rows.append(f'<tr><td colspan="{2 + days}" class="empty">暂无用户</td></tr>')

    foot_cells = []
    peak_day = None
    peak_val = 0
    for dk in window:
        v = day_totals[dk]
        if v > peak_val:
            peak_val = v
            peak_day = dk
        today_cls = ' day-today' if dk == today_key else ''
        foot_cells.append(
            f'<td class="day-cell{today_cls}">{fmt_bytes(v) if v else "—"}</td>'
        )

    today_total = day_totals.get(today_key, 0)
    avg_per_day = int(overall_total / days) if days else 0

    switcher = ''.join(
        f'<a class="btn btn-sm {"primary" if d == days else "secondary"}" '
        f'href="/admin/daily?days={d}">{d} 天</a>'
        for d in (7, 14, 30)
    )

    earliest_recorded = min(daily.keys()) if daily else '—'

    content = f'''<div class="grid grid-4">
  <div class="card stat"><div class="k">{days} 天总流量</div><div class="v big">{fmt_bytes(overall_total)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">今日已用</div><div class="v">{fmt_bytes(today_total)}</div><div class="small">{today_key}</div></div>
  <div class="card stat"><div class="k">日均</div><div class="v">{fmt_bytes(avg_per_day)}</div></div>
  <div class="card stat"><div class="k">峰值日</div><div class="v">{fmt_bytes(peak_val) if peak_val else "—"}</div><div class="small">{peak_day or "—"}</div></div>
</div>
<div class="card mt-md" style="padding:14px 18px;">
  <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px;">
    <div>
      <div class="bold">每日流量明细 · 最近 {days} 天</div>
      <div class="small">最早数据：{earliest_recorded} · 保留 {DAILY_RETENTION_DAYS} 天</div>
    </div>
    <div class="row gap-sm">{switcher}</div>
  </div>
</div>
<div class="card mt-md scroll-x" style="padding:0;overflow:auto;">
  <table class="table daily-table">
    <thead><tr>
      <th class="user-col" style="padding-left:18px;">用户</th>
      <th class="num">{days} 天累计</th>
      {"".join(col_headers)}
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
    <tfoot><tr>
      <th class="user-col" style="padding-left:18px;">合计</th>
      <td class="num user-total">{fmt_bytes(overall_total) if overall_total else "—"}</td>
      {"".join(foot_cells)}
    </tr></tfoot>
  </table>
</div>'''
    return render_admin_shell('daily', '每日流量', content,
                              badge=f'最近 {days} 天',
                              subtitle=f'{host} · 滚动窗口 {DAILY_RETENTION_DAYS} 天')


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

    historical = _render_daily_table_collapsed(host)
    poll_controls = (
        '<button class="btn ghost btn-sm" type="button" id="usage-refresh-now">立即刷新</button>'
        '<span class="badge poll-status" data-role="poll-status">已加载</span>'
    )

    content = f'''<div class="grid grid-4">
  <div class="card stat" data-stat="current_hour"><div class="k">当小时</div><div class="v big">{fmt_bytes(stats["current_hour_bytes"])}</div><div class="small">{stats["online"]} 在线</div></div>
  <div class="card stat" data-stat="today"><div class="k">今日</div><div class="v">{fmt_bytes(stats["today_bytes"])}</div><div class="small">昨日 {fmt_bytes(stats["yesterday_bytes"])}</div></div>
  <div class="card stat" data-stat="last_7d"><div class="k">近 7 天</div><div class="v">{fmt_bytes(stats["last_7d_bytes"])}</div><div class="small">日均 {fmt_bytes(stats["last_7d_bytes"] // 7)}</div></div>
  <div class="card stat" data-stat="cycle"><div class="k">本周期</div><div class="v">{fmt_bytes(stats["cycle_bytes"])}</div><div class="small">第 {stats["cycle_day"]} / {stats["cycle_total_days"]} 天</div></div>
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
                              subtitle=f'{host} · {LOCAL_TZ_LABEL}',
                              topbar_extra=poll_controls)


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
    poll_controls = (
        '<button class="btn ghost btn-sm" type="button" id="usage-refresh-now">立即刷新</button>'
        '<span class="badge poll-status" data-role="poll-status">已加载</span>'
    )

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
                              subtitle=f'{host} · {LOCAL_TZ_LABEL}',
                              topbar_extra=poll_controls)


def _render_daily_table_collapsed(host):
    """Inline-render the per-user historical table, no shell wrapping.

    Window matches DAILY_RETENTION_DAYS (currently 30) — the full retained range,
    so the collapsed section shows everything we have on disk.
    """
    days = DAILY_RETENTION_DAYS
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
    return (f'<div class="scroll-x">'
            f'<table class="table daily-table-collapsed">'
            f'<thead><tr><th>用户</th>{headers}</tr></thead>'
            f'<tbody>{"".join(rows_html) or f"<tr><td colspan={days + 1}>暂无数据</td></tr>"}</tbody>'
            f'</table>'
            f'</div>')


def probe_cron_heartbeat():
    """How long since the cron tick last wrote usage.json. Stale if >120s."""
    try:
        mt = USAGE_FILE.stat().st_mtime
        age = int(time.time() - mt)
        return {'ok': age < 120, 'label': f'{age} 秒前'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_systemd(unit):
    """`systemctl is-active <unit>` → ok if 'active'."""
    try:
        out = subprocess.run(['systemctl', 'is-active', unit],
                             capture_output=True, text=True, timeout=3)
        v = (out.stdout or '').strip()
        return {'ok': v == 'active', 'label': v or '未知'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_disk():
    try:
        u = shutil.disk_usage('/')
        free_pct = u.free * 100 / u.total
        return {'ok': free_pct > 15, 'label': f'{free_pct:.0f}% free'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_cert(path=None):
    p = Path(path) if path else Path('/root/hysteria/server.crt')
    try:
        # Force C locale so openssl emits English month names that strptime can parse.
        env = {**os.environ, 'LC_ALL': 'C'}
        out = subprocess.run(['openssl', 'x509', '-enddate', '-noout', '-in', str(p)],
                             capture_output=True, text=True, timeout=3, env=env)
        if out.returncode != 0 or '=' not in out.stdout:
            return {'ok': False, 'label': '未知'}
        end_str = out.stdout.split('=', 1)[1].strip()
        end_dt = datetime.strptime(end_str, '%b %d %H:%M:%S %Y %Z')
        days = (end_dt - datetime.utcnow()).days
        return {'ok': days > 14, 'label': f'{days} 天剩余'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_online():
    try:
        data = load_json(ONLINE_FILE, {})
        n = sum(int(v) for v in data.values())
        return {'ok': True, 'label': f'{n} 在线'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def _health_card(title, probe_result):
    cls = 'ok' if probe_result['ok'] else 'bad'
    return (f'<div class="card stat health-{cls}">'
            f'<div class="k">{html.escape(title)}</div>'
            f'<div class="v">{html.escape(probe_result["label"])}</div>'
            f'</div>')


def _fire_test_alert(cfg, actor):
    """Dispatch a synthetic alert on a background daemon thread so a slow or
    unreachable channel never blocks the admin request thread. SSRF note: the
    webhook URL is operator-supplied (admin-equivalent trust); no allowlisting
    by design. Returns the started thread (handy for tests)."""
    event = {
        'kind': 'test',
        'user': actor or 'admin',
        'details': {'note': '来自管理面板的测试告警'},
    }
    t = threading.Thread(
        target=alerts.dispatch, args=(event,), kwargs={'config': cfg}, daemon=True,
    )
    t.start()
    return t


_HEALTH_FLASH = {
    'alert dispatched': '测试告警已在后台发送，请在接收端确认是否收到',
    'alert sent': '测试告警已发送，请在接收端确认',
    'alert_no_channels': '未配置告警通道（缺少 alerts.json 或其中的 telegram/webhook）',
}


def render_health(host, flash=''):
    alert = render_prefixed_alert(flash, _HEALTH_FLASH)
    cards = [
        _health_card('cron 心跳', probe_cron_heartbeat()),
        _health_card('hysteria', probe_systemd('hysteria-server.service')),
        _health_card('xray', probe_systemd('xray.service')),
        _health_card('磁盘', probe_disk()),
        _health_card('TLS 证书', probe_cert()),
        _health_card('在线用户', probe_online()),
    ]
    content = (
        alert
        + '<div class="grid grid-3">' + ''.join(cards) + '</div>'
        '<meta http-equiv="refresh" content="30">'
    )
    test_btn = ('<form method="post" action="/admin/test-alert" class="inline-form-row">'
                '<button class="btn secondary btn-sm" type="submit">发送测试告警</button></form>')
    return render_admin_shell('health', '健康状态', content,
                              badge=host, subtitle='30 秒自动刷新',
                              topbar_extra=test_btn)


_SETTINGS_FLASH = {
    'password changed': '管理员密码已更新',
    'password_wrong': '当前密码不正确',
    'password_mismatch': '两次输入的新密码不一致',
    'password_short': '新密码至少 8 位',
}


def render_settings(host, flash=''):
    meta = ensure_meta()
    admin_user = html.escape(str(meta.get('admin_user', 'admin')))
    alert = render_prefixed_alert(flash, _SETTINGS_FLASH)
    content = f'''{alert}
<div class="card mb-md">
  <div class="small">管理员账号：<code>{admin_user}</code></div>
</div>
<div class="card" style="max-width:520px;">
  <div class="k">修改管理员密码</div>
  <form method="post" action="/admin/change-password" class="inline-form" autocomplete="off">
    <label>当前密码</label><input name="current" type="password" required>
    <label class="mt-sm">新密码（至少 8 位）</label><input name="new" type="password" minlength="8" required>
    <label class="mt-sm">确认新密码</label><input name="confirm" type="password" minlength="8" required>
    <button class="btn mt-md" type="submit">更新密码</button>
  </form>
  <div class="small mt-sm faint">更新后将注销所有已登录会话（其它设备需重新登录），但本设备会保持登录。</div>
</div>'''
    return render_admin_shell('settings', '设置', content, badge=host)


def render_reset_logs(host, limit=300):
    from collections import deque
    rows = []
    try:
        with RESET_LOG_FILE.open('r', encoding='utf-8') as f:
            raw_lines = list(deque(f, maxlen=limit))
    except FileNotFoundError:
        raw_lines = []
    for line in reversed(raw_lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        t = html.escape(str(entry.get('time', '')))
        actor = html.escape(str(entry.get('actor', '')))
        ip = html.escape(str(entry.get('ip', '')))
        action = html.escape(_action_label(str(entry.get('action', ''))))
        target = html.escape(str(entry.get('target', '')))
        month = html.escape(str(entry.get('month', '')))
        before = entry.get('before', {})
        after = entry.get('after', {})
        if isinstance(before, dict) and 'total' in before:
            detail = f'{fmt_bytes(before.get("total", 0))} → {fmt_bytes(after.get("total", 0))}'
        else:
            detail = ''
        rows.append(f'<tr><td class="small">{t}</td><td>{actor}</td><td class="small">{ip}</td>'
                    f'<td>{action}</td><td>{target}</td><td class="small">{month}</td>'
                    f'<td class="small">{html.escape(detail)}</td></tr>')
    table = ''.join(rows) if rows else f'<tr><td colspan="7" class="empty">暂无日志记录</td></tr>'
    content = f'''<div class="card scroll-x" style="padding:0;overflow:hidden;">
  <div class="row" style="padding:14px 18px;justify-content:space-between;border-bottom:1px solid var(--line);">
    <div class="bold">最近清零记录</div>
    <div class="small">最近 {limit} 条 · 最新在上</div>
  </div>
  <table class="table"><thead><tr><th style="padding-left:18px;">时间</th><th>操作人</th><th>IP</th><th>操作</th><th>目标</th><th>月份</th><th style="padding-right:18px;">流量变化</th></tr></thead>
  <tbody>{table}</tbody></table>
</div>'''
    return render_admin_shell('logs', '清零日志', content, badge=host)


def _load_yaml_file(path):
    import yaml
    text = path.read_text(encoding='utf-8')
    return yaml.safe_load(text) or {}


def _dump_yaml(data):
    import yaml
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


def load_template_config():
    """Load the subscription template as a dict. Returns {} if missing."""
    if not TEMPLATE_FILE.exists():
        return {}
    return _load_yaml_file(TEMPLATE_FILE)


def save_template_config(data):
    """Save dict to the subscription template."""
    TEMPLATE_FILE.write_text(_dump_yaml(data), encoding='utf-8')


_CONFIG_FLASH = {
    'saved': '模板已保存，所有用户下次拉订阅将使用新配置',
    'invalid_json': 'JSON 格式错误，请检查语法',
    'empty': '配置内容不能为空',
    'load_failed': '加载配置文件失败',
}


def render_config_editor(host, flash=''):
    alert = render_prefixed_alert(flash, _CONFIG_FLASH)

    try:
        data = load_template_config()
        config_json = json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as e:
        config_json = '{}'
        if not flash:
            alert = render_alert(f'加载配置失败: {e}', 'err')

    content = f'''{alert}
<div class="card mb-md">
  <div class="small mb-sm">编辑订阅模板（JSON 格式）。保存后所有用户下次拉订阅即获得新配置，每个用户的密码和 UUID 由服务端从 users.json 自动注入。</div>
  <div class="small">模板文件：<code>{html.escape(str(TEMPLATE_FILE))}</code></div>
</div>
<div class="card">
  <form method="post" action="/admin/config/save" id="configForm">
    <div id="jsonError" class="json-error"></div>
    <textarea name="config_json" id="configEditor" class="code-area code-tall" spellcheck="false">{html.escape(config_json)}</textarea>
    <div class="row mt-md">
      <button class="btn" type="submit">保存模板</button>
      <button class="btn secondary" type="button" id="cfgFormat">格式化 JSON</button>
      <button class="btn ghost" type="button" id="cfgCollapse">折叠/展开</button>
    </div>
  </form>
</div>
<script>
(function(){{
  var editor = document.getElementById('configEditor');
  var errorDiv = document.getElementById('jsonError');
  function showError(msg) {{ errorDiv.textContent=msg; errorDiv.classList.add('visible'); editor.classList.add('invalid'); }}
  function clearError() {{ errorDiv.classList.remove('visible'); editor.classList.remove('invalid'); }}
  function validateJson() {{
    try {{ JSON.parse(editor.value); clearError(); return true; }}
    catch(e) {{ showError('JSON 语法错误: ' + e.message); return false; }}
  }}
  document.getElementById('cfgFormat').addEventListener('click', function() {{
    try {{ editor.value = JSON.stringify(JSON.parse(editor.value), null, 2); clearError(); }}
    catch(e) {{ showError('JSON 语法错误: ' + e.message); }}
  }});
  document.getElementById('cfgCollapse').addEventListener('click', function() {{
    try {{
      var obj = JSON.parse(editor.value);
      var isCompact = !editor.value.includes('\\n');
      editor.value = isCompact ? JSON.stringify(obj, null, 2) : JSON.stringify(obj);
    }} catch(e) {{}}
  }});
  editor.addEventListener('keydown', function(e) {{
    if (e.key !== 'Tab') return;
    e.preventDefault();
    var s=this.selectionStart, t=this.selectionEnd;
    this.value = this.value.substring(0,s) + '  ' + this.value.substring(t);
    this.selectionStart = this.selectionEnd = s + 2;
  }});
  var validateTimer;
  editor.addEventListener('input', function() {{
    clearTimeout(validateTimer);
    validateTimer = setTimeout(validateJson, 500);
  }});
  document.getElementById('configForm').addEventListener('submit', function(e) {{
    if (!validateJson()) {{ e.preventDefault(); alert('JSON 格式错误，请修正后再保存'); }}
  }});
}})();
</script>'''
    return render_admin_shell('config', '订阅模板配置', content, badge=host)


def load_template_rules():
    """Load rules list from the subscription template."""
    import yaml
    if not TEMPLATE_FILE.exists():
        return []
    text = TEMPLATE_FILE.read_text(encoding='utf-8')
    data = yaml.safe_load(text)
    return data.get('rules', [])


def save_template_rules(rules):
    """Replace the rules section in the subscription template."""
    text = TEMPLATE_FILE.read_text(encoding='utf-8')
    lines = text.split('\n')
    start = None
    end = len(lines)
    for i, line in enumerate(lines):
        if start is None and re.match(r'^rules\s*:', line):
            start = i
        elif start is not None and line and not line[0].isspace() and not line.startswith('#'):
            end = i
            break
    new_rule_lines = ['# 6. 规则', 'rules:']
    for r in rules:
        new_rule_lines.append(f"  - '{r}'")
    if start is None:
        result = lines + [''] + new_rule_lines
    else:
        cut = start - 1 if start > 0 and lines[start - 1].startswith('#') else start
        result = lines[:cut] + new_rule_lines + lines[end:]
    TEMPLATE_FILE.write_text('\n'.join(result) + ('\n' if not result[-1].endswith('\n') else ''), encoding='utf-8')


def _parse_clash_rule(rule_str):
    """Parse 'TYPE,value,action[,extra]' into display parts."""
    parts = rule_str.split(',', 2)
    if len(parts) < 2:
        return rule_str, '', '', ''
    rtype = parts[0]
    if rtype == 'MATCH':
        return 'MATCH', '全部', parts[1] if len(parts) > 1 else '', ''
    if len(parts) == 2:
        return rtype, parts[1], '', ''
    # parts[2] may be "action" or "action,no-resolve"
    rest = parts[2].split(',', 1)
    action = rest[0]
    extra = rest[1] if len(rest) > 1 else ''
    return rtype, parts[1], action, extra


_RULE_TYPE_LABELS = {
    'DOMAIN-SUFFIX': '域名后缀', 'DOMAIN-KEYWORD': '域名关键词', 'DOMAIN': '完整域名',
    'IP-CIDR': 'IP 段', 'IP-CIDR6': 'IPv6 段', 'GEOIP': 'GeoIP',
    'RULE-SET': '规则集', 'MATCH': '兜底',
}
_ACTION_LABELS = {'DIRECT': '直连', 'REJECT': '拦截'}


_RULES_FLASH = {
    'rule_added': '规则已添加，客户端更新订阅后生效',
    'rule_deleted': '规则已删除，客户端更新订阅后生效',
    'pattern_empty': '匹配值不能为空',
    'invalid_rule_type': '无效的规则类型',
    'invalid_index': '无效的规则序号',
    'index_out_of_range': '规则序号超出范围',
    'raw_saved': '全部规则已保存，客户端更新订阅后生效',
    'raw_empty': '规则不能为空',
}


def render_rules(host, flash=''):
    rules = load_template_rules()
    alert = render_prefixed_alert(flash, _RULES_FLASH)

    rows = ''
    for i, rule_str in enumerate(rules):
        rtype, val, action, extra = _parse_clash_rule(rule_str)
        type_label = _RULE_TYPE_LABELS.get(rtype, rtype)
        action_label = _ACTION_LABELS.get(action, action)
        extra_tag = f' <span class="small">({html.escape(extra)})</span>' if extra else ''
        is_system = rtype in ('RULE-SET', 'GEOIP', 'MATCH')
        del_btn = ''
        if not is_system:
            del_btn = (
                f'<form method="post" action="/admin/rules/delete" class="inline-form-row" data-action="delete-rule">'
                f'<input type="hidden" name="index" value="{i}">'
                f'<button class="btn danger-btn btn-sm" type="submit">删除</button>'
                f'</form>'
            )
        tr_class = ' class="system-row"' if is_system else ''
        rows += (
            f'<tr{tr_class}><td>{i + 1}</td><td>{html.escape(type_label)}</td>'
            f'<td class="break">{html.escape(val)}</td>'
            f'<td>{html.escape(action_label)}{extra_tag}</td>'
            f'<td>{del_btn}</td></tr>'
        )

    rules_text = html.escape('\n'.join(rules))

    content = f'''{alert}
<div class="card mb-md">
  <div class="small">自定义规则优先级高于规则集，从上到下依次匹配。灰色行为内置规则集，不可删除。</div>
</div>
<div class="card scroll-x" style="padding:0;overflow:hidden;">
  <table class="table"><thead><tr><th style="padding-left:18px;width:50px;">#</th><th>类型</th><th>匹配</th><th>动作</th><th style="padding-right:18px;width:90px;">操作</th></tr></thead>
  <tbody>{rows or '<tr><td colspan="5" class="empty">暂无规则</td></tr>'}</tbody></table>
</div>

<div class="card mt-md">
  <div class="bold mb-md">添加自定义规则</div>
  <form method="post" action="/admin/rules/add" class="inline-form">
    <div class="grid grid-2">
      <div><label>规则类型</label><select name="rule_type">
        <option value="DOMAIN-SUFFIX">DOMAIN-SUFFIX（域名后缀）</option>
        <option value="DOMAIN-KEYWORD">DOMAIN-KEYWORD（域名关键词）</option>
        <option value="DOMAIN">DOMAIN（完整域名）</option>
        <option value="IP-CIDR">IP-CIDR（IP 段）</option>
      </select></div>
      <div><label>匹配值</label><input name="pattern" required placeholder="example.com 或 10.0.0.0/8"></div>
      <div><label>动作</label><select name="action">
        <option value="DIRECT">直连 (DIRECT)</option>
        <option value="🚀 节点选择">代理 (🚀 节点选择)</option>
        <option value="REJECT">拦截 (REJECT)</option>
      </select></div>
      <div><label>附加选项</label><select name="extra">
        <option value="">无</option>
        <option value="no-resolve">no-resolve（IP 规则跳过 DNS 解析）</option>
      </select></div>
    </div>
    <div class="row mt-md">
      <button class="btn" type="submit">添加规则（插入到最前）</button>
    </div>
  </form>
</div>

<div class="card mt-md">
  <details>
    <summary>直接编辑全部规则</summary>
    <form method="post" action="/admin/rules/raw" class="inline-form mt-md">
      <div class="small mb-sm">每行一条规则，格式：<code>TYPE,匹配值,动作</code>。保存后同步到所有订阅模板。</div>
      <textarea name="rules_raw" class="code-area code-med">{rules_text}</textarea>
      <div class="row mt-md">
        <button class="btn" type="submit">保存全部规则</button>
      </div>
    </form>
  </details>
</div>
<script>
document.addEventListener('submit', function(ev){{
  var f = ev.target;
  if (f && f.tagName==='FORM' && f.dataset.action==='delete-rule') {{
    if (!confirm('确认删除此规则？')) ev.preventDefault();
  }}
}});
</script>'''
    return render_admin_shell('rules', '订阅路由规则', content, badge=f'{len(rules)} 条')


def _handle_legacy_daily_redirect(handler):
    """Permanent redirect from old /admin/daily to /admin/usage."""
    handler.redirect("/admin/usage", status=301)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def parse_form(self):
        length = int(self.headers.get('Content-Length', '0') or 0)
        body = self.rfile.read(length).decode('utf-8', errors='ignore')
        return parse_qs(body)

    def send_response_body(self, code, body, ctype='text/plain; charset=utf-8', send_body=True, extra_headers=None):
        data = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        if 'text/html' in ctype:
            self.send_header('Cache-Control', 'no-store')
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _serve_static(self, payload_bytes, etag, ctype, send_payload):
        """Serve a cacheable static asset with ETag-aware 304 handling."""
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'public, max-age=86400')
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(payload_bytes)))
        self.send_header('Cache-Control', 'public, max-age=86400')
        self.send_header('ETag', etag)
        self.end_headers()
        if send_payload:
            self.wfile.write(payload_bytes)

    def redirect(self, to, cookie=None, status=302):
        self.send_response(status)
        self.send_header('Location', to)
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()

    def get_admin_actor(self):
        q = parse_qs(urlparse(self.path).query)
        token = (q.get('token') or [''])[0]
        meta = ensure_meta()
        admin_token = str(meta.get('admin_token') or '')
        if token and hmac.compare_digest(token, admin_token):
            return 'token-admin'
        sid = parse_cookies(self).get('sid', '')
        sessions = get_sessions()
        if sid in sessions:
            return sessions[sid].get('user', 'admin')
        return 'unknown'

    def write_reset_log(self, actor, action, target, before, after):
        line = {
            'time': datetime.utcnow().isoformat(timespec='seconds') + 'Z',
            'actor': actor,
            'ip': self.client_address[0] if self.client_address else '',
            'action': action,
            'target': target,
            'month': month_key(),
            'before': before,
            'after': after,
        }
        RESET_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with RESET_LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(json.dumps(line, ensure_ascii=True) + '\n')

    def handle_get(self, send_payload=True):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_qs(parsed.query)
        host = sanitize_host(self.headers.get('Host', '127.0.0.1'))
        base_url = safe_base_url(host, self.headers.get('X-Forwarded-Proto', 'http'))

        if path == '/static/style.css':
            self._serve_static(BASE_CSS_BYTES, BASE_CSS_ETAG, 'text/css; charset=utf-8', send_payload)
            return

        if path == '/static/admin-poll.js':
            self._serve_static(ADMIN_POLL_JS_BYTES, ADMIN_POLL_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload)
            return

        if path == '/static/usage.js':
            self._serve_static(USAGE_JS_BYTES, USAGE_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload)
            return

        if path == '/':
            self.send_response_body(200, render_home(host), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/login':
            self.send_response_body(200, render_login(host), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/logout':
            sid = parse_cookies(self).get('sid', '')
            delete_session(sid)
            self.redirect('/login', cookie='sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
            return

        if path.startswith('/sub/'):
            user = path.split('/', 2)[2]
            token = (q.get('token') or [''])[0]
            cfg = check_user_token(user, token)
            if not cfg:
                self.send_response_body(403, '无权限访问', send_body=send_payload)
                return
            if cfg.get('disabled'):
                self.send_response_body(403, '账号已停用，请联系管理员', send_body=send_payload)
                return
            yml = build_yaml(user, str(cfg.get('sub_token') or ''))
            tx, rx, used = scaled_usage_for_user(user)
            total = user_total_quota(cfg)
            payload = yml.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/yaml; charset=utf-8')
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{user}.yaml")
            self.send_header('profile-update-interval', '24')
            self.send_header('subscription-userinfo', f'upload={tx}; download={rx}; total={total}; expire=0')
            self.send_header('x-usage-total-bytes', str(used))
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if send_payload:
                self.wfile.write(payload)
            return

        if path.startswith('/panel/') and path.endswith('.json'):
            user = path[len('/panel/'):-len('.json')]
            token = (q.get('token') or [''])[0]
            cfg = check_user_token(user, token)
            if not cfg:
                self.send_response_body(403, '{"error":"forbidden"}',
                                        'application/json; charset=utf-8', send_payload)
                return
            if cfg.get('disabled'):
                self.send_response_body(403, '{"error":"disabled"}',
                                        'application/json; charset=utf-8', send_payload)
                return
            payload = _build_panel_json_payload(user, cfg, now=local_now())
            self.send_response_body(200, json.dumps(payload),
                                    'application/json; charset=utf-8', send_payload)
            return

        if path.startswith('/panel/'):
            user = path.split('/', 2)[2]
            token = (q.get('token') or [''])[0]
            cfg = check_user_token(user, token)
            if not cfg:
                self.send_response_body(403, '无权限访问', send_body=send_payload)
                return
            self.send_response_body(200, render_user_panel(host, base_url, user, token, cfg), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_admin(host, base_url, flash=flash), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/logs':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            self.send_response_body(200, render_reset_logs(host), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/usage':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            self.send_response_body(
                200, render_usage_page(host),
                'text/html; charset=utf-8', send_payload,
            )
            return

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

        if path == '/admin/usage.csv':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            window = (q.get('window') or ['cycle'])[0]
            now = local_now()
            body = _build_usage_csv(now=now, window=window)
            filename = f'usage-{window}-{now.strftime("%Y%m%d")}.csv'
            self.send_response_body(
                200, body,
                'text/csv; charset=utf-8', send_payload,
                extra_headers={'Content-Disposition': f'attachment; filename="{filename}"'},
            )
            return

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

        if path == '/admin/daily':
            _handle_legacy_daily_redirect(self)
            return

        if path == '/admin/health':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_health(host, flash=flash),
                                    'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/settings':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_settings(host, flash=flash),
                                    'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/config':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_config_editor(host, flash=flash), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/rules':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_rules(host, flash=flash), 'text/html; charset=utf-8', send_payload)
            return

        self.send_response_body(404, '页面不存在', send_body=send_payload)

    def do_GET(self):
        self.handle_get(send_payload=True)

    def do_HEAD(self):
        self.handle_get(send_payload=False)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        form = self.parse_form()
        meta = ensure_meta()

        if path.startswith('/panel/') and path.endswith('/rotate-token'):
            # User self-service: rotate sub_token. Auth is the current token
            # itself (passed in the form), so a compromised link can be
            # invalidated without admin involvement. Path is structured to
            # avoid colliding with the GET handler for /panel/<user>.
            user = path[len('/panel/'):-len('/rotate-token')]
            posted = (form.get('token') or [''])[0]
            cfg = check_user_token(user, posted)
            if not cfg:
                self.send_response_body(403, '无权限访问')
                return
            new_token = secrets.token_urlsafe(18)
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if not isinstance(users.get(user), dict):
                    self.send_response_body(404, '用户不存在')
                    return
                users[user]['sub_token'] = new_token
                save_json(USERS_FILE, users)
            # Drop any live hysteria session on the old token (= password) so a
            # compromised link can't keep transferring after rotation. Done
            # outside the file lock to avoid holding it across network I/O.
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            hy_kick([user])
            self.redirect(f'/panel/{user}?token={new_token}')
            return

        if path == '/login':
            ip = self.client_address[0] if self.client_address else ''
            host = sanitize_host(self.headers.get('Host', '127.0.0.1'))
            if _is_rate_limited(ip):
                self.send_response_body(200, render_login(host, msg='登录尝试过于频繁，请 1 小时后再试'), 'text/html; charset=utf-8', True)
                return
            user = (form.get('username') or [''])[0].strip()
            passwd = (form.get('password') or [''])[0]
            stored_hash = str(meta.get('admin_pass_hash') or '')
            ok = (user == meta.get('admin_user') and stored_hash and verify_secret(passwd, stored_hash))
            if ok:
                _clear_failures(ip)
                sid = create_session('admin')
                self.redirect('/admin?msg=login+success', cookie=f'sid={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax')
                return
            _record_failure(ip)
            self.send_response_body(200, render_login(host, msg='用户名或密码错误'), 'text/html; charset=utf-8', True)
            return

        if path == '/admin/update':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            new_password = (form.get('password') or [''])[0].strip()
            max_devices = parse_int_field((form.get('max_devices') or ['2'])[0], 2, 1, 100)
            quota_gb = parse_int_field((form.get('quota_gb') or ['150'])[0], 150, 1, 10240)
            guest = 'guest' in form
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect('/admin?msg=user+not+found')
                    return
                cfg = users[username]
                if new_password:
                    cfg['password_hash'] = hash_secret(new_password)
                cfg.pop('password', None)
                cfg['max_devices'] = max(1, max_devices)
                cfg['monthly_quota_bytes'] = max(1, quota_gb) * 1024 * 1024 * 1024
                cfg['metered'] = guest
                cfg['guest'] = guest
                if not cfg.get('sub_token'):
                    cfg['sub_token'] = secrets.token_urlsafe(18)
                users[username] = cfg
                save_json(USERS_FILE, users)
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            self.redirect('/admin?msg=updated+' + username)
            return

        if path == '/admin/add':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            password = (form.get('password') or [''])[0].strip()
            quota_gb = parse_int_field((form.get('quota_gb') or ['150'])[0], 150, 1, 10240)
            guest = 'guest' in form
            reset_token = 'reset_token' in form
            if not username:
                self.redirect('/admin?msg=user+empty')
                return
            if not is_valid_username(username):
                self.redirect('/admin?msg=err:username_invalid')
                return
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username in users and not reset_token:
                    self.redirect('/admin?msg=user_exists_use_reset_token')
                    return
                existing = users.get(username, {})
                existing_token = existing.get('sub_token')
                token = secrets.token_urlsafe(18) if (reset_token or not existing_token) else existing_token
                vless_uuid = str(existing.get('vless_uuid') or '').strip() or str(uuid.uuid4())
                entry = {
                    'metered': guest,
                    'guest': guest,
                    'max_devices': 2,
                    'monthly_quota_bytes': max(1, quota_gb) * 1024 * 1024 * 1024,
                    'sub_token': token,
                    'vless_uuid': vless_uuid,
                    'disabled': bool(existing.get('disabled')),
                }
                if password:
                    entry['password_hash'] = hash_secret(password)
                elif existing.get('password_hash'):
                    entry['password_hash'] = existing.get('password_hash')
                users[username] = entry
                save_json(USERS_FILE, users)
            # Re-syncing a suspended user back into xray would undo the suspend.
            # xray/tuic I/O runs outside the file lock.
            if not entry['disabled'] and xray_config.sync_user(username, vless_uuid):
                xray_config.reload_async()
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            self.redirect('/admin?msg=created+' + username)
            return

        if path in ('/admin/cycle-config', '/admin/settlement-day'):
            if not is_logged_in(self):
                self.redirect('/login')
                return
            try:
                day = int((form.get('day') or [''])[0])
            except (ValueError, TypeError):
                self.redirect('/admin?msg=err:settlement_invalid')
                return
            if day < 1 or day > 28:
                self.redirect('/admin?msg=err:settlement_invalid')
                return
            raw_len = (form.get('length') or [''])[0].strip()
            length = None
            if raw_len:
                try:
                    length = int(raw_len)
                except (ValueError, TypeError):
                    self.redirect('/admin?msg=err:cycle_length_invalid')
                    return
                if length < CYCLE_LENGTH_MIN or length > CYCLE_LENGTH_MAX:
                    self.redirect('/admin?msg=err:cycle_length_invalid')
                    return
            meta = load_json(META_FILE, {})
            meta['settlement_day'] = day
            if length is not None:
                meta['cycle_length_days'] = length
            # Re-anchor the cycle calendar so subsequent N-day blocks start
            # cleanly from the latest settlement_day (avoids the cycle "jumping"
            # mid-period after a config change).
            meta['cycle_anchor_date'] = _settlement_anchor_date(local_now(), day).strftime('%Y-%m-%d')
            save_json(META_FILE, meta)
            self.redirect(f'/admin?msg=settlement+{day}')
            return

        if path == '/admin/reset-usage':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            users = load_json(USERS_FILE, {})
            if username not in users:
                self.redirect('/admin?msg=user+not+found')
                return
            with usage_lock():
                now = local_now()
                usage = load_json(USAGE_FILE, {})
                mk = month_key(now)
                usage.setdefault(mk, {})
                tx, rx, total = usage_for_user(username, now=now)
                before = {'tx': tx, 'rx': rx, 'total': total}
                usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
                after = {'tx': 0, 'rx': 0, 'total': 0}
                save_json(USAGE_FILE, usage)
                _zero_cycle_daily_hourly_for([username], now=now)
                # Clear quota alert dedup so subsequent crossings re-fire (ADR-0001).
                alert_state = alerts.load_state()
                alerts.clear_quota_dedup_for(alert_state, [username])
                alerts.save_state(alert_state)
            self.write_reset_log(self.get_admin_actor(), 'reset_usage_user', username, before, after)
            self.redirect('/admin?msg=reset+usage+' + username)
            return

        if path == '/admin/refresh-usage':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            users = load_json(USERS_FILE, {})
            if username not in users:
                self.redirect('/admin?msg=user+not+found')
                return
            with usage_lock():
                now = local_now()
                usage = load_json(USAGE_FILE, {})
                mk = month_key(now)
                usage.setdefault(mk, {})
                tx, rx, total = usage_for_user(username, now=now)
                before = {'tx': tx, 'rx': rx, 'total': total}
                # Bank the cleared bytes into the preserved bucket so the
                # dashboard's '本周期总流量' stays put after this refresh.
                add_preserved_for_user(username, tx, rx, total, now=now)
                usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
                after = {'tx': 0, 'rx': 0, 'total': 0}
                save_json(USAGE_FILE, usage)
                _zero_cycle_daily_hourly_for([username], now=now)
                alert_state = alerts.load_state()
                alerts.clear_quota_dedup_for(alert_state, [username])
                alerts.save_state(alert_state)
            self.write_reset_log(self.get_admin_actor(), 'refresh_usage_user', username, before, after)
            self.redirect('/admin?msg=refresh+usage+' + username)
            return

        if path == '/admin/change-password':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            current = (form.get('current') or [''])[0]
            new = (form.get('new') or [''])[0]
            confirm = (form.get('confirm') or [''])[0]
            stored_hash = str(meta.get('admin_pass_hash') or '')
            if not (stored_hash and verify_secret(current, stored_hash)):
                self.redirect('/admin/settings?msg=err:password_wrong')
                return
            if len(new) < 8:
                self.redirect('/admin/settings?msg=err:password_short')
                return
            if new != confirm:
                self.redirect('/admin/settings?msg=err:password_mismatch')
                return
            meta_now = load_json(META_FILE, {})
            meta_now['admin_pass_hash'] = hash_secret(new)
            meta_now.pop('admin_pass', None)
            save_json(META_FILE, meta_now)
            # Revoke ALL existing admin sessions (a stolen sid is now dead),
            # then mint a fresh session for this device so the admin stays
            # logged in here. Mirrors the /login success cookie pattern.
            save_json(SESSIONS_FILE, {})
            sid = create_session('admin')
            self.redirect('/admin/settings?msg=password+changed',
                          cookie=f'sid={sid}; Path=/; Max-Age={SESSION_TTL}; HttpOnly; SameSite=Lax')
            return

        if path == '/admin/test-alert':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            cfg = alerts.load_config()
            if not isinstance(cfg, dict) or not (cfg.get('telegram') or cfg.get('webhook')):
                self.redirect('/admin/health?msg=err:alert_no_channels')
                return
            # Fire on a background thread; we redirect immediately rather than
            # block the request on outbound HTTP. Delivery is confirmed at the
            # receiver, so we report "dispatched" rather than guaranteed-sent.
            _fire_test_alert(cfg, self.get_admin_actor())
            self.redirect('/admin/health?msg=alert+dispatched')
            return

        if path == '/admin/rotate-token':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect('/admin?msg=user+not+found')
                    return
                if not isinstance(users.get(username), dict):
                    self.redirect('/admin?msg=user+not+found')
                    return
                users[username]['sub_token'] = secrets.token_urlsafe(18)
                save_json(USERS_FILE, users)
            # Drop any live hysteria session on the old token (= password) so a
            # connected attacker is forced off. Done outside the file lock.
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            hy_kick([username])
            self.write_reset_log(self.get_admin_actor(), 'rotate_token', username, {}, {})
            self.redirect('/admin?msg=rotated+' + username)
            return

        if path == '/admin/toggle-user':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect('/admin?msg=user+not+found')
                    return
                if not isinstance(users.get(username), dict):
                    self.redirect('/admin?msg=user+not+found')
                    return
                disable = not bool(users[username].get('disabled'))
                users[username]['disabled'] = disable
                vless_uuid = str(users[username].get('vless_uuid') or '').strip()
                save_json(USERS_FILE, users)
            # xray + tuic + hysteria side-effects run outside the file lock.
            if disable:
                # xray VLESS authenticates by UUID at the xray layer (not via
                # auth_backend), so disabling must also pull the user's xray
                # client entry; TUIC has the same static-user-map property.
                if xray_config.remove_user(username):
                    xray_config.reload_async()
                if tuic_config.sync_all(users=users):
                    tuic_config.reload_async()
                hy_kick([username])
                self.write_reset_log(self.get_admin_actor(), 'disable_user', username, {}, {})
                self.redirect('/admin?msg=disabled+' + username)
            else:
                if vless_uuid and xray_config.sync_user(username, vless_uuid):
                    xray_config.reload_async()
                if tuic_config.sync_all(users=users):
                    tuic_config.reload_async()
                self.write_reset_log(self.get_admin_actor(), 'enable_user', username, {}, {})
                self.redirect('/admin?msg=enabled+' + username)
            return

        if path == '/admin/reset-usage-all':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            with usage_lock():
                now = local_now()
                usage = load_json(USAGE_FILE, {})
                mk = month_key(now)
                usage.setdefault(mk, {})
                before_all = {}
                users = load_json(USERS_FILE, {})
                for username in users.keys():
                    tx, rx, total = usage_for_user(username, now=now)
                    before_all[username] = {'tx': tx, 'rx': rx, 'total': total}
                    usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
                save_json(USAGE_FILE, usage)
                _zero_cycle_daily_hourly_for(list(users.keys()), now=now)
                # Clear quota alert dedup for all users (ADR-0001).
                alert_state = alerts.load_state()
                alerts.clear_quota_dedup_for(alert_state, list(users.keys()))
                alerts.save_state(alert_state)
            self.write_reset_log(
                self.get_admin_actor(),
                'reset_usage_all',
                'all_users',
                before_all,
                {u: {'tx': 0, 'rx': 0, 'total': 0} for u in users.keys()},
            )
            self.redirect('/admin?msg=reset+usage+all')
            return

        if path == '/admin/delete':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect('/admin?msg=user+not+found')
                    return
                del users[username]
                save_json(USERS_FILE, users)
            # xray + hysteria side-effects run outside the file lock.
            hy_kick([username])
            if xray_config.remove_user(username):
                xray_config.reload_async()
            self.redirect('/admin?msg=deleted+' + username)
            return

        if path == '/admin/config/save':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            raw = (form.get('config_json') or [''])[0].strip()
            if not raw:
                self.redirect('/admin/config?msg=err:empty')
                return
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                self.redirect('/admin/config?msg=err:invalid_json')
                return
            save_template_config(data)
            self.redirect('/admin/config?msg=saved')
            return

        if path == '/admin/rules/add':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            rule_type = (form.get('rule_type') or ['DOMAIN-SUFFIX'])[0]
            pattern = (form.get('pattern') or [''])[0].strip()
            action = (form.get('action') or ['DIRECT'])[0]
            extra = (form.get('extra') or [''])[0]
            if not pattern:
                self.redirect('/admin/rules?msg=err:pattern_empty')
                return
            if rule_type not in ('DOMAIN-SUFFIX', 'DOMAIN-KEYWORD', 'DOMAIN', 'IP-CIDR'):
                self.redirect('/admin/rules?msg=err:invalid_rule_type')
                return
            rule_str = f'{rule_type},{pattern},{action}'
            if extra:
                rule_str += f',{extra}'
            rules = load_template_rules()
            rules.insert(0, rule_str)
            save_template_rules(rules)
            self.redirect('/admin/rules?msg=rule_added')
            return

        if path == '/admin/rules/delete':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            try:
                idx = int((form.get('index') or [''])[0])
            except (ValueError, IndexError):
                self.redirect('/admin/rules?msg=err:invalid_index')
                return
            rules = load_template_rules()
            if idx < 0 or idx >= len(rules):
                self.redirect('/admin/rules?msg=err:index_out_of_range')
                return
            rules.pop(idx)
            save_template_rules(rules)
            self.redirect('/admin/rules?msg=rule_deleted')
            return

        if path == '/admin/rules/raw':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            raw = (form.get('rules_raw') or [''])[0]
            rules = [line.strip() for line in raw.splitlines() if line.strip()]
            if not rules:
                self.redirect('/admin/rules?msg=err:raw_empty')
                return
            save_template_rules(rules)
            self.redirect('/admin/rules?msg=raw_saved')
            return

        self.send_response_body(404, '页面不存在')


if __name__ == '__main__':
    ensure_meta()
    migrate_plaintext_passwords()
    migrate_admin_password()
    srv = ThreadingHTTPServer(LISTEN, Handler)
    srv.serve_forever()
