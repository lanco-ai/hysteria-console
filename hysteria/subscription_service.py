#!/usr/bin/env python3
import html
import base64
import hashlib
import hmac
import json
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
import cost_calibrator
import cycle as cycle_util
import health
import health_widgets
import http_utils
import incident_console
import state_store
import subscription_profiles as profile_defs
import tuic_config
import usage_dashboard
import user_compat
import xray_config
from display import DISPLAY_MULTIPLIER, fmt_bytes
from timeutil import billing_cycle_key, local_now
from contextlib import contextmanager
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

USERS_FILE = Path('/root/hysteria/users.json')
USAGE_FILE = Path('/root/hysteria/state/usage.json')
USAGE_DAILY_FILE = Path('/root/hysteria/state/usage_daily.json')
USAGE_HOURLY_FILE = Path('/root/hysteria/state/usage_hourly.json')
PROTOCOL_USAGE_HOURLY_FILE = Path('/root/hysteria/state/protocol_usage_hourly.json')
COST_CALIBRATION_FILE = Path('/root/hysteria/state/cost_calibration.json')
DISPLAY_MULTIPLIER_STATE_FILE = Path('/root/hysteria/state/display_multiplier.json')
MULTIPLIER_AUTO_POLICY_FILE = Path('/root/hysteria/state/display_multiplier_auto.json')
USAGE_PRESERVED_FILE = Path('/root/hysteria/state/usage_preserved.json')
HOURLY_RETENTION_HOURS = 168
ONLINE_FILE = Path('/root/hysteria/state/online.json')
META_FILE = Path('/root/hysteria/subscription_meta.json')
TEMPLATE_FILE = Path('/root/hysteria/template.yaml')
BACKUP_DIR = Path('/root/hysteria/backups')
XRAY_CONFIG_FILE = Path('/usr/local/etc/xray/config.json')
SESSIONS_FILE = Path('/root/hysteria/state/panel_sessions.json')
RESET_LOG_FILE = Path('/root/hysteria/state/usage_reset.log')
USAGE_LOCK_FILE = Path('/root/hysteria/state/usage.lock')
TEMPLATE_LOCK_FILE = Path('/root/hysteria/state/template.lock')
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
MAX_FORM_BYTES = http_utils.MAX_FORM_BYTES

_STATIC_DIR = Path(__file__).resolve().parent
BASE_CSS_BYTES = (_STATIC_DIR / 'admin.css').read_bytes()
BASE_CSS_ETAG = '"' + hashlib.sha1(BASE_CSS_BYTES).hexdigest()[:16] + '"'
ADMIN_POLL_JS_BYTES = (_STATIC_DIR / 'admin_poll.js').read_bytes()
ADMIN_POLL_JS_ETAG = '"' + hashlib.sha1(ADMIN_POLL_JS_BYTES).hexdigest()[:16] + '"'
USAGE_JS_BYTES = (_STATIC_DIR / 'usage.js').read_bytes()
USAGE_JS_ETAG = '"' + hashlib.sha1(USAGE_JS_BYTES).hexdigest()[:16] + '"'


def load_json(path, default):
    return state_store.load_json(path, default)


def save_json(path, data):
    """Atomic write: serialize to a sibling temp file, fsync, then rename. Prevents
    truncated state files (which the readers fall back to `{}` on, silently losing
    the cycle/state tracking)."""
    state_store.save_json(path, data)


def save_text_atomic(path, text):
    """Atomic UTF-8 text write for operator-edited config files."""
    state_store.save_text_atomic(path, text)


@contextmanager
def usage_lock():
    with state_store.file_lock(USAGE_LOCK_FILE):
        yield


@contextmanager
def template_lock():
    with state_store.file_lock(TEMPLATE_LOCK_FILE):
        yield


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


def parse_date_field(raw):
    raw = str(raw or '').strip()
    if not raw:
        return ''
    try:
        datetime.strptime(raw, '%Y-%m-%d')
    except ValueError:
        return ''
    return raw


def parse_note_field(raw):
    return str(raw or '').strip()[:200]


def sanitize_host(raw_host):
    return http_utils.sanitize_host(raw_host)


def safe_base_url(host, forwarded_proto, forwarded_port=None):
    return http_utils.safe_base_url(host, forwarded_proto, forwarded_port)


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


SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX


def get_settlement_day():
    """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
    return cycle_util.settlement_day_from_meta(load_json(META_FILE, {}) or {})


def get_cycle_length_days():
    """Length of one billing cycle, in days. Editable via /admin/cycle-config.
    Cycles roll exactly every N days from `cycle_anchor_date` (or, if absent,
    from the most recent settlement_day on/before today)."""
    return cycle_util.cycle_length_from_meta(load_json(META_FILE, {}) or {})


def _settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now.date().
    Falls back through prev month / Feb edge cases."""
    return cycle_util.settlement_anchor_date(now, settlement_day)


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
    return cycle_util.cycle_anchor_date(now, meta)


def cycle_start_for(now, day=None, length=None, anchor=None):
    """Datetime at 00:00 local of the current cycle's start.

    For cycle_length_days==30 (default) the result matches the pre-existing
    calendar-month behaviour as long as the anchor is the most recent
    settlement_day. For shorter/longer N, cycles roll exactly every N days
    from the anchor — they intentionally do not re-align to calendar months."""
    meta = load_json(META_FILE, {}) or {}
    return cycle_util.cycle_start_for(now, day=day, length=length, anchor=anchor, meta=meta)


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
    return cycle_util.cycle_days(now, meta=load_json(META_FILE, {}) or {})


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
    return user_compat.total_quota_bytes(user_cfg)


def base_quota_bytes(user_cfg):
    return int((user_cfg or {}).get('monthly_quota_bytes', 0) or 0)


def quota_extra_gb(user_cfg):
    return int(round(user_compat.quota_extra_bytes(user_cfg) / 1024 / 1024 / 1024))


def user_expiry_state(user_cfg, *, today=None):
    today = today or local_now().date()
    exp = user_compat.expiry_date(user_cfg)
    if exp is None:
        return {'expires_at': '', 'expired': False, 'days_left': None, 'label': '不限期'}
    days_left = (exp - today).days
    if days_left < 0:
        label = f'已过期 {abs(days_left)} 天'
    elif days_left == 0:
        label = '今日到期'
    else:
        label = f'{days_left} 天后到期'
    return {
        'expires_at': exp.strftime('%Y-%m-%d'),
        'expired': days_left < 0,
        'days_left': days_left,
        'label': label,
    }



NODE_GROUP = profile_defs.NODE_GROUP
AUTO_GROUP = profile_defs.AUTO_GROUP
GITHUB_GROUP = profile_defs.GITHUB_GROUP
GPT_GROUP = profile_defs.GPT_GROUP
GOOGLE_GROUP = profile_defs.GOOGLE_GROUP
TELEGRAM_GROUP = profile_defs.TELEGRAM_GROUP
HY2_UDP_PROXY = profile_defs.HY2_UDP_PROXY
TUIC_UDP_PROXY = profile_defs.TUIC_UDP_PROXY
VLESS_TCP_PROXY = profile_defs.VLESS_TCP_PROXY
VLESS_BACKUP_PROXY = profile_defs.VLESS_BACKUP_PROXY
DIRECT_IP_RULE = profile_defs.DIRECT_IP_RULE
NOISY_TIMEOUT_IP_RULE = profile_defs.NOISY_TIMEOUT_IP_RULE
DIRECT_IP_RULES = profile_defs.DIRECT_IP_RULES
SUBSCRIPTION_PROFILES = profile_defs.SUBSCRIPTION_PROFILES
SUBSCRIPTION_PROFILE_ORDER = profile_defs.SUBSCRIPTION_PROFILE_ORDER
RULE_PACKS = profile_defs.RULE_PACKS
RULE_PACK_ORDER = profile_defs.RULE_PACK_ORDER


def _subscription_profile_context():
    return profile_defs.SubscriptionProfileContext(
        template_file=TEMPLATE_FILE,
        users_file=USERS_FILE,
        load_json=load_json,
    )


def normalize_subscription_profile(raw):
    return profile_defs.normalize_subscription_profile(raw)


def apply_subscription_profile(cfg, profile):
    return profile_defs.apply_subscription_profile(cfg, profile)


def render_profile_yaml(text, profile):
    return profile_defs.render_profile_yaml(text, profile)


def build_yaml(username, auth_secret, profile='default', *, generated_at=None):
    return profile_defs.build_yaml(
        _subscription_profile_context(), username, auth_secret, profile=profile,
        generated_at=generated_at,
    )


def subscription_template_mtime():
    return profile_defs.template_mtime_iso(TEMPLATE_FILE)

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


def is_secure_request(handler):
    return http_utils.is_secure_request(handler)


def is_same_origin_post(handler):
    return http_utils.is_same_origin_post(handler)


def session_cookie(sid, *, max_age=SESSION_TTL, secure=False):
    return http_utils.session_cookie(sid, max_age=max_age, secure=secure)


def clear_session_cookie(*, secure=False):
    return http_utils.clear_session_cookie(secure=secure)


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
        f'<meta name="theme-color" content="#07101f">'
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
    ('incidents', '/admin/incidents', '事故处理', 'pulse'),
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
  <div class="sidebar-brand"><span class="logo">H</span><span class="sidebar-brand-copy"><strong>Hysteria</strong><small>Network Console</small></span></div>
  <nav class="sidebar-nav" aria-label="管理导航">
    <div class="sidebar-section">控制中心</div>
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
        <button class="sidebar-toggle" id="sidebar-toggle" type="button" aria-label="切换侧边栏" aria-controls="sidebar" aria-expanded="false">{icon("menu")}</button>
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
  if (!sb || !sc || !bt) return;
  function setOpen(open) {{
    sb.classList.toggle('open', open);
    document.body.classList.toggle('sidebar-open', open);
    bt.setAttribute('aria-expanded', open ? 'true' : 'false');
  }}
  function close() {{ setOpen(false); }}
  bt.addEventListener('click', function() {{ setOpen(!sb.classList.contains('open')); }});
  sc.addEventListener('click', close);
  sb.querySelectorAll('a').forEach(function(link) {{ link.addEventListener('click', close); }});
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
        return f'已清除用户本周期已用流量：{msg.split(" ", 2)[2]}'
    if msg == 'reset usage all':
        return '已清除全部用户本周期已用流量'
    if msg.startswith('refresh usage '):
        return f'已刷新用户本周期已用流量（服务器总流量不变）：{msg.split(" ", 2)[2]}'
    if msg.startswith('deleted '):
        return f'已删除用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('rotated '):
        return f'已重置订阅令牌（旧链接已失效）：{msg.split(" ", 1)[1]}'
    if msg.startswith('disabled '):
        return f'已停用用户（已断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('paused '):
        return f'已暂停用户 1 小时（已断开连接）：{msg.split(" ", 1)[1]}'
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
<div class="card elev inline-form auth-card welcome-card" style="text-align:center;">
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
<div class="card elev inline-form auth-card login-card">
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


def render_subscription_profile_links(base_url, user, token):
    items = []
    for key in SUBSCRIPTION_PROFILE_ORDER:
        meta = SUBSCRIPTION_PROFILES[key]
        suffix = '' if key == 'default' else f'&profile={key}'
        url = f'{base_url}/sub/{user}?token={token}{suffix}'
        items.append(
            f'<a class="btn secondary profile-link" href="{html.escape(url)}">'
            f'<span>{html.escape(meta["label"])}</span>'
            f'<small>{html.escape(meta["desc"])}</small></a>'
        )
    return (
        '<div class="card mt-md">'
        '<div class="k">订阅模式</div>'
        '<div class="small">同一个账号可按场景导入不同策略，默认模式保持后台模板原样。'
        f'模板更新时间：{html.escape(subscription_template_mtime() or "未知")}。</div>'
        f'<div class="profile-links mt-md">{"".join(items)}</div>'
        '</div>'
    )


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
    quota_unlimited = total <= 0
    cls = 'unlimited' if quota_unlimited else ('danger' if percent >= 90 else '')
    total_label = '不限' if quota_unlimited else fmt_bytes(total)
    remain_label = '不限' if quota_unlimited else fmt_bytes(remain)
    percent_label = '不限' if quota_unlimited else f'{percent:.2f}%'
    reset_date, days_left, cycle_len = _cycle_reset_info(now)
    spark = sparkline_svg(daily_window_for_user(user, daily, days=30, today=now.date()))
    sub_path = f'/sub/{user}?token={token}'
    panel_path = f'/panel/{user}?token={token}'
    json_path = f'/panel/{user}.json?token={token}'
    sub_http = f'{base_url}{sub_path}'
    panel_http = f'{base_url}{panel_path}'
    max_devices_n = int(cfg.get('max_devices', 0) or 0)
    is_disabled = bool(cfg.get('disabled'))
    expiry = user_expiry_state(cfg, today=now.date())
    is_expired = bool(expiry['expired'])
    disabled_banner = ''
    if is_disabled:
        disabled_banner = '<div class="err">账号已停用，请联系管理员</div>'
    elif is_expired:
        disabled_banner = '<div class="err">账号已到期，请联系管理员续费</div>'
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
    poll_js = '' if (is_disabled or is_expired) else f'''  var pollUrl = {poll_url_js};
  var statusEl = document.querySelector('[data-role="poll-status"]');
  function fmtBytes(n) {{
    var v = Math.max(0, Number(n) || 0);
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (v >= 1024 && i < u.length - 1) {{ v /= 1024; i++; }}
    return v.toFixed(2) + ' ' + u[i];
  }}
  function fmtQuota(n, total) {{
    return Number(total) <= 0 ? '不限' : fmtBytes(n);
  }}
  function setRole(role, txt) {{
    var el = document.querySelector('[data-role="' + role + '"]');
    if (el && txt !== undefined) el.textContent = txt;
  }}
  function setStatus(txt, cls) {{
    if (!statusEl) return;
    statusEl.textContent = txt;
    statusEl.classList.remove('is-live', 'is-paused', 'is-error');
    if (cls) statusEl.classList.add(cls);
  }}
  function stamp() {{
    return new Date().toLocaleTimeString([], {{ hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }});
  }}
  var timer = null, inflight = false;
  function tick() {{
    if (inflight) return;
    inflight = true;
    setStatus('刷新中', 'is-live');
    fetch(pollUrl, {{ credentials: 'same-origin', cache: 'no-store' }})
      .then(function(r) {{ return r.ok ? r.json() : null; }})
      .catch(function() {{ return null; }})
      .then(function(d) {{
        if (!d) {{ setStatus('刷新失败', 'is-error'); return; }}
        setRole('used', fmtBytes(d.used_bytes));
        setRole('remain', fmtQuota(d.remain_bytes, d.total_bytes));
        setRole('online', d.online);
        var p = Number(d.percent);
        setRole('percent', Number(d.total_bytes) <= 0 ? '不限' : p.toFixed(2) + '%');
        setRole('txrx', '上传 ' + fmtBytes(d.tx_bytes) + ' · 下载 ' + fmtBytes(d.rx_bytes));
        var bar = document.querySelector('[data-role="bar"]');
        if (bar) {{
          bar.style.width = Number(d.total_bytes) <= 0 ? '0%' : p.toFixed(2) + '%';
          bar.classList.toggle('danger', Number(d.total_bytes) > 0 && p >= 90);
          bar.classList.toggle('unlimited', Number(d.total_bytes) <= 0);
        }}
        setStatus('更新于 ' + stamp(), 'is-live');
      }})
      .finally(function() {{ inflight = false; }});
  }}
  function start() {{ if (!timer) {{ tick(); timer = setInterval(tick, 10000); }} }}
  function stop() {{ if (timer) {{ clearInterval(timer); timer = null; }} }}
  document.addEventListener('visibilitychange', function() {{ if (document.hidden) {{ stop(); setStatus('已暂停', 'is-paused'); }} else start(); }});
  window.addEventListener('pagehide', stop);
  start();'''
    body = f'''<div class="wrap">
{disabled_banner}
<div class="nav user-panel-nav">
  <div class="row gap-sm">
    <span class="app-logo">H</span>
    <div>
      <div class="brand" style="font-size:16px;">用户面板</div>
      <div class="small">{html.escape(user)}</div>
    </div>
  </div>
  <div style="text-align:right;">
    <span class="badge">{html.escape(host)}</span>
    <div class="small faint poll-status" data-role="poll-status" aria-live="polite" aria-atomic="true" style="margin-top:4px;">实时刷新中…</div>
  </div>
</div>
<div class="grid grid-4 hero-stats">
  <div class="card stat"><div class="k">本周期已用</div><div class="v big" data-role="used">{fmt_bytes(used)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">总流量</div><div class="v">{total_label}</div></div>
  <div class="card stat"><div class="k">剩余流量</div><div class="v" data-role="remain">{remain_label}</div></div>
  <div class="card stat"><div class="k">在线设备</div><div class="v"><span data-role="online">{online}</span> <span class="faint" style="font-size:14px;font-weight:500;">/ {max_devices_n}</span></div></div>
</div>
<div class="card mt-md">
  <div class="row" style="justify-content:space-between;margin-bottom:10px;">
    <div class="k" style="margin:0;">流量进度</div>
    <div class="bold" style="font-variant-numeric:tabular-nums;" data-role="percent">{percent_label}</div>
  </div>
  <div class="bar"><div class="fill {cls}" data-role="bar" style="width:{'0' if quota_unlimited else f'{percent:.2f}'}%"></div></div>
  <div class="small mt-sm" data-role="txrx">上传 {fmt_bytes(tx)} · 下载 {fmt_bytes(rx)}</div>
  <div class="small mt-sm faint">本周期 {cycle_len} 天 · 重置于 {reset_date} · 还剩 {days_left} 天 · 有效期 {html.escape(expiry["label"])}</div>
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
{render_subscription_profile_links(base_url, user, token)}
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
    function manualCopy() {{ if (window.prompt) window.prompt('自动复制不可用，请手动复制下面的链接', text); }}
    if (!navigator.clipboard) {{ manualCopy(); return; }}
    navigator.clipboard.writeText(text).then(function() {{ flashCopied(btn); }})
      .catch(manualCopy);
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
        spark_cell = f'<td class="spark-cell" data-label="30 天趋势" data-role="spark">{sparkline_svg(daily_window_for_user(user, daily, days=30))}</td>'
    total = user_total_quota(cfg)
    quota_label = '不限' if total <= 0 else fmt_bytes(total)
    max_devices = int(cfg.get('max_devices', 0) or 0)
    base_gb = int(round(base_quota_bytes(cfg) / 1024 / 1024 / 1024)) if base_quota_bytes(cfg) > 0 else 0
    extra_gb = quota_extra_gb(cfg)
    panel = f'{base_url}/panel/{user}?token={cfg.get("sub_token", "")}'
    sub_http = f'{base_url}/sub/{user}?token={cfg.get("sub_token", "")}'
    metered = user_compat.is_metered(cfg)
    guest_checked = 'checked' if metered else ''
    tuic_allowed = user_compat.tuic_enabled(cfg)
    tuic_checked = 'checked' if tuic_allowed else ''
    expiry = user_expiry_state(cfg, today=(now or local_now()).date())
    expires_at = expiry['expires_at']
    expired_badge = '<span class="badge badge-danger">已过期</span>' if expiry['expired'] else ''
    expires_preview = f' · {expiry["label"]}' if expires_at else ''
    extra_preview = f' · 加量 {extra_gb} GB' if extra_gb else ''
    note = str(cfg.get('note') or '')
    note_preview = f'<div class="small faint">{html.escape(note)}</div>' if note else ''
    percent = pct(used, total)
    bar_cls = 'unlimited' if total <= 0 else ('danger' if percent >= 90 else '')
    bar_w = '0.0' if total <= 0 else f'{percent:.1f}'
    user_esc = html.escape(user)
    guest_badge = '<span class="badge badge-info">按量</span>' if metered else ''
    tuic_badge = '<span class="badge">TUIC</span>' if tuic_allowed else '<span class="badge badge-danger">TUIC 关闭</span>'
    disabled = bool(cfg.get('disabled'))
    disabled_badge = '<span class="badge badge-danger">已停用</span>' if disabled else ''
    guest_preview = ' · 按量' if metered else ''
    quota_preview = '不限' if total <= 0 else f'{base_gb or 150} GB{extra_preview}'
    summary_preview = f'<span class="summary-preview">{quota_preview} · {max_devices or 2} 设备{guest_preview}{expires_preview}</span>'
    percent_label = '不限' if total <= 0 else f'{percent:.1f}%'
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
    online_n = int(online.get(user, 0) or 0)
    return f'''<tr data-user="{user_esc}" data-online="{online_n}" data-percent="{percent:.1f}">
<td data-label="用户">
  <div class="row gap-sm" style="flex-wrap:nowrap;">
    <div class="user-avatar" aria-hidden="true">{html.escape(user[:1].upper())}</div>
    <div style="min-width:0;">
      <div class="bold">{user_esc} {guest_badge}{tuic_badge}{disabled_badge}{expired_badge}</div>
      <div class="small">在线 <span data-role="online">{online_n}</span> / {max_devices} 设备</div>
      {note_preview}
    </div>
  </div>
</td>
{spark_cell}
<td data-label="本周期用量">
  <div class="row" style="justify-content:space-between;margin-bottom:4px;">
    <span class="bold" data-role="used">{fmt_bytes(used)}</span>
    <span class="small">/ {quota_label}</span>
  </div>
  <div class="mini-bar"><div class="mini-fill {bar_cls}" data-role="bar" style="width:{bar_w}%"></div></div>
  <div class="small mt-sm" data-role="detail">{percent_label} · ↑{fmt_bytes(tx)} ↓{fmt_bytes(rx)}</div>
</td>
<td data-label="操作">
<details>
<summary>编辑套餐{summary_preview}</summary>
<form method="post" action="/admin/update" class="inline-form">
<input type="hidden" name="user" value="{user_esc}">
<label>兼容连接密码（可选）</label><input name="password" type="password" placeholder="留空则不修改">
<label class="mt-sm">设备数上限</label><input name="max_devices" type="number" min="1" value="{max_devices or 2}">
<label class="mt-sm">本周期流量上限 (GB)</label><input name="quota_gb" type="number" min="1" value="{base_gb or 150}">
<label class="mt-sm">加量包 (GB)</label><input name="quota_extra_gb" type="number" min="0" value="{extra_gb}">
<label class="mt-sm">到期日</label><input name="expires_at" type="date" value="{html.escape(expires_at)}">
	<label class="mt-sm">备注</label><input name="note" maxlength="200" value="{html.escape(note)}" placeholder="可选：续费状态/来源/说明">
	<label class="switch mt-sm"><input type="checkbox" name="guest" {guest_checked}>按量用户（参与配额计量）</label>
	<label class="switch mt-sm"><input type="checkbox" name="tuic_enabled" {tuic_checked}>允许 TUIC（TUIC 不参与单用户额度计量）</label>
	<button class="btn mt-md" type="submit">保存</button>
</form>
</details>
<div class="row gap-sm mt-sm user-actions">
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
<td class="link-cell" data-label="链接">
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
        f'<form method="post" action="/admin/cycle-config" class="inline-form-row cycle-config-form" style="margin:0;">'
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
<div class="grid grid-3 hero-stats">
  <div class="card stat"><div class="k">本周期总流量</div><div class="v big" id="total-used">{fmt_bytes(total_used)}</div><div class="small">{html.escape(cycle_range)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">计费周期</div><div class="v">{mk}</div><div class="small">每 {cycle_length} 天结算 · 第 {settlement_day} 日</div></div>
  <div class="card stat">
    <div class="k">快速操作</div>
    <form method="post" action="/admin/reset-usage-all" data-action="reset-all" style="margin:6px 0 0;">
      <button class="btn secondary btn-sm" type="submit">一键清空本周期用量</button>
    </form>
    <div class="row gap-sm mt-sm">
      <a class="btn ghost btn-sm" href="/admin/usage.csv?window=cycle">导出本周期 CSV</a>
      <a class="btn ghost btn-sm" href="/admin/usage.csv?window=30d">导出 30 天 CSV</a>
    </div>
  </div>
</div>
<div class="card card-flush mt-md scroll-x users-card">
  <div class="card-head">
    <div class="bold">用户列表</div>
    <div class="row gap-sm filter-toolbar" style="flex:1;justify-content:flex-end;flex-wrap:wrap;">
      <input id="user-filter" type="search" placeholder="搜索用户名…" aria-label="搜索用户名" autocomplete="off"
             class="user-filter-input" style="min-width:180px;max-width:260px;">
      <div class="row gap-sm filter-chips" role="group" aria-label="状态筛选">
        <button type="button" class="chip active" data-filter="all">全部</button>
        <button type="button" class="chip" data-filter="online">在线</button>
        <button type="button" class="chip" data-filter="over">超 90%</button>
      </div>
      <div class="small" id="filter-count" style="min-width:64px;text-align:right;">{len(users)} / {len(users)} 个</div>
      <div class="small faint">实时 · 5 s</div>
    </div>
  </div>
  <table class="table users-table" data-user-count="{len(users)}"><thead><tr><th style="padding-left:18px;">用户</th><th>30 天趋势</th><th>本周期用量</th><th>操作</th><th style="padding-right:18px;">链接</th></tr></thead><tbody>{rows}</tbody></table>
</div>
<div class="card mt-md create-user-card">
  <details class="summary-muted">
    <summary>新增用户</summary>
    <form method="post" action="/admin/add" class="inline-form mt-md">
          <div class="grid grid-3">
            <div><label>用户名</label><input name="user" required></div>
            <div><label>兼容连接密码（可选）</label><input name="password" type="password" placeholder="默认仅用订阅 token 认证"></div>
            <div><label>本周期流量上限 (GB)</label><input name="quota_gb" type="number" value="150" min="1"></div>
            <div><label>加量包 (GB)</label><input name="quota_extra_gb" type="number" value="0" min="0"></div>
            <div><label>到期日</label><input name="expires_at" type="date"></div>
            <div><label>备注</label><input name="note" maxlength="200" placeholder="可选"></div>
          </div>
      <div class="row mt-md">
	        <label class="switch"><input type="checkbox" name="guest" checked>按量用户（参与配额计量）</label>
	        <label class="switch"><input type="checkbox" name="tuic_enabled">允许 TUIC</label>
	        <label class="switch"><input type="checkbox" name="reset_token">已存在则重置订阅令牌</label>
      </div>
      <button class="btn mt-md" type="submit">创建用户</button>
    </form>
  </details>
</div>
<script src="/static/admin-poll.js" defer></script>
'''
    poll_status = '<span class="badge poll-status" data-role="admin-poll-status" aria-live="polite" aria-atomic="true">实时 · 5 s</span>'
    return render_admin_shell('dashboard', '总览', content,
                              badge=f'{len(users)} 个用户',
                              subtitle=f'{host} · 计费周期 {mk}',
                              topbar_extra=settle_form + poll_status)


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



def _usage_context():
    return usage_dashboard.UsageDashboardContext(
        display_multiplier=DISPLAY_MULTIPLIER,
        hourly_retention_hours=HOURLY_RETENTION_HOURS,
        daily_retention_days=DAILY_RETENTION_DAYS,
        local_tz_label=LOCAL_TZ_LABEL,
        users_file=USERS_FILE,
        usage_daily_file=USAGE_DAILY_FILE,
        usage_hourly_file=USAGE_HOURLY_FILE,
        online_file=ONLINE_FILE,
        load_json=load_json,
        local_now=local_now,
        cycle_days=_cycle_days,
        cycle_start_for=cycle_start_for,
        get_cycle_length_days=get_cycle_length_days,
        preserved_raw_for_cycle=preserved_raw_for_cycle,
        scaled_usage_for_user=scaled_usage_for_user,
        cycle_raw_for_user=_cycle_raw_for_user,
        user_total_quota=user_total_quota,
        user_expiry_state=user_expiry_state,
        pct=pct,
        fmt_bytes=fmt_bytes,
        render_admin_shell=render_admin_shell,
    )


def _scale_daily_entry(entry):
    return usage_dashboard.scale_daily_entry(_usage_context(), entry)


def _hour_key(dt):
    return usage_dashboard.hour_key(dt)


def _entry_total(entry):
    return usage_dashboard.entry_total(entry)


def _load_hourly_totals(*, now):
    return usage_dashboard.load_hourly_totals(_usage_context(), now=now)


def _load_heatmap_grid(*, now):
    return usage_dashboard.load_heatmap_grid(_usage_context(), now=now)


def _top_n_users(*, n=5, window_hours=24, now):
    return usage_dashboard.top_n_users(_usage_context(), n=n, window_hours=window_hours, now=now)


def _aggregate_stats(*, now, online):
    return usage_dashboard.aggregate_stats(_usage_context(), now=now, online=online)


def _build_usage_csv(*, now, window='cycle'):
    return usage_dashboard.build_usage_csv(_usage_context(), now=now, window=window)


def _build_usage_json_payload(*, now):
    return usage_dashboard.build_usage_json_payload(_usage_context(), now=now)


def _build_user_json_payload(uid, *, now):
    return usage_dashboard.build_user_json_payload(_usage_context(), uid, now=now)


def daily_window_for_user(uid, daily, *, days=30, today=None):
    return usage_dashboard.daily_window_for_user(
        _usage_context(), uid, daily, days=days, today=today,
    )


def sparkline_svg(values, *, height=24):
    return usage_dashboard.sparkline_svg(values, height=height)


def render_daily_usage(host, days=14):
    return usage_dashboard.render_daily_usage(_usage_context(), host, days=days)


def render_usage_page(host):
    return usage_dashboard.render_usage_page(_usage_context(), host)


def render_user_detail_page(uid, host):
    return usage_dashboard.render_user_detail_page(_usage_context(), uid, host)


def _render_daily_table_collapsed(host):
    return usage_dashboard.render_daily_table_collapsed(_usage_context(), host)


def _incident_context():
    return incident_console.IncidentConsoleContext(
        alerts=alerts,
        display_multiplier=DISPLAY_MULTIPLIER,
        users_file=USERS_FILE,
        usage_daily_file=USAGE_DAILY_FILE,
        usage_hourly_file=USAGE_HOURLY_FILE,
        online_file=ONLINE_FILE,
        subscription_profiles=SUBSCRIPTION_PROFILES,
        load_json=load_json,
        local_now=local_now,
        hour_key=_hour_key,
        entry_total=_entry_total,
        cycle_raw_for_user=_cycle_raw_for_user,
        aggregate_stats=_aggregate_stats,
        user_total_quota=user_total_quota,
        user_expiry_state=user_expiry_state,
        pct=pct,
        fmt_bytes=fmt_bytes,
        build_line_radar=build_line_radar,
        summarize_cost_calibration=summarize_cost_calibration,
        render_line_radar=render_line_radar,
        render_cost_calibrator=render_cost_calibrator,
        render_alert=render_alert,
        flash_text=flash_text,
        render_admin_shell=render_admin_shell,
    )


def build_incident_payload(*, now=None):
    return incident_console.build_incident_payload(_incident_context(), now=now)


def render_incidents(host, flash=''):
    return incident_console.render_incidents(_incident_context(), host, flash=flash)


def probe_cron_heartbeat():
    return health.probe_cron_heartbeat(USAGE_FILE)


def probe_systemd(unit):
    return health.probe_systemd(unit, runner=subprocess.run)


def probe_disk():
    return health.probe_disk(disk_usage=shutil.disk_usage)


def probe_cert(path=None):
    p = Path(path) if path else Path('/root/hysteria/server.crt')
    return health.probe_cert(p, runner=subprocess.run, environ=os.environ)


def probe_online():
    return health.probe_online(ONLINE_FILE, load_json=load_json)


def probe_xray_config_permissions():
    return health.probe_file_mode(XRAY_CONFIG_FILE, mode='640', group='hy2-xray')


def probe_hysteria_update():
    return health.probe_hysteria_update(runner=subprocess.run)


def probe_recent_backup():
    return health.probe_recent_backup(BACKUP_DIR, disk_usage=shutil.disk_usage)


def _health_card(title, probe_result):
    return health.health_card(title, probe_result)


def _health_widget_context():
    return health_widgets.HealthWidgetContext(
        display_multiplier=DISPLAY_MULTIPLIER,
        users_file=USERS_FILE,
        online_file=ONLINE_FILE,
        protocol_usage_hourly_file=PROTOCOL_USAGE_HOURLY_FILE,
        cost_calibration_file=COST_CALIBRATION_FILE,
        display_multiplier_state_file=DISPLAY_MULTIPLIER_STATE_FILE,
        multiplier_auto_policy_file=MULTIPLIER_AUTO_POLICY_FILE,
        subscription_profiles=SUBSCRIPTION_PROFILES,
        load_json=load_json,
        local_now=local_now,
        entry_total=_entry_total,
        probe_systemd=probe_systemd,
        fmt_bytes=fmt_bytes,
    )


def build_line_radar(*, now=None):
    return health_widgets.build_line_radar(_health_widget_context(), now=now)


def render_line_radar(now=None):
    return health_widgets.render_line_radar(_health_widget_context(), now=now)


def summarize_cost_calibration(*, now=None):
    return health_widgets.summarize_cost_calibration(_health_widget_context(), now=now)


def render_cost_calibrator(now=None):
    return health_widgets.render_cost_calibrator(_health_widget_context(), now=now)


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
    'multiplier_applied': '建议倍率已应用，订阅后台将自动重启后生效',
    'multiplier_low_confidence': '样本置信度不足，暂不应用建议倍率',
    'multiplier_invalid': '建议倍率无效，未应用',
    'multiplier_delta_too_large': '建议倍率变化过大，未应用',
    'multiplier_auto_saved': '自动调倍率策略已保存',
}


def render_health(host, flash=''):
    alert = render_prefixed_alert(flash, _HEALTH_FLASH)
    cards = [
        _health_card('cron 心跳', probe_cron_heartbeat()),
        _health_card('hysteria', probe_systemd('hysteria-server.service')),
        _health_card('xray', probe_systemd('xray.service')),
        _health_card('tuic', probe_systemd('tuic-server.service')),
        _health_card('限流 timer', probe_systemd('hysteria-traffic-limiter.timer')),
        _health_card('磁盘', probe_disk()),
        _health_card('TLS 证书', probe_cert()),
        _health_card('在线用户', probe_online()),
        _health_card('Xray 配置权限', probe_xray_config_permissions()),
        _health_card('Hysteria 更新', probe_hysteria_update()),
        _health_card('最近备份', probe_recent_backup()),
    ]
    content = (
        alert
        + '<div class="grid grid-3">' + ''.join(cards) + '</div>'
        + render_line_radar()
        + render_cost_calibrator()
        + '<meta http-equiv="refresh" content="30">'
    )
    test_btn = ('<form method="post" action="/admin/test-alert" class="inline-form-row">'
                '<button class="btn secondary btn-sm" type="submit">发送测试告警</button></form>')
    return render_admin_shell('health', '健康状态', content,
                              badge=host, subtitle='30 秒自动刷新',
                              topbar_extra=test_btn)


def restart_subscription_async():
    try:
        subprocess.Popen(
            ['systemd-run', '--no-block', '--on-active=2s',
             '--unit', f'hy2-subscription-restart-{int(time.time())}',
             'systemctl', 'restart', 'hysteria-subscription.service'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def apply_suggested_display_multiplier(*, actor='admin', now=None):
    now = now or local_now()
    summary = summarize_cost_calibration(now=now)
    policy = cost_calibrator.load_auto_policy(MULTIPLIER_AUTO_POLICY_FILE)
    decision = cost_calibrator.evaluate_multiplier_candidate(
        summary, DISPLAY_MULTIPLIER, policy,
        runtime_state=load_json(DISPLAY_MULTIPLIER_STATE_FILE, {}),
        now=now, manual=True)
    if decision.get('reason') == 'low_confidence':
        return 'multiplier_low_confidence'
    if decision.get('reason') == 'delta_too_large':
        return 'multiplier_delta_too_large'
    if not decision.get('apply'):
        return 'multiplier_invalid'
    cost_calibrator.write_multiplier_state(
        DISPLAY_MULTIPLIER_STATE_FILE,
        multiplier=decision['candidate'],
        previous_multiplier=DISPLAY_MULTIPLIER,
        summary=summary,
        mode=policy.get('mode', 'total'),
        actor=actor or 'admin',
        now=now,
        auto=False,
    )
    restart_subscription_async()
    return 'multiplier_applied'


def save_multiplier_auto_policy_from_form(form):
    policy = cost_calibrator.load_auto_policy(MULTIPLIER_AUTO_POLICY_FILE)
    policy.update({
        'enabled': 'enabled' in form,
        'mode': (form.get('mode') or ['total'])[0],
        'min_confidence': (form.get('min_confidence') or ['medium'])[0],
        'max_delta_percent': parse_int_field(
            (form.get('max_delta_percent') or ['25'])[0], 25, 1, 100),
        'min_delta_percent': parse_int_field(
            (form.get('min_delta_percent') or ['3'])[0], 3, 0, 50),
        'cooldown_hours': parse_int_field(
            (form.get('cooldown_hours') or ['24'])[0], 24, 1, 168),
    })
    cost_calibrator.save_auto_policy(policy, MULTIPLIER_AUTO_POLICY_FILE)


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
    save_text_atomic(TEMPLATE_FILE, _dump_yaml(data))


def replace_template_config(data):
    with template_lock():
        save_template_config(data)


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
    save_text_atomic(TEMPLATE_FILE, '\n'.join(result) + ('\n' if not result[-1].endswith('\n') else ''))


def add_template_rule(rule_str):
    with template_lock():
        rules = load_template_rules()
        rules.insert(0, rule_str)
        save_template_rules(rules)


def delete_template_rule(index):
    with template_lock():
        rules = load_template_rules()
        if index < 0 or index >= len(rules):
            return False
        rules.pop(index)
        save_template_rules(rules)
        return True


def replace_template_rules(rules):
    with template_lock():
        save_template_rules(rules)


def apply_rule_pack_to_template(pack_key):
    with template_lock():
        data = load_template_config()
        if not profile_defs.apply_rule_pack_to_clash_config(data, pack_key):
            return False
        save_template_config(data)
        return True


def apply_rule_pack_to_user(username, pack_key):
    username = str(username or '').strip()
    if not username:
        return False
    with usage_lock():
        users = load_json(USERS_FILE, {})
        cfg = users.get(username)
        if not isinstance(cfg, dict):
            return False
        if not profile_defs.apply_rule_pack_to_user_config(cfg, pack_key):
            return False
        users[username] = cfg
        save_json(USERS_FILE, users)
    return True


def safe_admin_next(raw, default='/admin'):
    target = str(raw or '').strip()
    if not target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc:
        return default
    if parsed.path != '/admin' and not parsed.path.startswith('/admin/'):
        return default
    query = f'?{parsed.query}' if parsed.query else ''
    return f'{parsed.path}{query}'


def with_flash(target, msg):
    parsed = urlparse(target)
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != 'msg'
    ]
    pairs.append(('msg', str(msg or '')))
    query = urlencode(pairs)
    return f'{parsed.path}?{query}' if query else parsed.path


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
    'rule_pack_applied': '规则包已应用，客户端更新订阅后生效',
    'invalid_rule_pack': '无效的规则包',
    'invalid_rule_pack_scope': '无效的应用范围',
    'rule_pack_user_missing': '请选择要应用的用户',
}


def render_rules(host, flash=''):
    rules = load_template_rules()
    users = load_json(USERS_FILE, {})
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
    pack_options = ''.join(
        f'<option value="{html.escape(key)}">{html.escape(RULE_PACKS[key]["label"])}'
        f' · {html.escape(RULE_PACKS[key]["desc"])}</option>'
        for key in RULE_PACK_ORDER
    )
    user_options = ''.join(
        f'<option value="{html.escape(uid)}">{html.escape(uid)}</option>'
        for uid in sorted(users)
    )

    content = f'''{alert}
<div class="card mb-md">
  <div class="small">自定义规则优先级高于规则集，从上到下依次匹配。灰色行为内置规则集，不可删除。</div>
</div>
<div class="card mb-md">
  <div class="bold mb-md">规则包</div>
  <form method="post" action="/admin/rule-pack/apply" class="inline-form">
    <div class="grid grid-3">
      <div><label>规则包</label><select name="pack">{pack_options}</select></div>
      <div><label>应用范围</label><select name="scope">
        <option value="global">全局模板</option>
        <option value="user">单个用户</option>
      </select></div>
      <div><label>用户（选择“单个用户”时生效）</label><select name="user">
        <option value="">选择用户</option>{user_options}
      </select></div>
    </div>
    <button class="btn mt-md" type="submit">应用规则包</button>
  </form>
  <div class="small mt-sm faint">全局模板影响所有用户；单个用户会写入 users.json 的个人 Clash 覆盖项。</div>
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


RequestTooLarge = http_utils.RequestTooLarge
BadRequest = http_utils.BadRequest


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def parse_form(self):
        return http_utils.parse_form(self, max_bytes=MAX_FORM_BYTES)

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
        base_url = safe_base_url(
            host,
            self.headers.get('X-Forwarded-Proto', 'http'),
            self.headers.get('X-Forwarded-Port', ''),
        )

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
            self.redirect('/login', cookie=clear_session_cookie(secure=is_secure_request(self)))
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
            if user_compat.is_expired(cfg, today=local_now().date()):
                self.send_response_body(403, '账号已到期，请联系管理员续费', send_body=send_payload)
                return
            profile = normalize_subscription_profile((q.get('profile') or ['default'])[0])
            generated_at = profile_defs.utc_now_iso()
            template_mtime = subscription_template_mtime()
            yml = build_yaml(
                user, str(cfg.get('sub_token') or ''),
                profile=profile, generated_at=generated_at)
            tx, rx, used = scaled_usage_for_user(user)
            total = user_total_quota(cfg)
            payload = yml.encode('utf-8')
            filename = f'{user}.yaml' if profile == 'default' else f'{user}-{profile}.yaml'
            self.send_response(200)
            self.send_header('Content-Type', 'text/yaml; charset=utf-8')
            self.send_header('Content-Disposition', f"attachment; filename*=UTF-8''{filename}")
            self.send_header('x-subscription-profile', profile)
            self.send_header('x-subscription-generated-at', generated_at)
            self.send_header('x-subscription-template-mtime', template_mtime)
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
            if user_compat.is_expired(cfg, today=local_now().date()):
                self.send_response_body(403, '{"error":"expired"}',
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

        if path == '/admin/incidents':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(200, render_incidents(host, flash=flash),
                                    'text/html; charset=utf-8', send_payload)
            return

        if path == '/admin/incidents/evidence.json':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            now = local_now()
            payload = build_incident_payload(now=now)
            filename = f'incident-evidence-{now.strftime("%Y%m%dT%H%M%S")}.json'
            self.send_response_body(
                200,
                json.dumps(payload, ensure_ascii=False, indent=2),
                'application/json; charset=utf-8',
                send_payload,
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
        if path != '/login' and not is_same_origin_post(self):
            self.send_response_body(403, '跨站请求被拒绝')
            return
        try:
            form = self.parse_form()
        except RequestTooLarge:
            self.send_response_body(413, '请求体过大')
            return
        except BadRequest:
            self.send_response_body(400, '请求体无效')
            return
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
            if user_compat.is_expired(cfg, today=local_now().date()):
                self.send_response_body(403, '账号已到期，请联系管理员续费')
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
                self.redirect('/admin?msg=login+success',
                              cookie=session_cookie(sid, secure=is_secure_request(self)))
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
            quota_extra_gb = parse_int_field((form.get('quota_extra_gb') or ['0'])[0], 0, 0, 10240)
            expires_at = parse_date_field((form.get('expires_at') or [''])[0])
            note = parse_note_field((form.get('note') or [''])[0])
            guest = 'guest' in form
            tuic_enabled = 'tuic_enabled' in form
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
                cfg['quota_extra_bytes'] = max(0, quota_extra_gb) * 1024 * 1024 * 1024
                if expires_at:
                    cfg['expires_at'] = expires_at
                else:
                    cfg.pop('expires_at', None)
                if note:
                    cfg['note'] = note
                else:
                    cfg.pop('note', None)
                cfg['metered'] = guest
                cfg['guest'] = guest
                cfg['tuic_enabled'] = tuic_enabled
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
            quota_extra_gb = parse_int_field((form.get('quota_extra_gb') or ['0'])[0], 0, 0, 10240)
            expires_at = parse_date_field((form.get('expires_at') or [''])[0])
            note = parse_note_field((form.get('note') or [''])[0])
            guest = 'guest' in form
            tuic_enabled = 'tuic_enabled' in form
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
                    'tuic_enabled': tuic_enabled,
                    'max_devices': 2,
                    'monthly_quota_bytes': max(1, quota_gb) * 1024 * 1024 * 1024,
                    'quota_extra_bytes': max(0, quota_extra_gb) * 1024 * 1024 * 1024,
                    'sub_token': token,
                    'vless_uuid': vless_uuid,
                    'disabled': bool(existing.get('disabled')),
                }
                if expires_at:
                    entry['expires_at'] = expires_at
                elif existing.get('expires_at'):
                    entry['expires_at'] = existing.get('expires_at')
                if note:
                    entry['note'] = note
                elif existing.get('note'):
                    entry['note'] = existing.get('note')
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
                          cookie=session_cookie(sid, secure=is_secure_request(self)))
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

        if path == '/admin/cost-multiplier/apply':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            result = apply_suggested_display_multiplier(actor=self.get_admin_actor())
            prefix = 'err:' if result != 'multiplier_applied' else ''
            self.redirect(f'/admin/health?msg={prefix}{result}')
            return

        if path == '/admin/cost-multiplier/auto':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            save_multiplier_auto_policy_from_form(form)
            self.redirect('/admin/health?msg=multiplier_auto_saved')
            return

        if path == '/admin/rotate-token':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            next_to = safe_admin_next((form.get('next') or [''])[0])
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                if not isinstance(users.get(username), dict):
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                users[username]['sub_token'] = secrets.token_urlsafe(18)
                save_json(USERS_FILE, users)
            # Drop any live hysteria session on the old token (= password) so a
            # connected attacker is forced off. Done outside the file lock.
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            hy_kick([username])
            self.write_reset_log(self.get_admin_actor(), 'rotate_token', username, {}, {})
            self.redirect(with_flash(next_to, 'rotated ' + username))
            return

        if path == '/admin/pause-user':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            minutes = parse_int_field((form.get('minutes') or ['60'])[0], 60, 1, 1440)
            next_to = safe_admin_next((form.get('next') or [''])[0])
            until = local_now() + timedelta(minutes=minutes)
            until_text = until.isoformat(timespec='seconds')
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                if not isinstance(users.get(username), dict):
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                users[username]['disabled'] = True
                users[username]['disabled_until'] = until_text
                save_json(USERS_FILE, users)
            if xray_config.remove_user(username):
                xray_config.reload_async()
            if tuic_config.sync_all(users=users):
                tuic_config.reload_async()
            hy_kick([username])
            self.write_reset_log(
                self.get_admin_actor(),
                'pause_user',
                username,
                {},
                {'disabled_until': until_text},
            )
            self.redirect(with_flash(next_to, 'paused ' + username))
            return

        if path == '/admin/toggle-user':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            next_to = safe_admin_next((form.get('next') or [''])[0])
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                if not isinstance(users.get(username), dict):
                    self.redirect(with_flash(next_to, 'user not found'))
                    return
                disable = not bool(users[username].get('disabled'))
                users[username]['disabled'] = disable
                users[username].pop('disabled_until', None)
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
                self.redirect(with_flash(next_to, 'disabled ' + username))
            else:
                if vless_uuid and xray_config.sync_user(username, vless_uuid):
                    xray_config.reload_async()
                if tuic_config.sync_all(users=users):
                    tuic_config.reload_async()
                self.write_reset_log(self.get_admin_actor(), 'enable_user', username, {}, {})
                self.redirect(with_flash(next_to, 'enabled ' + username))
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
            replace_template_config(data)
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
            add_template_rule(rule_str)
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
            if not delete_template_rule(idx):
                self.redirect('/admin/rules?msg=err:index_out_of_range')
                return
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
            replace_template_rules(rules)
            self.redirect('/admin/rules?msg=raw_saved')
            return

        if path == '/admin/rule-pack/apply':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            pack = (form.get('pack') or [''])[0]
            scope = (form.get('scope') or ['global'])[0]
            if pack not in RULE_PACKS:
                self.redirect('/admin/rules?msg=err:invalid_rule_pack')
                return
            if scope == 'global':
                if not apply_rule_pack_to_template(pack):
                    self.redirect('/admin/rules?msg=err:invalid_rule_pack')
                    return
            elif scope == 'user':
                username = (form.get('user') or [''])[0].strip()
                if not username:
                    self.redirect('/admin/rules?msg=err:rule_pack_user_missing')
                    return
                if not apply_rule_pack_to_user(username, pack):
                    self.redirect('/admin/rules?msg=err:rule_pack_user_missing')
                    return
            else:
                self.redirect('/admin/rules?msg=err:invalid_rule_pack_scope')
                return
            self.redirect('/admin/rules?msg=rule_pack_applied')
            return

        self.send_response_body(404, '页面不存在')


if __name__ == '__main__':
    ensure_meta()
    migrate_plaintext_passwords()
    migrate_admin_password()
    srv = ThreadingHTTPServer(LISTEN, Handler)
    srv.serve_forever()
