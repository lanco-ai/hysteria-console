#!/usr/bin/env python3
import html
import base64
import collections
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass

import alerts
import codex_dashboard
import codex_quota
import cost_calibrator
import cycle as cycle_util
import display as display_config
import health
import health_widgets
import hysteria_update
import http_utils
import incident_console
import landing_egress
import revocation_queue
import rotation_recovery
import static_access
import state_store
import subscription_profiles as profile_defs
import tuic_config
import usage_dashboard
import user_compat
import xray_config
from display import DISPLAY_MULTIPLIER, fmt_bytes
from timeutil import billing_cycle_key, local_now
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from concurrent.futures import ThreadPoolExecutor
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
DEVICE_ADMISSIONS_FILE = Path('/root/hysteria/state/device_admissions.json')
META_FILE = Path('/root/hysteria/subscription_meta.json')
TEMPLATE_FILE = Path('/root/hysteria/template.yaml')
BACKUP_DIR = Path('/root/hysteria/backups')
XRAY_CONFIG_FILE = Path('/usr/local/etc/xray/config.json')
SESSIONS_FILE = Path('/root/hysteria/state/panel_sessions.json')
USER_SESSIONS_FILE = Path('/root/hysteria/state/user_panel_sessions.json')
ROTATION_RECEIPTS_FILE = Path(
    '/root/hysteria/state/credential_rotation_receipts.json',
)
REVOCATION_QUEUE_FILE = Path(
    '/root/hysteria/state/credential_revocations.json',
)
_DELETE_TARGET_GENERATION = 'user-deleted-v1'
_DELETE_PREVIOUS_PREFIX = 'user-revision-v1:'
RESET_LOG_FILE = Path('/root/hysteria/state/usage_reset.log')
USAGE_LOCK_FILE = Path('/root/hysteria/state/usage.lock')
TEMPLATE_LOCK_FILE = Path('/root/hysteria/state/template.lock')
HY_API_BASE = 'http://127.0.0.1:25413'
HY_API_SECRET_FILE = '/root/hysteria/api_secret'
HY_API_SECRET_PLACEHOLDER = '__HY_API_SECRET__'
HY_API_SECRET_FALLBACK = '__HY_API_SECRET__'
CONFIGURED_PUBLIC_HOST_PLACEHOLDER = '__HY_SERVER_HOST__'
CONFIGURED_PUBLIC_HOST = CONFIGURED_PUBLIC_HOST_PLACEHOLDER
HY_KICK_TIMEOUT_SECONDS = 3.0
HY_KICK_MAX_RESPONSE_BYTES = 1024
_LIVE_CORE_STATE_PATHS = (
    '/root/hysteria/users.json',
    '/root/hysteria/subscription_meta.json',
    '/root/hysteria/state/usage.json',
    '/root/hysteria/state/usage_daily.json',
)
_WORKER_ERROR_LOG_LOCK = threading.Lock()
_WORKER_ERROR_LOG_STATE = {}
_WORKER_ERROR_LOG_INITIAL_SECONDS = 5.0
_WORKER_ERROR_LOG_MAX_SECONDS = 300.0


def _using_live_core_state():
    return tuple(map(str, (
        USERS_FILE,
        META_FILE,
        USAGE_FILE,
        USAGE_DAILY_FILE,
    ))) == _LIVE_CORE_STATE_PATHS


@dataclass(frozen=True)
class CredentialActionResult:
    """Structured, secret-free outcome for a revocation side effect."""

    action: str
    target: str
    attempted: bool
    ok: bool
    code: str
    retryable: bool

    def __bool__(self):
        return self.ok


def _normalize_service_action(service, raw):
    if isinstance(raw, static_access.ServiceActionResult):
        return raw
    ok = raw is True
    return static_access.ServiceActionResult(
        service=service,
        action='stop_fail_closed',
        attempted=True,
        ok=ok,
        effect_confirmed=ok,
        marker_persisted=ok,
        code='stopped' if ok else 'unconfirmed',
        retryable=not ok,
    )


def _fail_closed_static_access(reason):
    """Immediately revoke file-backed proxy auth when core state is unsafe."""
    live = _using_live_core_state()
    outcomes = {}
    for service in static_access.SERVICES:
        raw = static_access.stop_fail_closed(
            service,
            reason=reason,
            live=live,
        )
        outcomes[service] = _normalize_service_action(service, raw)
    return outcomes


def _state_failure_requires_static_stop(exc, *, post_path=''):
    del post_path
    if isinstance(exc, state_store.CriticalStateUnavailable):
        return True
    if not isinstance(
        exc,
        state_store.AtomicReplaceDurabilityUncertain,
    ):
        return False
    core_paths = {
        str(Path(path))
        for path in (
            USERS_FILE,
            META_FILE,
            USAGE_FILE,
            USAGE_DAILY_FILE,
        )
    }
    return str(Path(exc.path)) in core_paths


def _static_stop_confirmed(outcomes):
    return (
        isinstance(outcomes, dict)
        and len(outcomes) == len(static_access.SERVICES)
        and all(
            getattr(outcome, 'effect_confirmed', False)
            for outcome in outcomes.values()
        )
    )


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
        return CredentialActionResult(
            action='hysteria_kick',
            target='',
            attempted=False,
            ok=True,
            code='not_needed',
            retryable=False,
        )
    target = ','.join(sorted(str(user) for user in usernames))
    connection = None
    try:
        body = json.dumps(list(usernames)).encode('utf-8')
        deadline = time.monotonic() + HY_KICK_TIMEOUT_SECONDS
        connection = http.client.HTTPConnection(
            '127.0.0.1',
            25413,
            timeout=HY_KICK_TIMEOUT_SECONDS,
        )
        connection.request(
            'POST',
            '/kick',
            body=body,
            headers={
                'Authorization': get_hy_api_secret(),
                'Content-Type': 'application/json',
                'Content-Length': str(len(body)),
            },
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('kick request deadline exceeded')
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response = connection.getresponse()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('kick response deadline exceeded')
        if connection.sock is not None:
            connection.sock.settimeout(remaining)
        response_body = response.read(HY_KICK_MAX_RESPONSE_BYTES + 1)
        if len(response_body) > HY_KICK_MAX_RESPONSE_BYTES:
            return CredentialActionResult(
                action='hysteria_kick',
                target=target,
                attempted=True,
                ok=False,
                code='response_too_large',
                retryable=True,
            )
        status = int(getattr(response, 'status', 0) or 0)
        if 200 <= status < 300:
            return CredentialActionResult(
                action='hysteria_kick',
                target=target,
                attempted=True,
                ok=True,
                code='accepted',
                retryable=False,
            )
        return CredentialActionResult(
            action='hysteria_kick',
            target=target,
            attempted=True,
            ok=False,
            code='unexpected_status',
            retryable=True,
        )
    except Exception as exc:
        return CredentialActionResult(
            action='hysteria_kick',
            target=target,
            attempted=True,
            ok=False,
            code=type(exc).__name__,
            retryable=True,
        )
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
LISTEN = ('127.0.0.1', 8081)
SERVER_MAX_WORKERS = 32
SERVER_REQUEST_QUEUE = 64
STATE_LOCK_TIMEOUT_SECONDS = 15.0
SESSION_TTL = 86400
SESSION_MAX_PER_IDENTITY = 16
SESSION_MAX_GLOBAL = 2048
USER_SESSION_PANEL_PASSWORD = 'panel_password'
USER_SESSION_SUBSCRIPTION_TOKEN = 'subscription_token'
USER_SESSION_CREDENTIAL_KINDS = {
    USER_SESSION_PANEL_PASSWORD,
    USER_SESSION_SUBSCRIPTION_TOKEN,
}
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256
PASSWORD_HASH_MAX_LENGTH = 512
PBKDF2_ROUNDS_MIN = 100_000
PBKDF2_ROUNDS_MAX = 1_000_000
PBKDF2_SALT_BYTES = 16
PBKDF2_DIGEST_BYTES = hashlib.sha256().digest_size
MAX_FORM_BYTES = http_utils.MAX_FORM_BYTES


class CredentialRotationCommitted(state_store.CriticalStateUnavailable):
    """The token changed, but derived static authorization did not sync.

    The new credential is intentionally kept out of the exception message so
    generic error logging cannot disclose it. A local request handler may use
    the attributes to deliver the already-committed credential exactly once.
    """

    def __init__(
        self,
        user,
        new_token,
        user_config,
        *,
        durability_uncertain=False,
    ):
        super().__init__(
            'credential rotation committed but static access is pending',
        )
        self.user = str(user)
        self.new_token = str(new_token)
        self.user_config = dict(user_config)
        self.durability_uncertain = bool(durability_uncertain)


_STATIC_DIR = Path(__file__).resolve().parent
BASE_CSS_BYTES = (_STATIC_DIR / 'admin.css').read_bytes()
BASE_CSS_ETAG = '"' + hashlib.sha1(BASE_CSS_BYTES).hexdigest()[:16] + '"'
ADMIN_POLL_JS_BYTES = (_STATIC_DIR / 'admin_poll.js').read_bytes()
ADMIN_POLL_JS_ETAG = '"' + hashlib.sha1(ADMIN_POLL_JS_BYTES).hexdigest()[:16] + '"'
USAGE_JS_BYTES = (_STATIC_DIR / 'usage.js').read_bytes()
USAGE_JS_ETAG = '"' + hashlib.sha1(USAGE_JS_BYTES).hexdigest()[:16] + '"'
CODEX_QUOTA_JS_BYTES = (_STATIC_DIR / 'codex_quota.js').read_bytes()
CODEX_QUOTA_JS_ETAG = '"' + hashlib.sha1(CODEX_QUOTA_JS_BYTES).hexdigest()[:16] + '"'
HOME_JS_BYTES = (_STATIC_DIR / 'static' / 'home.js').read_bytes()
HOME_JS_ETAG = '"' + hashlib.sha1(HOME_JS_BYTES).hexdigest()[:16] + '"'

# Strict whitelist for /static/fonts/*.woff2 — no path traversal, no arbitrary files.
STATIC_FONT_FILES = {
    '/static/fonts/inter-var.woff2': (
        (_STATIC_DIR / 'static' / 'fonts' / 'inter-var.woff2').read_bytes(),
        'font/woff2',
    ),
    '/static/fonts/jetbrains-mono.woff2': (
        (_STATIC_DIR / 'static' / 'fonts' / 'jetbrains-mono.woff2').read_bytes(),
        'font/woff2',
    ),
}


def _etag_matches(raw_header, current_etag):
    """Weakly compare an If-None-Match list with a generated asset ETag."""
    current = str(current_etag or '').strip()
    if current.startswith('W/'):
        current = current[2:].strip()
    for candidate in str(raw_header or '').split(','):
        candidate = candidate.strip()
        if candidate == '*':
            return True
        if candidate.startswith('W/'):
            candidate = candidate[2:].strip()
        if candidate and candidate == current:
            return True
    return False


def _static_asset_cache_control(query, etag):
    requested = str((query.get('v') or [''])[0])
    current = str(etag or '').strip('"')
    if requested and hmac.compare_digest(requested, current):
        return 'public, max-age=31536000, immutable'
    return 'public, max-age=86400'


def load_json(path, default, *, required=None):
    critical_paths = {
        str(Path(USERS_FILE)),
        str(Path(USAGE_FILE)),
        str(Path(USAGE_DAILY_FILE)),
        str(Path(META_FILE)),
    }
    if required is None:
        required = str(Path(path)) in critical_paths
    try:
        return state_store.load_json_strict(path, default, required=required)
    except state_store.StateStoreError as exc:
        if str(Path(path)) in critical_paths:
            raise state_store.CriticalStateUnavailable(
                str(exc),
            ) from exc
        raise


_request_multiplier = ContextVar('request_display_multiplier', default=None)


def request_multiplier_snapshot(function):
    """Lazy, isolated snapshot, cleared even when a handler fails."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        token = _request_multiplier.set([])
        try:
            return function(*args, **kwargs)
        finally:
            _request_multiplier.reset(token)
    return wrapped


def current_display_multiplier():
    """Read the active billing multiplier for each request.

    The panel is long-lived while the limiter/auth hook are short-lived.
    Dynamic reads keep displayed usage and enforcement aligned immediately
    after an operator applies a calibrated multiplier.
    """
    snapshot = _request_multiplier.get()
    if snapshot:
        return snapshot[0]
    path = DISPLAY_MULTIPLIER_STATE_FILE
    if not _using_live_core_state():
        path = Path(USAGE_FILE).parent / Path(path).name
    try:
        multiplier = display_config.effective_display_multiplier_strict(path=path)
        if snapshot is not None:
            snapshot.append(multiplier)
        return multiplier
    except ValueError as exc:
        raise state_store.CriticalStateUnavailable(
            f'display multiplier policy is invalid: {path}',
        ) from exc


def save_json(path, data):
    """Atomic write: serialize to a sibling temp file, fsync, then rename. Prevents
    truncated state files (which the readers fall back to `{}` on, silently losing
    the cycle/state tracking)."""
    try:
        state_store.save_json(path, data)
    except OSError as exc:
        raise state_store.StateStoreError(
            f'cannot persist JSON state: {Path(path)}',
        ) from exc


def save_text_atomic(path, text):
    """Atomic UTF-8 text write for operator-edited config files."""
    try:
        state_store.save_text_atomic(path, text)
    except OSError as exc:
        raise state_store.StateStoreError(
            f'cannot persist text state: {Path(path)}',
        ) from exc


@contextmanager
def usage_lock():
    with state_store.file_lock(
        USAGE_LOCK_FILE,
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        yield


@contextmanager
def meta_lock():
    with state_store.file_lock(
        Path(str(META_FILE) + '.lock'),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        yield


@contextmanager
def template_lock():
    with state_store.file_lock(
        TEMPLATE_LOCK_FILE,
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        yield


def is_valid_username(name):
    """A creatable username: 1-64 chars of [A-Za-z0-9_.-], not ending in
    `.json`. The `.json` exclusion avoids route-extraction ambiguity with
    `/panel/<user>.json`; the charset blocks path/HTML-injection sinks."""
    return user_compat.is_valid_username(name)


def parse_int_field(raw, default, min_value, max_value):
    try:
        value = int(str(raw).strip())
    except Exception:
        return default
    return max(min_value, min(max_value, value))


def parse_bounded_int_field(raw, min_value, max_value):
    """Parse an integer without silently changing an operator's input."""
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if value < min_value or value > max_value:
        return None
    return value


def configured_max_devices(cfg, default=2):
    """Return the stored device cap; an explicit zero means unlimited."""
    return user_compat.configured_max_devices(cfg, default)


def content_revision(value):
    """Return a stable opaque revision for compare-and-swap form updates."""
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def user_config_revision(cfg):
    return content_revision(cfg if isinstance(cfg, dict) else {})


def _delete_previous_generation(cfg):
    """Bind a deletion WAL record to one complete account incarnation."""
    return _DELETE_PREVIOUS_PREFIX + user_config_revision(cfg)


def _is_delete_revocation_task(task):
    return hmac.compare_digest(
        str(task.get('target_generation') or ''),
        _DELETE_TARGET_GENERATION,
    )


def revision_matches(cfg, expected):
    expected = str(expected or '').strip().lower()
    return bool(
        re.fullmatch(r'[0-9a-f]{64}', expected)
        and hmac.compare_digest(user_config_revision(cfg), expected)
    )


def parse_date_field(raw):
    """Parse date string to YYYY-MM-DD or '' on error.

    Rules (strict):
    - '' or None or whitespace → ''
    - Must be exactly YYYY-MM-DD
    - year in 2000..2099 inclusive
    - valid Gregorian date (including 2028-02-29)

    Returns normalized YYYY-MM-DD on success, '' on any failure.
    """
    raw = str(raw or '').strip()
    if not raw:
        return ''
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
        return ''
    try:
        parsed = datetime.strptime(raw, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return ''
    if not 2000 <= parsed.year <= 2099:
        return ''
    return parsed.isoformat()


def parse_note_field(raw):
    return str(raw or '').strip()[:200]


def sanitize_host(raw_host):
    return http_utils.sanitize_host(raw_host)


def configured_public_host(request_host=''):
    """Use the deploy-time host; request Host is only a source-tree fallback."""
    configured = str(CONFIGURED_PUBLIC_HOST or '').strip()
    if configured == CONFIGURED_PUBLIC_HOST_PLACEHOLDER:
        configured = request_host
    return sanitize_host(configured)


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
    with usage_lock():
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
    /admin/settings. Meta initialization fails if this credential cannot be
    persisted; silently creating an inaccessible admin hash would lock out the
    operator.
    Path follows META_FILE so tests (which repoint META_FILE) stay isolated."""
    try:
        path = Path(META_FILE).parent / 'admin_initial_password.txt'
        state_store.save_text_atomic(
            path,
            (
                f"# hy2 auto-generated initial admin password.\n"
                f"# Log in at /admin (user: {user}), rotate it at /admin/settings, then delete this file.\n"
                f"{user}:{password}\n"
            ),
        )
        os.chmod(str(path), 0o600)
        return True
    except OSError:
        return False


def load_meta():
    """Load runtime admin state fail-closed after deployment initialization."""
    return load_json(META_FILE, {}, required=True)


def ensure_meta():
    """Explicit first-deploy initializer for subscription_meta.json.

    Runtime request paths use ``load_meta`` instead, so deleting live admin
    state cannot silently generate a new password/token and lock out the
    operator. deploy.sh is the sole production caller allowed to initialize a
    missing file.
    """
    with meta_lock():
        meta = load_json(META_FILE, {}, required=False)
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
            if not _write_initial_admin_password(
                meta.get('admin_user', 'admin'), initial,
            ):
                raise state_store.StateStoreError(
                    'cannot persist initial admin credential',
                )
            changed = True
        if changed:
            save_json(META_FILE, meta)
        return meta


def migrate_admin_password():
    with meta_lock():
        meta = load_meta()
        plain = str(meta.get('admin_pass') or '')
        if plain:
            meta['admin_pass_hash'] = hash_secret(plain)
            del meta['admin_pass']
            save_json(META_FILE, meta)


def _change_admin_password(current, new, confirm):
    """Validate and persist an admin password change under the Meta lock."""
    with meta_lock():
        meta = load_meta()
        stored_hash = str(meta.get('admin_pass_hash') or '')
        if not (
            len(current) <= PASSWORD_MAX_LENGTH
            and stored_hash
            and verify_secret(current, stored_hash)
        ):
            return 'password_wrong', ''
        if len(new) < PASSWORD_MIN_LENGTH:
            return 'password_short', ''
        if len(new) > PASSWORD_MAX_LENGTH:
            return 'password_long', ''
        if new != confirm:
            return 'password_mismatch', ''
        new_hash = hash_secret(new)
        meta['admin_pass_hash'] = new_hash
        meta.pop('admin_pass', None)
        save_json(META_FILE, meta)
        return 'ok', new_hash


SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX


def get_settlement_day():
    """Day-of-month when the billing cycle rolls over. Editable via /admin/cycle-config."""
    return cycle_util.settlement_day_from_meta(load_meta())


def get_cycle_length_days():
    """Length of one billing cycle, in days. Editable via /admin/cycle-config.
    Cycles roll exactly every N days from `cycle_anchor_date` (or, if absent,
    from the most recent settlement_day on/before today)."""
    return cycle_util.cycle_length_from_meta(load_meta())


def _settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now.date().
    Falls back through prev month / Feb edge cases."""
    return cycle_util.settlement_anchor_date(now, settlement_day)


def _update_cycle_meta(day, length=None, *, now=None):
    """Update cycle settings without overwriting a concurrent admin rekey."""
    current_time = now or local_now()
    with meta_lock():
        meta = load_meta()
        meta['settlement_day'] = day
        if length is not None:
            meta['cycle_length_days'] = length
        meta['cycle_anchor_date'] = _settlement_anchor_date(
            current_time, day,
        ).strftime('%Y-%m-%d')
        save_json(META_FILE, meta)
        return meta


def get_cycle_anchor_date(now=None):
    """The anchor date (a settlement day in the past or today) that all N-day
    cycle blocks count from. Read from META_FILE if persisted, else derive
    from the current settlement_day. Storing the anchor keeps cycle boundaries
    stable across the inevitable jump that would otherwise happen each month
    when settlement_day recurs (e.g. with cycle_length=15, the most-recent-
    settlement-day-of-month anchor would skip cycles)."""
    if now is None:
        now = local_now()
    meta = load_meta()
    return cycle_util.cycle_anchor_date(now, meta)


def cycle_start_for(now, day=None, length=None, anchor=None):
    """Datetime at 00:00 local of the current cycle's start.

    For cycle_length_days==30 (default) the result matches the pre-existing
    calendar-month behaviour as long as the anchor is the most recent
    settlement_day. For shorter/longer N, cycles roll exactly every N days
    from the anchor — they intentionally do not re-align to calendar months."""
    meta = load_meta()
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
    return cycle_util.cycle_days(now, meta=load_meta())


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


def _critical_authorization_state(detail):
    raise state_store.CriticalStateUnavailable(
        f'authorization state is invalid: {detail}',
    )


def _strict_usage_entry_total(entry, *, field):
    """Read one canonical daily entry without allowing quota fail-open values."""
    if isinstance(entry, dict):
        unknown = set(entry) - {'tx', 'rx', 'total'}
        if unknown:
            _critical_authorization_state(
                f'{field} has unsupported fields',
            )
        values = {}
        for direction in ('tx', 'rx', 'total'):
            value = entry.get(direction, 0)
            if isinstance(value, bool):
                _critical_authorization_state(
                    f'{field}.{direction} must be a non-negative integer',
                )
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                _critical_authorization_state(
                    f'{field}.{direction} must be a non-negative integer',
                )
            if parsed < 0 or (
                isinstance(value, str) and str(parsed) != value.strip()
            ):
                _critical_authorization_state(
                    f'{field}.{direction} must be a non-negative integer',
                )
            values[direction] = parsed
        if values['total'] != values['tx'] + values['rx']:
            _critical_authorization_state(
                f'{field}.total must equal tx + rx',
            )
        return values['total']
    if isinstance(entry, bool):
        _critical_authorization_state(
            f'{field} must be a non-negative integer',
        )
    try:
        total = int(entry or 0)
    except (TypeError, ValueError):
        _critical_authorization_state(
            f'{field} must be a non-negative integer',
        )
    if total < 0 or (
        isinstance(entry, str) and str(total) != entry.strip()
    ):
        _critical_authorization_state(
            f'{field} must be a non-negative integer',
        )
    return total


def _cycle_usage_sum_strict(daily, cycle_days, username):
    """Raw cycle bytes for one user, with the exact original validation."""
    used = 0
    for day_key in cycle_days:
        bucket = daily.get(day_key, {})
        if not isinstance(bucket, dict):
            _critical_authorization_state(
                f'usage day {day_key!r} must be an object',
            )
        used += _strict_usage_entry_total(
            bucket.get(username, 0),
            field=f'{day_key}.{username}',
        )
    return used


# Small LRU for raw per-user cycle usage sums. This caches ONLY the
# usage_daily.json aggregation step — never the authorization decision.
# Every other input (disabled, expires_at, quota, vless_uuid, multiplier)
# is re-read and re-applied on every plan build.
# Capacity scales with the user count: one plan build inserts up to one
# entry per metered user, so a fixed small cap would evict entries before
# they are ever reused once more than a handful of users exist. A hard cap
# keeps pathological user counts from growing the cache without bound.
_CYCLE_USAGE_CACHE_MIN = 32
_CYCLE_USAGE_CACHE_HARD_MAX = 1024
_cycle_usage_cache_lock = threading.Lock()
_cycle_usage_cache = collections.OrderedDict()


def _cycle_usage_cache_bound(user_count):
    """Effective capacity for one plan build: scales with users, never
    below the floor, never above the hard cap."""
    return min(
        _CYCLE_USAGE_CACHE_HARD_MAX,
        max(_CYCLE_USAGE_CACHE_MIN, 4 * user_count),
    )


def _prune_cycle_usage_cache(max_entries):
    """Shrink to the effective bound. Caller holds _cycle_usage_cache_lock.
    Runs on hits too: after a user-count shrink, a hit-only sequence must
    not keep the cache above the current bound indefinitely."""
    while len(_cycle_usage_cache) > max_entries:
        _cycle_usage_cache.popitem(last=False)


def _usage_daily_file_version():
    """(mtime_ns, size) version of the live usage_daily file, or None."""
    try:
        st = USAGE_DAILY_FILE.stat()
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size)


def _cached_cycle_usage_sum(daily, cycle_days, username, *, version,
                            max_entries):
    """Cycle usage sum with a bounded cache keyed on the exact inputs.

    Cache hit requires: same cycle days, same usage_daily file version
    (mtime_ns + size), same username, and live core state. Anything else —
    including tests and alternate roots — bypasses the cache entirely."""
    if version is None:
        return _cycle_usage_sum_strict(daily, cycle_days, username)
    key = (tuple(cycle_days), version, username)
    with _cycle_usage_cache_lock:
        hit = _cycle_usage_cache.get(key)
        if hit is not None:
            _cycle_usage_cache.move_to_end(key)
            _prune_cycle_usage_cache(max_entries)
            return hit
    # Compute outside the lock; strict validation raises before any caching.
    used = _cycle_usage_sum_strict(daily, cycle_days, username)
    with _cycle_usage_cache_lock:
        _cycle_usage_cache[key] = used
        _cycle_usage_cache.move_to_end(key)
        _prune_cycle_usage_cache(max_entries)
    return used


def _validate_authorization_meta(meta):
    if not isinstance(meta, dict):
        _critical_authorization_state('subscription metadata must be an object')
    for field, minimum, maximum in (
        ('settlement_day', 1, 28),
        ('cycle_length_days', CYCLE_LENGTH_MIN, CYCLE_LENGTH_MAX),
    ):
        if field not in meta:
            continue
        value = meta[field]
        if isinstance(value, bool):
            _critical_authorization_state(f'{field} is invalid')
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            _critical_authorization_state(f'{field} is invalid')
        if not minimum <= parsed <= maximum or (
            isinstance(value, str) and str(parsed) != value.strip()
        ):
            _critical_authorization_state(f'{field} is invalid')
    anchor = meta.get('cycle_anchor_date')
    if anchor not in (None, ''):
        if not isinstance(anchor, str):
            _critical_authorization_state('cycle_anchor_date is invalid')
        try:
            datetime.strptime(anchor, '%Y-%m-%d')
        except ValueError:
            _critical_authorization_state('cycle_anchor_date is invalid')


def _build_static_access_plan(users, daily, meta, *, now=None, usage_version=None):
    """Derive the exact generated-proxy authorization set from core state.

    usage_version: (mtime_ns, size) of the usage_daily file the `daily` dict
    was loaded from, taken under usage_lock. When provided, the raw per-user
    cycle aggregation may be served from a small LRU cache. The cache never
    stores allow/deny decisions — disabled/expires/quota/uuid/multiplier are
    re-evaluated on every call."""
    current = now or local_now()
    if not _using_live_core_state():
        # Tests and alternate roots must never share the live aggregation
        # cache — one test's usage data must not leak into another.
        usage_version = None
    _validate_authorization_meta(meta)
    multiplier_path = DISPLAY_MULTIPLIER_STATE_FILE
    if not _using_live_core_state():
        multiplier_path = Path(USAGE_FILE).parent / Path(
            DISPLAY_MULTIPLIER_STATE_FILE
        ).name
    try:
        multiplier = display_config.effective_display_multiplier_strict(
            path=multiplier_path,
        )
    except ValueError as exc:
        raise state_store.CriticalStateUnavailable(
            'display multiplier policy is invalid',
        ) from exc
    cycle_days = cycle_util.cycle_days(current, meta=meta)
    plan = {}
    claimed_vless_uuids = {}
    for username, cfg in users.items():
        if not is_valid_username(username):
            _critical_authorization_state(
                f'invalid user key {username!r}',
            )
        config_error = user_compat.authorization_config_error(cfg)
        if config_error:
            _critical_authorization_state(
                f'user {username!r}: {config_error}',
            )
        if user_compat.is_inactive(cfg, today=current.date()):
            plan[username] = None
            continue
        quota = user_compat.total_quota_bytes(cfg)
        if user_compat.is_metered(cfg) and quota > 0:
            used = _cached_cycle_usage_sum(
                daily, cycle_days, username, version=usage_version,
                max_entries=_cycle_usage_cache_bound(len(users)),
            )
            if used * multiplier >= quota:
                plan[username] = None
                continue
        vless_uuid = str(cfg.get('vless_uuid') or '').strip()
        if vless_uuid:
            try:
                uuid_key = uuid.UUID(vless_uuid).hex
            except (ValueError, AttributeError, TypeError):
                _critical_authorization_state(
                    f'user {username!r}: vless_uuid is invalid',
                )
            previous = claimed_vless_uuids.get(uuid_key)
            if previous is not None:
                _critical_authorization_state(
                    f'users {previous!r} and {username!r} share vless_uuid',
                )
            claimed_vless_uuids[uuid_key] = username
            plan[username] = vless_uuid
    return plan


def _build_landing_access_plan(users, direct_plan, egress_nodes):
    """Derive fail-closed residential VLESS identities from active users."""
    result = {}
    claimed = {}
    for direct_user, value in (direct_plan or {}).items():
        if not value:
            continue
        try:
            key = uuid.UUID(str(value).strip()).hex
        except (ValueError, AttributeError, TypeError):
            _critical_authorization_state(
                f'user {direct_user!r}: active vless_uuid is invalid',
            )
        claimed[key] = f'direct:{direct_user}'
    nodes = egress_nodes if isinstance(egress_nodes, dict) else {}
    for username, cfg in sorted((users or {}).items()):
        if not direct_plan.get(username) or not isinstance(cfg, dict):
            continue
        allowed = cfg.get('landing_allowed_egress_ids')
        if allowed is None or allowed == []:
            continue
        if not isinstance(allowed, list):
            _critical_authorization_state(
                f'user {username!r}: landing authorization is invalid',
            )
        raw_uuid = str(cfg.get('landing_vless_uuid') or '').strip()
        try:
            parsed = uuid.UUID(raw_uuid)
        except (ValueError, AttributeError, TypeError):
            _critical_authorization_state(
                f'user {username!r}: landing_vless_uuid is invalid',
            )
        uuid_key = parsed.hex
        previous = claimed.get(uuid_key)
        if previous is not None:
            _critical_authorization_state(
                f'landing identity for {username!r} conflicts with {previous}',
            )
        allowed_ids = {
            value for value in allowed
            if isinstance(value, str) and value in nodes
        }
        if not allowed_ids:
            continue
        selected = cfg.get('landing_selected_egress_id')
        if selected not in allowed_ids:
            selected = None
        parsed_uuid = str(parsed)
        claimed[uuid_key] = f'landing:{username}'
        result[str(username)] = {
            'uuid': parsed_uuid,
            'selected_id': selected,
        }
    return result


def _sync_static_access_from_users(users, *, now=None):
    """Exact-reconcile both generated proxy configs. Caller holds usage_lock."""
    live = _using_live_core_state()
    if not live:
        # Alternate roots are validation/test artifacts. They must never read
        # host billing state or create generated credentials beside it.
        return False, False
    daily = load_json(USAGE_DAILY_FILE, {})
    # usage_lock is held, so the file cannot change between the load and this
    # stat — the version provably describes the dict we just read. It keys
    # the raw cycle-usage aggregation cache inside the plan builder.
    usage_version = _usage_daily_file_version()
    meta = load_meta()
    plan = _build_static_access_plan(
        users, daily, meta, now=now, usage_version=usage_version,
    )
    try:
        egress_registry = landing_egress.load_registry()
    except state_store.InvalidJsonState:
        egress_registry = landing_egress.empty_registry()
    egress_nodes = egress_registry['nodes']
    landing_plan = _build_landing_access_plan(users, plan, egress_nodes)
    xray_kwargs = {'prune_unknown': True}
    tuic_kwargs = {}
    try:
        xray_changed = xray_config.apply_user_plan(
            plan,
            landing_plan=landing_plan,
            egress_nodes=egress_nodes,
            **xray_kwargs,
        )
        static_access.recover_if_pending(
            xray_config.RELOAD_SERVICE,
            live=live,
        )
        tuic_changed = tuic_config.sync_user_plan(
            users, plan, **tuic_kwargs,
        )
        static_access.recover_if_pending(
            tuic_config.RELOAD_SERVICE,
            live=live,
        )
    except state_store.CriticalStateUnavailable:
        raise
    except Exception as exc:
        raise state_store.CriticalStateUnavailable(
            'generated static authorization could not be reconciled',
        ) from exc
    return xray_changed, tuic_changed


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
    m = current_display_multiplier()
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
        if (
            not isinstance(plain, str)
            or len(plain) > PASSWORD_MAX_LENGTH
            or not isinstance(stored_hash, str)
            or len(stored_hash) > PASSWORD_HASH_MAX_LENGTH
        ):
            return False
        algorithm, rounds_s, salt_b64, digest_b64 = stored_hash.split('$')
        if (
            algorithm != 'pbkdf2_sha256'
            or not rounds_s.isascii()
            or not rounds_s.isdigit()
        ):
            return False
        rounds = int(rounds_s)
        if not PBKDF2_ROUNDS_MIN <= rounds <= PBKDF2_ROUNDS_MAX:
            return False
        salt = base64.b64decode(
            salt_b64 + ('=' * (-len(salt_b64) % 4)),
            altchars=b'-_',
            validate=True,
        )
        expected = base64.b64decode(
            digest_b64 + ('=' * (-len(digest_b64) % 4)),
            altchars=b'-_',
            validate=True,
        )
        if (
            len(salt) != PBKDF2_SALT_BYTES
            or len(expected) != PBKDF2_DIGEST_BYTES
        ):
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', plain.encode('utf-8'), salt, rounds)
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


# In-memory login failure tracker: {ip: [timestamp, ...]}
# Bounded so an attacker rotating through many source IPs can't grow this
# dict without limit; entries are also dropped when their timestamp list
# decays to empty so cleanly-decayed IPs don't linger as zero-cost ghosts.
_login_failures: dict = {}
_user_login_failures: dict = {}
_login_failures_lock = threading.Lock()
_login_attempts_inflight: dict = {}
_LOGIN_MAX = 3        # max failures
_LOGIN_WINDOW = 3600  # seconds (1 hour)
_LOGIN_FAILURES_MAX_IPS = 1024


def _prune_failures_locked(ip, failures, now):
    times = [t for t in failures.get(ip, []) if now - t < _LOGIN_WINDOW]
    if times:
        failures[ip] = times
    else:
        failures.pop(ip, None)
    return times


def _is_rate_limited(ip, failures=None):
    failures = _login_failures if failures is None else failures
    with _login_failures_lock:
        times = _prune_failures_locked(ip, failures, time.time())
        return len(times) >= _LOGIN_MAX


def _record_failure(ip, failures=None):
    failures = _login_failures if failures is None else failures
    with _login_failures_lock:
        if ip not in failures and len(failures) >= _LOGIN_FAILURES_MAX_IPS:
            # Dicts preserve insertion order; evict the oldest tracked IP.
            oldest = next(iter(failures))
            failures.pop(oldest, None)
        failures.setdefault(ip, []).append(time.time())


def _begin_login_attempt(ip, failures=None):
    """Atomically reserve one of the allowed password-verification slots.

    Counting only after PBKDF2 verification lets a burst of concurrent
    requests all observe the same pre-failure state.  The short-lived
    reservation closes that race without holding the global mutex while the
    expensive hash runs.
    """
    failures = _login_failures if failures is None else failures
    key = (id(failures), ip)
    with _login_failures_lock:
        times = _prune_failures_locked(ip, failures, time.time())
        inflight = int(_login_attempts_inflight.get(key, 0))
        if len(times) + inflight >= _LOGIN_MAX:
            return False
        _login_attempts_inflight[key] = inflight + 1
        return True


def _finish_login_attempt(ip, succeeded, failures=None):
    failures = _login_failures if failures is None else failures
    key = (id(failures), ip)
    with _login_failures_lock:
        inflight = max(0, int(_login_attempts_inflight.get(key, 0)) - 1)
        if inflight:
            _login_attempts_inflight[key] = inflight
        else:
            _login_attempts_inflight.pop(key, None)
        if succeeded is True:
            failures.pop(ip, None)
            return
        if succeeded is None:
            return
        if ip not in failures and len(failures) >= _LOGIN_FAILURES_MAX_IPS:
            failures.pop(next(iter(failures)), None)
        failures.setdefault(ip, []).append(time.time())


def check_user_token(user, token):
    users = load_json(USERS_FILE, {})
    cfg = users.get(user)
    if not cfg:
        return None
    expected = str(cfg.get('sub_token') or '')
    supplied = str(token or '')
    if not _safe_secret_equal(supplied, expected):
        return None
    return cfg


def _visible_rotation_matches(user, new_token, new_uuid):
    """Check whether a post-replace durability error exposed our generation."""
    try:
        visible = state_store.load_json_strict(
            USERS_FILE,
            {},
            required=True,
        )
    except state_store.StateStoreError:
        return False
    cfg = visible.get(user)
    return (
        isinstance(cfg, dict)
        and _safe_secret_equal(cfg.get('sub_token'), new_token)
        and hmac.compare_digest(
            str(cfg.get('vless_uuid') or ''),
            str(new_uuid or ''),
        )
    )


def _save_users_for_rotation(
    users,
    *,
    user,
    new_token,
    new_uuid,
):
    """Return true when replace happened but directory durability is unknown."""
    try:
        save_json(USERS_FILE, users)
        return False
    except state_store.AtomicReplaceDurabilityUncertain:
        if _visible_rotation_matches(user, new_token, new_uuid):
            return True
        raise


def _rotate_user_token_if_current(
    user,
    posted,
    *,
    today=None,
    include_config=False,
    new_token=None,
    new_uuid=None,
):
    """Rotate every exported proxy credential after rechecking the old token."""
    def result(status, token='', xray=False, tuic=False, config=None):
        base = (status, token, xray, tuic)
        return (*base, config) if include_config else base

    with usage_lock():
        effective_today = today or local_now().date()
        users = load_json(USERS_FILE, {})
        cfg = users.get(user)
        if not isinstance(cfg, dict):
            return result('forbidden')
        expected = str(cfg.get('sub_token') or '')
        supplied = str(posted or '')
        if not _safe_secret_equal(supplied, expected):
            return result('forbidden')
        if cfg.get('disabled'):
            return result('disabled')
        if user_compat.is_expired(cfg, today=effective_today):
            return result('expired')
        new_token = str(new_token or secrets.token_urlsafe(18))
        new_uuid = str(new_uuid or uuid.uuid4())
        cfg['sub_token'] = new_token
        cfg['vless_uuid'] = new_uuid
        users[user] = cfg
        durability_uncertain = _save_users_for_rotation(
            users,
            user=user,
            new_token=new_token,
            new_uuid=new_uuid,
        )
        if durability_uncertain:
            raise CredentialRotationCommitted(
                user,
                new_token,
                cfg,
                durability_uncertain=True,
            )
        try:
            xray_changed, tuic_changed = _sync_static_access_from_users(
                users,
            )
        except state_store.CriticalStateUnavailable as exc:
            raise CredentialRotationCommitted(
                user, new_token, cfg,
            ) from exc
        return result(
            'ok', new_token, xray_changed, tuic_changed, dict(cfg),
        )


def parse_cookies(handler):
    raw = handler.headers.get('Cookie', '')
    ck = SimpleCookie()
    try:
        ck.load(raw)
    except Exception:
        return {}
    return {k: v.value for k, v in ck.items()}


def parse_query_params(path):
    try:
        return parse_qs(
            urlparse(path).query,
            max_num_fields=http_utils.MAX_FORM_FIELDS,
        )
    except ValueError:
        return {}


def _safe_secret_equal(supplied, expected):
    left = str(supplied or '')
    right = str(expected or '')
    if (
        not left
        or not right
        or len(left) > PASSWORD_MAX_LENGTH
        or len(right) > PASSWORD_MAX_LENGTH
    ):
        return False
    return hmac.compare_digest(
        left.encode('utf-8'),
        right.encode('utf-8'),
    )


def is_secure_request(handler):
    return http_utils.is_secure_request(handler)


def is_same_origin_post(handler):
    return http_utils.is_same_origin_post(handler)


def session_cookie(sid, *, max_age=SESSION_TTL, secure=False):
    return http_utils.session_cookie(sid, max_age=max_age, secure=secure)


def clear_session_cookie(*, secure=False):
    return http_utils.clear_session_cookie(secure=secure)


def _session_lock_file(path):
    return Path(str(path) + '.lock')


def _credential_generation(stored_hash):
    """Stable, non-secret marker used to invalidate sessions after a rekey."""
    value = str(stored_hash or '').encode('utf-8')
    return hashlib.sha256(value).hexdigest() if value else ''


def _alive_sessions(path):
    sessions = load_json(path, {})
    if not isinstance(sessions, dict):
        sessions = {}
    now = int(time.time())
    alive = {}
    for sid, info in sessions.items():
        if not isinstance(info, dict):
            continue
        try:
            expires_at = int(info.get('exp', 0))
        except (TypeError, ValueError):
            continue
        if expires_at > now:
            alive[sid] = info
    return sessions, alive


def _get_sessions(path):
    with state_store.file_lock(
        _session_lock_file(path),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        sessions, alive = _alive_sessions(path)
        if alive != sessions:
            save_json(path, alive)
    return alive


def _create_session(
    path, username, credential_generation='', credential_kind='',
):
    with state_store.file_lock(
        _session_lock_file(path),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        _sessions, alive = _alive_sessions(path)
        # Successful logins are attacker-controlled for any valid account.
        # Bound the file so repeated logins cannot grow every request's JSON
        # parse/write cost without limit. `exp` is monotonic with creation time.
        own = sorted(
            (
                (sid, info)
                for sid, info in alive.items()
                if info.get('user') == username
            ),
            key=lambda item: int(item[1].get('exp', 0)),
            reverse=True,
        )
        keep_own = {
            sid for sid, _info in own[: SESSION_MAX_PER_IDENTITY - 1]
        }
        alive = {
            sid: info
            for sid, info in alive.items()
            if info.get('user') != username or sid in keep_own
        }
        if len(alive) >= SESSION_MAX_GLOBAL:
            newest = sorted(
                alive.items(),
                key=lambda item: int(item[1].get('exp', 0)),
                reverse=True,
            )
            alive = dict(newest[: SESSION_MAX_GLOBAL - 1])
        sid = secrets.token_urlsafe(24)
        info = {'user': username, 'exp': int(time.time()) + SESSION_TTL}
        if credential_generation:
            info['credential_generation'] = credential_generation
        if credential_kind:
            info['credential_kind'] = credential_kind
        alive[sid] = info
        save_json(path, alive)
        return sid


def _delete_session(path, sid):
    if not sid:
        return
    with state_store.file_lock(
        _session_lock_file(path),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        sessions, alive = _alive_sessions(path)
        if sid in alive:
            del alive[sid]
        if alive != sessions:
            save_json(path, alive)


def _delete_sessions_for(path, username):
    with state_store.file_lock(
        _session_lock_file(path),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        sessions, alive = _alive_sessions(path)
        kept = {sid: info for sid, info in alive.items() if info.get('user') != username}
        if kept != sessions:
            save_json(path, kept)


def _replace_sessions_with_new(
    path, username, *, revoke_all=False, credential_generation='',
    credential_kind='',
):
    """Revoke matching sessions and mint the replacement in one transaction."""
    with state_store.file_lock(
        _session_lock_file(path),
        timeout=STATE_LOCK_TIMEOUT_SECONDS,
    ):
        _sessions, alive = _alive_sessions(path)
        if revoke_all:
            alive = {}
        else:
            alive = {
                sid: info for sid, info in alive.items()
                if info.get('user') != username
            }
        sid = secrets.token_urlsafe(24)
        info = {'user': username, 'exp': int(time.time()) + SESSION_TTL}
        if credential_generation:
            info['credential_generation'] = credential_generation
        if credential_kind:
            info['credential_kind'] = credential_kind
        alive[sid] = info
        save_json(path, alive)
        return sid


def get_sessions():
    return _get_sessions(SESSIONS_FILE)


def create_session(username='admin', credential_generation=''):
    return _create_session(SESSIONS_FILE, username, credential_generation)


def delete_session(sid):
    _delete_session(SESSIONS_FILE, sid)


def get_user_sessions():
    return _get_sessions(USER_SESSIONS_FILE)


def create_user_session(
    username,
    credential_generation='',
    credential_kind=USER_SESSION_PANEL_PASSWORD,
):
    if credential_kind not in USER_SESSION_CREDENTIAL_KINDS:
        raise ValueError('invalid user session credential kind')
    return _create_session(
        USER_SESSIONS_FILE,
        username,
        credential_generation,
        credential_kind,
    )


def delete_user_session(sid):
    _delete_session(USER_SESSIONS_FILE, sid)


def delete_user_sessions_for(username):
    _delete_sessions_for(USER_SESSIONS_FILE, username)


def _scoped_state_path(path):
    """Keep alternate/test state roots isolated from the host runtime."""
    target = Path(path)
    if _using_live_core_state():
        return target
    return Path(USAGE_FILE).parent / target.name


@dataclass(frozen=True)
class RecoverableRotationResult:
    status: str
    new_token: str = ''
    user_config: dict | None = None
    xray_changed: bool = False
    tuic_changed: bool = False
    sync_pending: bool = False
    durability_uncertain: bool = False
    sync_error: Exception | None = None
    task_id: str = ''
    replayed: bool = False


def _rotation_receipts_path():
    state_root = Path(USAGE_FILE).parent
    if state_root != Path('/root/hysteria/state'):
        return state_root / Path(ROTATION_RECEIPTS_FILE).name
    return Path(ROTATION_RECEIPTS_FILE)


def _revocation_queue_path():
    state_root = Path(USAGE_FILE).parent
    if state_root != Path('/root/hysteria/state'):
        return state_root / Path(REVOCATION_QUEUE_FILE).name
    return Path(REVOCATION_QUEUE_FILE)


def _rotation_session_allows(sid, user, posted, cfg):
    """Validate the browser session used to initiate a fresh rotation."""
    if not sid or not isinstance(cfg, dict):
        return False
    info = get_user_sessions().get(sid)
    if not isinstance(info, dict) or info.get('user') != user:
        return False
    kind = str(
        info.get('credential_kind') or USER_SESSION_PANEL_PASSWORD,
    )
    generation = str(info.get('credential_generation') or '')
    if kind == USER_SESSION_SUBSCRIPTION_TOKEN:
        return bool(generation) and hmac.compare_digest(
            generation,
            _credential_generation(posted),
        )
    if kind == USER_SESSION_PANEL_PASSWORD:
        current = _credential_generation(
            cfg.get('panel_pass_hash'),
        )
        # Preserve legacy password sessions that predate generation binding;
        # newly minted sessions always carry the generation.
        return not generation or (
            bool(current) and hmac.compare_digest(generation, current)
        )
    return False


def _recoverable_user_rotation(
    user,
    posted,
    *,
    request_id,
    session_id,
    today=None,
):
    """Commit or replay one browser-bound self-service token rotation."""
    if not rotation_recovery.valid_request_id(request_id):
        return RecoverableRotationResult('bad_request')
    receipt_path = _rotation_receipts_path()
    queue_path = _revocation_queue_path()
    receipt = rotation_recovery.lookup_bound(
        receipt_path,
        user=user,
        request_id=request_id,
        session_id=session_id,
    )
    replayed = receipt is not None
    with usage_lock():
        effective_today = today or local_now().date()
        users = load_json(USERS_FILE, {})
        cfg = users.get(user)
        if not isinstance(cfg, dict):
            return RecoverableRotationResult('forbidden')
        current_token = str(cfg.get('sub_token') or '')
        current_generation = _credential_generation(current_token)
        if receipt is None:
            # A concurrent copy of the same HTTP request may have prepared the
            # receipt while this request waited for the canonical user lock.
            receipt = rotation_recovery.lookup_bound(
                receipt_path,
                user=user,
                request_id=request_id,
                session_id=session_id,
            )
            replayed = receipt is not None

        if receipt is None:
            if not _rotation_session_allows(
                session_id, user, posted, cfg,
            ):
                return RecoverableRotationResult('forbidden')
            if not _safe_secret_equal(posted, current_token):
                return RecoverableRotationResult('forbidden')
            if cfg.get('disabled'):
                return RecoverableRotationResult('disabled')
            if user_compat.is_expired(cfg, today=effective_today):
                return RecoverableRotationResult('expired')
            new_token = secrets.token_urlsafe(18)
            new_uuid = str(uuid.uuid4())
            new_generation = _credential_generation(new_token)
            try:
                receipt = rotation_recovery.prepare(
                    receipt_path,
                    user=user,
                    request_id=request_id,
                    session_id=session_id,
                    old_generation=current_generation,
                    new_generation=new_generation,
                    new_token=new_token,
                    new_uuid=new_uuid,
                )
            except PermissionError:
                return RecoverableRotationResult('forbidden')
        else:
            new_token = str(receipt.get('new_token') or '')
            new_uuid = str(receipt.get('new_uuid') or '')
            new_generation = str(
                receipt.get('new_generation') or '',
            )
            try:
                receipt = rotation_recovery.prepare(
                    receipt_path,
                    user=user,
                    request_id=request_id,
                    session_id=session_id,
                    old_generation=str(
                        receipt.get('old_generation') or '',
                    ),
                    new_generation=new_generation,
                    new_token=new_token,
                    new_uuid=new_uuid,
                )
            except PermissionError:
                return RecoverableRotationResult('forbidden')

        new_token = str(receipt.get('new_token') or '')
        new_uuid = str(receipt.get('new_uuid') or '')
        new_generation = str(receipt.get('new_generation') or '')
        previous_generation = str(
            receipt.get('old_generation') or '',
        )
        task_id = revocation_queue.task_id_for(user, request_id)
        try:
            revocation_queue.prepare(
                queue_path,
                task_id=task_id,
                user=user,
                previous_generation=previous_generation,
                target_generation=new_generation,
                static_services=static_access.SERVICES,
            )
        except PermissionError:
            return RecoverableRotationResult('conflict')

        current_token = str(cfg.get('sub_token') or '')
        current_generation = _credential_generation(current_token)
        canonical_matches = (
            hmac.compare_digest(current_generation, new_generation)
            and hmac.compare_digest(
                str(cfg.get('vless_uuid') or ''),
                new_uuid,
            )
        )
        if not canonical_matches:
            if (
                not hmac.compare_digest(
                    current_generation,
                    previous_generation,
                )
                or not _safe_secret_equal(posted, current_token)
            ):
                rotation_recovery.discard(
                    receipt_path,
                    user=user,
                    request_id=request_id,
                )
                revocation_queue.discard(queue_path, task_id)
                return RecoverableRotationResult('conflict')
            cfg['sub_token'] = new_token
            cfg['vless_uuid'] = new_uuid
            users[user] = cfg

        # Rewriting a replayed matching generation establishes a fresh
        # directory-fsync point after a prior uncertain post-replace failure.
        durability_uncertain = _save_users_for_rotation(
            users,
            user=user,
            new_token=new_token,
            new_uuid=new_uuid,
        )
        if durability_uncertain:
            return RecoverableRotationResult(
                'ok',
                new_token=new_token,
                user_config=dict(cfg),
                sync_pending=True,
                durability_uncertain=True,
                sync_error=CredentialRotationCommitted(
                    user,
                    new_token,
                    cfg,
                    durability_uncertain=True,
                ),
                task_id=task_id,
                replayed=replayed,
            )
        try:
            xray_changed, tuic_changed = _sync_static_access_from_users(
                users,
            )
        except state_store.CriticalStateUnavailable as exc:
            return RecoverableRotationResult(
                'ok',
                new_token=new_token,
                user_config=dict(cfg),
                sync_pending=True,
                sync_error=CredentialRotationCommitted(
                    user,
                    new_token,
                    cfg,
                ),
                task_id=task_id,
                replayed=replayed,
            )
        return RecoverableRotationResult(
            'ok',
            new_token=new_token,
            user_config=dict(cfg),
            xray_changed=bool(xray_changed),
            tuic_changed=bool(tuic_changed),
            task_id=task_id,
            replayed=replayed,
        )


def _action_succeeded(result):
    if isinstance(
        result,
        (CredentialActionResult, static_access.ServiceActionResult),
    ):
        return result.ok
    # Existing integrations historically returned None on accepted kick.
    return result is None or result is True


def _schedule_static_reload(service, *, changed):
    if not _using_live_core_state():
        return CredentialActionResult(
            action='static_reload',
            target=service,
            attempted=False,
            ok=True,
            code='not_live',
            retryable=False,
        )
    module = (
        xray_config
        if service == static_access.XRAY_SERVICE
        else tuic_config
    )
    loader = module.reload_async
    marker = module._reload_pending_path(module.CONFIG_FILE)
    if not changed and not marker.exists():
        return CredentialActionResult(
            action='static_reload',
            target=service,
            attempted=False,
            ok=True,
            code='already_applied',
            retryable=False,
        )
    try:
        scheduled = loader()
    except Exception as exc:
        return CredentialActionResult(
            action='static_reload',
            target=service,
            attempted=True,
            ok=False,
            code=type(exc).__name__,
            retryable=True,
        )
    # A false return with no marker means the generation was already ACKed
    # (or completed in the scheduling race). A retained marker is the durable
    # signal that this handoff still needs attention.
    marker_pending = marker.exists()
    ok = scheduled is True or (not changed and not marker_pending)
    return CredentialActionResult(
        action='static_reload',
        target=service,
        attempted=True,
        ok=ok,
        code=(
            'scheduled'
            if scheduled is True
            else (
                'already_applied'
                if not changed and not marker_pending
                else 'not_scheduled'
            )
        ),
        retryable=not ok,
    )


def _record_static_retry(task_id, services):
    if not task_id or not services:
        return True
    try:
        return revocation_queue.add_static_services(
            _revocation_queue_path(),
            task_id,
            services,
        )
    except (state_store.StateStoreError, OSError):
        return False


def _record_kick_attempt(
    task_id,
    result,
    *,
    completed_static_services=(),
):
    if not task_id:
        return False
    try:
        revocation_queue.complete_attempt(
            _revocation_queue_path(),
            task_id,
            kick_ok=_action_succeeded(result),
            stopped_services=completed_static_services,
        )
        return True
    except (state_store.StateStoreError, OSError):
        return False


def _attempt_revocation_side_effects(
    task_id,
    username,
    *,
    xray_changed=False,
    tuic_changed=False,
    sync_error=None,
):
    """Attempt one durable static-auth and Hysteria revocation handoff."""
    uncertain = False
    static_outcomes = {}
    completed_static_services = []
    if sync_error is not None:
        static_outcomes = _fail_closed_static_access(sync_error)
        completed_static_services.extend(
            service
            for service, outcome in static_outcomes.items()
            if outcome.ok
        )
    else:
        for service, changed in (
            (static_access.XRAY_SERVICE, xray_changed),
            (static_access.TUIC_SERVICE, tuic_changed),
        ):
            reload_result = _schedule_static_reload(
                service,
                changed=bool(changed),
            )
            if reload_result.ok:
                completed_static_services.append(service)
                continue
            raw = static_access.stop_fail_closed(
                service,
                reason=RuntimeError(
                    'credential reload scheduling failed',
                ),
                live=_using_live_core_state(),
            )
            outcome = _normalize_service_action(service, raw)
            static_outcomes[service] = outcome
            if outcome.ok:
                completed_static_services.append(service)

    retry_services = [
        service
        for service, outcome in static_outcomes.items()
        if not outcome.ok
    ]
    if retry_services and not _record_static_retry(
        task_id,
        retry_services,
    ):
        uncertain = True

    kick_result = hy_kick([username])
    kick_recorded = _record_kick_attempt(
        task_id,
        kick_result,
        completed_static_services=completed_static_services,
    )
    if not _action_succeeded(kick_result) or not kick_recorded:
        uncertain = True
    if any(
        not outcome.effect_confirmed
        for outcome in static_outcomes.values()
    ):
        uncertain = True
    return {
        'uncertain': uncertain,
        'static_outcomes': static_outcomes,
        'kick_result': kick_result,
    }


def _process_one_revocation_task():
    """Retry one durable task; all process/network I/O stays outside locks."""
    path = _revocation_queue_path()
    task = revocation_queue.claim_due(path)
    if not task:
        return False
    task_id = task['task_id']
    static_pending = tuple(task.get('static_services', ()))
    is_delete = _is_delete_revocation_task(task)
    sync_error = None
    xray_changed = False
    tuic_changed = False
    generation = ''
    try:
        with usage_lock():
            users = load_json(USERS_FILE, {})
            cfg = users.get(task['user'])
            if is_delete:
                current_delete_generation = (
                    _delete_previous_generation(cfg)
                    if isinstance(cfg, dict)
                    else ''
                )
                expected_delete_generation = str(
                    task.get('previous_generation') or '',
                )
                superseded = task['user'] in users and not (
                    current_delete_generation
                    and hmac.compare_digest(
                        current_delete_generation,
                        expected_delete_generation,
                    )
                )
                if not superseded:
                    if task['user'] in users:
                        users.pop(task['user'], None)
                    # Even an already-absent user is rewritten before any
                    # destructive history cleanup. This establishes a fresh,
                    # explicit durability point after a prior post-replace
                    # directory-fsync uncertainty.
                    save_json(USERS_FILE, users)
                    _purge_user_history_locked(task['user'])
                # A different complete revision is a same-name replacement,
                # not the account incarnation named by this WAL record. Keep
                # its data intact, but still reconcile current static auth and
                # perform the username-wide kicks that retire old sessions.
                generation = _DELETE_TARGET_GENERATION
            else:
                generation = _credential_generation(
                    cfg.get('sub_token') if isinstance(cfg, dict) else '',
                )
            if (
                static_pending
                and (
                    is_delete
                    or hmac.compare_digest(
                        generation,
                        str(task.get('target_generation') or ''),
                    )
                )
            ):
                try:
                    xray_changed, tuic_changed = (
                        _sync_static_access_from_users(users)
                    )
                except (state_store.StateStoreError, OSError) as exc:
                    sync_error = exc
    except (state_store.StateStoreError, OSError) as exc:
        try:
            revocation_queue.release_claim(path, task_id)
        except (state_store.StateStoreError, OSError):
            pass
        if _state_failure_requires_static_stop(exc):
            _fail_closed_static_access(exc)
        return False
    if not hmac.compare_digest(
        generation,
        str(task.get('target_generation') or ''),
    ):
        if hmac.compare_digest(
            generation,
            str(task.get('previous_generation') or ''),
        ):
            if int(task.get('expires_at', 0)) <= int(time.time()):
                # A generic rotation whose previous generation is still
                # canonical after its replay window never committed. It is
                # safe to retire this pre-commit intent so abandoned requests
                # cannot consume bounded queue capacity forever. Deletions do
                # not take this branch: their WAL is itself authorization to
                # redo the bound account deletion.
                revocation_queue.discard(path, task_id)
            else:
                # Intent was durably prepared but canonical commit has not
                # become visible yet. Leave it armed for the request replay.
                revocation_queue.release_claim(path, task_id)
        else:
            # A newer generation superseded this task. Its own task covers the
            # username-wide kick, so the stale intent can be discarded.
            revocation_queue.discard(path, task_id)
        return False

    completed_static = []
    if sync_error is not None:
        outcomes = _fail_closed_static_access(sync_error)
        completed_static.extend(
            service
            for service in static_pending
            if outcomes.get(service) is not None
            and outcomes[service].ok
        )
    else:
        changed_by_service = {
            static_access.XRAY_SERVICE: bool(xray_changed),
            static_access.TUIC_SERVICE: bool(tuic_changed),
        }
        for service in static_pending:
            reload_result = _schedule_static_reload(
                service,
                changed=changed_by_service.get(service, False),
            )
            if reload_result.ok:
                completed_static.append(service)
                continue
            raw = static_access.stop_fail_closed(
                service,
                reason=RuntimeError(
                    'credential revocation reload retry failed',
                ),
                live=_using_live_core_state(),
            )
            outcome = _normalize_service_action(service, raw)
            if outcome.ok:
                completed_static.append(service)
    kick_result = hy_kick([task['user']])
    try:
        revocation_queue.complete_attempt(
            path,
            task_id,
            kick_ok=_action_succeeded(kick_result),
            stopped_services=completed_static,
        )
    except (state_store.StateStoreError, OSError):
        return False
    return True


def _prune_expired_rotation_receipts():
    try:
        removed = rotation_recovery.prune_expired(
            _rotation_receipts_path(),
        )
        _reset_worker_error_log('rotation_receipt_cleanup')
        return removed
    except (state_store.StateStoreError, OSError) as exc:
        _log_worker_error_throttled(
            'rotation_receipt_cleanup',
            exc,
            'credential rotation receipt cleanup deferred',
        )
        return 0


def _reset_worker_error_log(category):
    with _WORKER_ERROR_LOG_LOCK:
        _WORKER_ERROR_LOG_STATE.pop(str(category), None)


def _log_worker_error_throttled(category, exc, message):
    """Log only exception type with bounded exponential suppression."""
    key = str(category)
    now = time.monotonic()
    with _WORKER_ERROR_LOG_LOCK:
        state = _WORKER_ERROR_LOG_STATE.get(key, {})
        next_log_at = float(state.get('next_log_at', 0.0))
        delay = float(
            state.get(
                'delay',
                _WORKER_ERROR_LOG_INITIAL_SECONDS,
            ),
        )
        if now < next_log_at:
            return False
        print(
            f'{message}: {type(exc).__name__}',
            file=sys.stderr,
        )
        _WORKER_ERROR_LOG_STATE[key] = {
            'next_log_at': now + delay,
            'delay': min(delay * 2, _WORKER_ERROR_LOG_MAX_SECONDS),
        }
        return True


def _revocation_worker_loop(stop_event):
    while not stop_event.wait(1.0):
        _prune_expired_rotation_receipts()
        # Bound work per wake so a damaged endpoint cannot monopolize the
        # subscription service.
        for _index in range(8):
            try:
                progressed = _process_one_revocation_task()
            except Exception as exc:
                _log_worker_error_throttled(
                    'credential_revocation_retry',
                    exc,
                    'credential revocation retry paused',
                )
                break
            _reset_worker_error_log('credential_revocation_retry')
            if not progressed:
                break


def _clear_alert_dedup_for_users(usernames, *, quota_only):
    """Best-effort alert-state transaction for reset/delete operations.

    Alert state is auxiliary. A corrupt file must remain untouched for
    operator repair, but it must not roll back an otherwise valid accounting
    reset or user deletion.
    """
    alert_path = _scoped_state_path(alerts.STATE_FILE)
    try:
        if quota_only:
            alerts.clear_quota_dedup_transaction(usernames, alert_path)
        else:
            alerts.clear_user_dedup_transaction(usernames, alert_path)
        return True
    except (state_store.StateStoreError, OSError) as exc:
        print(
            f'alert dedup update skipped: auxiliary state unavailable: {exc}',
            file=sys.stderr,
        )
        return False


def _purge_user_history_locked(username):
    """Remove user-keyed state before hard deletion. Caller holds usage_lock."""
    for configured_path in (
        USAGE_FILE,
        USAGE_DAILY_FILE,
        USAGE_HOURLY_FILE,
        USAGE_PRESERVED_FILE,
    ):
        path = _scoped_state_path(configured_path)
        data = load_json(path, {})
        changed = False
        for bucket in data.values():
            if isinstance(bucket, dict) and username in bucket:
                bucket.pop(username, None)
                changed = True
        if changed:
            save_json(path, data)

    online_path = _scoped_state_path(ONLINE_FILE)
    online = load_json(online_path, {})
    if username in online:
        online.pop(username, None)
        save_json(online_path, online)

    admission_path = _scoped_state_path(DEVICE_ADMISSIONS_FILE)
    with state_store.file_lock(
        admission_path.with_name(admission_path.name + '.lock')
    ):
        admissions = load_json(admission_path, {})
        if username in admissions:
            admissions.pop(username, None)
            save_json(admission_path, admissions)

    _clear_alert_dedup_for_users([username], quota_only=False)

    _delete_sessions_for(
        _scoped_state_path(USER_SESSIONS_FILE),
        username,
    )


def user_session_cookie(sid, *, max_age=SESSION_TTL, secure=False):
    cookie = f'usid={sid}; Path=/; Max-Age={max_age}; HttpOnly; SameSite=Lax'
    if secure:
        cookie += '; Secure'
    return cookie


def clear_user_session_cookie(*, secure=False):
    cookie = 'usid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
    if secure:
        cookie += '; Secure'
    return cookie


def get_logged_in_user_context(handler):
    """Return the authenticated user and the credential that minted the session."""
    sid = parse_cookies(handler).get('usid', '')
    info = get_user_sessions().get(sid)
    if not isinstance(info, dict):
        return '', ''
    username = str(info.get('user') or '')
    if not is_valid_username(username):
        delete_user_session(sid)
        return '', ''
    kind = str(
        info.get('credential_kind') or USER_SESSION_PANEL_PASSWORD
    )
    if kind not in USER_SESSION_CREDENTIAL_KINDS:
        delete_user_session(sid)
        return '', ''
    generation = str(info.get('credential_generation') or '')
    if kind == USER_SESSION_SUBSCRIPTION_TOKEN and not generation:
        delete_user_session(sid)
        return '', ''
    if generation:
        cfg = load_json(USERS_FILE, {}).get(username)
        credential = (
            cfg.get('sub_token')
            if (
                isinstance(cfg, dict)
                and kind == USER_SESSION_SUBSCRIPTION_TOKEN
            )
            else (
                cfg.get('panel_pass_hash')
                if isinstance(cfg, dict)
                else ''
            )
        )
        current = _credential_generation(
            credential,
        )
        if not current or not hmac.compare_digest(generation, current):
            delete_user_session(sid)
            return '', ''
    return username, kind


def get_logged_in_user(handler):
    return get_logged_in_user_context(handler)[0]


def user_panel_access_error(
    cfg,
    session_kind,
    *,
    today=None,
):
    """Return the first authorization state blocking an active user panel.

    Authentication and account lifecycle are deliberately separate: an
    already-issued session may remain cryptographically valid after an
    administrator disables the account or its expiry date passes.  Every
    protected panel route uses this helper so password- and token-minted
    sessions apply the same lifecycle policy, while only password sessions are
    subject to the initial-password-change gate.
    """
    if not isinstance(cfg, dict):
        return 'forbidden'
    if (
        session_kind == USER_SESSION_PANEL_PASSWORD
        and cfg.get('panel_password_must_change')
    ):
        return 'password_change_required'
    if cfg.get('disabled'):
        return 'disabled'
    effective_today = today or local_now().date()
    if user_compat.is_expired(cfg, today=effective_today):
        return 'expired'
    return ''


def is_logged_in(handler):
    q = parse_query_params(handler.path)
    token = (q.get('token') or [''])[0]
    meta = load_meta()
    admin_token = str(meta.get('admin_token') or '')
    if _safe_secret_equal(token, admin_token):
        return True
    sid = parse_cookies(handler).get('sid', '')
    sessions = get_sessions()
    info = sessions.get(sid)
    if not isinstance(info, dict):
        return False
    generation = str(info.get('credential_generation') or '')
    if generation:
        current = _credential_generation(meta.get('admin_pass_hash'))
        if not current or not hmac.compare_digest(generation, current):
            delete_session(sid)
            return False
    return True


def render_panel_link_required():
    """Shown when /user/panel is opened without a user session.

    Share recipients should not land on the admin/user login form. This page
    has no username, no token field, and no credential prompt — only a
    pointer back to the dedicated login route for password users who
    arrived here by mistake.
    """
    body = '''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>
<div class="auth-scene auth-scene-single">
  <div class="auth-card">
    <div class="auth-card-brand">
      <div class="auth-card-logo">H</div>
      <div class="auth-card-brand-text">
        <strong>Hysteria</strong>
        <small>用户面板</small>
      </div>
    </div>
    <h1 class="auth-card-title">请使用管理员提供的专属链接</h1>
    <p class="auth-card-subtitle">此页面需要通过管理员发给你的专属面板链接打开。链接打开后地址栏不再包含密钥。</p>
    <a class="auth-back" href="/login">前往登录</a>
  </div>
</div>'''
    return html_page('专属链接 · Hysteria', body, body_class='page-auth')


def html_page(title, body, body_class=''):
    cls = f' class="{body_class}"' if body_class else ''
    css_version = BASE_CSS_ETAG.strip('"')
    page_body = body if body_class == 'has-shell' else (
        '<a class="skip-link" href="#main-content">跳到主内容</a>'
        f'<main id="main-content" tabindex="-1">{body}</main>'
    )
    return (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light">'
        f'<meta name="theme-color" content="#F8F7F3">'
        f'<title>{html.escape(title)}</title>'
        f'<link rel="stylesheet" href="/static/style.css?v={css_version}">'
        f'</head><body{cls}>{page_body}</body></html>'
    )


# Inline SVG icons (24×24 stroke icons, sized down via .sidebar-link svg).
_ICONS = {
    'traffic': '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 4v16h18"/><path d="m6 14 4-5 4 3 6-7"/><path d="M16 5h4v4"/></svg>',
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
    raw = _ICONS.get(name, '')
    return raw.replace('<svg ', '<svg aria-hidden="true" focusable="false" ', 1) if raw else ''


def render_alert(msg, kind='flash', *, element_id=''):
    if not msg:
        return ''
    role = 'alert' if kind == 'err' else 'status'
    live = 'assertive' if kind == 'err' else 'polite'
    id_attr = f' id="{html.escape(str(element_id), quote=True)}"' if element_id else ''
    return (
        f'<div{id_attr} class="{kind}" role="{role}" aria-live="{live}" aria-atomic="true">'
        f'{html.escape(msg)}</div>'
    )


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


def render_logout_confirmation(host, *, user_panel=False):
    action = '/user/logout' if user_panel else '/logout'
    cancel = '/user/panel' if user_panel else '/admin'
    title = '退出用户面板？' if user_panel else '退出管理后台？'
    body = f'''<div class="auth-page">
<div class="auth-wrap">
<div class="auth-card">
  <div class="auth-brand">
    <span class="auth-logo">H</span>
    <div class="auth-brand-text">
      <strong>{html.escape(host)}</strong>
      <small>安全退出</small>
    </div>
  </div>
  <h1 class="auth-title">{title}</h1>
  <p class="auth-subtitle">确认后会结束当前设备的登录会话；其他设备不受影响。</p>
  <form method="post" action="{action}">
    <button class="btn danger-btn mt-md" type="submit" style="width:100%;">确认退出</button>
  </form>
  <a class="auth-back" href="{cancel}">返回</a>
</div>
</div>'''
    return html_page('确认退出', body)


_SIDEBAR_NAV = [
    ('dashboard', '/admin', '总览', 'dashboard'),
    ('usage', '/admin/usage', '流量分析', 'traffic'),
    ('codex', '/admin/codex', 'Codex 额度', 'chart'),
    ('incidents', '/admin/incidents', '事故处理', 'pulse'),
    ('health', '/admin/health', '健康状态', 'pulse'),
    ('config', '/admin/config', '模板配置', 'config'),
    ('rules', '/admin/rules', '路由规则', 'rules'),
    ('landing-egresses', '/admin/landing-egresses', '家宽出口', 'rules'),
    ('logs', '/admin/logs', '清零日志', 'logs'),
    ('settings', '/admin/settings', '设置', 'lock'),
]


def _landing_registry_or_empty():
    try:
        return landing_egress.load_registry()
    except (state_store.InvalidJsonState, OSError):
        return landing_egress.empty_registry()


def _authorized_landing_nodes(cfg, registry=None):
    registry = registry or _landing_registry_or_empty()
    nodes = registry.get('nodes', {}) if isinstance(registry, dict) else {}
    allowed = cfg.get('landing_allowed_egress_ids', []) if isinstance(cfg, dict) else []
    if not isinstance(allowed, list):
        return []
    return [
        nodes[node_id]
        for node_id in allowed
        if isinstance(node_id, str)
        and isinstance(nodes.get(node_id), dict)
        and nodes[node_id].get('enabled') is True
    ]


def _enabled_landing_public_nodes(registry=None):
    registry = registry or _landing_registry_or_empty()
    nodes = registry.get('nodes', {}) if isinstance(registry, dict) else {}
    return [
        landing_egress.public_node(node)
        for _node_id, node in sorted(nodes.items())
        if isinstance(node, dict) and node.get('enabled') is True
    ]


def _ensure_landing_vless_uuid(cfg, users):
    current = str(cfg.get('landing_vless_uuid') or '').strip()
    if current:
        return current

    def uuid_identity(value):
        raw = str(value or '').strip()
        if not raw:
            return ''
        try:
            return uuid.UUID(raw).hex
        except (ValueError, AttributeError):
            # Invalid persisted values remain occupied as raw identities here;
            # their schema validation still fails closed in config generation.
            return 'raw:' + raw.lower()

    occupied = {
        uuid_identity(item.get(field))
        for item in users.values() if isinstance(item, dict)
        for field in ('vless_uuid', 'landing_vless_uuid')
    }
    occupied.add(uuid_identity(cfg.get('vless_uuid')))
    occupied.discard('')
    while True:
        candidate = str(uuid.uuid4())
        if uuid_identity(candidate) not in occupied:
            cfg['landing_vless_uuid'] = candidate
            return candidate


def render_landing_egress_selector(cfg, *, password_session):
    nodes = _authorized_landing_nodes(cfg)
    if not nodes:
        return ''
    selected = str(cfg.get('landing_selected_egress_id') or '')
    rows = []
    options = []
    for node in nodes:
        public = landing_egress.public_node(node)
        node_id = public['id']
        checked = ' selected' if node_id == selected else ''
        options.append(
            f'<option value="{html.escape(node_id, quote=True)}"{checked}>'
            f'{html.escape(public["name"])}</option>'
        )
        health = public.get('health') or {}
        health_label = str(health.get('status') or '未探测')
        rows.append(
            '<div><dt>' + html.escape(public['name']) + '</dt><dd>'
            '<code class="mono">' + html.escape(public['exit_ip']) + '</code>'
            + (' · ' + html.escape(public.get('isp', '')) if public.get('isp') else '')
            + (' · ' + html.escape(public.get('region', '')) if public.get('region') else '')
            + ' · ' + html.escape(health_label) + '</dd></div>'
        )
    if password_session:
        action = (
            '<form method="post" action="/user/landing-egress/select" class="mt-md">'
            f'<input type="hidden" name="user_revision" value="{user_config_revision(cfg)}">'
            '<label>选择家宽出口<select name="egress_id" required>'
            + ''.join(options)
            + '</select></label>'
            '<button class="btn primary mt-sm" type="submit">切换出口</button>'
            '<div class="small faint mt-sm">切换前会验证真实出口 IP；60 秒内只能切换一次。</div>'
            '</form>'
        )
    else:
        action = '<div class="small faint mt-md">使用面板密码登录后可切换。</div>'
    return (
        '<aside class="plan-section" aria-label="真实家宽出口">'
        '<header class="section-head"><h2 class="section-title">真实家宽出口</h2></header>'
        '<dl class="user-kv">' + ''.join(rows) + '</dl>' + action + '</aside>'
    )


def render_landing_egresses(host, flash=''):
    registry = _landing_registry_or_empty()
    users = load_json(USERS_FILE, {})
    cards = []
    for node_id, node in sorted(registry.get('nodes', {}).items()):
        public = landing_egress.public_node(node)
        endpoint = f'{node["socks_ip"]}:{node["socks_port"]}'
        cards.append(
            '<tr><td>' + html.escape(public['name']) + '</td>'
            '<td><code class="mono">' + html.escape(endpoint) + '</code></td>'
            '<td><code class="mono">' + html.escape(public['exit_ip']) + '</code></td>'
            '<td><span class="badge '
            + ('badge-success">启用' if public.get('enabled') else 'badge-neutral">禁用')
            + '</span></td>'
            '<td><div class="row gap-sm">'
            '<form method="post" action="/admin/landing-egress/check">'
            f'<input type="hidden" name="id" value="{html.escape(node_id, quote=True)}">'
            '<button class="btn btn-ghost btn-sm" type="submit">健康检查</button></form>'
            '<form method="post" action="/admin/landing-egress/delete">'
            f'<input type="hidden" name="id" value="{html.escape(node_id, quote=True)}">'
            '<button class="btn danger-btn btn-sm" type="submit">删除</button></form>'
            '</div></td></tr>'
        )
    rows = ''.join(cards) or (
        '<tr><td colspan="5" class="empty">尚未配置家宽出口</td></tr>'
    )
    access_forms = []
    registry_nodes = registry.get('nodes', {})
    if registry_nodes:
        for username, cfg in sorted(users.items()):
            if not isinstance(cfg, dict):
                continue
            allowed = cfg.get('landing_allowed_egress_ids', [])
            allowed = allowed if isinstance(allowed, list) else []
            choices = []
            for node_id, node in sorted(registry_nodes.items()):
                checked = ' checked' if node_id in allowed else ''
                disabled = ' disabled' if node.get('enabled') is not True else ''
                state = '（已禁用）' if disabled else ''
                choices.append(
                    '<label class="landing-access-choice">'
                    f'<input type="checkbox" name="egress_id" value="{html.escape(node_id, quote=True)}"{checked}{disabled}>'
                    f'<span>{html.escape(node.get("name", node_id))}{state}</span></label>'
                )
            access_forms.append(
                '<form method="post" action="/admin/user-landing-access" class="landing-access-row">'
                '<div class="landing-access-user"><strong>'
                f'{html.escape(str(username))}</strong>'
                '<span class="small faint">可授权一个或多个出口</span></div>'
                f'<input type="hidden" name="user" value="{html.escape(str(username), quote=True)}">'
                f'<input type="hidden" name="user_revision" value="{user_config_revision(cfg)}">'
                '<fieldset class="landing-access-choices"><legend class="sr-only">'
                f'{html.escape(str(username))} 可使用的家宽出口</legend>'
                + ''.join(choices)
                + '</fieldset><button class="btn btn-secondary btn-sm" type="submit">保存授权</button></form>'
            )
    if not registry_nodes:
        access_content = (
            '<div class="landing-empty">请先添加家宽出口节点，再为用户分配访问权限。</div>'
        )
    elif access_forms:
        access_content = '<div class="landing-access-list">' + ''.join(access_forms) + '</div>'
    else:
        access_content = '<div class="landing-empty">暂无用户</div>'
    flash_html = render_alert(flash) if flash else ''
    content = f'''{flash_html}
<section class="form-section landing-node-list" aria-labelledby="landing-node-list-title">
  <h2 class="form-section-title" id="landing-node-list-title">家宽出口节点</h2>
  <p class="form-section-desc">管理真实 SOCKS5 出口。密码仅写入服务器状态文件，不会在页面中回显。</p>
  <div class="data-table-wrap" tabindex="0" aria-label="家宽出口节点表格，可横向滚动"><table class="data-table"><caption class="sr-only">家宽出口节点列表</caption><thead><tr><th scope="col">名称</th><th scope="col">SOCKS5 入口</th><th scope="col">预期出口</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
<section class="form-section" aria-labelledby="landing-node-form-title">
  <h2 class="form-section-title" id="landing-node-form-title">新增或更新节点</h2>
  <p class="form-section-desc">节点 ID 保存后保持不变；更新凭据时填写新值，留空则保留原值。</p>
  <form method="post" action="/admin/landing-egress/save" class="landing-node-form">
    <input type="hidden" name="registry_revision" value="{content_revision(registry)}">
    <div class="form-grid landing-node-grid">
      <div class="form-field"><label for="landing-node-id">节点 ID</label><input id="landing-node-id" name="id" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-node-name">显示名称</label><input id="landing-node-name" name="name" required></div>
      <div class="form-field"><label for="landing-socks-ip">SOCKS5 IP</label><input id="landing-socks-ip" name="socks_ip" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-socks-port">SOCKS5 端口</label><input id="landing-socks-port" name="socks_port" type="number" min="1" max="65535" required></div>
      <div class="form-field"><label for="landing-socks-username">SOCKS5 用户名</label><input id="landing-socks-username" name="socks_username" autocomplete="off"></div>
      <div class="form-field"><label for="landing-socks-password">SOCKS5 密码</label><input id="landing-socks-password" name="socks_password" type="password" autocomplete="new-password"></div>
      <div class="form-field"><label for="landing-expected-exit-ip">预期出口 IP</label><input id="landing-expected-exit-ip" name="expected_exit_ip" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-isp">运营商</label><input id="landing-isp" name="isp"></div>
      <div class="form-field"><label for="landing-region">地区</label><input id="landing-region" name="region"></div>
    </div>
    <div class="landing-node-actions"><label class="switch"><input name="enabled" type="checkbox" value="1" checked>启用节点</label><button class="btn btn-primary" type="submit">保存节点</button></div>
  </form>
</section>
<section class="form-section" aria-labelledby="landing-access-title">
  <h2 class="form-section-title" id="landing-access-title">用户授权</h2>
  <p class="form-section-desc">用户只能在这里授权的节点中切换；SOCKS5 地址和凭据不会显示在用户面板。</p>
  {access_content}
</section>'''
    return render_admin_shell(
        'landing-egresses', '家宽出口', content, badge=host,
        subtitle='真实 SOCKS5 出口与授权',
    )


def render_admin_shell(active, page_title, content, *, badge='', subtitle='', topbar_extra=''):
    """Wrap admin page content in the sidebar + topbar app shell."""
    nav_parts = []
    groups = (
        ('概览与用量', ('dashboard', 'usage', 'codex')),
        ('运行维护', ('health', 'incidents', 'logs', 'settings')),
        ('网络配置', ('config', 'rules', 'landing-egresses')),
    )
    entries = {entry[0]: entry for entry in _SIDEBAR_NAV}
    grouped_entries = []
    for group_title, keys in groups:
        grouped_entries.append((None, '', group_title, ''))
        grouped_entries.extend(entries[key] for key in keys)
    for key, href, label, icon_name in grouped_entries:
        if key is None:
            nav_parts.append(f'<div class="sidebar-section">{html.escape(label)}</div>')
            continue
        current = ' aria-current="page"' if key == active else ''
        active_class = 'active' if key == active else ''
        nav_parts.append(
            f'<a href="{href}" class="sidebar-link {active_class}"{current} title="{html.escape(label, quote=True)}" aria-label="{html.escape(label, quote=True)}">'
            f'{icon(icon_name)}<span>{html.escape(label)}</span></a>'
        )
    nav_items = ''.join(nav_parts)
    badge_html = f'<span class="badge">{html.escape(badge)}</span>' if badge else ''
    sub_html = f'<small>{html.escape(subtitle)}</small>' if subtitle else ''
    body = f'''<script>
(function(){{
  try {{
    if (localStorage.getItem('hy2.sidebar-motion') === 'enabled') {{
      document.documentElement.classList.add('sidebar-motion-enabled');
    }}
    if (localStorage.getItem('hy2.sidebar') === 'collapsed') {{
      document.documentElement.classList.add('sidebar-pre-collapsed');
    }}
  }} catch (e) {{}}
}})();
</script>
<a class="skip-link" href="#main-content">跳到主内容</a>
<div class="app">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-brand">
    <span class="sidebar-logo">H</span>
    <div class="sidebar-brand-text">
      <strong>Hysteria</strong>
      <small>Network Console</small>
    </div>
    <button class="sidebar-close" id="sidebar-close" type="button" aria-label="关闭导航" aria-controls="sidebar">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
  <button class="sidebar-collapse" id="sidebar-collapse" type="button" aria-label="折叠侧边栏" aria-pressed="false" title="折叠侧边栏">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>
  </button>
  <nav class="sidebar-nav" aria-label="管理导航">
    {nav_items}
  </nav>
  <div class="sidebar-footer">
    <form method="post" action="/logout">
      <button type="submit" class="sidebar-logout" title="退出登录" aria-label="退出登录">{icon("logout")}<span>退出登录</span></button>
    </form>
  </div>
</aside>
<div class="scrim" id="scrim" aria-hidden="true"></div>
<div class="main">
  <header class="topbar">
    <div class="topbar-inner">
      <div class="topbar-left">
        <button class="sidebar-toggle" id="sidebar-toggle" type="button" aria-label="切换侧边栏" aria-expanded="false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
        <h1 class="page-title">{html.escape(page_title)}{sub_html}</h1>
      </div>
      <div class="topbar-right">{topbar_extra}{badge_html}</div>
    </div>
  </header>
  <main class="content" id="main-content" tabindex="-1">{content}</main>
</div>
</div>
<script>
(function() {{
  var sb = document.getElementById('sidebar');
  var sc = document.getElementById('scrim');
  var bt = document.getElementById('sidebar-toggle');
  var cb = document.getElementById('sidebar-close');
  var collapseBtn = document.getElementById('sidebar-collapse');
  var app = document.querySelector('.app');
  var main = document.querySelector('.main');
  var skip = document.querySelector('.skip-link');
  var motionToggle = document.getElementById('sidebar-motion-toggle');
  if (!sb || !sc || !bt || !cb) return;
  function setCollapsed(collapsed) {{
    collapsed = Boolean(collapsed) && !isMobile();
    sb.classList.toggle('collapsed', collapsed);
    if (app) app.classList.toggle('sidebar-collapsed', collapsed);
    document.documentElement.classList.toggle('sidebar-pre-collapsed', collapsed);
    if (collapseBtn) {{
      collapseBtn.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
      collapseBtn.setAttribute('aria-label', collapsed ? '展开侧边栏' : '折叠侧边栏');
      collapseBtn.setAttribute('title', collapsed ? '展开侧边栏' : '折叠侧边栏');
    }}
    try {{ localStorage.setItem('hy2.sidebar', collapsed ? 'collapsed' : 'expanded'); }} catch (e) {{}}
  }}
  try {{
    setCollapsed(localStorage.getItem('hy2.sidebar') === 'collapsed');
  }} catch (e) {{}}
  requestAnimationFrame(function() {{
    requestAnimationFrame(function() {{
      document.documentElement.classList.remove('sidebar-pre-collapsed');
      if (app) app.classList.add('anim-ready');
    }});
  }});
  if (collapseBtn) collapseBtn.addEventListener('click', function() {{
    setCollapsed(!sb.classList.contains('collapsed'));
  }});
  if (motionToggle) {{
    motionToggle.checked = document.documentElement.classList.contains('sidebar-motion-enabled');
    motionToggle.addEventListener('change', function() {{
      var enabled = Boolean(motionToggle.checked);
      document.documentElement.classList.toggle('sidebar-motion-enabled', enabled);
      try {{
        if (enabled) localStorage.setItem('hy2.sidebar-motion', 'enabled');
        else localStorage.removeItem('hy2.sidebar-motion');
      }} catch (e) {{}}
    }});
  }}
  function isMobile() {{ return window.innerWidth <= 880; }}
  function focusableItems() {{
    return Array.prototype.slice.call(
      sb.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])')
    );
  }}
  function setOpen(open, restoreFocus) {{
    open = Boolean(open && isMobile());
    sb.classList.toggle('open', open);
    document.body.classList.toggle('sidebar-open', open);
    bt.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (isMobile()) {{
      if (open) {{
        sb.removeAttribute('inert');
        if (main) main.setAttribute('inert', '');
        if (skip) skip.setAttribute('inert', '');
        var first = sb.querySelector('#sidebar-close, a, button');
        if (first) first.focus();
      }} else {{
        sb.setAttribute('inert', '');
        if (main) main.removeAttribute('inert');
        if (skip) skip.removeAttribute('inert');
        if (restoreFocus) bt.focus();
      }}
    }} else {{
      sb.removeAttribute('inert');
      if (main) main.removeAttribute('inert');
      if (skip) skip.removeAttribute('inert');
    }}
  }}
  function close(restoreFocus) {{ setOpen(false, restoreFocus); }}
  bt.addEventListener('click', function() {{ setOpen(!sb.classList.contains('open')); }});
  cb.addEventListener('click', function() {{ close(true); }});
  sc.addEventListener('click', function() {{ close(true); }});
  sb.querySelectorAll('a').forEach(function(link) {{ link.addEventListener('click', function() {{ close(false); }}); }});
  document.addEventListener('keydown', function(ev) {{
    if (ev.key === 'Escape' && sb.classList.contains('open')) {{
      ev.preventDefault();
      close(true);
      return;
    }}
    if (ev.key === 'Tab' && sb.classList.contains('open')) {{
      var items = focusableItems();
      if (!items.length) {{
        ev.preventDefault();
        return;
      }}
      var first = items[0];
      var last = items[items.length - 1];
      var active = document.activeElement;
      if (ev.shiftKey && (active === first || !sb.contains(active))) {{
        ev.preventDefault();
        last.focus();
      }} else if (!ev.shiftKey && (active === last || !sb.contains(active))) {{
        ev.preventDefault();
        first.focus();
      }}
    }}
  }});
  window.addEventListener('resize', function() {{
    setOpen(sb.classList.contains('open'));
    if (isMobile()) {{
      sb.classList.remove('collapsed');
      if (app) app.classList.remove('sidebar-collapsed');
    }} else {{
      try {{
        setCollapsed(localStorage.getItem('hy2.sidebar') === 'collapsed');
      }} catch (e) {{}}
    }}
  }});
  document.addEventListener('submit', function(ev) {{
    var form = ev.target;
    if (ev.defaultPrevented || !form || form.tagName !== 'FORM') return;
    var message = form.getAttribute('data-confirm');
    if (message) {{
      if (!window.confirm(message)) ev.preventDefault();
      else form.__hy2Confirmed = true;
    }}
  }});
  setOpen(false);
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
    if msg.startswith('deleted_retry '):
        return (
            '删除请求已安全记录；旧授权、历史数据或连接仍在后台复核，'
            '系统会持续自动重试，直至确认完成：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('deleted '):
        return f'已删除用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('rotated_pending '):
        return (
            '已重置订阅令牌；已确认暂停 Xray/TUIC，正在等待安全同步，'
            'Hysteria 连接断开请求将延迟复核：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated_static_pending '):
        return (
            '已重置订阅令牌；受影响的静态代理因重载未能安排而已确认暂停，'
            '正在等待安全同步：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated_retry '):
        return (
            '已重置订阅令牌，但未能确认所有旧连接或静态代理均已停止；'
            '系统会持续自动重试，直至确认完成：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated '):
        return (
            '已重置订阅令牌（旧订阅/面板链接已失效，'
            '连接断开请求正在复核）：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('disabled '):
        return f'已停用用户（已请求断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('paused '):
        return f'已暂停用户 1 小时（已请求断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('enabled '):
        return f'已启用用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('settlement '):
        return f'已更新结算日：每月 {msg.split(" ", 1)[1]} 日'
    maps = {
        'user not found': '用户不存在',
        'user empty': '用户名不能为空',
        'user_exists_use_reset_token': '用户已存在；请在用户列表中使用“重置订阅”，以执行完整的连接撤销与审计流程',
        'username_invalid': '用户名只能包含字母、数字、点、下划线、连字符，且不能以 .json 结尾',
        'panel_password_short': '用户面板登录密码至少需要 8 位',
        'panel_password_long': f'用户面板登录密码不能超过 {PASSWORD_MAX_LENGTH} 位',
        'proxy_password_long': f'代理连接密码不能超过 {PASSWORD_MAX_LENGTH} 位',
        'max_devices_invalid': '设备数上限必须是 0–100 之间的整数；0 表示不限设备',
        'quota_invalid': '基础流量上限必须是 0–10240 之间的整数；0 表示不限流量',
        'quota_extra_invalid': '加量包必须是 0–10240 之间的整数',
        'expiry_invalid': '到期日无效，请使用 YYYY-MM-DD 格式',
        'note_too_long': '备注不能超过 200 个字符',
        'landing_too_long': '落地家宽信息不能超过 120 个字符',
        'landing_invalid': '落地家宽信息不能包含控制字符',
        'landing_ip_invalid': '请输入合法的 IPv4 或 IPv6 地址',
        'settlement_invalid': '结算日无效（请输入 1–28 之间的整数）',
        'cycle_length_invalid': f'周期长度无效（请输入 {CYCLE_LENGTH_MIN}–{CYCLE_LENGTH_MAX} 之间的整数）',
    }
    return maps.get(msg, msg)


def render_home(host):
    """Public product overview with explicitly illustrative, non-live previews."""
    body = '''<header class="site-header">
  <a href="/" class="site-brand"><span aria-hidden="true">H</span><strong>Hysteria<small>NETWORK CONSOLE</small></strong></a>
  <nav aria-label="首页导航"><a href="#services">服务能力</a><a href="#console-preview">控制台预览</a></nav>
  <a class="site-button site-button-small" href="/login">进入控制台 <span aria-hidden="true">↗</span></a>
</header>
<main class="site-main">
<section class="site-hero">
  <div class="site-hero-copy">
    <p class="site-eyebrow"><span></span> YOUR NETWORK, IN FOCUS</p>
    <h1>连接网络，<br><em>掌控全局。</em></h1>
    <p class="site-lead">从多协议接入到流量洞察，<br>在一个清晰的控制台里，管理你的网络。</p>
    <div class="site-actions"><a class="site-button" href="/login">进入控制台 <span aria-hidden="true">→</span></a><a class="site-text-link" href="#services">查看服务能力 <span aria-hidden="true">↓</span></a></div>
    <div class="site-protocols"><span>接入协议</span><b>Hysteria2</b><b>VLESS Reality</b><b>TUIC</b></div>
  </div>
  <div class="site-topology" role="img" aria-label="概念示意：Hysteria 控制台连接多协议接入、流量统计和健康监测">
    <div class="site-topology-grid"></div>
    <div class="site-topology-caption"><span>NETWORK ARCHITECTURE</span><span>连接架构示意</span></div>
    <svg class="site-topology-lines" viewBox="0 0 520 460" fill="none" aria-hidden="true">
      <circle cx="260" cy="230" r="155" stroke="#cbdde2" stroke-dasharray="3 9"/>
      <circle cx="260" cy="230" r="108" stroke="#dde7e9"/>
      <path d="M260 103V180M116 305H185L220 263M404 305H335L300 263" stroke="#85afbe" stroke-width="1.5"/>
      <circle cx="260" cy="147" r="4" fill="#6798aa"/><circle cx="157" cy="305" r="4" fill="#6798aa"/><circle cx="363" cy="305" r="4" fill="#6798aa"/>
    </svg>
    <div class="site-hub"><span>H</span><strong>Hysteria</strong><small>NETWORK CONSOLE</small></div>
    <div class="site-node site-node-top"><span>01 / ACCESS</span><strong>多协议接入</strong><small>Hysteria2 · VLESS · TUIC</small></div>
    <div class="site-node site-node-left"><span>02 / INSIGHT</span><strong>流量统计</strong><small>用量 · 配额 · 周期</small></div>
    <div class="site-node site-node-right"><span>03 / HEALTH</span><strong>健康监测</strong><small>状态 · 告警 · 维护</small></div>
    <div class="site-topology-footer"><i></i> 一个入口，清晰连接每一环</div>
  </div>
</section>

<section class="site-services" id="services" aria-labelledby="services-title">
  <div class="site-section-heading"><div><p class="site-eyebrow">01 / CAPABILITIES</p><h2 id="services-title">复杂的网络，清晰的管理。</h2></div><p>把连接、用量和维护，<br>放在同一个工作空间。</p></div>
  <div class="site-feature-grid">
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">↗</span><span class="site-feature-index">01</span><h3>多协议接入</h3><p>集中管理接入协议与订阅模板，让不同使用场景拥有合适的连接方式。</p><div class="site-feature-tags"><span>Hysteria2</span><span>VLESS Reality</span><span>TUIC</span></div></article>
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">▥</span><span class="site-feature-index">02</span><h3>流量与配额</h3><p>查看流量变化，管理用户配额与账期，了解每一份用量的去向。</p><div class="site-feature-tags"><span>趋势分析</span><span>用户配额</span><span>周期管理</span></div></article>
    <article class="site-feature"><span class="site-feature-icon" aria-hidden="true">⌁</span><span class="site-feature-index">03</span><h3>健康与维护</h3><p>汇总服务状态、证书与更新记录，为日常维护提供清晰的检查入口。</p><div class="site-feature-tags"><span>服务探测</span><span>健康状态</span><span>更新管理</span></div></article>
  </div>
</section>

<section class="site-preview-section" id="console-preview" aria-labelledby="preview-title">
  <div class="site-section-heading"><div><p class="site-eyebrow">02 / WORKSPACE</p><h2 id="preview-title">全局在眼前，操作有条理。</h2></div><p>从细节到全貌，<br>无需在多个工具间切换。</p></div>
  <div class="site-console">
    <div class="site-console-top"><strong><span aria-hidden="true">H /</span> 控制台预览</strong><span class="site-demo-label">界面示意 · 非实时数据</span></div>
    <div class="site-preview-nav" aria-label="预览内容"><a id="demo-tab-traffic" href="#demo-traffic" data-demo="traffic">流量分析</a><a id="demo-tab-users" href="#demo-users" data-demo="users">用户管理</a><a id="demo-tab-health" href="#demo-health" data-demo="health">健康状态</a></div>
    <section class="site-demo-panel" id="demo-traffic" aria-labelledby="demo-tab-traffic">
      <div class="site-demo-heading"><div><h3>流量趋势</h3><p>观察用量变化，合理分配资源。</p></div><span>最近 7 天 · 示例</span></div>
      <div class="site-demo-stats"><div><span>示例总用量</span><strong>60.2 <small>GB</small></strong></div><div><span>示例上行</span><strong>12.4 <small>GB</small></strong></div><div><span>示例下行</span><strong>47.8 <small>GB</small></strong></div></div>
      <figure class="site-chart"><svg viewBox="0 0 900 200" role="img" aria-label="示意折线图：一周内流量上下波动，并在周五达到高点，不代表实际用量">
        <defs><linearGradient id="site-chart-fill" x1="0" y1="0" x2="0" y2="1"><stop stop-color="#87b5c5" stop-opacity=".3"/><stop offset="1" stop-color="#87b5c5" stop-opacity="0"/></linearGradient></defs>
        <path d="M0 25H900M0 80H900M0 135H900M0 190H900" stroke="#e8edef" fill="none"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48V195H0Z" fill="url(#site-chart-fill)"/>
        <path d="M0 160C60 160 85 91 150 104S245 158 300 118S400 99 450 68S530 108 600 40S700 118 750 81S850 72 900 48" stroke="#6598ac" stroke-width="2.5" fill="none"/>
      </svg><figcaption><span>周一</span><span>周二</span><span>周三</span><span>周四</span><span>周五</span><span>周六</span><span>周日</span></figcaption></figure>
    </section>
    <section class="site-demo-panel" id="demo-users" aria-labelledby="demo-tab-users">
      <div class="site-demo-heading"><div><h3>用户与配额</h3><p>查看使用情况，维护每位用户的连接权限。</p></div><span>示例账号 · 非真实用户</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">A</span><div><strong>示例用户 A</strong><small>本周期用量</small></div><meter min="0" max="100" value="42" aria-label="示例用户 A 已用 42% 配额">42%</meter><span>42 / 100 GB</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">B</span><div><strong>示例用户 B</strong><small>本周期用量</small></div><meter min="0" max="100" value="68" aria-label="示例用户 B 已用 68% 配额">68%</meter><span>68 / 100 GB</span></div>
      <div class="site-demo-user"><span class="site-demo-avatar">C</span><div><strong>示例用户 C</strong><small>本周期用量</small></div><meter min="0" max="100" value="15" aria-label="示例用户 C 已用 15% 配额">15%</meter><span>15 / 100 GB</span></div>
      <p class="site-preview-note">实际用户、订阅链接与套餐信息仅在管理员登录后展示。</p>
    </section>
    <section class="site-demo-panel" id="demo-health" aria-labelledby="demo-tab-health">
      <div class="site-demo-heading"><div><h3>服务健康</h3><p>将服务检查和维护入口集中呈现。</p></div><span>状态展示示例</span></div>
      <div class="site-demo-health"><span>协议服务</span><span>Hysteria2 / Xray / TUIC</span><b>正常 · 示例</b></div>
      <div class="site-demo-health"><span>认证服务</span><span>连接认证与访问控制</span><b>正常 · 示例</b></div>
      <div class="site-demo-health"><span>证书与备份</span><span>有效期与最近备份检查</span><b>已检查 · 示例</b></div>
      <div class="site-demo-health"><span>版本维护</span><span>检查更新与历史记录</span><b>待检查 · 示例</b></div>
      <p class="site-preview-note">这里不提供实时运行状态，实际检查结果请进入控制台查看。</p>
    </section>
  </div>
</section>

<section class="site-closing"><div><p class="site-eyebrow">LESS FRICTION. MORE CLARITY.</p><h2>让网络管理，回归简单。</h2><p>从一个清晰的控制台开始。</p></div><a href="/login" class="site-button">进入控制台 <span aria-hidden="true">→</span></a></section>
</main>
<footer class="site-footer"><a href="/" class="site-footer-brand">Hysteria <span>Network Console</span></a><span>连接 · 洞察 · 管理</span></footer>'''
    body += '<script src="/static/home.js?v=' + HOME_JS_ETAG.strip('"') + '" defer></script>'
    return html_page('Hysteria · 连接网络，掌控全局', body, body_class='page-home page-site')


def render_login(host, msg='', msg_kind='err', active_tab='admin', username=''):
    """Administrator-only entry; preserve the existing admin POST contract."""
    username_esc = html.escape(username if active_tab == 'admin' else '', quote=True)
    message = msg if active_tab == 'admin' else '请使用管理员账号登录控制台。'
    error = render_alert(message, msg_kind) if message else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo"><span class="login-mark" aria-hidden="true">H</span>Hysteria</a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>
<main class="login-stage">
  <div class="login-layout">
    <section class="login-story" aria-labelledby="login-story-title">
      <div class="login-eyebrow"><span></span> NETWORK CONSOLE</div>
      <h1 id="login-story-title">连接网络，<br><span>掌控全局。</span></h1>
      <p class="login-description">让每一次连接，清晰可见。<br>在一个控制台中，管理你的网络。</p>
      <div class="login-network" aria-hidden="true">
        <svg viewBox="0 0 460 220" fill="none">
          <defs><linearGradient id="login-line"><stop stop-color="#b9d8e1"/><stop offset="1" stop-color="#68a8bf"/></linearGradient></defs>
          <path d="M28 162H94L155 101H253L314 40H425M94 162H235L285 112H422M155 101V46H212M253 101V182H375" stroke="#d1dce0" stroke-width="1"/>
          <path d="M28 162H94L155 101H253L314 40H425" stroke="url(#login-line)" stroke-width="2"/>
          <circle cx="155" cy="101" r="20" fill="#dceef3" fill-opacity=".65"/>
          <circle cx="155" cy="101" r="6" fill="#508da3" stroke="white" stroke-width="3"/>
          <circle cx="253" cy="101" r="5" fill="#fff" stroke="#7aa5b5" stroke-width="2"/>
          <circle cx="314" cy="40" r="5" fill="#fff" stroke="#7aa5b5" stroke-width="2"/>
          <circle cx="285" cy="112" r="4" fill="#9bbbc6"/>
          <rect x="365" y="170" width="30" height="24" rx="5" fill="#fff" stroke="#cad8dd"/>
          <path d="M375 178h10m-10 7h6" stroke="#83a4b0" stroke-width="2" stroke-linecap="round"/>
          <circle cx="425" cy="40" r="3" fill="#83a4b0"/>
          <circle cx="28" cy="162" r="3" fill="#83a4b0"/>
        </svg>
      </div>
      <div class="login-story-foot"><span>HYSTERIA</span><span>连接 · 洞察 · 管理</span></div>
    </section>
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-panel-kicker">{icon('lock')}<span>管理员访问</span></div>
      <h2 id="login-title">登录控制台</h2>
      <p class="login-subtitle">使用管理员账号登录。</p>
      <form method="post" action="/login" class="login-form" id="form-admin">
        <div class="login-feedback">{error}</div>
        <div class="field">
          <label class="label" for="admin-username">管理员账号</label>
          <input class="input" id="admin-username" name="admin_username" value="{username_esc}" required autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="输入管理员账号">
        </div>
        <div class="field">
          <label class="label" for="admin-password">密码</label>
          <div class="login-password">
            <input class="input" id="admin-password" name="admin_password" type="password" required maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="current-password" placeholder="输入密码">
            <button type="button" id="login-password-toggle" aria-controls="admin-password" aria-label="显示密码" aria-pressed="false" hidden>显示</button>
          </div>
        </div>
        <button class="btn btn-primary login-submit auth-submit" type="submit"><span class="auth-submit-text">登录控制台</span><span aria-hidden="true">→</span></button>
        <span id="login-progress" class="sr-only" role="status" aria-live="polite"></span>
      </form>
      <div class="login-panel-foot">{icon('lock')}<span>仅限授权管理员访问</span></div>
    </section>
  </div>
  <footer class="login-footer">Hysteria <span>／</span> Network Console</footer>
</main>
<script>
(function() {{
  var form = document.getElementById('form-admin');
  var password = document.getElementById('admin-password');
  var toggle = document.getElementById('login-password-toggle');
  var button = form.querySelector('.auth-submit');
  var progress = document.getElementById('login-progress');
  toggle.hidden = false;
  toggle.addEventListener('click', function() {{
    var visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    toggle.textContent = visible ? '隐藏' : '显示';
    toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    toggle.setAttribute('aria-pressed', String(visible));
  }});
  function reset() {{
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.querySelector('.auth-submit-text').textContent = '登录控制台';
    progress.textContent = '';
    password.type = 'password';
    toggle.textContent = '显示';
    toggle.setAttribute('aria-label', '显示密码');
    toggle.setAttribute('aria-pressed', 'false');
  }}
  form.addEventListener('submit', function(event) {{
    if (button.disabled) {{ event.preventDefault(); return; }}
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.querySelector('.auth-submit-text').textContent = '正在验证…';
    progress.textContent = '正在验证登录信息';
  }});
  window.addEventListener('pageshow', reset);
}})();
</script>'''
    return html_page('管理员登录 · Hysteria', body, body_class='page-auth page-admin-login')


def render_user_login(host, msg='', username=''):
    alert = render_alert(msg, 'err') if msg else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>

<div class="auth-scene">
  <div class="auth-body">
    <div class="auth-brand">
      <div class="auth-brand-content">
        <div class="auth-brand-eyebrow">User · Panel</div>
        <h1 class="auth-brand-title">查看用量，<br>管理订阅。</h1>
        <div class="auth-brand-features">
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M18 20V4M6 20v-4"/></svg>
            实时流量统计
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            独立访问密码
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            统一订阅链接
          </div>
        </div>
      </div>
    </div>

    <div class="auth-form-panel">
      <div class="auth-card">
        <div class="auth-card-brand">
          <div class="auth-card-logo">H</div>
          <div class="auth-card-brand-text">
            <strong>Hysteria</strong>
            <small>用户面板</small>
          </div>
        </div>
        <h2 class="auth-card-title">用户登录</h2>
        <p class="auth-card-subtitle">登录后查看用量和订阅信息</p>
        {alert}
        <form method="post" action="/user/login" class="auth-form">
          <div class="field">
            <label class="label" for="user-username">用户名</label>
            <input class="input" id="user-username" name="username" value="{html.escape(username, quote=True)}" required autofocus autocomplete="username" placeholder="输入用户名">
          </div>
          <div class="field">
            <label class="label" for="user-password">面板密码</label>
            <input class="input" id="user-password" name="password" type="password" required maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="current-password" placeholder="输入密码">
          </div>
          <button class="btn btn-primary btn-full" type="submit">登录</button>
        </form>
        <div class="auth-note">面板密码由管理员设置；原订阅链接仍可继续使用。</div>
        <a class="auth-back" href="/">返回首页</a>
      </div>
    </div>
  </div>
</div>'''
    return html_page('用户登录', body, body_class='page-auth')


def render_user_change_password(host, user, msg=''):
    messages = {
        'current password wrong': '当前密码不正确',
        'new password short': '新密码至少需要 8 位',
        'new password long': f'新密码不能超过 {PASSWORD_MAX_LENGTH} 位',
        'new password mismatch': '两次输入的新密码不一致',
        'new password same': '新密码不能与当前密码相同',
    }
    alert = render_alert(messages.get(msg, msg), 'err') if msg else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/user/panel" class="auth-header-back">← 返回面板</a>
  </div>
</header>

<div class="auth-scene">
  <div class="auth-body">
    <div class="auth-brand">
      <div class="auth-brand-content">
        <div class="auth-brand-eyebrow">Security · 安全</div>
        <h1 class="auth-brand-title">保护你的<br>账户安全。</h1>
        <div class="auth-brand-features">
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            使用强密码
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            保护订阅访问
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            实时生效
          </div>
        </div>
      </div>
    </div>

    <div class="auth-form-panel">
      <div class="auth-card">
        <div class="auth-card-brand">
          <div class="auth-card-logo">H</div>
          <div class="auth-card-brand-text">
            <strong>Hysteria</strong>
            <small>用户面板</small>
          </div>
        </div>
        <h2 class="auth-card-title">修改面板密码</h2>
        <p class="auth-card-subtitle">{html.escape(user)} · {html.escape(host)}</p>
        {alert}
      <form method="post" action="/user/change-password" class="auth-form">
        <div class="field">
          <label class="label" for="user-current-password">当前密码</label>
          <input class="input" id="user-current-password" name="current" type="password" required maxlength="{PASSWORD_MAX_LENGTH}" autofocus autocomplete="current-password" placeholder="输入当前密码">
        </div>
        <div class="field">
          <label class="label" for="user-new-password">新密码</label>
          <input class="input" id="user-new-password" name="new" type="password" required minlength="{PASSWORD_MIN_LENGTH}" maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="new-password" placeholder="输入新密码">
        </div>
        <div class="field">
          <label class="label" for="user-confirm-password">再次输入新密码</label>
          <input class="input" id="user-confirm-password" name="confirm" type="password" required minlength="{PASSWORD_MIN_LENGTH}" maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="new-password" placeholder="再次输入新密码">
        </div>
        <button class="btn btn-primary btn-full" type="submit">保存新密码</button>
        <div class="auth-note">使用至少 8 位、且未在其他网站使用的密码。保存后其他设备上的用户面板会话将自动退出。</div>
      </form>
      <a class="auth-back" href="/user/panel">返回用户面板</a>
    </div>
  </div>
</div>
</div>'''
    return html_page('修改面板密码', body, body_class='page-auth')


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


def subscription_profile_url(base_url, user, token, profile='default'):
    """Return the canonical subscription URL for one normalized profile."""
    key = normalize_subscription_profile(profile)
    params = {'token': token}
    if key != 'default':
        params['profile'] = key
    return f'{base_url}/sub/{user}?{urlencode(params)}'


def subscription_profile_qr_path(user, token, profile='default'):
    key = normalize_subscription_profile(profile)
    params = {'token': token}
    if key != 'default':
        params['profile'] = key
    return f'/panel/{user}/qr.svg?{urlencode(params)}'


def render_profile_qr_svg(base_url, user, token, profile='default'):
    return render_qr_svg(subscription_profile_url(base_url, user, token, profile))


def render_subscription_profile_links(base_url, user, token):
    items = []
    for key in SUBSCRIPTION_PROFILE_ORDER:
        meta = SUBSCRIPTION_PROFILES[key]
        url = subscription_profile_url(base_url, user, token, key)
        qr_path = subscription_profile_qr_path(user, token, key)
        selected = key == 'default'
        items.append(
            f'<button type="button" class="profile-tab{" is-active" if selected else ""}" '
            f'data-profile-option data-profile="{key}" '
            f'data-profile-label="{html.escape(meta["label"], quote=True)}" '
            f'data-profile-desc="{html.escape(meta["desc"], quote=True)}" '
            f'data-profile-url="{html.escape(url, quote=True)}" '
            f'data-profile-qr="{html.escape(qr_path, quote=True)}" '
            f'aria-current="{"true" if selected else "false"}">'
            f'<span class="profile-tab-label">{html.escape(meta["label"])}</span>'
            f'</button>'
        )
    default_meta = SUBSCRIPTION_PROFILES['default']
    default_url = subscription_profile_url(base_url, user, token, 'default')
    default_qr = subscription_profile_qr_path(user, token, 'default')
    return (
        '<div class="connection-panel">'
        '<div class="connection-head">'
        '<div><div class="k">订阅模式</div>'
        f'<div class="connection-desc small" id="profile-selected-desc">{html.escape(default_meta["desc"])}</div>'
        '</div>'
        f'<div class="connection-head-right">'
        f'<span class="badge connection-badge" id="profile-selected-badge">{html.escape(default_meta["label"])}</span>'
        f'<span class="connection-muted small faint sr-only" id="profile-selected-title">{html.escape(default_meta["label"])}</span>'
        f'</div>'
        '</div>'
        f'<div class="profile-tabs" role="group" aria-label="选择订阅模式">{"".join(items)}</div>'
        '<div class="connection-url">'
        f'<code class="mono url-text" id="sub">{html.escape(default_url)}</code>'
        f'<button class="btn primary btn-sm" type="button" id="profile-copy" data-copy="{html.escape(default_url, quote=True)}">'
        f'{icon("copy")}<span>复制链接</span></button>'
        '</div>'
        '<div class="connection-actions">'
        f'<a class="btn secondary btn-sm" id="profile-open" href="{html.escape(default_url, quote=True)}">'
        f'{icon("open")}<span>打开配置</span></a>'
        f'<button class="btn ghost btn-sm" type="button" id="profile-show-qr" '
        f'data-qr="{html.escape(default_qr, quote=True)}" aria-expanded="false" aria-controls="profile-qr-panel">'
        f'<span>二维码</span></button>'
        '</div>'
        '<div class="profile-qr-panel" id="profile-qr-panel" hidden>'
        '<div class="qr-wrap"><img id="profile-qr-image" width="220" height="220" alt="当前订阅模式二维码"></div>'
        '<div class="small faint" id="profile-qr-status" role="status" aria-live="polite">在另一台设备上用客户端扫码导入；二维码仅在这里按需生成。</div>'
        '</div>'
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
        'max_devices': configured_max_devices(cfg),
        'percent': round(pct(used, total), 2),
    }


def render_user_panel(
    host,
    base_url,
    user,
    token,
    cfg,
    *,
    session_auth=False,
    session_kind=USER_SESSION_PANEL_PASSWORD,
    notice='',
):
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
    panel_path = '/user/panel' if session_auth else f'/panel/{user}?token={token}'
    json_path = '/user/panel.json' if session_auth else f'/panel/{user}.json?token={token}'
    sub_http = f'{base_url}{sub_path}'
    panel_http = f'{base_url}{panel_path}'
    max_devices_n = configured_max_devices(cfg)
    max_devices_label = (
        '· 设备不限'
        if max_devices_n == 0
        else f'/ {max_devices_n}'
    )
    is_disabled = bool(cfg.get('disabled'))
    expiry = user_expiry_state(cfg, today=now.date())
    is_expired = bool(expiry['expired'])
    disabled_banner = ''
    if is_disabled:
        disabled_banner = render_alert('账号已停用，请联系管理员', 'err')
    elif is_expired:
        disabled_banner = render_alert('账号已到期，请联系管理员续费', 'err')
    inactive = is_disabled or is_expired
    notice_messages = {
        'token_rotated': (
            'Token 已重置，旧订阅与面板链接已失效。系统已请求断开'
            '现有 Hysteria 连接，并将在短暂延迟后再次复核；'
            '静态代理的新凭证正在应用。'
        ),
        'token_rotated_sync_pending': (
            'Token 已重置。已确认暂停 Xray/TUIC，待安全同步后恢复；'
            'Hysteria 已切换为新凭证，现有连接的断开请求正在复核。'
        ),
        'token_rotated_static_pending': (
            'Token 已重置。受影响的静态代理因重载未能安排而已确认暂停，'
            '将在下一次安全同步后恢复；Hysteria 连接断开请求正在复核。'
        ),
        'token_rotated_revocation_retry': (
            'Token 已重置，但未能确认所有旧连接或静态代理均已停止。'
            '系统会持续自动重试，直至确认完成；完成前请勿继续使用'
            '旧的 Xray/TUIC 凭证。'
        ),
        'token_rotated_session_recovery': (
            'Token 已重置，但新的登录会话未能保存。请立即复制下方的'
            '新订阅链接或面板链接；原浏览器可在 5 分钟内重放同一'
            '操作取回这枚 Token。'
        ),
        'token_rotated_sync_pending_recovery': (
            'Token 已重置，但新的登录会话未能保存；同时 Xray/TUIC '
            '正在等待安全同步。请立即复制下方的新订阅链接或面板'
            '链接；原浏览器可在 5 分钟内重放同一操作取回这枚 Token。'
        ),
        'token_rotated_static_pending_recovery': (
            'Token 已重置，但新的登录会话未能保存；受影响的静态代理'
            '已确认暂停并等待安全同步。请立即复制下方的新链接；'
            '原浏览器可在 5 分钟内重放同一操作取回这枚 Token。'
        ),
        'token_rotated_revocation_retry_recovery': (
            'Token 已重置，但新的登录会话未能保存，且旧连接撤销仍在'
            '后台重试。请立即复制下方的新链接；原浏览器可在 5 分钟'
            '内重放同一操作取回这枚 Token。'
        ),
    }
    notice_code = str(notice or '')
    notice_message = notice_messages.get(notice_code, '')
    notice_banner = render_alert(
        notice_message,
        'flash' if notice_code == 'token_rotated' else 'err',
    )
    if inactive:
        import_assistant = (
            '<div class="card mt-md">'
            '<h2 class="section-title">订阅操作已暂停</h2>'
            '<div class="small">当前账号不可拉取订阅、生成二维码或重置令牌。'
            '恢复或续费后，这些操作会重新出现；历史用量仍可查看。</div>'
            '</div>'
        )
    else:
        import_assistant = render_subscription_profile_links(base_url, user, token)
    password_session = (
        session_auth and session_kind == USER_SESSION_PANEL_PASSWORD
    )
    account_action = ''
    if session_auth:
        change_password_action = (
            f'<a class="btn ghost btn-sm" href="/user/change-password">'
            f'{icon("lock")}<span>修改密码</span></a>'
            if password_session and not inactive else ''
        )
        account_action = (
            f'<div class="row gap-sm mt-sm">'
            f'{change_password_action}'
            f'<form method="post" action="/user/logout" '
            f'class="inline-form-row">'
            f'<button class="btn ghost btn-sm" type="submit">'
            f'{icon("logout")}<span>退出登录</span></button></form>'
            f'</div>'
        )
    # Suspended accounts get a 403 from /panel/<user>.json, so don't emit the
    # live-refresh loop (it would just spam '刷新失败'). The embedded URL escapes
    # '<' so a malicious username can't break out of the <script> element.
    poll_url_js = json.dumps(json_path).replace('<', '\\u003c')
    poll_js = '' if inactive else f'''  var pollUrl = {poll_url_js};
  var statusEl = document.querySelector('[data-role="poll-status"]');
  var statusAnnouncer = document.getElementById('panel-status-announcer');
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
  function announce(txt) {{
    if (statusAnnouncer && statusAnnouncer.textContent !== txt) statusAnnouncer.textContent = txt;
  }}
  function stamp() {{
    return new Date().toLocaleTimeString([], {{ hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' }});
  }}
  var timer = null, inflight = false, running = false;
  var failures = 0, activeController = null;
  function retryDelay() {{
    var exponent = Math.min(failures, 3);
    var base = Math.min(240000, 30000 * Math.pow(2, exponent));
    return Math.min(240000, base + (failures ? Math.floor(Math.random() * 4001) : 0));
  }}
  function clearScheduled() {{
    if (timer) {{ clearTimeout(timer); timer = null; }}
  }}
  function scheduleNext() {{
    if (!running || document.hidden || timer) return;
    timer = setTimeout(function() {{ timer = null; tick(); }}, retryDelay());
  }}
  function tick() {{
    if (inflight || !running || document.hidden) return;
    clearScheduled();
    inflight = true;
    setStatus('刷新中', 'is-live');
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    activeController = controller;
    var timedOut = false;
    var timeout = setTimeout(function() {{
      timedOut = true;
      if (controller) controller.abort();
    }}, 8000);
    fetch(pollUrl, {{ credentials: 'same-origin', cache: 'no-store', signal: controller ? controller.signal : undefined }})
      .then(function(r) {{
        if (r.status === 401) {{
          stop();
          setStatus('登录已失效', 'is-error');
          announce('登录已失效，请重新登录');
          return null;
        }}
        if (r.status === 403) {{
          return r.json()
            .catch(function() {{ return {{ error: 'forbidden' }}; }})
            .then(function(payload) {{
              return {{ accessError: payload.error || 'forbidden' }};
            }});
        }}
        return r.ok ? r.json() : null;
      }})
      .catch(function(error) {{
        if (error && error.name === 'AbortError' && !timedOut) {{
          return {{ stopped: true }};
        }}
        return null;
      }})
      .then(function(d) {{
        if (d && d.stopped) return;
        if (d && d.accessError) {{
          stop();
          var accessMessages = {{
            disabled: '账号已停用，请联系管理员',
            expired: '账号已到期，请联系管理员续费',
            password_change_required: '请先修改初始密码',
            forbidden: '账号状态已变化，请重新登录'
          }};
          var accessMessage = accessMessages[d.accessError] || accessMessages.forbidden;
          setStatus(accessMessage, 'is-error');
          announce(accessMessage);
          return;
        }}
        if (!d) {{
          failures = Math.min(failures + 1, 8);
          if (statusEl && statusEl.textContent !== '登录已失效') {{
            setStatus(timedOut ? '请求超时 · 稍后重试' : '更新失败 · 稍后重试', 'is-error');
            announce(timedOut ? '用量更新请求超时，系统稍后自动重试' : '用量自动更新失败，系统稍后自动重试');
          }}
          return;
        }}
        failures = 0;
        setRole('used', fmtBytes(d.used_bytes));
        setRole('remain', fmtQuota(d.remain_bytes, d.total_bytes));
        setRole('online', d.online);
        setRole('device-limit', Number(d.max_devices) === 0 ? '· 设备不限' : '/ ' + d.max_devices);
        var p = Number(d.percent);
        setRole('percent', Number(d.total_bytes) <= 0 ? '不限' : p.toFixed(2) + '%');
        setRole('txrx', '上传 ' + fmtBytes(d.tx_bytes) + ' · 下载 ' + fmtBytes(d.rx_bytes));
        var bar = document.querySelector('[data-role="bar"]');
        if (bar) {{
          bar.style.width = Number(d.total_bytes) <= 0 ? '0%' : p.toFixed(2) + '%';
          bar.classList.toggle('danger', Number(d.total_bytes) > 0 && p >= 90);
          bar.classList.toggle('unlimited', Number(d.total_bytes) <= 0);
          bar.setAttribute('aria-valuenow', Number(d.total_bytes) <= 0 ? '0' : p.toFixed(2));
          bar.setAttribute('aria-valuetext', Number(d.total_bytes) <= 0 ? '不限' : p.toFixed(2) + '%');
        }}
        setStatus('更新于 ' + stamp(), 'is-live');
      }})
      .finally(function() {{
        clearTimeout(timeout);
        if (activeController === controller) activeController = null;
        inflight = false;
        scheduleNext();
      }});
  }}
  function start() {{
    if (running) return;
    running = true;
    failures = 0;
    tick();
  }}
  function stop() {{
    running = false;
    clearScheduled();
    if (activeController) activeController.abort();
  }}
  document.addEventListener('visibilitychange', function() {{ if (document.hidden) {{ stop(); setStatus('已暂停', 'is-paused'); }} else start(); }});
  window.addEventListener('pagehide', stop);
  start();'''
    if password_session:
        panel_link_hint = (
            '此地址不含订阅令牌，其他设备需要先使用用户名和面板密码登录。'
        )
    elif session_auth:
        panel_link_hint = (
            '此地址不含订阅令牌，仅当前设备的登录会话可直接访问；'
            '其他设备仍需使用原面板链接。'
        )
    else:
        panel_link_hint = '重置后旧链接立即失效，需用新链接重新订阅。'
    rotation_request_id = secrets.token_urlsafe(24)

    # Rotate-token form goes into the Account & Security section.
    token_action_html = ''
    session_url_html = ''
    if not inactive:
        token_action_html = (
            f'<form method="post" action="/panel/{html.escape(user)}/rotate-token" '
            f'data-action="rotate-token" class="inline-form-row">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="rotation_id" value="{html.escape(rotation_request_id)}">'
            f'<button class="btn danger-btn btn-sm" type="submit">'
            f'{icon("lock")}<span>重置 Token</span></button>'
            f'</form>'
        )
        # Session URL stays inside a details block — collapsed by default,
        # never shown as naked large body text.
        session_url_html = (
            f'<details class="account-session-details">'
            f'<summary>查看当前会话详情</summary>'
            f'<div class="copy-mono"><code class="mono">{html.escape(panel_http)}</code></div>'
            f'<div class="small faint mt-sm">{html.escape(panel_link_hint)}</div>'
            f'</details>'
        )
    initial_poll_status = '已暂停更新' if inactive else '自动更新 · 30 s'

    # Cycle day index (e.g. "第 6 / 30 天") — purely cosmetic, derived from
    # the same cycle data the page already exposes via reset_date.
    days_into_cycle = max(1, cycle_len - max(days_left, 0))

    def _status_label():
        if is_disabled:
            return '停用', 'is-error'
        if is_expired:
            return '已到期', 'is-error'
        return '正常', 'is-live'

    account_status_label, account_status_class = _status_label()

    profile_meta = SUBSCRIPTION_PROFILES.get('default', {})
    profile_label = profile_meta.get('label', '默认')
    landing = user_compat.landing_fields(cfg)
    landing_section = ''
    if landing:
        rows = ''
        if landing.get('landing_isp'):
            rows += (
                '<div><dt>运营商</dt><dd>'
                f'{html.escape(landing["landing_isp"])}</dd></div>'
            )
        if landing.get('landing_region'):
            rows += (
                '<div><dt>地区</dt><dd>'
                f'{html.escape(landing["landing_region"])}</dd></div>'
            )
        if landing.get('landing_ip'):
            rows += (
                '<div><dt>家宽 IP</dt><dd><code class="mono">'
                f'{html.escape(landing["landing_ip"])}</code></dd></div>'
            )
        if landing.get('landing_note'):
            rows += (
                '<div><dt>说明</dt><dd>'
                f'{html.escape(landing["landing_note"])}</dd></div>'
            )
        landing_section = (
            '<aside class="plan-section" aria-label="落地家宽">'
            '<header class="section-head">'
            '<h2 class="section-title">落地家宽</h2>'
            '</header>'
            f'<dl class="user-kv">{rows}</dl>'
            '</aside>'
        )
    real_landing_section = render_landing_egress_selector(
        cfg,
        password_session=password_session,
    )

    body = f'''<div class="wrap user-panel">
{notice_banner}
{disabled_banner}
<header class="user-panel-header">
  <div class="user-panel-brand">
    <div>
      <div class="small faint">Hysteria</div>
      <h1 class="user-panel-title">个人控制台</h1>
      <div class="user-panel-subtitle faint">订阅、用量与设备</div>
    </div>
  </div>
  <div class="user-panel-account">
    <div class="user-panel-name mono">{html.escape(user)}</div>
    <div class="user-panel-status">
      <span class="badge {account_status_class}">{html.escape(account_status_label)}</span>
    </div>
    <div class="user-panel-actions">
      {account_action}
    </div>
  </div>
</header>

<section class="usage-section" aria-label="本周期用量">
  <header class="section-head">
    <h2 class="section-title">本周期用量</h2>
    <div class="poll-status small" data-role="poll-status">{initial_poll_status}</div>
    <span class="sr-only" id="panel-status-announcer" role="status" aria-live="polite"></span>
  </header>
  <div class="usage-kpis">
    <div class="usage-kpi">
      <div class="k">已用流量</div>
      <div class="v" data-role="used">{fmt_bytes(used)}</div>
      <div class="sub">{('不限额' if quota_unlimited else f'{percent:.1f}%')}</div>
    </div>
    <div class="usage-kpi">
      <div class="k">剩余额度</div>
      <div class="v" data-role="remain">{remain_label}</div>
      <div class="sub">{('100.0%' if quota_unlimited else (f'{100 - percent:.1f}%' if percent > 0 else '0.0%'))}</div>
    </div>
    <div class="usage-kpi">
      <div class="k">计费周期</div>
      <div class="v usage-date">{html.escape(reset_date)}</div>
      <div class="sub">第 {days_into_cycle} / {cycle_len} 天 · 还剩 {days_left} 天</div>
    </div>
  </div>
  <div class="usage-progress">
    <div class="usage-progress-head">
      <span class="k">本周期</span>
      <span class="bold" data-role="percent" style="font-variant-numeric:tabular-nums;">{percent_label}</span>
    </div>
    <div class="bar"><div class="fill {cls}" data-role="bar" role="progressbar"
         aria-label="本周期流量" aria-valuemin="0" aria-valuemax="100"
         aria-valuenow="{'0' if quota_unlimited else f'{percent:.2f}'}"
         aria-valuetext="{html.escape(percent_label)}"
         style="width:{'0' if quota_unlimited else f'{percent:.2f}'}%"></div></div>
    <div class="small mt-sm" data-role="txrx">上传 {fmt_bytes(tx)} · 下载 {fmt_bytes(rx)}</div>
  </div>
</section>

<section class="connection-section" aria-label="连接与订阅">
  <header class="section-head">
    <h2 class="section-title">连接与订阅</h2>
  </header>
  {import_assistant}
</section>

<aside class="plan-section" aria-label="套餐与设备">
  <header class="section-head">
    <h2 class="section-title">套餐与设备</h2>
  </header>
  <dl class="user-kv">
    <div><dt>套餐</dt><dd>{html.escape(profile_label)}</dd></div>
    <div><dt>总额度</dt><dd class="mono">{total_label}</dd></div>
    <div><dt>设备</dt><dd><span data-role="online">{online}</span> <span class="faint" data-role="device-limit">{html.escape(max_devices_label)}</span></dd></div>
    <div><dt>周期</dt><dd>{cycle_len} 天</dd></div>
    <div><dt>重置</dt><dd class="mono">{html.escape(reset_date)}</dd></div>
    <div><dt>有效期</dt><dd>{html.escape(expiry["label"])}</dd></div>
  </dl>
</aside>
{landing_section}
{real_landing_section}

<section class="trend-section" aria-label="近 30 天用量趋势">
  <header class="section-head">
    <h2 class="section-title">近 30 天用量趋势</h2>
    <div class="small faint">轻量辅助阅读</div>
  </header>
  <div class="trend-body">{spark}</div>
</section>

<section class="account-section" aria-label="账户与安全">
  <header class="section-head">
    <h2 class="section-title">账户与安全</h2>
  </header>
  <div class="account-actions">
    {account_action}
    {token_action_html}
  </div>
  {session_url_html}
</section>

</div>
<script>
(function() {{
  var profileOptions = document.querySelectorAll('[data-profile-option]');
  var profileBadge = document.getElementById('profile-selected-badge');
  var profileTitle = document.getElementById('profile-selected-title');
  var profileDesc = document.getElementById('profile-selected-desc');
  var profileCopy = document.getElementById('profile-copy');
  var profileOpen = document.getElementById('profile-open');
  var profileShowQr = document.getElementById('profile-show-qr');
  var profileQrPanel = document.getElementById('profile-qr-panel');
  var profileQrImage = document.getElementById('profile-qr-image');
  var profileQrStatus = document.getElementById('profile-qr-status');
  var profileSubUrl = document.getElementById('sub');
  function setQrStatus(text) {{ if (profileQrStatus) profileQrStatus.textContent = text; }}
  function selectProfile(option) {{
    if (!option) return;
    profileOptions.forEach(function(item) {{
      var selected = item === option;
      item.classList.toggle('selected', selected);
      item.setAttribute('aria-current', selected ? 'true' : 'false');
    }});
    var label = option.getAttribute('data-profile-label') || '';
    var desc = option.getAttribute('data-profile-desc') || '';
    var url = option.getAttribute('data-profile-url') || option.href || '';
    var qr = option.getAttribute('data-profile-qr') || '';
    if (profileBadge) profileBadge.textContent = label;
    if (profileTitle) profileTitle.textContent = label;
    if (profileDesc) profileDesc.textContent = desc;
    // Visible subscription URL text must mirror the active profile so the
    // displayed value and the copied value stay in sync after every switch.
    if (profileSubUrl) profileSubUrl.textContent = url;
    if (profileCopy) profileCopy.setAttribute('data-copy', url);
    if (profileOpen) profileOpen.setAttribute('href', url);
    if (profileShowQr) profileShowQr.setAttribute('data-qr', qr);
    if (profileQrImage) profileQrImage.setAttribute('alt', label + '订阅二维码');
    if (profileQrPanel && !profileQrPanel.hidden && profileQrImage) {{
      setQrStatus('二维码生成中…');
      profileQrImage.src = qr;
    }} else if (profileQrImage) {{
      profileQrImage.removeAttribute('src');
    }}
  }}
  if (profileQrImage) {{
    profileQrImage.addEventListener('load', function() {{ setQrStatus('二维码已生成，可在另一台设备上扫码导入。'); }});
    profileQrImage.addEventListener('error', function() {{
      setQrStatus('二维码暂不可用，请使用“复制当前模式链接”导入。');
    }});
  }}
  function flashCopied(btn) {{
    var label = btn.querySelector('span');
    var prev = label ? label.textContent : '';
    if (label) label.textContent = '已复制 ✓';
    btn.disabled = true;
    setTimeout(function() {{ if (label) label.textContent = prev; btn.disabled = false; }}, 1400);
  }}
  document.addEventListener('click', function(ev) {{
    var option = ev.target.closest ? ev.target.closest('[data-profile-option]') : null;
    if (option) {{ ev.preventDefault(); selectProfile(option); return; }}
    var qrButton = ev.target.closest ? ev.target.closest('#profile-show-qr') : null;
    if (qrButton && profileQrPanel && profileQrImage) {{
      ev.preventDefault();
      var opening = profileQrPanel.hidden;
      profileQrPanel.hidden = !opening;
      qrButton.setAttribute('aria-expanded', opening ? 'true' : 'false');
      var qrLabel = qrButton.querySelector('span');
      if (qrLabel) qrLabel.textContent = opening ? '隐藏二维码' : '显示二维码';
      if (opening) {{
        setQrStatus('二维码生成中…');
        profileQrImage.src = qrButton.getAttribute('data-qr') || '';
      }} else {{
        profileQrImage.removeAttribute('src');
        setQrStatus('在另一台设备上用客户端扫码导入；二维码仅在这里按需生成。');
      }}
      return;
    }}
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
    # Sparklines are initial-render only. The five-second overview payload
    # intentionally excludes SVG so polling does not rebuild this cell.
    if daily is not None:
        spark_cell = f'<td class="spark-cell" headers="users-col-trend" data-label="30 天趋势" data-role="spark">{sparkline_svg(daily_window_for_user(user, daily, days=30))}</td>'
    total = user_total_quota(cfg)
    quota_label = '不限' if total <= 0 else fmt_bytes(total)
    max_devices = configured_max_devices(cfg)
    user_revision = user_config_revision(cfg)
    revision_query = f'revision={user_revision}'
    device_limit_summary = (
        '不限设备'
        if max_devices == 0
        else f'{max_devices} 设备'
    )
    online_device_summary = (
        f'在线 <span data-role="online">{int(online.get(user, 0) or 0)}</span>'
        ' · 设备不限'
        if max_devices == 0
        else (
            f'在线 <span data-role="online">{int(online.get(user, 0) or 0)}</span>'
            f' / {max_devices} 设备'
        )
    )
    base_gb = int(round(base_quota_bytes(cfg) / 1024 / 1024 / 1024)) if base_quota_bytes(cfg) > 0 else 0
    extra_gb = quota_extra_gb(cfg)
    panel = f'{base_url}/panel/{user}?token={cfg.get("sub_token", "")}'
    sub_http = f'{base_url}/sub/{user}?token={cfg.get("sub_token", "")}'
    metered = user_compat.is_metered(cfg)
    tuic_allowed = user_compat.tuic_enabled(cfg)
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
    disabled_badge = ('<span class="badge badge-danger" data-role="disabled-badge">已停用</span>'
                     if disabled else
                     '<span class="badge badge-danger" data-role="disabled-badge" hidden>已停用</span>')
    guest_preview = ' · 按量' if metered else ''
    quota_preview = '不限' if total <= 0 else f'{base_gb} GB{extra_preview}'
    summary_preview = f'<span class="summary-preview">{quota_preview} · {device_limit_summary}{guest_preview}{expires_preview}</span>'
    expires_attr = html.escape(expires_at, quote=True)
    note_attr = html.escape(note, quote=True)
    percent_label = '不限' if total <= 0 else f'{percent:.1f}%'
    if disabled:
        toggle_button = (
            f'<button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" '
            f'name="user" value="{user_esc}" data-user="{user_esc}" '
            f'formaction="/admin/toggle-user?{revision_query}&amp;desired=enabled" '
            'data-action="enable-user" '
            'title="恢复该用户的连接权限">启用</button>'
        )
    else:
        toggle_button = (
            f'<button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" '
            f'name="user" value="{user_esc}" data-user="{user_esc}" '
            f'formaction="/admin/toggle-user?{revision_query}&amp;desired=disabled" '
            'data-action="disable-user" '
            'title="临时停用：拒绝新连接并断开现有会话，不删除用户">暂停</button>'
        )
    online_n = int(online.get(user, 0) or 0)
    return f'''<tr data-user="{user_esc}" data-online="{online_n}" data-percent="{percent:.1f}" data-revision="{user_revision}">
<td headers="users-col-user" data-label="用户">
  <div class="row gap-sm" style="flex-wrap:nowrap;">
    <div class="user-avatar" aria-hidden="true">{html.escape(user[:1].upper())}</div>
    <div style="min-width:0;">
      <div class="bold">{user_esc} {guest_badge}{tuic_badge}{disabled_badge}{expired_badge}</div>
      <div class="small">{online_device_summary}</div>
      {note_preview}
    </div>
  </div>
</td>
{spark_cell}
<td headers="users-col-usage" data-label="本周期用量">
  <div class="row" style="justify-content:space-between;margin-bottom:4px;">
    <span class="bold" data-role="used">{fmt_bytes(used)}</span>
    <span class="small">/ {quota_label}</span>
  </div>
  <div class="mini-bar"><div class="mini-fill {bar_cls}" data-role="bar" role="progressbar"
       aria-label="{user_esc} 本周期流量" aria-valuemin="0" aria-valuemax="100"
       aria-valuenow="{bar_w}" aria-valuetext="{html.escape(percent_label)}" style="width:{bar_w}%"></div></div>
  <div class="small mt-sm" data-role="detail">{percent_label} · ↑{fmt_bytes(tx)} ↓{fmt_bytes(rx)}</div>
</td>
<td headers="users-col-actions" data-label="操作">
<div class="edit-user-control">
  <button type="button" class="btn secondary btn-sm edit-user"
          data-edit-user="{user_esc}" data-user-revision="{user_revision}" data-max-devices="{max_devices}"
          data-quota-gb="{base_gb}" data-quota-extra-gb="{extra_gb}"
          data-expires-at="{expires_attr}" data-note="{note_attr}"
          data-landing-isp="{html.escape(user_compat.landing_field(cfg, 'landing_isp'), quote=True)}"
          data-landing-region="{html.escape(user_compat.landing_field(cfg, 'landing_region'), quote=True)}"
          data-landing-note="{html.escape(user_compat.landing_field(cfg, 'landing_note'), quote=True)}"
          data-landing-ip="{html.escape(user_compat.landing_field(cfg, 'landing_ip'), quote=True)}"
          data-metered="{'1' if metered else '0'}" data-tuic-enabled="{'1' if tuic_allowed else '0'}">编辑套餐</button>
  {summary_preview}
</div>
<div class="row gap-sm mt-sm user-actions">
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/reset-usage?{revision_query}"
          data-action="reset-user-usage"
          title="清空该用户已用流量，且从服务器总流量中扣除">清流量</button>
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/refresh-usage?{revision_query}"
          data-action="refresh-user-usage"
          title="清空该用户已用流量，但保留在服务器总流量中">刷新流量</button>
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/rotate-token?{revision_query}" data-action="rotate-user-token"
          title="重置该用户订阅令牌，旧订阅/面板链接立即失效">重置订阅</button>
  {toggle_button}
  <button class="btn danger-btn btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/delete?{revision_query}" data-action="delete-user">删除</button>
</div>
<div class="row-error small" style="display:none;color:var(--danger);margin-top:4px;"></div>
</td>
<td class="link-cell" headers="users-col-links" data-label="链接">
  <div class="link-row">
    <a href="{html.escape(panel)}" target="_blank" rel="noopener">{icon("dashboard")}<span>面板</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(panel)}"
            title="复制专属面板链接（首次打开后地址栏不再含密钥）" aria-label="复制 {user_esc} 的专属面板链接">{icon("copy")}<span class="copy-label">复制专属面板</span></button>
  </div>
  <div class="link-row">
    <a href="{html.escape(sub_http)}" target="_blank" rel="noopener">{icon("open")}<span>订阅</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(sub_http)}"
            title="复制订阅链接" aria-label="复制 {user_esc} 的订阅链接">{icon("copy")}</button>
  </div>
</td>
</tr>'''


def render_admin(host, base_url, flash='', *, create_draft=None, create_error_field=''):
    users = load_json(USERS_FILE, {})
    landing_registry = _landing_registry_or_empty()
    online = load_json(ONLINE_FILE, {})
    now = local_now()
    mk = month_key(now)
    daily = load_json(USAGE_DAILY_FILE, {})
    total_used = sum(scaled_usage_for_user(u, daily=daily, now=now)[2] for u in users)
    total_used += int(
        preserved_raw_for_cycle(now=now) * current_display_multiplier()
    )
    settlement_day = get_settlement_day()
    cycle_length = get_cycle_length_days()
    cycle_start = cycle_start_for(now)
    cycle_end = cycle_start + timedelta(days=cycle_length - 1)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_range = f'{cycle_start.strftime("%m/%d")} → {cycle_end.strftime("%m/%d")} · 第 {cycle_day}/{cycle_length} 天'
    settle_form = (
        f'<form method="post" action="/admin/cycle-config" class="inline-form-row cycle-config-form" '
        f'data-confirm="更改结算日或周期会重新锚定计费日历，确认保存？" style="margin:0;">'
        f'<label for="cycle-day" class="small" style="margin-right:6px;">结算日</label>'
        f'<input id="cycle-day" name="day" type="number" min="1" max="28" value="{settlement_day}" '
        f'style="width:60px;margin-right:6px;" required>'
        f'<label for="cycle-length" class="small" style="margin-right:6px;">周期</label>'
        f'<input id="cycle-length" name="length" type="number" min="{CYCLE_LENGTH_MIN}" max="{CYCLE_LENGTH_MAX}" '
        f'value="{cycle_length}" style="width:60px;margin-right:2px;" required>'
        f'<span class="small" style="margin-right:6px;">天</span>'
        f'<button class="btn ghost btn-sm" type="submit">保存</button>'
        f'</form>'
    )
    recovering_create = isinstance(create_draft, dict)
    if recovering_create:
        # Only the explicit, non-sensitive fields below are ever read back from
        # the draft. Passwords and tokens therefore cannot become HTML values.
        draft = create_draft
        alert = ''
        create_error = render_alert(
            flash_text(flash), 'err', element_id='create-add-error',
        )
    else:
        draft = {}
        alert = render_alert(
            flash_text(flash),
            'err' if flash.startswith('err:') else 'flash',
        )
        create_error = ''

    def draft_value(name, default=''):
        return html.escape(str(draft.get(name, default)), quote=True)

    def validation_attrs(field_id):
        if recovering_create and create_error_field == field_id:
            return (
                ' aria-invalid="true" aria-describedby="create-add-error"'
                ' autofocus'
            )
        return ''

    create_user = draft_value('user')
    create_quota_gb = draft_value('quota_gb', 150)
    create_quota_extra_gb = draft_value('quota_extra_gb', 0)
    create_expires_at = draft_value('expires_at')
    create_note = draft_value('note')
    create_landing_initial = str(draft.get('landing_initial_egress_id') or '')
    landing_options = ['<option value="">暂不分配</option>']
    for public_node in _enabled_landing_public_nodes(landing_registry):
        node_id = str(public_node['id'])
        selected = ' selected' if node_id == create_landing_initial else ''
        landing_options.append(
            f'<option value="{html.escape(node_id, quote=True)}"{selected}>'
            f'{html.escape(public_node["name"])}</option>'
        )
    if len(landing_options) > 1:
        create_landing_field = (
            '<div class="form-field"><label for="create-landing-initial-egress">'
            '初始家宽出口</label><select class="select" '
            'id="create-landing-initial-egress" name="landing_initial_egress_id"'
            f'{validation_attrs("create-landing-initial-egress")}>'
            + ''.join(landing_options)
            + '</select><span class="hint">选中后立即授权并设为初始出口；后续可增加更多节点</span></div>'
        )
    else:
        create_landing_field = (
            '<div class="form-field"><label for="create-landing-initial-egress">'
            '初始家宽出口</label><select class="select" '
            'id="create-landing-initial-egress" name="landing_initial_egress_id" disabled>'
            '<option value="">暂无可用节点</option></select>'
            '<span class="hint"><a href="/admin/landing-egresses">先添加或启用家宽出口节点</a></span></div>'
        )
    create_open = ' open' if recovering_create else ''
    create_guest_checked = ' checked' if (
        bool(draft.get('guest')) if recovering_create else True
    ) else ''
    create_tuic_checked = ' checked' if bool(draft.get('tuic_enabled')) else ''
    rows = ''.join(row_form(u, cfg, online, host, base_url, daily=daily, now=now) for u, cfg in users.items())
    if rows:
        rows += '<tr id="filter-empty" hidden><td colspan="5" class="empty">没有符合当前筛选条件的用户</td></tr>'
    else:
        rows = '<tr><td colspan="5" class="empty">暂无用户，使用下方表单创建第一个用户</td></tr>'
    content = f'''{alert}
<div class="overview-stats">
  <div class="overview-stat">
    <div class="label">本周期总流量</div>
    <div class="value" id="total-used">{fmt_bytes(total_used)}</div>
    <div class="sub">{html.escape(cycle_range)}</div>
  </div>
  <div class="overview-stat">
    <div class="label">计费周期</div>
    <div class="value">{mk}</div>
    <div class="sub">每 {cycle_length} 天结算 · 第 {settlement_day} 日</div>
  </div>
  <div class="overview-stat">
    <div class="label">快速操作</div>
    <form method="post" action="/admin/reset-usage-all" data-action="reset-all">
      <button class="btn btn-sm danger-btn" type="submit" style="margin-top:10px;">清空本周期用量</button>
    </form>
    <div class="quick-actions">
      <a class="btn btn-sm" href="/admin/usage.csv?window=cycle">导出 CSV</a>
    </div>
  </div>
</div>
<!-- Shared hidden form used by all per-user action buttons (清流量 / 刷新流量 /
     重置订阅 / 暂停 / 启用 / 删除).  Buttons declare form="user-action-form"
     so clicks route through this form; JS reads ev.submitter to determine which
     action (formaction) to POST.  Must appear before .users-section so the
     form is in the DOM when any button is clicked. -->
<form method="post" id="user-action-form" hidden></form>
<!-- Edit-user dialog: opened by .edit-user buttons via showModal().  The JS
     reads data-* from the clicked button and pre-fills every field.  Must not
     expose sub_token or password_hash. -->
<dialog id="user-edit-dialog" class="admin-dialog">
  <div class="dialog-inner">
    <div class="dialog-head">
      <h2 class="dialog-title" id="user-edit-title">编辑用户</h2>
      <button type="button" class="dialog-close" data-dialog-close aria-label="关闭">×</button>
    </div>
      <form method="post" id="user-edit-form" action="/admin/update">
      <input type="hidden" name="user" id="edit-user-name">
      <input type="hidden" name="user_revision" id="edit-user-revision">
      <div class="form-grid">
        <div class="form-field"><label for="edit-max-devices">最大设备数</label>
          <input id="edit-max-devices" name="max_devices" type="number" min="0" max="999" value="2"></div>
        <div class="form-field"><label for="edit-quota-gb">流量上限 GB</label>
          <input id="edit-quota-gb" name="quota_gb" type="number" min="0" max="10240" value="150"></div>
        <div class="form-field"><label for="edit-quota-extra-gb">加量包 GB</label>
          <input id="edit-quota-extra-gb" name="quota_extra_gb" type="number" min="0" max="10240" value="0"></div>
        <div class="form-field"><label for="edit-expires-at">到期日</label>
          <input id="edit-expires-at" name="expires_at" type="date" min="2000-01-01" max="2099-12-31"><span class="hint">留空 = 不限期；年份范围 2000–2099</span></div>
        <div class="form-field" style="grid-column:1/-1"><label for="edit-note">备注</label>
          <input id="edit-note" name="note" maxlength="200" placeholder="可选"></div>
        <div class="form-field"><label for="edit-landing-isp">落地运营商</label>
          <input id="edit-landing-isp" name="landing_isp" maxlength="120" placeholder="可选，仅展示"></div>
        <div class="form-field"><label for="edit-landing-region">落地地区</label>
          <input id="edit-landing-region" name="landing_region" maxlength="120" placeholder="可选，仅展示"></div>
        <div class="form-field"><label for="edit-landing-ip">家宽 IP</label>
          <input id="edit-landing-ip" name="landing_ip" maxlength="45" autocomplete="off" spellcheck="false" placeholder="可选，仅支持 IPv4 / IPv6"></div>
        <div class="form-field" style="grid-column:1/-1"><label for="edit-landing-note">落地说明</label>
          <input id="edit-landing-note" name="landing_note" maxlength="120" placeholder="可选，仅展示，不影响出口"></div>
        <div class="form-field"><label for="edit-panel-password">面板密码</label>
          <input id="edit-panel-password" name="panel_password" type="password" minlength="8" maxlength="256"
                 autocomplete="new-password" placeholder="留空保持不变"><span class="hint">至少 8 位</span></div>
        <div class="form-field"><label for="edit-proxy-password">代理密码</label>
          <input id="edit-proxy-password" name="password" type="password" maxlength="256"
                 autocomplete="new-password" placeholder="留空保持不变"></div>
      </div>
      <div class="form-options">
        <label class="switch"><input type="checkbox" name="guest" id="edit-guest">按量用户</label>
        <label class="switch"><input type="checkbox" name="tuic_enabled" id="edit-tuic-enabled">允许 TUIC</label>
      </div>
      <div class="dialog-foot">
        <button type="button" class="btn ghost btn-sm" data-dialog-close>取消</button>
        <button type="submit" class="btn primary btn-sm">保存更改</button>
      </div>
    </form>
  </div>
</dialog>
<div class="users-section">
  <div class="users-header">
    <h2 class="users-title">用户列表</h2>
    <div class="users-toolbar">
      <input id="user-filter" type="search" placeholder="搜索…" aria-label="搜索用户名" autocomplete="off" class="user-filter-input">
      <div class="filter-chips" role="group" aria-label="状态筛选">
        <button type="button" class="chip active" data-filter="all" aria-pressed="true">全部</button>
        <button type="button" class="chip" data-filter="online" aria-pressed="false">在线</button>
        <button type="button" class="chip" data-filter="over" aria-pressed="false">超限</button>
      </div>
      <span class="filter-count" id="filter-count" role="status" aria-live="polite">{len(users)} 用户</span>
    </div>
  </div>
  <div class="small faint mt-sm">“复制专属面板”会复制每位用户各自的安全入口；打开后地址栏会安全归一为 <code>/user/panel</code>。</div>
  <div class="users-table-wrap">
    <table class="users-table" data-user-count="{len(users)}"><caption class="sr-only">用户、套餐用量、管理操作与订阅链接</caption><thead><tr><th>用户</th><th>趋势</th><th>用量</th><th>操作</th><th>链接</th></tr></thead><tbody>{rows}</tbody></table>
  </div>
</div>
<details class="create-section"{create_open}>
  <summary class="create-toggle">+ 新增用户</summary>
  <div class="create-form">
    {create_error}
    <form method="post" action="/admin/add" class="inline-form">
      <div class="create-grid">
        <div class="form-field"><label for="create-user">用户名</label><input id="create-user" name="user" value="{create_user}" maxlength="64" required autocomplete="off"{validation_attrs('create-user')}></div>
        <div class="form-field"><label for="create-panel-password">面板密码</label><input id="create-panel-password" name="panel_password" type="password" minlength="8" maxlength="256" autocomplete="new-password" placeholder="可选"{validation_attrs('create-panel-password')}><span class="hint">至少 8 位</span></div>
        <div class="form-field"><label for="create-proxy-password">代理密码</label><input id="create-proxy-password" name="password" type="password" maxlength="256" autocomplete="new-password" placeholder="可选"{validation_attrs('create-proxy-password')}></div>
        <div class="form-field"><label for="create-quota-gb">流量上限 GB</label><input id="create-quota-gb" name="quota_gb" type="number" value="{create_quota_gb}" min="0" max="10240" required{validation_attrs('create-quota-gb')}><span class="hint">0 = 不限</span></div>
        <div class="form-field"><label for="create-quota-extra-gb">加量包 GB</label><input id="create-quota-extra-gb" name="quota_extra_gb" type="number" value="{create_quota_extra_gb}" min="0" max="10240" required{validation_attrs('create-quota-extra-gb')}></div>
        <div class="form-field"><label for="create-expires-at">到期日</label><input id="create-expires-at" name="expires_at" type="date" value="{create_expires_at}" min="2000-01-01" max="2099-12-31"><span class="hint">留空 = 不限期；年份范围 2000–2099</span></div>
        {create_landing_field}
        <div class="form-field" style="grid-column:1/-1"><label for="create-note">备注</label><input id="create-note" name="note" value="{create_note}" maxlength="200" placeholder="可选"></div>
      </div>
      <div class="form-options">
        <label class="switch"><input type="checkbox" name="guest"{create_guest_checked}>按量用户</label>
        <label class="switch"><input type="checkbox" name="tuic_enabled"{create_tuic_checked}>允许 TUIC</label>
      </div>
      <button class="btn" type="submit">创建</button>
    </form>
  </div>
</details>
<script src="/static/admin-poll.js?v={ADMIN_POLL_JS_ETAG.strip('"')}" defer></script>
'''
    poll_status = (
        '<button class="badge poll-status poll-status-button" data-role="admin-poll-status" '
        'type="button" title="立即更新">自动更新 · 30 s</button>'
        '<span class="sr-only" id="admin-poll-announcer" role="status" aria-live="polite"></span>'
    )
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
        display_multiplier=current_display_multiplier(),
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
        asset_version=USAGE_JS_ETAG.strip('"'),
        user_revision=user_config_revision,
    )


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


def _build_overview_json_payload(*, now):
    return usage_dashboard.build_overview_json_payload(_usage_context(), now=now)


def _build_overview_user(username, *, now):
    """Fresh single-user overview row, same schema as /admin/overview.json.

    Returned inside mutation JSON responses so the client can patch the row
    directly instead of re-fetching the whole overview."""
    ctx = _usage_context()
    users = ctx.load_json(ctx.users_file, {})
    cfg = users.get(username)
    if not isinstance(cfg, dict):
        return None
    online = ctx.load_json(ctx.online_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})
    return usage_dashboard.build_overview_user_entry(
        ctx, username, cfg, online=online, daily=daily, now=now,
    )


def _static_reload_status():
    """Read-only view of the existing Xray/TUIC reload-pending markers.

    Never writes, never schedules a reload — it only reports whether the
    durable markers left by reload_async() are still present."""
    xray_pending = xray_config._has_reload_pending(xray_config.CONFIG_FILE)
    tuic_pending = tuic_config._has_reload_pending(tuic_config.CONFIG_FILE)
    return {
        'pending': bool(xray_pending or tuic_pending),
        'xray': bool(xray_pending),
        'tuic': bool(tuic_pending),
    }


def _build_analytics_json_payload(*, now, include_charts=True):
    return usage_dashboard.build_analytics_json_payload(
        _usage_context(), now=now, include_charts=include_charts,
    )


def _build_user_json_payload(uid, *, now, include_charts=True):
    return usage_dashboard.build_user_json_payload(
        _usage_context(), uid, now=now, include_charts=include_charts,
    )


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


def render_codex_page(host):
    payload = codex_quota.build_dashboard_payload(range_key='day')
    return codex_dashboard.render_page(
        payload,
        render_admin_shell=render_admin_shell,
        asset_version=CODEX_QUOTA_JS_ETAG.strip('"'),
    )


def render_user_detail_page(uid, host):
    return usage_dashboard.render_user_detail_page(_usage_context(), uid, host)


def _render_daily_table_collapsed(host):
    return usage_dashboard.render_daily_table_collapsed(_usage_context(), host)


def _incident_context():
    return incident_console.IncidentConsoleContext(
        alerts=alerts,
        display_multiplier=current_display_multiplier(),
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
        render_line_radar_summary=render_line_radar_summary,
        render_cost_calibrator=render_cost_calibrator,
        render_alert=render_alert,
        flash_text=flash_text,
        render_admin_shell=render_admin_shell,
        user_revision=user_config_revision,
    )


def build_incident_payload(*, now=None):
    return incident_console.build_incident_payload(_incident_context(), now=now)


def render_incidents(host, flash=''):
    return incident_console.render_incidents(_incident_context(), host, flash=flash)


def probe_cron_heartbeat():
    # usage_daily.json is the authoritative quota ledger and is committed
    # before the legacy cycle summary. Its mtime therefore reflects successful
    # accounting progress even when refreshing usage.json subsequently fails.
    return health.probe_cron_heartbeat(USAGE_DAILY_FILE)


def probe_systemd(unit):
    return health.probe_systemd(unit, runner=subprocess.run)


def probe_auth_readiness():
    return health.probe_auth_readiness(timeout=1.0)


def probe_disk():
    return health.probe_disk(disk_usage=shutil.disk_usage)


def probe_cert(path=None):
    p = Path(path) if path else Path('/root/hysteria/server.crt')
    return health.probe_cert(p, runner=subprocess.run, environ=os.environ)


def probe_panel_tls():
    return health.probe_panel_tls(
        '/etc/nginx/sites-enabled/hysteria-panel-https.conf',
        '/root/hysteria/state/https_required',
        runner=subprocess.run,
        environ=os.environ,
    )


def probe_certbot_renewal():
    return health.probe_certbot_renewal(
        '/root/hysteria/state/https_required',
        runner=subprocess.run,
    )


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


def _render_health_top_kpis():
    """Run all probes and derive 4 top-level KPIs."""
    probes = (
        ('整体状态', _probe_overall_status),
        ('在线服务', _probe_online_services),
        ('HTTPS 证书', _probe_https_cert),
        ('磁盘空间', _probe_disk_kpi),
    )

    def run(item):
        title, fn = item
        try:
            result = fn()
        except Exception:
            result = {'ok': False, 'label': '—'}
        return title, result

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix='health-kpi') as ex:
        results = dict(ex.map(run, probes))
    return results


def _probe_overall_status():
    """Healthy if all core services are up and no certs are expiring."""
    checks = [
        ('鉴权服务', lambda: probe_systemd('hysteria-auth.service')),
        ('Hysteria', lambda: probe_systemd('hysteria-server.service')),
        ('Xray', lambda: probe_systemd('xray.service')),
        ('TUIC', lambda: probe_systemd('tuic-server.service')),
        ('证书自动续期', probe_certbot_renewal),
    ]
    ok_count = 0
    for name, fn in checks:
        try:
            r = fn()
            if r.get('ok'):
                ok_count += 1
        except Exception:
            pass
    total = len(checks)
    ok = ok_count == total
    label = f'{ok_count}/{total} 正常' if ok else f'{ok_count}/{total} 异常'
    return {'ok': ok, 'label': label}


def _probe_online_services():
    try:
        data = load_json(ONLINE_FILE, {})
        n = sum(int(v) for v in data.values())
        return {'ok': n > 0, 'label': f'{n} 在线'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def _probe_https_cert():
    certs = [
        probe_cert(),
        probe_panel_tls(),
    ]
    worst = None
    for r in certs:
        if not r.get('ok'):
            worst = r
    if worst is None:
        return {'ok': True, 'label': '全部有效'}
    return worst


def _probe_disk_kpi():
    return probe_disk()


def _health_top_kpi_card(title, probe_result, is_text=False):
    is_ok = bool(probe_result['ok'])
    label = probe_result.get('label', '—')
    badge_cls = 'badge' if is_ok else 'badge badge-danger'
    badge_inner = f'<span class="{badge_cls}">{html.escape(label)}</span>' if is_text else html.escape(label)
    return (
        f'<div class="health-kpi-card">'
        f'<div class="health-kpi-label">{html.escape(title)}</div>'
        f'<div class="health-kpi-value{" is-text" if is_text else ""}">{badge_inner}</div>'
        f'</div>'
    )


def _render_health_cards():
    """Run independent probes concurrently while preserving card order."""
    probes = (
        ('CRON 心跳', probe_cron_heartbeat),
        ('鉴权服务', lambda: probe_systemd('hysteria-auth.service')),
        ('鉴权依赖', probe_auth_readiness),
        ('Hysteria', lambda: probe_systemd('hysteria-server.service')),
        ('Xray', lambda: probe_systemd('xray.service')),
        ('TUIC', lambda: probe_systemd('tuic-server.service')),
        ('限流 Timer', lambda: probe_systemd('hysteria-traffic-limiter.timer')),
        ('TLS 证书', probe_cert),
        ('面板 HTTPS', probe_panel_tls),
        ('证书自动续期', probe_certbot_renewal),
        ('在线用户', probe_online),
        ('Xray 配置权限', probe_xray_config_permissions),
        ('Hysteria 更新', probe_hysteria_update),
        ('最近备份', probe_recent_backup),
        ('磁盘', probe_disk),
    )

    def run_probe(item):
        title, probe = item
        try:
            result = probe()
            if not isinstance(result, dict):
                raise ValueError('invalid health probe result')
        except Exception:
            result = {'ok': False, 'label': '探测失败'}
        return _health_card(title, result)

    with ThreadPoolExecutor(
        max_workers=min(8, len(probes)),
        thread_name_prefix='health-probe',
    ) as executor:
        return ''.join(executor.map(run_probe, probes))


def _health_widget_context():
    return health_widgets.HealthWidgetContext(
        display_multiplier=current_display_multiplier(),
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


def render_line_radar_summary(now=None):
    return health_widgets.render_line_radar_summary(_health_widget_context(), now=now)


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
    'hysteria_updated': 'Hysteria 已更新到目标版本',
    'hysteria_update_check_failed': '检查更新失败，请查看日志',
    'hysteria_update_rolled_back': '更新失败，已自动回滚到上一个版本',
    'hysteria_update_failed': '更新失败，请查看日志或手动回滚',
    'hysteria_update_skipped': '更新已跳过：缺少新鲜备份或受策略限制',
    'hysteria_update_busy': '另一个更新正在进行中，请稍后重试',
    'hysteria_update_scheduled': 'Hysteria 更新已进入后台队列',
}


def render_health(host, flash=''):
    alert = render_prefixed_alert(flash, _HEALTH_FLASH)
    kpis = _render_health_top_kpis()
    kpi_cards = ''.join(
        _health_top_kpi_card(title, result, is_text=(title == '整体状态'))
        for title, result in kpis.items()
    )
    content = (
        alert
        + '<div class="admin-page health-page">'

        # --- Page header controls ---
        # (rendered by render_admin_shell topbar_extra)

        # --- Top 4 KPIs (refreshed with the service snapshot) ---
        + '<div class="health-top-kpis" id="health-live-kpis">' + kpi_cards + '</div>'

        # --- Core services table (live-refreshed) ---
        + '<section class="admin-section">'
        + '<div class="admin-section-header">'
        + '<h2 class="admin-section-title">核心服务</h2>'
        + '</div>'
        + '<div class="admin-section-body no-pad">'
        + '<div class="data-table-wrap" tabindex="0" aria-label="核心服务状态，可横向滚动">'
        + '<table class="data-table" id="health-live-grid">'
        + '<thead><tr><th>服务</th><th>状态</th></tr></thead>'
        + '<tbody>' + _render_health_cards() + '</tbody>'
        + '</table>'
        + '</div>'
        + '</div>'
        + '</section>'

        # --- Infrastructure ---
        + '<section class="admin-section">'
        + '<div class="admin-section-header">'
        + '<h2 class="admin-section-title">基础设施与生命周期</h2>'
        + '</div>'
        + '<div class="admin-section-body no-pad">'
        + '<div class="data-table-wrap" tabindex="0" aria-label="基础设施状态，可横向滚动">'
        + '<table class="data-table">'
        + '<thead><tr><th>项目</th><th>状态</th></tr></thead>'
        + '<tbody>'
        + _health_card('TLS 证书', probe_cert())
        + _health_card('面板 HTTPS', probe_panel_tls())
        + _health_card('证书自动续期', probe_certbot_renewal())
        + _health_card('最近备份', probe_recent_backup())
        + _health_card('Xray 配置权限', probe_xray_config_permissions())
        + _health_card('Hysteria 更新', probe_hysteria_update())
        + _health_card('在线用户', probe_online())
        + '</tbody>'
        + '</table>'
        + '</div>'
        + '</div>'
        + '</section>'

        # --- Line radar ---
        + render_line_radar()

        # --- Cost calibrator (advanced ops) ---
        + render_cost_calibrator()
        + '<div id="health-live-update">' + hysteria_update.render_history() + '</div>'

        + '</div>'
        + f'''<script src="/static/admin-poll.js?v={ADMIN_POLL_JS_ETAG.strip('"')}" defer></script>'''
        + '''<span class="sr-only" id="health-refresh-announcer" role="status" aria-live="polite"></span>
<script>
(function(){
  var grid = document.getElementById('health-live-grid');
  var status = document.getElementById('health-refresh-status');
  var announcer = document.getElementById('health-refresh-announcer');
  var button = document.getElementById('health-refresh-now');
  var timer = null, inflight = false, running = false;
  var failures = 0, activeController = null;
  function stamp(){ return new Date().toLocaleTimeString([], {hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
  function retryDelay(){
    var exponent = Math.min(failures, 3);
    var base = Math.min(240000, 30000 * Math.pow(2, exponent));
    return Math.min(240000, base + (failures ? Math.floor(Math.random() * 4001) : 0));
  }
  function clearScheduled(){
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function scheduleNext(){
    if (!running || document.hidden || timer) return;
    timer = setTimeout(function(){ timer = null; refresh(false); }, retryDelay());
  }
  function refresh(manual){
    if (!grid || inflight || !running || document.hidden) return;
    if (manual) failures = 0;
    clearScheduled();
    inflight = true;
    if (button) button.disabled = true;
    if (status) status.textContent = '更新中';
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    activeController = controller;
    var timedOut = false;
    var timeout = setTimeout(function(){
      timedOut = true;
      if (controller) controller.abort();
    }, 10000);
    fetch('/admin/health.fragment?snapshot=1', {credentials:'same-origin',cache:'no-store',signal:controller ? controller.signal : undefined})
      .then(function(response){
        if (response.status === 401) throw new Error('login');
        if (!response.ok) throw new Error('http');
        return response.json();
      })
      .then(function(snapshot){
        if (typeof snapshot.rows !== 'string' || typeof snapshot.kpis !== 'string' || typeof snapshot.update !== 'string') throw new Error('payload');
        var tbody = grid.querySelector('tbody');
        if (tbody) tbody.innerHTML = snapshot.rows;
        if (tbody) tbody.querySelectorAll('[data-health]').forEach(function(row){
          document.querySelectorAll('.health-page [data-health]').forEach(function(other){
            if (!grid.contains(other) && other.getAttribute('data-health') === row.getAttribute('data-health')) other.innerHTML = row.innerHTML;
          });
        });
        document.getElementById('health-live-kpis').innerHTML = snapshot.kpis;
        var update = document.getElementById('health-live-update');
        if (!update.contains(document.activeElement) && !update.querySelector('button:disabled')) update.innerHTML = snapshot.update;
        document.querySelectorAll('[data-local-time]').forEach(function(el){
          var date = new Date(el.getAttribute('datetime'));
          if (!isNaN(date.getTime())) el.textContent = date.toLocaleString();
        });
        failures = 0;
        if (status) status.textContent = '更新 ' + stamp();
      })
      .catch(function(error){
        if (error && error.name === 'AbortError' && !timedOut) return;
        failures = Math.min(failures + 1, 8);
        var message = error.message === 'login' ? '登录已失效' : '更新失败';
        if (timedOut) message = '请求超时';
        if (error.message === 'login') stop();
        if (status) status.textContent = message;
        if (announcer) announcer.textContent = message + (
          error.message === 'login' ? '，请重新登录' : '，系统稍后自动重试'
        );
      })
      .finally(function(){
        clearTimeout(timeout);
        if (activeController === controller) activeController = null;
        inflight = false;
        if (button) button.disabled = false;
        scheduleNext();
      });
  }
  function start(){
    if (running) return;
    running = true;
    failures = 0;
    scheduleNext();
  }
  function stop(){
    running = false;
    clearScheduled();
    if (activeController) activeController.abort();
  }
  if (button) button.addEventListener('click', function(){ refresh(true); });
  document.addEventListener('visibilitychange', function(){
    if (document.hidden) stop();
    else { start(); refresh(true); }
  });
  window.addEventListener('pagehide', stop);
  document.querySelectorAll('[data-local-time]').forEach(function(el){
    var date = new Date(el.getAttribute('datetime'));
    if (!isNaN(date.getTime())) el.textContent = date.toLocaleString();
  });
  start();
})();
</script>'''
    )
    test_btn = ('<form method="post" action="/admin/test-alert" class="inline-form-row">'
                '<button class="btn secondary btn-sm" type="submit">发送测试告警</button></form>')
    refresh_controls = (
        '<button class="btn ghost btn-sm" id="health-refresh-now" type="button">立即刷新</button>'
        '<span class="badge poll-status" id="health-refresh-status">自动更新 · 30s</span>'
        '<span class="badge poll-status" data-role="admin-poll-status">更新器就绪</span>'
        '<span class="sr-only" id="admin-poll-announcer" role="status" aria-live="polite"></span>'
    )
    return render_admin_shell('health', '健康状态', content,
                              badge=host, subtitle='状态卡片每 30 秒自动更新',
                              topbar_extra=refresh_controls + test_btn)


def render_health_fragment():
    return _render_health_cards()


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
    previous_multiplier = current_display_multiplier()
    summary = summarize_cost_calibration(now=now)
    policy = cost_calibrator.load_auto_policy(MULTIPLIER_AUTO_POLICY_FILE)
    decision = cost_calibrator.evaluate_multiplier_candidate(
        summary, previous_multiplier, policy,
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
        previous_multiplier=previous_multiplier,
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
    'password_long': f'密码不能超过 {PASSWORD_MAX_LENGTH} 位',
}


def render_settings(host, flash=''):
    meta = load_meta()
    admin_user = html.escape(str(meta.get('admin_user', 'admin')))
    alert = render_prefixed_alert(flash, _SETTINGS_FLASH)
    content = f'''{alert}
<div class="admin-page settings-page">
  <section class="form-section">
    <div class="form-section-title">管理员账号</div>
    <div class="form-section-desc">当前登录的管理员账号名称。</div>
    <div class="small">账号：<code>{admin_user}</code></div>
  </section>

  <section class="form-section" style="max-width:560px;">
    <div class="form-section-title">侧边栏动画</div>
    <div class="form-section-desc">默认跟随系统的减少动态效果设置；此偏好仅保存在当前浏览器，且只影响桌面侧边栏。</div>
    <label class="switch"><input type="checkbox" id="sidebar-motion-toggle">本浏览器强制显示侧边栏动画</label>
  </section>

  <section class="form-section" style="max-width:560px;">
    <div class="form-section-title">修改管理员密码</div>
    <div class="form-section-desc">定期更换管理员密码可以降低被泄露的风险。</div>
    <form method="post" action="/admin/change-password" class="inline-form">
      <div class="form-field">
        <label for="settings-current-password">当前密码</label>
        <input id="settings-current-password" name="current" type="password" maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="current-password" required>
      </div>
      <div class="form-field">
        <label for="settings-new-password">新密码（至少 8 位）</label>
        <input id="settings-new-password" name="new" type="password" minlength="{PASSWORD_MIN_LENGTH}" maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="new-password" required>
      </div>
      <div class="form-field">
        <label for="settings-confirm-password">确认新密码</label>
        <input id="settings-confirm-password" name="confirm" type="password" minlength="{PASSWORD_MIN_LENGTH}" maxlength="{PASSWORD_MAX_LENGTH}" autocomplete="new-password" required>
      </div>
      <div class="row mt-md">
        <button class="btn btn-primary" type="submit">更新密码</button>
      </div>
    </form>
    <div class="small mt-sm faint">更新后将注销所有已登录会话（其它设备需重新登录），但本设备会保持登录。</div>
  </section>
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
    content = f'''<div class="admin-section">
  <div class="admin-section-header">
    <h2 class="admin-section-title">最近清零记录</h2>
    <div class="small">最近 {limit} 条 · 最新在上</div>
  </div>
  <div class="data-table-wrap" tabindex="0" aria-label="清零日志，可横向滚动">
    <table class="data-table">
      <thead>
        <tr><th>时间</th><th>操作人</th><th>IP</th><th>操作</th><th>目标</th><th>月份</th><th>流量变化</th></tr>
      </thead>
      <tbody>{table}</tbody>
    </table>
  </div>
</div>'''
    return render_admin_shell('logs', '清零日志', content, badge=host)


def _load_yaml_file(path):
    import yaml
    text = path.read_text(encoding='utf-8')
    return yaml.safe_load(text) or {}


def _dump_yaml(data):
    import yaml
    return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)


class TemplateConfigError(ValueError):
    """The operator template exists but cannot be safely interpreted."""


class TemplateConflictError(RuntimeError):
    """The template changed after an operator opened an edit form."""


def _template_bytes_unlocked():
    if not TEMPLATE_FILE.exists():
        return b''
    return TEMPLATE_FILE.read_bytes()


def _template_revision_unlocked():
    return hashlib.sha256(_template_bytes_unlocked()).hexdigest()


def _validate_template_revision_unlocked(expected_revision):
    expected = str(expected_revision or '').strip().lower()
    if not (
        re.fullmatch(r'[0-9a-f]{64}', expected)
        and hmac.compare_digest(_template_revision_unlocked(), expected)
    ):
        raise TemplateConflictError('template revision changed')


def load_template_config():
    """Load the subscription template as a dict. Returns {} if missing."""
    if not TEMPLATE_FILE.exists():
        return {}
    try:
        data = _load_yaml_file(TEMPLATE_FILE)
    except Exception as exc:
        raise TemplateConfigError('template YAML is invalid') from exc
    if not isinstance(data, dict):
        raise TemplateConfigError('template root must be a mapping')
    return data


def load_template_config_snapshot():
    """Return a config and revision captured under the template lock."""
    with template_lock():
        raw = _template_bytes_unlocked()
        try:
            import yaml
            data = yaml.safe_load(raw.decode('utf-8')) or {}
        except Exception as exc:
            raise TemplateConfigError('template YAML is invalid') from exc
        if not isinstance(data, dict):
            raise TemplateConfigError('template root must be a mapping')
        return data, hashlib.sha256(raw).hexdigest()


def save_template_config(data):
    """Save dict to the subscription template."""
    save_text_atomic(TEMPLATE_FILE, _dump_yaml(data))


def replace_template_config(data, expected_revision=None):
    with template_lock():
        if expected_revision is not None:
            _validate_template_revision_unlocked(expected_revision)
        save_template_config(data)


_CONFIG_FLASH = {
    'saved': '模板已保存，所有用户下次拉订阅将使用新配置',
    'invalid_json': 'JSON 格式错误，请检查语法',
    'empty': '配置内容不能为空',
    'load_failed': '加载配置文件失败',
    'save_failed': '保存失败，服务器未修改模板；你的草稿已保留，可稍后重试',
    'conflict': '模板已被其他操作更新，本次保存未覆盖新版本；你的草稿仍保留在下方，请复制后重新加载并合并',
    'schema_invalid': '模板结构无效：proxies 与 proxy-groups 必须包含具备名称和类型的对象，rules 必须包含可解析的规则字符串',
}


def validate_template_config(data):
    if not isinstance(data, dict):
        return False
    if any(
        key not in data
        for key in ('proxies', 'proxy-groups', 'rules')
    ):
        return False
    proxies = data.get('proxies', [])
    groups = data.get('proxy-groups', [])
    rules = data.get('rules', [])
    if not all(isinstance(items, list) for items in (proxies, groups, rules)):
        return False
    proxy_names = set()
    for proxy in proxies:
        if not isinstance(proxy, dict):
            return False
        name = proxy.get('name')
        proxy_type = proxy.get('type')
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(proxy_type, str)
            or not proxy_type.strip()
            or name in proxy_names
        ):
            return False
        proxy_names.add(name)
    group_names = set()
    for group in groups:
        if not isinstance(group, dict):
            return False
        name = group.get('name')
        group_type = group.get('type')
        if (
            not isinstance(name, str)
            or not name.strip()
            or not isinstance(group_type, str)
            or not group_type.strip()
            or name in group_names
        ):
            return False
        for member_key in ('proxies', 'use'):
            if member_key in group and (
                not isinstance(group[member_key], list)
                or any(
                    not isinstance(member, str) or not member.strip()
                    for member in group[member_key]
                )
            ):
                return False
        group_names.add(name)
    if any(not validate_clash_rule(rule) for rule in rules):
        return False
    try:
        import yaml
        round_trip = yaml.safe_load(_dump_yaml(data))
    except Exception:
        return False
    if not isinstance(round_trip, dict):
        return False
    return True


def validate_clash_rule(rule):
    if not isinstance(rule, str) or not rule or len(rule) > 2048:
        return False
    if any(ord(ch) < 32 for ch in rule):
        return False
    parts = [part.strip() for part in rule.split(',')]
    if len(parts) < 2 or not parts[0] or not parts[-1]:
        return False
    if parts[0] == 'MATCH':
        return len(parts) == 2
    return len(parts) >= 3 and bool(parts[1])


def render_config_editor(
    host,
    flash='',
    *,
    draft=None,
    expected_revision=None,
):
    alert = render_prefixed_alert(flash, _CONFIG_FLASH)
    load_failed = False
    editor_error_attrs = (
        ' aria-invalid="true" autofocus'
        if flash.startswith('err:')
        else ''
    )

    if draft is not None:
        config_json = str(draft)
        template_revision = str(expected_revision or '')
        if not re.fullmatch(r'[0-9a-f]{64}', template_revision):
            template_revision = ''
    else:
        try:
            data, template_revision = load_template_config_snapshot()
            config_json = json.dumps(data, ensure_ascii=False, indent=2)
        except Exception:
            load_failed = True
            with template_lock():
                template_revision = _template_revision_unlocked()
            try:
                config_json = TEMPLATE_FILE.read_text(encoding='utf-8')
            except (OSError, UnicodeError):
                config_json = ''
            if not flash:
                alert = render_alert(
                    '模板加载失败。为避免覆盖原配置，编辑与保存已锁定；'
                    '请修复文件后重新加载。',
                    'err',
                )

    locked_attrs = (
        ' readonly aria-readonly="true" data-load-failed="true"'
        if load_failed
        else ''
    )
    disabled_attrs = ' disabled aria-disabled="true"' if load_failed else ''
    recovery_actions = (
        '<a class="btn btn-secondary" href="/admin/config">重新加载模板</a>'
        if load_failed
        else (
            '<a class="btn btn-secondary" href="/admin/config">'
            '打开最新版本（请先复制草稿）</a>'
            if flash.removeprefix('err:') == 'conflict'
            else ''
        )
    )
    content = f'''{alert}
<div class="admin-page">
  <section class="form-section">
    <div class="form-section-title">模板说明与影响范围</div>
    <div class="form-section-desc">编辑 JSON 格式的订阅模板，校验通过后转换为 YAML 保存。这是整份替换，不会合并保留遗漏的原内容。</div>
    <ul class="template-impact"><li>影响后续订阅：用户更新订阅并应用后，客户端才会使用新配置。</li><li>不修改 Hysteria、Xray 等代理服务的运行配置，也不会重启代理服务。</li><li>每个用户的密码和 UUID 由服务端自动注入。覆盖前请自行保留原模板副本。</li></ul>
    <div class="small">模板文件：<code>{html.escape(str(TEMPLATE_FILE))}</code></div>
  </section>

  <section class="code-panel">
    <form method="post" action="/admin/config/save" id="configForm"
          data-confirm="将覆盖整份订阅模板，不会合并旧内容；影响用户后续订阅，不修改代理服务运行配置。确认保存？">
      <div class="code-panel-header">
        <div class="code-panel-title">模板 JSON</div>
        <div class="code-panel-actions">
          <button class="btn btn-ghost btn-sm" type="button" id="cfgFormat"{disabled_attrs}>格式化 JSON</button>
          <button class="btn btn-ghost btn-sm" type="button" id="cfgCollapse"{disabled_attrs}>折叠/展开</button>
        </div>
      </div>
      <div class="code-panel-body">
        <input type="hidden" name="template_revision" value="{html.escape(template_revision, quote=True)}">
        <div class="field-help" id="configEditorHelp">Tab 插入两个空格；按 Esc 后再按 Tab 可移出编辑器，Shift+Tab 可直接返回上一个控件。</div>
        <div id="jsonError" class="json-error" role="alert" aria-live="assertive"></div>
        <textarea name="config_json" id="configEditor" class="code-area code-tall" aria-label="订阅模板 JSON"
                  aria-describedby="configEditorHelp jsonError" spellcheck="false"{editor_error_attrs}{locked_attrs}>{html.escape(config_json)}</textarea>
        <div class="row mt-md gap-md">
          <button class="btn btn-primary" type="submit"{disabled_attrs}>保存订阅模板</button>
          {recovery_actions}
        </div>
      </div>
    </form>
  </section>
</div>
<script>
(function(){{
  var editor = document.getElementById('configEditor');
  var errorDiv = document.getElementById('jsonError');
  function showError(msg) {{ errorDiv.textContent=msg; errorDiv.classList.add('visible'); editor.classList.add('invalid'); editor.setAttribute('aria-invalid', 'true'); }}
  function clearError() {{ errorDiv.textContent=''; errorDiv.classList.remove('visible'); editor.classList.remove('invalid'); editor.removeAttribute('aria-invalid'); }}
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
  var allowFocusExit = false;
  editor.addEventListener('keydown', function(e) {{
    if (e.key === 'Escape') {{ allowFocusExit = true; return; }}
    if (e.key !== 'Tab') {{ allowFocusExit = false; return; }}
    if (e.shiftKey || allowFocusExit) {{ allowFocusExit = false; return; }}
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
    if (!validateJson()) {{ e.preventDefault(); editor.focus(); }}
  }});
}})();
</script>'''
    return render_admin_shell('config', '订阅模板配置', content, badge=host)


def load_template_rules():
    """Load rules list from the subscription template."""
    if not TEMPLATE_FILE.exists():
        return []
    data = load_template_config()
    rules = data.get('rules', [])
    if not isinstance(rules, list) or any(
        not isinstance(rule, str) for rule in rules
    ):
        raise TemplateConfigError('template rules must be a string list')
    return rules


def load_template_rules_snapshot():
    data, revision = load_template_config_snapshot()
    rules = data.get('rules', [])
    if not isinstance(rules, list) or any(
        not isinstance(rule, str) for rule in rules
    ):
        raise TemplateConfigError('template rules must be a string list')
    return rules, revision


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
    new_rule_lines = [
        '# 6. 规则',
        'rules:' if rules else 'rules: []',
    ]
    for r in rules:
        # JSON strings are valid YAML scalars and safely escape quotes,
        # backslashes and control characters without changing the rule value.
        new_rule_lines.append(f'  - {json.dumps(str(r), ensure_ascii=False)}')
    if start is None:
        result = lines + [''] + new_rule_lines
    else:
        cut = start - 1 if start > 0 and lines[start - 1].startswith('#') else start
        result = lines[:cut] + new_rule_lines + lines[end:]
    rendered = '\n'.join(result) + ('\n' if not result[-1].endswith('\n') else '')
    import yaml
    parsed = yaml.safe_load(rendered)
    if not isinstance(parsed, dict) or not isinstance(parsed.get('rules'), list):
        raise ValueError('rendered rules are not valid YAML')
    save_text_atomic(TEMPLATE_FILE, rendered)


def add_template_rule(rule_str, expected_revision=None):
    with template_lock():
        if expected_revision is not None:
            _validate_template_revision_unlocked(expected_revision)
        rules = load_template_rules()
        rules.insert(0, rule_str)
        save_template_rules(rules)


def delete_template_rule(index, expected_revision=None, expected_rule=None):
    with template_lock():
        if expected_revision is not None:
            _validate_template_revision_unlocked(expected_revision)
        rules = load_template_rules()
        if index < 0 or index >= len(rules):
            return False
        if expected_rule is not None and not hmac.compare_digest(
            rules[index].encode('utf-8'),
            str(expected_rule).encode('utf-8'),
        ):
            raise TemplateConflictError('rule changed at requested index')
        rules.pop(index)
        save_template_rules(rules)
        return True


def replace_template_rules(rules, expected_revision=None):
    with template_lock():
        if expected_revision is not None:
            _validate_template_revision_unlocked(expected_revision)
        save_template_rules(rules)


def apply_rule_pack_to_template(pack_key, expected_revision=None):
    with template_lock():
        if expected_revision is not None:
            _validate_template_revision_unlocked(expected_revision)
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


def _json_request(handler):
    """True when the client explicitly wants JSON (Accept header or _json=1 query)."""
    parsed = urlparse(handler.path)
    if parse_qs(parsed.query, keep_blank_values=True).get('_json'):
        return True
    accept = handler.headers.get('Accept') or ''
    return 'application/json' in accept.lower()


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


def without_admin_bearer(raw_target):
    """Remove legacy admin bearer parameters before entering the UI."""
    parsed = urlparse(str(raw_target or ''))
    if parsed.path != '/admin' and not parsed.path.startswith('/admin/'):
        return '/admin'
    pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != 'token'
    ]
    query = urlencode(pairs)
    return f'{parsed.path}?{query}' if query else parsed.path


def is_admin_ui_document(path):
    """Return true only for full-page admin routes, never data/fragment APIs."""
    if path.startswith('/admin/user/') and not path.endswith('.json'):
        return True
    return path in {
        '/admin',
        '/admin/codex',
        '/admin/config',
        '/admin/daily',
        '/admin/health',
        '/admin/incidents',
        '/admin/logs',
        '/admin/rules',
        '/admin/settings',
        '/admin/usage',
    }


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
    'invalid_pattern': '匹配值不能包含逗号、换行或控制字符',
    'invalid_rule_schema': '规则格式无效；请使用 TYPE,匹配值,动作（MATCH 规则使用 MATCH,动作）',
    'invalid_action': '无效的规则动作',
    'invalid_extra': '无效的附加选项',
    'load_failed': '模板当前不可解析，规则修改未执行',
    'conflict': '规则已被其他页面更新，本次操作未执行；请查看最新规则后重试',
}


def render_rules(
    host,
    flash='',
    *,
    raw_draft=None,
    expected_revision=None,
):
    try:
        rules, template_revision = load_template_rules_snapshot()
    except (TemplateConfigError, OSError, UnicodeError):
        content = f'''{render_alert(
            '模板加载失败。为避免误删或覆盖，所有规则修改已锁定；'
            '请修复模板后重试。',
            'err',
        )}
<div class="card">
  <h2 class="section-title mb-sm">路由规则暂不可编辑</h2>
  <p class="small mb-md">当前文件未被修改。可前往模板页查看保留的原始内容，或修复文件后重新加载。</p>
  <div class="row">
    <a class="btn secondary" href="/admin/rules">重新加载规则</a>
    <a class="btn ghost" href="/admin/config">查看模板恢复页</a>
  </div>
</div>'''
        return render_admin_shell(
            'rules',
            '订阅路由规则',
            content,
            badge='不可用',
        )
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
                f'<input type="hidden" name="expected_rule" value="{html.escape(rule_str, quote=True)}">'
                f'<input type="hidden" name="template_revision" value="{template_revision}">'
                f'<button class="btn btn-danger btn-sm" type="submit">删除</button>'
                f'</form>'
            )
        tr_class = ' class="system-row"' if is_system else ''
        rows += (
            f'<tr{tr_class}><td>{i + 1}</td><td>{html.escape(type_label)}</td>'
            f'<td class="break">{html.escape(val)}</td>'
            f'<td>{html.escape(action_label)}{extra_tag}</td>'
            f'<td>{del_btn}</td></tr>'
        )

    rules_text = html.escape(
        str(raw_draft)
        if raw_draft is not None
        else '\n'.join(rules)
    )
    submitted_revision = str(expected_revision or template_revision)
    if not re.fullmatch(r'[0-9a-f]{64}', submitted_revision):
        submitted_revision = ''
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
<div class="admin-page">

  <!-- Page intro -->
  <div class="rules-intro">
    <div class="rules-intro-note">自定义规则优先级高于规则集，从上到下依次匹配。灰色行为内置规则集，不可删除。</div>
  </div>

  <!-- Layer 1: 操作区 — 双栏 -->
  <div class="rules-ops-grid">

    <!-- 规则包 -->
    <div class="op-panel">
      <div class="op-panel-title">规则包</div>
      <div class="op-panel-desc">应用规则包会修改所选范围的路由配置。全局模板影响所有用户；单个用户会写入 users.json 的个人 Clash 覆盖项。</div>
      <form method="post" action="/admin/rule-pack/apply" class="op-form"
            data-confirm="应用规则包会修改所选范围的路由配置，确认继续？">
        <input type="hidden" name="template_revision" value="{template_revision}">
        <div class="op-form-grid">
          <div class="field">
            <label for="rule-pack">规则包</label>
            <select id="rule-pack" name="pack" class="select">{pack_options}</select>
          </div>
          <div class="field">
            <label for="rule-pack-scope">应用范围</label>
            <select id="rule-pack-scope" name="scope" class="select">
              <option value="global">全局模板</option>
              <option value="user">单个用户</option>
            </select>
          </div>
          <div class="field">
            <label for="rule-pack-user">用户（选择"单个用户"时生效）</label>
            <select id="rule-pack-user" name="user" class="select" disabled>
              <option value="">选择用户</option>{user_options}
            </select>
          </div>
        </div>
        <div class="op-form-footer">
          <span class="op-footer-hint">应用后将更新所选范围</span>
          <button class="btn btn-secondary" type="submit">应用规则包</button>
        </div>
      </form>
    </div>

    <!-- 添加自定义规则 -->
    <div class="op-panel">
      <div class="op-panel-title">添加自定义规则</div>
      <form method="post" action="/admin/rules/add" class="op-form">
        <input type="hidden" name="template_revision" value="{template_revision}">
        <div class="op-form-grid">
          <div class="field">
            <label for="new-rule-type">规则类型</label>
            <select id="new-rule-type" name="rule_type" class="select">
              <option value="DOMAIN-SUFFIX">DOMAIN-SUFFIX（域名后缀）</option>
              <option value="DOMAIN-KEYWORD">DOMAIN-KEYWORD（域名关键词）</option>
              <option value="DOMAIN">DOMAIN（完整域名）</option>
              <option value="IP-CIDR">IP-CIDR（IP 段）</option>
            </select>
          </div>
          <div class="field">
            <label for="new-rule-pattern">匹配值</label>
            <input id="new-rule-pattern" name="pattern" required class="input" placeholder="example.com 或 10.0.0.0/8">
          </div>
          <div class="field">
            <label for="new-rule-action">动作</label>
            <select id="new-rule-action" name="action" class="select">
              <option value="DIRECT">直连 (DIRECT)</option>
              <option value="🚀 节点选择">代理 (🚀 节点选择)</option>
              <option value="REJECT">拦截 (REJECT)</option>
            </select>
          </div>
          <div class="field">
            <label for="new-rule-extra">附加选项</label>
            <select id="new-rule-extra" name="extra" class="select">
              <option value="">无</option>
              <option value="no-resolve">no-resolve（IP 规则跳过 DNS 解析）</option>
            </select>
          </div>
        </div>
        <div class="op-form-footer">
          <span class="op-footer-hint">将插入规则列表最前</span>
          <button class="btn btn-primary" type="submit">+ 添加规则</button>
        </div>
      </form>
    </div>

  </div><!-- /.rules-ops-grid -->

  <!-- Layer 2: 直接编辑全部规则 — 默认折叠 -->
  <details class="rules-raw-editor">
    <summary class="rules-raw-summary">
      <span>直接编辑全部规则</span>
      <span class="rules-raw-badge">高级操作</span>
    </summary>
    <div class="rules-raw-body">
      <div class="rules-raw-help">每行一条规则，格式：<code>TYPE,匹配值,动作</code>。保存后同步到所有订阅模板。</div>
      <form method="post" action="/admin/rules/raw" class="op-form"
            data-confirm="保存会替换全部共享路由规则，并影响所有用户订阅，确认继续？">
        <input type="hidden" name="template_revision" value="{html.escape(submitted_revision, quote=True)}">
        <div class="field">
          <label for="rules-raw" class="sr-only">全部路由规则</label>
          <textarea id="rules-raw" name="rules_raw" class="rules-raw-textarea">{rules_text}</textarea>
        </div>
        <div class="op-form-footer">
          <button class="btn btn-danger" type="submit">覆盖全部规则</button>
        </div>
      </form>
    </div>
  </details>

  <!-- Layer 3: 当前规则列表 — 结果查看区 -->
  <section class="admin-section">
    <div class="admin-section-header">
      <h2 class="admin-section-title">当前规则列表</h2>
      <div class="small">{len(rules)} 条</div>
    </div>
    <div class="data-table-wrap" tabindex="0" aria-label="路由规则，可横向滚动">
      <table class="data-table">
        <thead>
          <tr><th style="width:50px;">#</th><th>类型</th><th>匹配</th><th>动作</th><th style="width:90px;">操作</th></tr>
        </thead>
        <tbody>{rows or '<tr><td colspan="5" class="empty">暂无规则</td></tr>'}</tbody>
      </table>
    </div>
  </section>

</div><!-- /.admin-page -->
<script>
var rulePackScope = document.getElementById('rule-pack-scope');
var rulePackUser = document.getElementById('rule-pack-user');
function syncRulePackUser() {{
  if (!rulePackScope || !rulePackUser) return;
  var needsUser = rulePackScope.value === 'user';
  rulePackUser.disabled = !needsUser;
  rulePackUser.required = needsUser;
  if (!needsUser) rulePackUser.value = '';
}}
if (rulePackScope) rulePackScope.addEventListener('change', syncRulePackUser);
syncRulePackUser();
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


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Thread-per-request server with explicit process-level backpressure."""

    daemon_threads = True
    block_on_close = True
    allow_reuse_address = True
    request_queue_size = SERVER_REQUEST_QUEUE

    def __init__(
        self,
        server_address,
        request_handler_class,
        *,
        max_workers=SERVER_MAX_WORKERS,
        bind_and_activate=True,
    ):
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > 256
        ):
            raise ValueError('max_workers must be between 1 and 256')
        self.max_workers = max_workers
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(
            server_address,
            request_handler_class,
            bind_and_activate=bind_and_activate,
        )

    @staticmethod
    def _reject_over_capacity(request):
        body = b'Service temporarily busy; retry shortly.\n'
        response = (
            b'HTTP/1.1 503 Service Unavailable\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            + f'Content-Length: {len(body)}\r\n'.encode('ascii')
            + b'Retry-After: 5\r\n'
            b'Cache-Control: no-store\r\n'
            b'X-Content-Type-Options: nosniff\r\n'
            b'Connection: close\r\n'
            b'\r\n'
            + body
        )
        try:
            request.settimeout(0.5)
            request.sendall(response)
        except OSError:
            pass
        finally:
            try:
                request.shutdown(2)
            except OSError:
                pass
            request.close()

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            self._reject_over_capacity(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'hy2-panel'
    sys_version = ''

    def log_message(self, fmt, *args):
        return

    def parse_form(self):
        return http_utils.parse_form(self, max_bytes=MAX_FORM_BYTES)

    def _send_security_headers(self):
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Cross-Origin-Opener-Policy', 'same-origin')
        self.send_header(
            'Permissions-Policy',
            'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
        )
        self.send_header(
            'Content-Security-Policy',
            "default-src 'self'; base-uri 'none'; object-src 'none'; "
            "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'",
        )

    def send_response_body(self, code, body, ctype='text/plain; charset=utf-8', send_body=True, extra_headers=None):
        data = body.encode('utf-8')
        extra_headers = extra_headers or {}
        for key, value in extra_headers.items():
            if '\r' in str(key) or '\n' in str(key) or '\r' in str(value) or '\n' in str(value):
                raise ValueError('invalid response header')
        extra_names = {str(k).lower() for k in extra_headers}
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self._send_security_headers()
        if 'cache-control' not in extra_names:
            self.send_header('Cache-Control', 'no-store')
        for k, v in extra_headers.items():
            self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(data)

    def _serve_static(self, payload_bytes, etag, ctype, send_payload,
                       cache_control='public, max-age=86400'):
        """Serve a cacheable static asset with ETag-aware 304 handling."""
        if _etag_matches(self.headers.get('If-None-Match'), etag):
            self.send_response(304)
            self._send_security_headers()
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', cache_control)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(payload_bytes)))
        self._send_security_headers()
        self.send_header('Cache-Control', cache_control)
        self.send_header('ETag', etag)
        self.end_headers()
        if send_payload:
            self.wfile.write(payload_bytes)

    def redirect(self, to, cookie=None, status=302):
        if '\r' in str(to) or '\n' in str(to):
            to = '/'
        self.send_response(status)
        self.send_header('Location', to)
        self.send_header('Content-Length', '0')
        self._send_security_headers()
        self.send_header('Cache-Control', 'no-store')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()

    def send_user_state_conflict(self, next_to='/admin', *, draft=None):
        target = safe_admin_next(next_to)
        host = configured_public_host(
            self.headers.get('Host', '127.0.0.1'),
        )
        draft_html = ''
        if isinstance(draft, dict):
            labels = (
                ('user', '用户'),
                ('max_devices', '设备数上限'),
                ('quota_gb', '基础流量（GB）'),
                ('quota_extra_gb', '加量包（GB）'),
                ('expires_at', '到期日'),
                ('note', '备注'),
                ('guest', '按量用户'),
                ('tuic_enabled', '允许 TUIC'),
            )
            items = ''.join(
                '<dt class="small faint">'
                f'{html.escape(label)}</dt><dd class="break">'
                f'{html.escape(str(draft.get(key, "")))}</dd>'
                for key, label in labels
            )
            draft_html = (
                '<div class="card mt-md">'
                '<h2 class="section-title mb-sm">未保存的非敏感草稿</h2>'
                '<p class="small mb-md">可先复制这些值，再刷新并合并。'
                '出于安全考虑，密码字段不会回显；如有填写请重新输入。</p>'
                f'<dl class="draft-summary">{items}</dl></div>'
            )
        content = (
            '<div class="card">'
            '<div class="err" role="alert">'
            '这名用户在页面打开后已被其他操作修改。'
            '为避免覆盖新状态，本次操作没有执行；请刷新后确认最新信息。'
            '</div><div class="row mt-md">'
            f'<a class="btn" href="{html.escape(target, quote=True)}">'
            '刷新并返回</a></div></div>'
            f'{draft_html}'
        )
        self.send_response_body(
            409,
            render_admin_shell(
                'dashboard',
                '用户状态已变化',
                content,
                badge=host,
            ),
            'text/html; charset=utf-8',
        )

    def _send_mutation_json(self, status, payload):
        """JSON response shared by the AJAX admin mutation endpoints.

        Only reached when the client explicitly asked for JSON; plain form
        POSTs keep their original flash/redirect behaviour."""
        self.send_response_body(
            status,
            json.dumps(payload, ensure_ascii=False),
            'application/json; charset=utf-8',
        )

    def _mutation_unauthorized(self):
        """Reject an unauthenticated mutation: JSON 401 or login redirect."""
        if _json_request(self):
            self._send_mutation_json(
                401, {'ok': False, 'reason': 'login_required'},
            )
        else:
            self.redirect('/login')

    def _mutation_user_not_found(self, username, next_to):
        if _json_request(self):
            self._send_mutation_json(
                404,
                {'ok': False, 'reason': 'user_not_found',
                 'username': username},
            )
        else:
            self.redirect(with_flash(next_to, 'user not found'))

    def _mutation_conflict(self, username, next_to):
        if _json_request(self):
            self._send_mutation_json(
                409,
                {'ok': False, 'reason': 'conflict', 'username': username},
            )
        else:
            self.send_user_state_conflict(next_to)

    def _send_toggle_json(self, status, username, reason, next_to):
        """Send a toggle-user result as JSON or redirect based on Accept header."""
        if _json_request(self):
            body = {
                'ok': status == 200,
                'username': username,
                'reason': reason,
                'desired': reason if reason in ('disabled', 'enabled') else None,
            }
            if status == 200:
                # Success carries the fresh row so the client patches the
                # table directly instead of re-fetching /admin/overview.json,
                # plus the current reload-pending markers so the UI can show
                # whether the change has reached the proxies yet.
                body['user'] = _build_overview_user(username, now=local_now())
                body['reload'] = _static_reload_status()
            self.send_response_body(
                status,
                json.dumps(body, ensure_ascii=False),
                'application/json; charset=utf-8',
            )
        else:
            # Fall back to original flash-redirect behaviour for non-JSON clients
            if status == 200:
                msg = f'{reason} {username}'
                self.redirect(with_flash(next_to, msg))
            elif status == 404:
                self.redirect(with_flash(next_to, 'user not found'))
            elif status == 409:
                self.send_user_state_conflict(next_to)
            else:
                self.redirect(with_flash(next_to, f'error {status}'))

    def get_admin_actor(self):
        q = parse_query_params(self.path)
        token = (q.get('token') or [''])[0]
        meta = load_meta()
        admin_token = str(meta.get('admin_token') or '')
        if _safe_secret_equal(token, admin_token):
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
            'ip': http_utils.request_client_ip(self),
            'action': action,
            'target': target,
            'month': month_key(),
            'before': before,
            'after': after,
        }
        try:
            RESET_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with RESET_LOG_FILE.open('a', encoding='utf-8') as f:
                os.fchmod(f.fileno(), 0o600)
                f.write(json.dumps(line, ensure_ascii=True) + '\n')
                f.flush()
                os.fsync(f.fileno())
        except OSError as exc:
            # The authorization/accounting mutation has already committed.
            # Do not misreport it as failed and invite a destructive retry.
            print(
                f'CRITICAL: audit log append failed: {exc}',
                file=sys.stderr,
            )

    def handle_get(self, send_payload=True):
        parsed = urlparse(self.path)
        path = parsed.path
        q = parse_query_params(self.path)

        if path == '/healthz':
            # Process liveness is not sufficient readiness: the panel must be
            # able to read every core state file it needs without falling back
            # to fabricated defaults.
            load_meta()
            load_json(USERS_FILE, {}, required=True)
            load_json(USAGE_FILE, {}, required=True)
            load_json(USAGE_DAILY_FILE, {}, required=True)
            self.send_response_body(
                204, '', 'text/plain; charset=utf-8', send_payload,
            )
            return

        host = configured_public_host(
            self.headers.get('Host', '127.0.0.1'),
        )
        base_url = safe_base_url(
            host,
            self.headers.get('X-Forwarded-Proto', 'http'),
            self.headers.get('X-Forwarded-Port', ''),
        )

        if (
            send_payload
            and is_admin_ui_document(path)
        ):
            supplied_admin_token = (q.get('token') or [''])[0]
            if supplied_admin_token:
                meta = load_meta()
                expected_admin_token = str(
                    meta.get('admin_token') or ''
                )
                if _safe_secret_equal(
                    supplied_admin_token,
                    expected_admin_token,
                ):
                    generation = _credential_generation(
                        meta.get('admin_pass_hash'),
                    )
                    if not generation:
                        self.send_response_body(
                            503,
                            '管理员会话状态暂不可用，请稍后重试。',
                        )
                        return
                    try:
                        sid = create_session('admin', generation)
                    except (state_store.StateStoreError, OSError):
                        self.send_response_body(
                            503,
                            '管理员会话暂时无法保存，请稍后重试。',
                        )
                        return
                    self.redirect(
                        without_admin_bearer(self.path),
                        cookie=session_cookie(
                            sid,
                            secure=is_secure_request(self),
                        ),
                        status=303,
                    )
                    return

        if path == '/static/style.css':
            self._serve_static(
                BASE_CSS_BYTES, BASE_CSS_ETAG, 'text/css; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, BASE_CSS_ETAG),
            )
            return

        if path == '/static/admin-poll.js':
            self._serve_static(ADMIN_POLL_JS_BYTES, ADMIN_POLL_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload,
                               cache_control=_static_asset_cache_control(q, ADMIN_POLL_JS_ETAG))
            return

        if path == '/static/usage.js':
            self._serve_static(USAGE_JS_BYTES, USAGE_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload,
                               cache_control=_static_asset_cache_control(q, USAGE_JS_ETAG))
            return

        if path == '/static/codex-quota.js':
            self._serve_static(CODEX_QUOTA_JS_BYTES, CODEX_QUOTA_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload,
                               cache_control=_static_asset_cache_control(q, CODEX_QUOTA_JS_ETAG))
            return

        if path == '/static/home.js':
            self._serve_static(HOME_JS_BYTES, HOME_JS_ETAG,
                               'application/javascript; charset=utf-8', send_payload,
                               cache_control=_static_asset_cache_control(q, HOME_JS_ETAG))
            return

        font_entry = STATIC_FONT_FILES.get(path)
        if font_entry is not None:
            font_bytes, font_ctype = font_entry
            self._serve_static(
                font_bytes,
                '"' + hashlib.sha1(font_bytes).hexdigest()[:16] + '"',
                font_ctype,
                send_payload,
                cache_control='public, max-age=31536000, immutable',
            )
            return

        if path == '/':
            self.send_response_body(200, render_home(host), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/login':
            self.send_response_body(200, render_login(host), 'text/html; charset=utf-8', send_payload)
            return

        if path == '/user/login':
            # Unified login - redirect to /login
            self.redirect('/login', status=303)
            return

        if path == '/logout':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            self.send_response_body(
                200, render_logout_confirmation(host),
                'text/html; charset=utf-8', send_payload,
            )
            return

        if path == '/user/logout':
            if not get_logged_in_user(self):
                self.redirect('/login')
                return
            self.send_response_body(
                200, render_logout_confirmation(host, user_panel=True),
                'text/html; charset=utf-8', send_payload,
            )
            return

        if path == '/user/change-password':
            user, session_kind = get_logged_in_user_context(self)
            if (
                not user
                or session_kind != USER_SESSION_PANEL_PASSWORD
            ):
                self.redirect('/login')
                return
            cfg = load_json(USERS_FILE, {}).get(user)
            if not isinstance(cfg, dict):
                self.redirect('/login', cookie=clear_user_session_cookie(secure=is_secure_request(self)))
                return
            access_error = user_panel_access_error(
                cfg, session_kind, today=local_now().date(),
            )
            if access_error in ('disabled', 'expired'):
                self.redirect('/user/panel')
                return
            msg = (q.get('msg') or [''])[0]
            self.send_response_body(
                200, render_user_change_password(host, user, msg=msg),
                'text/html; charset=utf-8', send_payload,
            )
            return

        if path == '/user/panel.json':
            user, session_kind = get_logged_in_user_context(self)
            if not user:
                self.send_response_body(401, '{"error":"login_required"}',
                                        'application/json; charset=utf-8', send_payload)
                return
            cfg = load_json(USERS_FILE, {}).get(user)
            access_error = user_panel_access_error(
                cfg, session_kind, today=local_now().date(),
            )
            if access_error:
                self.send_response_body(
                    403,
                    json.dumps(
                        {'error': access_error},
                        ensure_ascii=True,
                        separators=(',', ':'),
                    ),
                    'application/json; charset=utf-8',
                    send_payload,
                )
                return
            payload = _build_panel_json_payload(user, cfg, now=local_now())
            self.send_response_body(200, json.dumps(payload),
                                    'application/json; charset=utf-8', send_payload)
            return

        if path == '/user/panel':
            user, session_kind = get_logged_in_user_context(self)
            if not user:
                self.send_response_body(
                    403,
                    render_panel_link_required(),
                    'text/html; charset=utf-8',
                    send_payload,
                )
                return
            cfg = load_json(USERS_FILE, {}).get(user)
            if not isinstance(cfg, dict):
                self.redirect('/login', cookie=clear_user_session_cookie(secure=is_secure_request(self)))
                return
            access_error = user_panel_access_error(
                cfg, session_kind, today=local_now().date(),
            )
            if access_error == 'password_change_required':
                self.redirect('/user/change-password')
                return
            # Disabled/expired users receive a helpful status-only page with an
            # authorization status.  Never pass their bearer into the renderer:
            # this makes the no-secret property explicit even if the template is
            # extended later.
            inactive = access_error in ('disabled', 'expired')
            token = '' if inactive else str(cfg.get('sub_token') or '')
            self.send_response_body(
                403 if inactive else 200,
                render_user_panel(
                    host,
                    base_url,
                    user,
                    token,
                    cfg,
                    session_auth=True,
                    session_kind=session_kind,
                    notice=(q.get('msg') or [''])[0],
                ),
                'text/html; charset=utf-8', send_payload,
            )
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
            filename = f'{user}.yaml' if profile == 'default' else f'{user}-{profile}.yaml'
            self.send_response_body(
                200, yml, 'text/yaml; charset=utf-8', send_payload,
                extra_headers={
                    'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
                    'x-subscription-profile': profile,
                    'x-subscription-generated-at': generated_at,
                    'x-subscription-template-mtime': template_mtime,
                    'profile-update-interval': '24',
                    'subscription-userinfo': (
                        f'upload={tx}; download={rx}; total={total}; expire=0'
                    ),
                    'x-usage-total-bytes': str(used),
                },
            )
            return

        if path.startswith('/panel/') and path.endswith('/qr.svg'):
            user = path[len('/panel/'):-len('/qr.svg')]
            token = (q.get('token') or [''])[0]
            cfg = check_user_token(user, token)
            if not cfg:
                self.send_response_body(403, '无权限访问', send_body=send_payload)
                return
            if cfg.get('disabled'):
                self.send_response_body(403, '账号已停用', send_body=send_payload)
                return
            if user_compat.is_expired(cfg, today=local_now().date()):
                self.send_response_body(403, '账号已到期', send_body=send_payload)
                return
            profile = normalize_subscription_profile((q.get('profile') or ['default'])[0])
            svg = render_profile_qr_svg(base_url, user, token, profile)
            if not svg:
                self.send_response_body(503, '二维码暂不可用', send_body=send_payload)
                return
            self.send_response_body(
                200, svg, 'image/svg+xml; charset=utf-8', send_payload,
                extra_headers={'Cache-Control': 'private, no-store'},
            )
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
            if cfg.get('disabled'):
                self.send_response_body(403, '账号已停用，请联系管理员', send_body=send_payload)
                return
            if user_compat.is_expired(cfg, today=local_now().date()):
                self.send_response_body(403, '账号已到期，请联系管理员续费', send_body=send_payload)
                return
            if send_payload:
                sid = create_user_session(
                    user,
                    _credential_generation(
                        str(cfg.get('sub_token') or ''),
                    ),
                    USER_SESSION_SUBSCRIPTION_TOKEN,
                )
                self.redirect(
                    '/user/panel',
                    cookie=user_session_cookie(
                        sid,
                        secure=is_secure_request(self),
                    ),
                    status=303,
                )
                return
            self.send_response_body(
                200,
                render_user_panel(host, base_url, user, token, cfg),
                'text/html; charset=utf-8',
                send_payload,
            )
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

        if path == '/admin/codex':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            self.send_response_body(
                200, render_codex_page(host),
                'text/html; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/codex.json':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"error":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            range_key = (q.get('range') or ['day'])[0]
            payload = codex_quota.build_dashboard_payload(range_key=range_key)
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
                extra_headers={'Cache-Control': 'no-store'},
            )
            return

        if path == '/admin/overview.json':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"error":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            payload = _build_overview_json_payload(now=local_now())
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/reload-status.json':
            # Read-only view of the existing Xray/TUIC reload-pending markers.
            # It never writes state, never schedules a reload and never
            # regenerates config — it only reports the durable markers.
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"ok":false,"reason":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            payload = {'ok': True}
            payload.update(_static_reload_status())
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
                extra_headers={'Cache-Control': 'no-store'},
            )
            return

        if path == '/admin/hysteria-update/status.json':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"ok":false,"reason":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                    extra_headers={'Cache-Control': 'no-store'},
                )
                return
            payload = hysteria_update.public_status()
            self.send_response_body(
                200,
                json.dumps(
                    payload, ensure_ascii=False, separators=(',', ':'),
                ),
                'application/json; charset=utf-8', send_payload,
                extra_headers={'Cache-Control': 'no-store'},
            )
            return

        if path == '/admin/analytics.json':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"error":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            summary_only = (q.get('summary') or ['0'])[0].lower() in ('1', 'true', 'yes')
            payload = _build_analytics_json_payload(
                now=local_now(), include_charts=not summary_only,
            )
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/usage-history':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '<div class="err" role="alert">登录已失效，请重新登录</div>',
                    'text/html; charset=utf-8', send_payload,
                )
                return
            self.send_response_body(
                200, _render_daily_table_collapsed(host),
                'text/html; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/usage.json':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"error":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            payload = _build_usage_json_payload(now=local_now())
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/usage.csv':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            window = (q.get('window') or ['cycle'])[0]
            if window not in ('cycle', '30d'):
                self.send_response_body(400, '无效的导出时间范围', send_body=send_payload)
                return
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
                content = (
                    '<div class="card">'
                    '<div class="err" role="alert">找不到该用户，可能已被删除或链接已过期。</div>'
                    '<div class="row mt-md">'
                    f'{back_to_admin("返回用户列表")}'
                    '</div></div>'
                )
                self.send_response_body(
                    404,
                    render_admin_shell(
                        'dashboard',
                        '用户不存在',
                        content,
                        badge=host,
                    ),
                    'text/html; charset=utf-8',
                    send_payload,
                )
                return
            self.send_response_body(200, out,
                                    'text/html; charset=utf-8', send_payload)
            return

        if path.startswith('/admin/user/') and path.endswith('.json'):
            if not is_logged_in(self):
                self.send_response_body(
                    401, '{"error":"login_required"}',
                    'application/json; charset=utf-8', send_payload,
                )
                return
            uid = path[len('/admin/user/'):-len('.json')]
            summary_only = (q.get('summary') or ['0'])[0].lower() in ('1', 'true', 'yes')
            payload = _build_user_json_payload(
                uid, now=local_now(), include_charts=not summary_only,
            )
            if payload is None:
                self.send_response_body(404, '{"error":"not found"}',
                                        'application/json; charset=utf-8', send_payload)
                return
            self.send_response_body(
                200, json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'application/json; charset=utf-8', send_payload,
            )
            return

        if path == '/admin/daily':
            _handle_legacy_daily_redirect(self)
            return

        if path == '/admin/health.fragment':
            if not is_logged_in(self):
                self.send_response_body(
                    401, '<div class="err" role="alert">登录已失效</div>',
                    'text/html; charset=utf-8', send_payload,
                )
                return
            if q.get('snapshot') == ['1']:
                snapshot = {
                    'rows': render_health_fragment(),
                    'kpis': ''.join(
                        _health_top_kpi_card(title, result, is_text=(title == '整体状态'))
                        for title, result in _render_health_top_kpis().items()
                    ),
                    'update': hysteria_update.render_history(),
                }
                self.send_response_body(200, json.dumps(snapshot), 'application/json; charset=utf-8', send_payload)
                return
            self.send_response_body(
                200, render_health_fragment(),
                'text/html; charset=utf-8', send_payload,
            )
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

        if path == '/admin/landing-egresses':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            flash = (q.get('msg') or [''])[0]
            self.send_response_body(
                200, render_landing_egresses(host, flash=flash),
                'text/html; charset=utf-8', send_payload,
            )
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

    @request_multiplier_snapshot
    def do_GET(self):
        try:
            self.handle_get(send_payload=True)
        except (state_store.StateStoreError, OSError) as exc:
            stopped = _state_failure_requires_static_stop(exc)
            stop_outcomes = {}
            if stopped:
                stop_outcomes = _fail_closed_static_access(exc)
            self.send_response_body(
                503,
                (
                    (
                        '核心授权状态暂不可用；已确认静态代理暂停'
                        if _static_stop_confirmed(stop_outcomes)
                        else
                        '核心授权状态暂不可用；静态代理停止状态未完全确认，'
                        '系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8', True,
            )

    @request_multiplier_snapshot
    def do_HEAD(self):
        try:
            self.handle_get(send_payload=False)
        except (state_store.StateStoreError, OSError) as exc:
            stopped = _state_failure_requires_static_stop(exc)
            stop_outcomes = {}
            if stopped:
                stop_outcomes = _fail_closed_static_access(exc)
            self.send_response_body(
                503,
                (
                    (
                        '核心授权状态暂不可用；已确认静态代理暂停'
                        if _static_stop_confirmed(stop_outcomes)
                        else
                        '核心授权状态暂不可用；静态代理停止状态未完全确认，'
                        '系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8', False,
            )

    @request_multiplier_snapshot
    def do_POST(self):
        try:
            self._do_POST()
        except (state_store.StateStoreError, OSError) as exc:
            path = urlparse(self.path).path
            stopped = _state_failure_requires_static_stop(
                exc, post_path=path,
            )
            stop_outcomes = {}
            if stopped:
                stop_outcomes = _fail_closed_static_access(exc)
            self.send_response_body(
                503,
                (
                    (
                        '授权变更未能安全完成；为避免数据覆盖，'
                        '已确认静态代理暂停'
                        if _static_stop_confirmed(stop_outcomes)
                        else
                        '授权变更未能安全完成；为避免数据覆盖，'
                        '静态代理停止状态未完全确认，系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8', True,
            )

    def _do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
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
        meta = load_meta()
        request_user_revision = (
            form.get('user_revision')
            or query.get('revision')
            or ['']
        )[0]

        if path == '/logout':
            sid = parse_cookies(self).get('sid', '')
            delete_session(sid)
            self.redirect(
                '/login',
                cookie=clear_session_cookie(secure=is_secure_request(self)),
                status=303,
            )
            return

        if path == '/user/logout':
            sid = parse_cookies(self).get('usid', '')
            delete_user_session(sid)
            self.redirect(
                '/login',
                cookie=clear_user_session_cookie(secure=is_secure_request(self)),
                status=303,
            )
            return

        if path.startswith('/panel/') and path.endswith('/rotate-token'):
            # A short-lived recovery receipt binds this mutation to the clean
            # panel's browser session and idempotency key. It is prepared
            # before users.json changes, so a lost response can safely replay
            # the same credential without placing it in a URL or log.
            user = path[len('/panel/'):-len('/rotate-token')]
            posted = (form.get('token') or [''])[0]
            request_id = (form.get('rotation_id') or [''])[0]
            original_sid = parse_cookies(self).get('usid', '')
            rotation = _recoverable_user_rotation(
                user,
                posted,
                request_id=request_id,
                session_id=original_sid,
            )
            if rotation.status == 'bad_request':
                self.send_response_body(
                    400,
                    '重置请求已过期或缺少幂等标识，请返回用户面板重试。',
                )
                return
            if rotation.status == 'forbidden':
                self.send_response_body(403, '无权限访问')
                return
            if rotation.status == 'disabled':
                self.send_response_body(403, '账号已停用，请联系管理员')
                return
            if rotation.status == 'expired':
                self.send_response_body(403, '账号已到期，请联系管理员续费')
                return
            if rotation.status == 'conflict':
                self.send_response_body(
                    409,
                    html_page(
                        'Token 已再次变更',
                        '<div class="wrap"><div class="card">'
                        '<h1>Token 已再次变更</h1>'
                        '<div class="err" role="alert">'
                        '管理员或另一个会话已完成更新，本次恢复凭据已作废。'
                        '请使用管理员提供的最新链接，或用面板密码重新登录。'
                        '</div><div class="row mt-md">'
                        '<a class="btn" href="/login">返回登录</a>'
                        '</div></div></div>',
                    ),
                    'text/html; charset=utf-8',
                    True,
                )
                return

            revocation_uncertain = False
            static_outcomes = {}
            completed_static_services = []
            if rotation.sync_pending:
                static_outcomes = _fail_closed_static_access(
                    rotation.sync_error
                    or RuntimeError('credential sync pending'),
                )
                completed_static_services.extend(
                    service
                    for service, outcome in static_outcomes.items()
                    if outcome.ok
                )
            else:
                for service, changed in (
                    (
                        static_access.XRAY_SERVICE,
                        rotation.xray_changed,
                    ),
                    (
                        static_access.TUIC_SERVICE,
                        rotation.tuic_changed,
                    ),
                ):
                    reload_result = _schedule_static_reload(
                        service,
                        changed=changed,
                    )
                    if reload_result.ok:
                        completed_static_services.append(service)
                    else:
                        raw = static_access.stop_fail_closed(
                            service,
                            reason=RuntimeError(
                                'credential reload scheduling failed',
                            ),
                            live=_using_live_core_state(),
                        )
                        static_outcomes[service] = (
                            _normalize_service_action(service, raw)
                        )
                        if static_outcomes[service].ok:
                            completed_static_services.append(service)
            retry_services = [
                service
                for service, outcome in static_outcomes.items()
                if not outcome.ok
            ]
            if retry_services and not _record_static_retry(
                rotation.task_id,
                retry_services,
            ):
                revocation_uncertain = True

            kick_result = hy_kick([user])
            kick_recorded = _record_kick_attempt(
                rotation.task_id,
                kick_result,
                completed_static_services=completed_static_services,
            )
            if (
                not _action_succeeded(kick_result)
                or not kick_recorded
            ):
                revocation_uncertain = True

            confirmed_static_pause = (
                rotation.sync_pending
                and len(static_outcomes) == len(static_access.SERVICES)
                and all(
                    outcome.effect_confirmed
                    for outcome in static_outcomes.values()
                )
            )
            if revocation_uncertain or any(
                not outcome.effect_confirmed
                for outcome in static_outcomes.values()
            ):
                notice = 'token_rotated_revocation_retry'
            elif rotation.sync_pending and confirmed_static_pause:
                notice = 'token_rotated_sync_pending'
            elif static_outcomes:
                notice = 'token_rotated_static_pending'
            else:
                notice = 'token_rotated'

            generation_conflict = False
            try:
                with usage_lock():
                    latest = load_json(USERS_FILE, {}).get(user)
                    current_generation = _credential_generation(
                        latest.get('sub_token')
                        if isinstance(latest, dict)
                        else '',
                    )
                    expected_generation = _credential_generation(
                        rotation.new_token,
                    )
                    if (
                        not current_generation
                        or not hmac.compare_digest(
                            current_generation,
                            expected_generation,
                        )
                    ):
                        generation_conflict = True
                        sid = ''
                    else:
                        sid = create_user_session(
                            user,
                            expected_generation,
                            USER_SESSION_SUBSCRIPTION_TOKEN,
                        )
            except (state_store.StateStoreError, OSError):
                sid = ''
            if generation_conflict:
                self.send_response_body(
                    409,
                    html_page(
                        'Token 已被后续更新',
                        '<div class="wrap"><div class="card">'
                        '<h1>Token 已被后续更新</h1>'
                        '<div class="err" role="alert">'
                        '本次 Token 已提交，但在创建新会话前又被更新。'
                        '为避免交付过期凭据，本页不显示旧一代 Token；'
                        '请使用最新管理员链接或面板密码重新登录。'
                        '</div><div class="row mt-md">'
                        '<a class="btn" href="/login">返回登录</a>'
                        '</div></div></div>',
                    ),
                    'text/html; charset=utf-8',
                    True,
                )
                return
            if sid:
                try:
                    receipt_bound = (
                        rotation_recovery.bind_replacement_session(
                            _rotation_receipts_path(),
                            user=user,
                            request_id=request_id,
                            original_session_id=original_sid,
                            replacement_session_id=sid,
                        )
                    )
                except (state_store.StateStoreError, OSError):
                    receipt_bound = False
                if receipt_bound:
                    self.redirect(
                        f'/user/panel?msg={notice}',
                        cookie=user_session_cookie(
                            sid,
                            secure=is_secure_request(self),
                        ),
                        status=303,
                    )
                    return

            if not sid or not receipt_bound:
                recovery_notice = (
                    'token_rotated_revocation_retry_recovery'
                    if notice == 'token_rotated_revocation_retry'
                    else (
                    'token_rotated_sync_pending_recovery'
                    if notice == 'token_rotated_sync_pending'
                    else (
                        'token_rotated_static_pending_recovery'
                        if notice == 'token_rotated_static_pending'
                        else 'token_rotated_session_recovery'
                    )
                    )
                )
                cfg = rotation.user_config
                if not isinstance(cfg, dict):
                    raise
                self.send_response_body(
                    200,
                    render_user_panel(
                        configured_public_host(
                            self.headers.get('Host', '127.0.0.1'),
                        ),
                        safe_base_url(
                            configured_public_host(
                                self.headers.get('Host', '127.0.0.1'),
                            ),
                            self.headers.get(
                                'X-Forwarded-Proto', 'http',
                            ),
                            self.headers.get('X-Forwarded-Port', ''),
                        ),
                        user,
                        rotation.new_token,
                        cfg,
                        notice=recovery_notice,
                    ),
                    'text/html; charset=utf-8',
                    True,
                )
                return

        if path == '/login':
            ip = http_utils.request_client_ip(self)
            host = configured_public_host(
                self.headers.get('Host', '127.0.0.1'),
            )

            # Determine which tab was submitted
            admin_username = (form.get('admin_username') or [''])[0].strip()
            admin_password = (form.get('admin_password') or [''])[0]
            user_username = (form.get('user_username') or [''])[0].strip()
            user_password = (form.get('user_password') or [''])[0]

            if admin_username:
                # Admin login
                if not _begin_login_attempt(ip):
                    self.send_response_body(
                        429,
                        render_login(host, msg='登录尝试过于频繁，请 1 小时后再试', active_tab='admin', username=admin_username),
                        'text/html; charset=utf-8',
                        True,
                        extra_headers={'Retry-After': str(_LOGIN_WINDOW)},
                    )
                    return
                stored_hash = str(meta.get('admin_pass_hash') or '')
                ok = (
                    admin_username == meta.get('admin_user')
                    and len(admin_password) <= PASSWORD_MAX_LENGTH
                    and stored_hash
                    and verify_secret(admin_password, stored_hash)
                )
                _finish_login_attempt(ip, ok)
                if ok:
                    sid = create_session(
                        'admin', _credential_generation(stored_hash),
                    )
                    self.redirect('/admin?msg=login+success',
                                  cookie=session_cookie(sid, secure=is_secure_request(self)))
                    return
                self.send_response_body(
                    200,
                    render_login(host, msg='用户名或密码错误', active_tab='admin', username=admin_username),
                    'text/html; charset=utf-8', True,
                )
                return

            if user_username:
                # User login
                if not _begin_login_attempt(ip, _user_login_failures):
                    self.send_response_body(
                        429,
                        render_login(host, msg='登录尝试过于频繁，请 1 小时后再试', active_tab='user', username=user_username),
                        'text/html; charset=utf-8', True,
                        extra_headers={'Retry-After': str(_LOGIN_WINDOW)},
                    )
                    return
                cfg = load_json(USERS_FILE, {}).get(user_username)
                stored_hash = str(cfg.get('panel_pass_hash') or '') if isinstance(cfg, dict) else ''
                ok = bool(
                    is_valid_username(user_username)
                    and len(user_password) <= PASSWORD_MAX_LENGTH
                    and stored_hash
                    and verify_secret(user_password, stored_hash)
                )
                if ok and cfg.get('disabled'):
                    _finish_login_attempt(ip, None, _user_login_failures)
                    self.send_response_body(
                        200,
                        render_login(host, msg='账号已停用，请联系管理员', active_tab='user', username=user_username),
                        'text/html; charset=utf-8', True,
                    )
                    return
                if ok and user_compat.is_expired(cfg, today=local_now().date()):
                    _finish_login_attempt(ip, None, _user_login_failures)
                    self.send_response_body(
                        200,
                        render_login(host, msg='账号已到期，请联系管理员续费', active_tab='user', username=user_username),
                        'text/html; charset=utf-8', True,
                    )
                    return
                _finish_login_attempt(ip, ok, _user_login_failures)
                if ok:
                    sid = create_user_session(
                        user_username, _credential_generation(stored_hash),
                    )
                    target = '/user/change-password' if cfg.get('panel_password_must_change') else '/user/panel'
                    self.redirect(target, cookie=user_session_cookie(
                        sid, secure=is_secure_request(self)))
                    return
                self.send_response_body(
                    200,
                    render_login(host, msg='用户名或密码错误', active_tab='user', username=user_username),
                    'text/html; charset=utf-8', True,
                )
                return

            # No credentials provided
            self.send_response_body(
                200,
                render_login(host, msg='请输入用户名和密码'),
                'text/html; charset=utf-8', True,
            )
            return

        if path == '/user/change-password':
            user, session_kind = get_logged_in_user_context(self)
            if (
                not user
                or session_kind != USER_SESSION_PANEL_PASSWORD
            ):
                self.redirect('/login')
                return
            current_cfg = load_json(USERS_FILE, {}).get(user)
            access_error = user_panel_access_error(
                current_cfg, session_kind, today=local_now().date(),
            )
            if access_error == 'forbidden':
                self.redirect(
                    '/login',
                    cookie=clear_user_session_cookie(
                        secure=is_secure_request(self),
                    ),
                )
                return
            if access_error in ('disabled', 'expired'):
                self.redirect('/user/panel')
                return
            current = (form.get('current') or [''])[0]
            new = (form.get('new') or [''])[0]
            confirm = (form.get('confirm') or [''])[0]
            if len(new) < PASSWORD_MIN_LENGTH:
                self.redirect('/user/change-password?' + urlencode({'msg': 'new password short'}))
                return
            if len(new) > PASSWORD_MAX_LENGTH:
                self.redirect('/user/change-password?' + urlencode({'msg': 'new password long'}))
                return
            if new != confirm:
                self.redirect('/user/change-password?' + urlencode({'msg': 'new password mismatch'}))
                return
            with usage_lock():
                users = load_json(USERS_FILE, {})
                cfg = users.get(user)
                locked_access_error = user_panel_access_error(
                    cfg, session_kind, today=local_now().date(),
                )
                if locked_access_error == 'forbidden':
                    self.redirect(
                        '/login',
                        cookie=clear_user_session_cookie(
                            secure=is_secure_request(self),
                        ),
                    )
                    return
                if locked_access_error in ('disabled', 'expired'):
                    self.redirect('/user/panel')
                    return
                stored_hash = str(cfg.get('panel_pass_hash') or '') if isinstance(cfg, dict) else ''
                if not (
                    len(current) <= PASSWORD_MAX_LENGTH
                    and stored_hash
                    and verify_secret(current, stored_hash)
                ):
                    self.redirect('/user/change-password?' + urlencode({'msg': 'current password wrong'}))
                    return
                if verify_secret(new, stored_hash):
                    self.redirect('/user/change-password?' + urlencode({'msg': 'new password same'}))
                    return
                new_hash = hash_secret(new)
                cfg['panel_pass_hash'] = new_hash
                cfg.pop('panel_password_must_change', None)
                users[user] = cfg
                save_json(USERS_FILE, users)
            sid = _replace_sessions_with_new(
                USER_SESSIONS_FILE, user,
                credential_generation=_credential_generation(new_hash),
                credential_kind=USER_SESSION_PANEL_PASSWORD,
            )
            self.redirect('/user/panel', cookie=user_session_cookie(
                sid, secure=is_secure_request(self)))
            return

        if path == '/user/landing-egress/select':
            user, session_kind = get_logged_in_user_context(self)
            if not user or session_kind != USER_SESSION_PANEL_PASSWORD:
                self.send_response_body(403, '仅面板密码会话可以切换家宽出口')
                return
            requested_id = (form.get('egress_id') or [''])[0].strip()
            users = load_json(USERS_FILE, {})
            cfg = users.get(user)
            if not isinstance(cfg, dict):
                self.send_response_body(403, '用户状态无效')
                return
            if user_panel_access_error(
                cfg, session_kind, today=local_now().date(),
            ):
                self.send_response_body(403, '当前账户不能切换家宽出口')
                return
            if not revision_matches(cfg, request_user_revision):
                self.send_response_body(409, '用户配置已更新，请刷新后重试')
                return
            registry = _landing_registry_or_empty()
            nodes = registry.get('nodes', {})
            allowed = cfg.get('landing_allowed_egress_ids', [])
            node = nodes.get(requested_id)
            if (
                not isinstance(allowed, list)
                or requested_id not in allowed
                or not isinstance(node, dict)
                or node.get('enabled') is not True
            ):
                self.send_response_body(403, '无权选择该家宽出口')
                return
            probed_node_revision = content_revision(node)
            changed_at = str(cfg.get('landing_egress_changed_at') or '')
            if changed_at:
                try:
                    previous_change = datetime.fromisoformat(changed_at)
                    elapsed = (local_now() - previous_change).total_seconds()
                except (TypeError, ValueError):
                    self.send_response_body(409, '家宽出口状态无法确认')
                    return
                if elapsed < 60:
                    self.send_response_body(
                        429, '切换过于频繁，请稍后重试',
                        extra_headers={'Retry-After': str(max(1, int(60 - elapsed)))},
                    )
                    return
            try:
                landing_egress.probe_exit(node)
            except landing_egress.LandingEgressProbeError:
                self.send_response_body(422, '家宽出口健康检查失败，未修改选择')
                return
            with usage_lock():
                original_users_text = Path(USERS_FILE).read_text(
                    encoding='utf-8',
                )
                users = load_json(USERS_FILE, {})
                cfg = users.get(user)
                registry = _landing_registry_or_empty()
                node = registry.get('nodes', {}).get(requested_id)
                allowed = cfg.get('landing_allowed_egress_ids', []) if isinstance(cfg, dict) else []
                if not isinstance(cfg, dict) or not revision_matches(cfg, request_user_revision):
                    self.send_response_body(409, '用户配置已更新，请刷新后重试')
                    return
                if (
                    not isinstance(allowed, list)
                    or requested_id not in allowed
                    or not isinstance(node, dict)
                    or node.get('enabled') is not True
                ):
                    self.send_response_body(403, '无权选择该家宽出口')
                    return
                if content_revision(node) != probed_node_revision:
                    self.send_response_body(409, '家宽出口配置已更新，请重试')
                    return
                cfg['landing_selected_egress_id'] = requested_id
                cfg['landing_egress_changed_at'] = local_now().isoformat()
                users[user] = cfg
                save_json(USERS_FILE, users)
                try:
                    xray_changed, tuic_changed = (
                        _sync_static_access_from_users(users)
                    )
                except Exception:
                    state_store.save_text_atomic(
                        USERS_FILE,
                        original_users_text,
                    )
                    original_users = json.loads(original_users_text)
                    try:
                        _sync_static_access_from_users(original_users)
                    except Exception:
                        pass
                    raise
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect(
                '/user/panel?' + urlencode({'msg': 'landing_egress_selected'}),
                status=303,
            )
            return

        if path == '/admin/landing-egress/save':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            node_id = (form.get('id') or [''])[0].strip()
            expected_registry_revision = (
                form.get('registry_revision') or ['']
            )[0]
            with usage_lock():
                registry = landing_egress.load_registry()
                if not hmac.compare_digest(
                    content_revision(registry),
                    str(expected_registry_revision),
                ):
                    self.send_response_body(409, '节点列表已更新，请刷新后重试')
                    return
                existing = registry.get('nodes', {}).get(node_id, {})
                username = (form.get('socks_username') or [''])[0]
                password = (form.get('socks_password') or [''])[0]
                if isinstance(existing, dict):
                    if not username and existing.get('socks_username'):
                        username = existing['socks_username']
                    if not password and existing.get('socks_password'):
                        password = existing['socks_password']
                raw_node = {
                    'id': node_id,
                    'name': (form.get('name') or [''])[0],
                    'socks_ip': (form.get('socks_ip') or [''])[0],
                    'socks_port': (form.get('socks_port') or [''])[0],
                    'socks_username': username,
                    'socks_password': password,
                    'expected_exit_ip': (form.get('expected_exit_ip') or [''])[0],
                    'isp': (form.get('isp') or [''])[0],
                    'region': (form.get('region') or [''])[0],
                    'enabled': 'enabled' in form,
                }
                if isinstance(existing, dict) and existing.get('health'):
                    raw_node['health'] = existing['health']
                try:
                    node = landing_egress.validate_node(raw_node)
                except landing_egress.LandingEgressValidationError as exc:
                    self.send_response_body(422, '节点配置无效：' + exc.code)
                    return
                registry['nodes'][node_id] = node
                landing_egress.save_registry(registry)
                users = load_json(USERS_FILE, {})
                xray_changed, tuic_changed = _sync_static_access_from_users(users)
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect(
                '/admin/landing-egresses?' + urlencode({'msg': '节点已保存'}),
                status=303,
            )
            return

        if path == '/admin/landing-egress/delete':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            node_id = (form.get('id') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                referenced = any(
                    isinstance(cfg, dict)
                    and (
                        node_id in cfg.get('landing_allowed_egress_ids', [])
                        or cfg.get('landing_selected_egress_id') == node_id
                    )
                    for cfg in users.values()
                )
                if referenced:
                    self.send_response_body(409, '节点仍被用户引用，无法删除')
                    return
                registry = landing_egress.load_registry()
                registry.get('nodes', {}).pop(node_id, None)
                landing_egress.save_registry(registry)
                xray_changed, tuic_changed = _sync_static_access_from_users(users)
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect(
                '/admin/landing-egresses?' + urlencode({'msg': '节点已删除'}),
                status=303,
            )
            return

        if path == '/admin/landing-egress/check':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            node_id = (form.get('id') or [''])[0].strip()
            registry = landing_egress.load_registry()
            node = registry.get('nodes', {}).get(node_id)
            if not isinstance(node, dict):
                self.send_response_body(404, '节点不存在')
                return
            probed_node_revision = content_revision(node)
            try:
                observed = landing_egress.probe_exit(node)
            except landing_egress.LandingEgressProbeError as exc:
                status = 'unhealthy'
                observed = ''
                error_code = exc.code
            else:
                status = 'healthy'
                error_code = ''
            with usage_lock():
                registry = landing_egress.load_registry()
                current = registry.get('nodes', {}).get(node_id)
                if not isinstance(current, dict):
                    self.send_response_body(409, '节点已被修改')
                    return
                if content_revision(current) != probed_node_revision:
                    self.send_response_body(409, '节点已被修改')
                    return
                current['health'] = {
                    'status': status,
                    'observed_ip': observed,
                    'checked_at': local_now().isoformat(),
                    'error_code': error_code,
                }
                registry['nodes'][node_id] = current
                landing_egress.save_registry(registry)
            self.redirect(
                '/admin/landing-egresses?' + urlencode({
                    'msg': '节点健康' if status == 'healthy' else '节点不可用',
                }),
                status=303,
            )
            return

        if path == '/admin/user-landing-access':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            requested_ids = list(dict.fromkeys(form.get('egress_id') or []))
            with usage_lock():
                original_users_text = Path(USERS_FILE).read_text(
                    encoding='utf-8',
                )
                users = load_json(USERS_FILE, {})
                cfg = users.get(username)
                if not isinstance(cfg, dict):
                    self.send_response_body(404, '用户不存在')
                    return
                if not revision_matches(cfg, request_user_revision):
                    self.send_response_body(409, '用户配置已更新，请刷新后重试')
                    return
                registry = landing_egress.load_registry()
                nodes = registry.get('nodes', {})
                if any(
                    node_id not in nodes or nodes[node_id].get('enabled') is not True
                    for node_id in requested_ids
                ):
                    self.send_response_body(422, '授权节点无效或已禁用')
                    return
                cfg['landing_allowed_egress_ids'] = requested_ids
                if requested_ids:
                    _ensure_landing_vless_uuid(cfg, users)
                if cfg.get('landing_selected_egress_id') not in requested_ids:
                    cfg.pop('landing_selected_egress_id', None)
                users[username] = cfg
                save_json(USERS_FILE, users)
                try:
                    xray_changed, tuic_changed = (
                        _sync_static_access_from_users(users)
                    )
                except Exception:
                    state_store.save_text_atomic(
                        USERS_FILE,
                        original_users_text,
                    )
                    try:
                        _sync_static_access_from_users(
                            json.loads(original_users_text),
                        )
                    except Exception:
                        pass
                    raise
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect(
                '/admin/landing-egresses?' + urlencode({'msg': '用户授权已更新'}),
                status=303,
            )
            return

        if path == '/admin/update':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            expected_revision = request_user_revision
            panel_password = (form.get('panel_password') or [''])[0]
            new_password = (form.get('password') or [''])[0].strip()
            max_devices = parse_bounded_int_field(
                (form.get('max_devices') or [''])[0], 0, 100,
            )
            quota_gb = parse_bounded_int_field(
                (form.get('quota_gb') or [''])[0], 0, 10240,
            )
            quota_extra_gb = parse_bounded_int_field(
                (form.get('quota_extra_gb') or [''])[0], 0, 10240,
            )
            expires_raw = (form.get('expires_at') or [''])[0]
            note_raw = (form.get('note') or [''])[0]
            expires_at = parse_date_field(expires_raw)
            note = parse_note_field(note_raw)
            landing_values = {}
            landing_error = None
            for landing_name in user_compat.LANDING_FIELDS:
                parser = (
                    user_compat.parse_landing_ip_write
                    if landing_name == 'landing_ip'
                    else user_compat.parse_landing_write
                )
                value, err = parser(
                    (form.get(landing_name) or [''])[0],
                )
                if err:
                    landing_error = err
                    break
                landing_values[landing_name] = value
            guest = 'guest' in form
            tuic_enabled = 'tuic_enabled' in form

            def respond_update_error(message):
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                self.send_response_body(
                    422,
                    render_admin(
                        host,
                        safe_base_url(
                            host,
                            self.headers.get('X-Forwarded-Proto', 'http'),
                            self.headers.get('X-Forwarded-Port', ''),
                        ),
                        flash=message,
                    ),
                    'text/html; charset=utf-8',
                )

            if max_devices is None:
                respond_update_error('err:max_devices_invalid')
                return
            if quota_gb is None:
                respond_update_error('err:quota_invalid')
                return
            if quota_extra_gb is None:
                respond_update_error('err:quota_extra_invalid')
                return
            if str(expires_raw).strip() and not expires_at:
                respond_update_error('err:expiry_invalid')
                return
            if len(str(note_raw).strip()) > 200:
                respond_update_error('err:note_too_long')
                return
            if landing_error:
                respond_update_error('err:' + landing_error)
                return
            if panel_password and len(panel_password) < 8:
                self.redirect('/admin?msg=err:panel_password_short')
                return
            if len(panel_password) > PASSWORD_MAX_LENGTH:
                self.redirect('/admin?msg=err:panel_password_long')
                return
            if len(new_password) > PASSWORD_MAX_LENGTH:
                self.redirect('/admin?msg=err:proxy_password_long')
                return
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self.redirect('/admin?msg=user+not+found')
                    return
                cfg = users[username]
                if (
                    not isinstance(cfg, dict)
                    or not revision_matches(cfg, expected_revision)
                ):
                    self.send_user_state_conflict(
                        '/admin',
                        draft={
                            'user': username,
                            'max_devices': max_devices,
                            'quota_gb': quota_gb,
                            'quota_extra_gb': quota_extra_gb,
                            'expires_at': expires_at,
                            'note': note,
                            'guest': '是' if guest else '否',
                            'tuic_enabled': (
                                '是' if tuic_enabled else '否'
                            ),
                        },
                    )
                    return
                if panel_password:
                    cfg['panel_pass_hash'] = hash_secret(panel_password)
                    cfg['panel_password_must_change'] = True
                if new_password:
                    cfg['password_hash'] = hash_secret(new_password)
                cfg.pop('password', None)
                cfg['max_devices'] = max_devices
                cfg['monthly_quota_bytes'] = quota_gb * 1024 * 1024 * 1024
                cfg['quota_extra_bytes'] = quota_extra_gb * 1024 * 1024 * 1024
                if expires_at:
                    cfg['expires_at'] = expires_at
                else:
                    cfg.pop('expires_at', None)
                if note:
                    cfg['note'] = note
                else:
                    cfg.pop('note', None)
                for landing_name, landing_value in landing_values.items():
                    if landing_value:
                        cfg[landing_name] = landing_value
                    else:
                        cfg.pop(landing_name, None)
                cfg['metered'] = guest
                cfg['guest'] = guest
                cfg['tuic_enabled'] = tuic_enabled
                if not cfg.get('sub_token'):
                    cfg['sub_token'] = secrets.token_urlsafe(18)
                if not str(cfg.get('vless_uuid') or '').strip():
                    cfg['vless_uuid'] = str(uuid.uuid4())
                users[username] = cfg
                save_json(USERS_FILE, users)
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users)
                )
            if panel_password:
                delete_user_sessions_for(username)
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect('/admin?msg=updated+' + username)
            return

        if path == '/admin/add':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            panel_password = (form.get('panel_password') or [''])[0]
            password = (form.get('password') or [''])[0].strip()
            quota_gb_raw = (form.get('quota_gb') or [''])[0]
            quota_extra_gb_raw = (form.get('quota_extra_gb') or [''])[0]
            quota_gb = parse_bounded_int_field(
                quota_gb_raw, 0, 10240,
            )
            quota_extra_gb = parse_bounded_int_field(
                quota_extra_gb_raw, 0, 10240,
            )
            expires_raw = (form.get('expires_at') or [''])[0]
            note_raw = (form.get('note') or [''])[0]
            landing_initial_egress_id = (
                form.get('landing_initial_egress_id') or ['']
            )[0].strip()
            expires_at = parse_date_field(expires_raw)
            note = parse_note_field(note_raw)
            guest = 'guest' in form
            tuic_enabled = 'tuic_enabled' in form
            create_draft = {
                'user': username,
                'quota_gb': quota_gb_raw,
                'quota_extra_gb': quota_extra_gb_raw,
                'expires_at': expires_raw,
                'note': note_raw,
                'landing_initial_egress_id': landing_initial_egress_id,
                'guest': guest,
                'tuic_enabled': tuic_enabled,
            }

            def respond_create_error(message, field_id):
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                base_url = safe_base_url(
                    host,
                    self.headers.get('X-Forwarded-Proto', 'http'),
                    self.headers.get('X-Forwarded-Port', ''),
                )
                self.send_response_body(
                    422,
                    render_admin(
                        host,
                        base_url,
                        flash=message,
                        create_draft=create_draft,
                        create_error_field=field_id,
                    ),
                    'text/html; charset=utf-8',
                )

            if not username:
                respond_create_error('user empty', 'create-user')
                return
            if not is_valid_username(username):
                respond_create_error('err:username_invalid', 'create-user')
                return
            if quota_gb is None:
                respond_create_error(
                    'err:quota_invalid', 'create-quota-gb',
                )
                return
            if quota_extra_gb is None:
                respond_create_error(
                    'err:quota_extra_invalid', 'create-quota-extra-gb',
                )
                return
            if str(expires_raw).strip() and not expires_at:
                respond_create_error(
                    'err:expiry_invalid', 'create-expires-at',
                )
                return
            if len(str(note_raw).strip()) > 200:
                respond_create_error(
                    'err:note_too_long', 'create-note',
                )
                return
            if panel_password and len(panel_password) < 8:
                respond_create_error(
                    'err:panel_password_short', 'create-panel-password',
                )
                return
            if len(panel_password) > PASSWORD_MAX_LENGTH:
                respond_create_error(
                    'err:panel_password_long', 'create-panel-password',
                )
                return
            if len(password) > PASSWORD_MAX_LENGTH:
                respond_create_error(
                    'err:proxy_password_long', 'create-proxy-password',
                )
                return
            if landing_initial_egress_id:
                registry = _landing_registry_or_empty()
                initial_node = registry.get('nodes', {}).get(
                    landing_initial_egress_id,
                )
                if (
                    not isinstance(initial_node, dict)
                    or initial_node.get('enabled') is not True
                ):
                    respond_create_error(
                        '家宽出口已不可用，请重新选择',
                        'create-landing-initial-egress',
                    )
                    return
            user_exists = False
            landing_became_unavailable = False
            with usage_lock():
                original_users_text = Path(USERS_FILE).read_text(
                    encoding='utf-8',
                )
                users = load_json(USERS_FILE, {})
                if landing_initial_egress_id:
                    registry = landing_egress.load_registry()
                    initial_node = registry.get('nodes', {}).get(
                        landing_initial_egress_id,
                    )
                    landing_became_unavailable = (
                        not isinstance(initial_node, dict)
                        or initial_node.get('enabled') is not True
                    )
                if landing_became_unavailable:
                    pass
                elif username in users:
                    user_exists = True
                else:
                    entry = {
                        'metered': guest,
                        'guest': guest,
                        'tuic_enabled': tuic_enabled,
                        'monthly_quota_bytes': quota_gb * 1024 * 1024 * 1024,
                        'quota_extra_bytes': quota_extra_gb * 1024 * 1024 * 1024,
                        'sub_token': secrets.token_urlsafe(18),
                        'vless_uuid': str(uuid.uuid4()),
                        'disabled': False,
                        'max_devices': 2,
                    }
                    if expires_at:
                        entry['expires_at'] = expires_at
                    if note:
                        entry['note'] = note
                    if password:
                        entry['password_hash'] = hash_secret(password)
                    if panel_password:
                        entry['panel_pass_hash'] = hash_secret(panel_password)
                        entry['panel_password_must_change'] = True
                    if landing_initial_egress_id:
                        entry['landing_allowed_egress_ids'] = [
                            landing_initial_egress_id,
                        ]
                        entry['landing_selected_egress_id'] = (
                            landing_initial_egress_id
                        )
                        _ensure_landing_vless_uuid(entry, users)
                    users[username] = entry
                    save_json(USERS_FILE, users)
                    try:
                        xray_changed, tuic_changed = (
                            _sync_static_access_from_users(users)
                        )
                    except Exception:
                        state_store.save_text_atomic(
                            USERS_FILE,
                            original_users_text,
                        )
                        try:
                            _sync_static_access_from_users(
                                json.loads(original_users_text),
                            )
                        except Exception:
                            pass
                        raise
            if landing_became_unavailable:
                respond_create_error(
                    '家宽出口已不可用，请重新选择',
                    'create-landing-initial-egress',
                )
                return
            if user_exists:
                respond_create_error(
                    'user_exists_use_reset_token', 'create-user',
                )
                return
            if panel_password:
                delete_user_sessions_for(username)
            # Re-syncing a suspended user back into xray would undo the suspend.
            # Config writes are committed with the user snapshot above; service
            # reloads stay outside the lock because they can perform process I/O.
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
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
            # Re-read under META.lock so this RMW cannot restore an older
            # password hash saved by a concurrent request.
            _update_cycle_meta(day, length)
            with usage_lock():
                users = load_json(USERS_FILE, {})
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users)
                )
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.redirect(f'/admin?msg=settlement+{day}')
            return

        if path == '/admin/reset-usage':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            username = (form.get('user') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._mutation_user_not_found(username, '/admin')
                    return
                if not revision_matches(
                    users.get(username), request_user_revision,
                ):
                    self._mutation_conflict(username, '/admin')
                    return
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
                _clear_alert_dedup_for_users([username], quota_only=True)
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users, now=now)
                )
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.write_reset_log(self.get_admin_actor(), 'reset_usage_user', username, before, after)
            if _json_request(self):
                self._send_mutation_json(200, {
                    'ok': True,
                    'username': username,
                    'user': _build_overview_user(username, now=local_now()),
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect('/admin?msg=reset+usage+' + username)
            return

        if path == '/admin/refresh-usage':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            username = (form.get('user') or [''])[0].strip()
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._mutation_user_not_found(username, '/admin')
                    return
                if not revision_matches(
                    users.get(username), request_user_revision,
                ):
                    self._mutation_conflict(username, '/admin')
                    return
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
                _clear_alert_dedup_for_users([username], quota_only=True)
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users, now=now)
                )
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.write_reset_log(self.get_admin_actor(), 'refresh_usage_user', username, before, after)
            if _json_request(self):
                self._send_mutation_json(200, {
                    'ok': True,
                    'username': username,
                    'user': _build_overview_user(username, now=local_now()),
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect('/admin?msg=refresh+usage+' + username)
            return

        if path == '/admin/change-password':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            current = (form.get('current') or [''])[0]
            new = (form.get('new') or [''])[0]
            confirm = (form.get('confirm') or [''])[0]
            result, new_hash = _change_admin_password(current, new, confirm)
            if result != 'ok':
                self.redirect(f'/admin/settings?msg=err:{result}')
                return
            # Revoke ALL existing admin sessions (a stolen sid is now dead),
            # then mint a fresh session for this device so the admin stays
            # logged in here. Mirrors the /login success cookie pattern.
            sid = _replace_sessions_with_new(
                SESSIONS_FILE, 'admin', revoke_all=True,
                credential_generation=_credential_generation(new_hash),
            )
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

        if path == '/admin/hysteria-update/check':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            try:
                info = hysteria_update.check_and_record()
                flash = 'checked ' + (info.get('latest') or '')
            except state_store.LockTimeout:
                if _json_request(self):
                    self._send_mutation_json(
                        409, {'ok': False, 'reason': 'update_busy'},
                    )
                    return
                flash = 'err:hysteria_update_busy'
            except Exception:
                if _json_request(self):
                    self._send_mutation_json(
                        502, {'ok': False, 'reason': 'update_check_failed'},
                    )
                    return
                flash = 'err:hysteria_update_check_failed'
            else:
                if _json_request(self):
                    self._send_mutation_json(200, {
                        'ok': True,
                        'status': 'checked',
                        'current': info.get('current') or '',
                        'latest': info.get('latest') or '',
                        'update_available': bool(
                            info.get('update_available')
                        ),
                        'pending': False,
                    })
                    return
            self.redirect('/admin/health?msg=' + flash.replace(' ', '+'))
            return

        if path == '/admin/hysteria-update/apply':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            try:
                state = hysteria_update.schedule_apply_async()
            except state_store.LockTimeout:
                if _json_request(self):
                    self._send_mutation_json(
                        409, {'ok': False, 'reason': 'update_busy'},
                    )
                    return
                self.redirect('/admin/health?msg=err:hysteria_update_busy')
                return
            except Exception:
                if _json_request(self):
                    self._send_mutation_json(
                        503, {'ok': False, 'reason': 'update_schedule_failed'},
                    )
                    return
                self.redirect(
                    '/admin/health?msg=err:hysteria_update_failed'
                )
                return
            if _json_request(self):
                self._send_mutation_json(
                    202, hysteria_update.public_status(state),
                )
                return
            self.redirect('/admin/health?msg=hysteria_update_scheduled')
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
                self._mutation_unauthorized()
                return
            # Snapshot the actor before any mutation. A later session-lock
            # timeout must never turn a committed rotation into a false 503.
            actor = self.get_admin_actor()
            username = (form.get('user') or [''])[0].strip()
            next_to = safe_admin_next((form.get('next') or [''])[0])
            sync_pending = False
            sync_error = None
            task_id = ''
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._mutation_user_not_found(username, next_to)
                    return
                if not isinstance(users.get(username), dict):
                    self._mutation_user_not_found(username, next_to)
                    return
                if not revision_matches(
                    users.get(username), request_user_revision,
                ):
                    self._mutation_conflict(username, next_to)
                    return
                previous_generation = _credential_generation(
                    users[username].get('sub_token'),
                )
                new_token = secrets.token_urlsafe(18)
                new_uuid = str(uuid.uuid4())
                new_generation = _credential_generation(new_token)
                task_id = revocation_queue.task_id_for(
                    username,
                    secrets.token_urlsafe(24),
                )
                revocation_queue.prepare(
                    _revocation_queue_path(),
                    task_id=task_id,
                    user=username,
                    previous_generation=previous_generation,
                    target_generation=new_generation,
                    static_services=static_access.SERVICES,
                )
                users[username]['sub_token'] = new_token
                users[username]['vless_uuid'] = new_uuid
                durability_uncertain = _save_users_for_rotation(
                    users,
                    user=username,
                    new_token=new_token,
                    new_uuid=new_uuid,
                )
                if durability_uncertain:
                    sync_pending = True
                    sync_error = CredentialRotationCommitted(
                        username,
                        new_token,
                        users[username],
                        durability_uncertain=True,
                    )
                    xray_changed = False
                    tuic_changed = False
                else:
                    try:
                        xray_changed, tuic_changed = (
                            _sync_static_access_from_users(users)
                        )
                    except state_store.CriticalStateUnavailable as exc:
                        sync_pending = True
                        sync_error = exc
                        xray_changed = False
                        tuic_changed = False

            revocation_uncertain = False
            static_outcomes = {}
            completed_static_services = []
            if sync_pending:
                static_outcomes = _fail_closed_static_access(sync_error)
                completed_static_services.extend(
                    service
                    for service, outcome in static_outcomes.items()
                    if outcome.ok
                )
            else:
                for service, changed in (
                    (static_access.XRAY_SERVICE, xray_changed),
                    (static_access.TUIC_SERVICE, tuic_changed),
                ):
                    reload_result = _schedule_static_reload(
                        service,
                        changed=changed,
                    )
                    if reload_result.ok:
                        completed_static_services.append(service)
                    else:
                        raw = static_access.stop_fail_closed(
                            service,
                            reason=RuntimeError(
                                'credential reload scheduling failed',
                            ),
                            live=_using_live_core_state(),
                        )
                        static_outcomes[service] = (
                            _normalize_service_action(service, raw)
                        )
                        if static_outcomes[service].ok:
                            completed_static_services.append(service)
            retry_services = [
                service
                for service, outcome in static_outcomes.items()
                if not outcome.ok
            ]
            if retry_services and not _record_static_retry(
                task_id,
                retry_services,
            ):
                revocation_uncertain = True
            kick_result = hy_kick([username])
            kick_recorded = _record_kick_attempt(
                task_id,
                kick_result,
                completed_static_services=completed_static_services,
            )
            if (
                not _action_succeeded(kick_result)
                or not kick_recorded
            ):
                revocation_uncertain = True
            self.write_reset_log(
                actor,
                'rotate_token',
                username,
                {},
                {},
            )
            confirmed_static_pause = (
                sync_pending
                and len(static_outcomes) == len(static_access.SERVICES)
                and all(
                    outcome.effect_confirmed
                    for outcome in static_outcomes.values()
                )
            )
            if revocation_uncertain or any(
                not outcome.effect_confirmed
                for outcome in static_outcomes.values()
            ):
                flash = 'err:rotated_retry ' + username
            elif sync_pending and confirmed_static_pause:
                flash = 'err:rotated_pending ' + username
            elif static_outcomes:
                flash = 'err:rotated_static_pending ' + username
            else:
                flash = 'rotated ' + username
            if _json_request(self):
                # The token changed, so the row's subscription/panel links
                # must be refreshed client-side along with the row itself.
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                base_url = safe_base_url(
                    host,
                    self.headers.get('X-Forwarded-Proto', 'http'),
                    self.headers.get('X-Forwarded-Port', ''),
                )
                new_token = users.get(username, {}).get('sub_token', '')
                self._send_mutation_json(200, {
                    'ok': True,
                    'username': username,
                    'flash': flash.split(' ', 1)[0],
                    'user': _build_overview_user(username, now=local_now()),
                    'links': {
                        'panel': f'{base_url}/panel/{username}?token={new_token}',
                        'sub': f'{base_url}/sub/{username}?token={new_token}',
                    },
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect(with_flash(next_to, flash))
            return

        if path == '/admin/pause-user':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            username = (form.get('user') or [''])[0].strip()
            minutes = parse_int_field((form.get('minutes') or ['60'])[0], 60, 1, 1440)
            next_to = safe_admin_next((form.get('next') or [''])[0])
            until = local_now() + timedelta(minutes=minutes)
            until_text = until.isoformat(timespec='seconds')
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._mutation_user_not_found(username, next_to)
                    return
                if not isinstance(users.get(username), dict):
                    self._mutation_user_not_found(username, next_to)
                    return
                if not revision_matches(
                    users.get(username), request_user_revision,
                ):
                    self._mutation_conflict(username, next_to)
                    return
                users[username]['disabled'] = True
                users[username]['disabled_until'] = until_text
                save_json(USERS_FILE, users)
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users)
                )
            delete_user_sessions_for(username)
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            hy_kick([username])
            self.write_reset_log(
                self.get_admin_actor(),
                'pause_user',
                username,
                {},
                {'disabled_until': until_text},
            )
            if _json_request(self):
                self._send_mutation_json(200, {
                    'ok': True,
                    'username': username,
                    'disabled_until': until_text,
                    'user': _build_overview_user(username, now=local_now()),
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect(with_flash(next_to, 'paused ' + username))
            return

        if path == '/admin/toggle-user':
            if not is_logged_in(self):
                if _json_request(self):
                    self.send_response_body(401, '{"ok":false,"reason":"login_required"}',
                                           'application/json; charset=utf-8')
                else:
                    self.redirect('/login')
                return
            username = (form.get('user') or [''])[0].strip()
            next_to = safe_admin_next((form.get('next') or [''])[0])
            desired = (query.get('desired') or [''])[0]
            if desired not in ('disabled', 'enabled'):
                if _json_request(self):
                    self.send_response_body(422, '{"ok":false,"reason":"invalid_desired"}',
                                           'application/json; charset=utf-8')
                else:
                    self.send_response_body(422, '目标用户状态无效')
                return
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._send_toggle_json(404, username, 'user_not_found', next_to)
                    return
                if not isinstance(users.get(username), dict):
                    self._send_toggle_json(404, username, 'user_not_found', next_to)
                    return
                if not revision_matches(
                    users.get(username), request_user_revision,
                ):
                    self._send_toggle_json(409, username, 'conflict', next_to)
                    return
                disable = desired == 'disabled'
                users[username]['disabled'] = disable
                users[username].pop('disabled_until', None)
                save_json(USERS_FILE, users)
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users)
                )
            # Config commits share the user-state lock above.
            if disable:
                delete_user_sessions_for(username)
                if xray_changed:
                    xray_config.reload_async()
                if tuic_changed:
                    tuic_config.reload_async()
                hy_kick([username])
                self.write_reset_log(self.get_admin_actor(), 'disable_user', username, {}, {})
            else:
                if xray_changed:
                    xray_config.reload_async()
                if tuic_changed:
                    tuic_config.reload_async()
                self.write_reset_log(self.get_admin_actor(), 'enable_user', username, {}, {})
            self._send_toggle_json(200, username, desired, next_to)
            return

        if path == '/admin/reset-usage-all':
            if not is_logged_in(self):
                self._mutation_unauthorized()
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
                _clear_alert_dedup_for_users(
                    list(users.keys()), quota_only=True
                )
                xray_changed, tuic_changed = (
                    _sync_static_access_from_users(users, now=now)
                )
            if xray_changed:
                xray_config.reload_async()
            if tuic_changed:
                tuic_config.reload_async()
            self.write_reset_log(
                self.get_admin_actor(),
                'reset_usage_all',
                'all_users',
                before_all,
                {u: {'tx': 0, 'rx': 0, 'total': 0} for u in users.keys()},
            )
            if _json_request(self):
                # Global reset touches every row, so return the full user
                # list in the overview schema for the client to patch.
                overview = _build_overview_json_payload(now=local_now())
                self._send_mutation_json(200, {
                    'ok': True,
                    'users': overview['users'],
                    'total_used': overview['total_used'],
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect('/admin?msg=reset+usage+all')
            return

        if path == '/admin/delete':
            if not is_logged_in(self):
                self._mutation_unauthorized()
                return
            username = (form.get('user') or [''])[0].strip()
            task_id = ''
            delete_error = None
            sync_error = None
            cleanup_error = None
            xray_changed = False
            tuic_changed = False
            with usage_lock():
                users = load_json(USERS_FILE, {})
                if username not in users:
                    self._mutation_user_not_found(username, '/admin')
                    return
                cfg = users.get(username)
                if not isinstance(cfg, dict):
                    self._mutation_user_not_found(username, '/admin')
                    return
                if not revision_matches(
                    cfg, request_user_revision,
                ):
                    self._mutation_conflict(username, '/admin')
                    return
                task_id = revocation_queue.task_id_for(
                    username,
                    secrets.token_urlsafe(24),
                )
                revocation_queue.prepare(
                    _revocation_queue_path(),
                    task_id=task_id,
                    user=username,
                    previous_generation=_delete_previous_generation(cfg),
                    target_generation=_DELETE_TARGET_GENERATION,
                    static_services=static_access.SERVICES,
                )
                del users[username]
                try:
                    save_json(USERS_FILE, users)
                except (state_store.StateStoreError, OSError) as exc:
                    # The WAL was durable first, so the worker may safely redo
                    # this exact account revision even when replace never ran.
                    # A post-replace durability uncertainty is likewise left
                    # for the worker to re-save before deleting history.
                    delete_error = exc
                if delete_error is None:
                    try:
                        xray_changed, tuic_changed = (
                            _sync_static_access_from_users(users)
                        )
                    except (state_store.StateStoreError, OSError) as exc:
                        sync_error = exc
                    try:
                        _purge_user_history_locked(username)
                    except (state_store.StateStoreError, OSError) as exc:
                        cleanup_error = exc

            # Config commits share the state lock. Process and network
            # side-effects remain outside it; the WAL retains every
            # unconfirmed reload/stop and the required second kick.
            outcome = _attempt_revocation_side_effects(
                task_id,
                username,
                xray_changed=xray_changed,
                tuic_changed=tuic_changed,
                sync_error=delete_error or sync_error,
            )
            retry_pending = bool(
                delete_error
                or sync_error
                or cleanup_error
                or outcome['uncertain']
            )
            flash = (
                'err:deleted_retry ' + username
                if retry_pending
                else 'deleted ' + username
            )
            if _json_request(self):
                self._send_mutation_json(200, {
                    'ok': True,
                    'username': username,
                    'deleted': True,
                    'flash': flash.split(' ', 1)[0],
                    'reload': _static_reload_status(),
                })
            else:
                self.redirect(with_flash('/admin', flash))
            return

        if path == '/admin/config/save':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            raw = (form.get('config_json') or [''])[0]
            expected_revision = (
                form.get('template_revision') or ['']
            )[0]
            host = configured_public_host(
                self.headers.get('Host', '127.0.0.1'),
            )
            if not raw.strip():
                self.send_response_body(
                    422,
                    render_config_editor(
                        host,
                        flash='err:empty',
                        draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                self.send_response_body(
                    422,
                    render_config_editor(
                        host,
                        flash='err:invalid_json',
                        draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            if not validate_template_config(data):
                self.send_response_body(
                    422,
                    render_config_editor(
                        host,
                        flash='err:schema_invalid',
                        draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            try:
                replace_template_config(
                    data,
                    expected_revision=expected_revision,
                )
            except TemplateConflictError:
                self.send_response_body(
                    409,
                    render_config_editor(
                        host,
                        flash='err:conflict',
                        draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            except (state_store.StateStoreError, OSError):
                self.send_response_body(
                    503,
                    render_config_editor(
                        host,
                        flash='err:save_failed',
                        draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
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
            if (
                ',' in pattern
                or any(ord(ch) < 32 for ch in pattern)
                or len(pattern) > 512
            ):
                self.redirect('/admin/rules?msg=err:invalid_pattern')
                return
            if action not in ('DIRECT', 'REJECT', '🚀 节点选择'):
                self.redirect('/admin/rules?msg=err:invalid_action')
                return
            if extra not in ('', 'no-resolve'):
                self.redirect('/admin/rules?msg=err:invalid_extra')
                return
            rule_str = f'{rule_type},{pattern},{action}'
            if extra:
                rule_str += f',{extra}'
            expected_revision = (
                form.get('template_revision') or ['']
            )[0]
            try:
                add_template_rule(
                    rule_str,
                    expected_revision=expected_revision,
                )
            except TemplateConflictError:
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                self.send_response_body(
                    409,
                    render_rules(host, flash='err:conflict'),
                    'text/html; charset=utf-8',
                )
                return
            except TemplateConfigError:
                self.redirect('/admin/rules?msg=err:load_failed')
                return
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
            expected_revision = (
                form.get('template_revision') or ['']
            )[0]
            expected_rule = (
                form.get('expected_rule') or ['']
            )[0]
            try:
                deleted = delete_template_rule(
                    idx,
                    expected_revision=expected_revision,
                    expected_rule=expected_rule,
                )
            except TemplateConflictError:
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                self.send_response_body(
                    409,
                    render_rules(host, flash='err:conflict'),
                    'text/html; charset=utf-8',
                )
                return
            except TemplateConfigError:
                self.redirect('/admin/rules?msg=err:load_failed')
                return
            if not deleted:
                self.redirect('/admin/rules?msg=err:index_out_of_range')
                return
            self.redirect('/admin/rules?msg=rule_deleted')
            return

        if path == '/admin/rules/raw':
            if not is_logged_in(self):
                self.redirect('/login')
                return
            raw = (form.get('rules_raw') or [''])[0]
            expected_revision = (
                form.get('template_revision') or ['']
            )[0]
            rules = [line.strip() for line in raw.splitlines() if line.strip()]
            if not rules:
                self.redirect('/admin/rules?msg=err:raw_empty')
                return
            if (
                len(rules) > 5000
                or any(not validate_clash_rule(rule) for rule in rules)
            ):
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                self.send_response_body(
                    422,
                    render_rules(
                        host,
                        flash='err:invalid_rule_schema',
                        raw_draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            try:
                replace_template_rules(
                    rules,
                    expected_revision=expected_revision,
                )
            except TemplateConflictError:
                host = configured_public_host(
                    self.headers.get('Host', '127.0.0.1'),
                )
                self.send_response_body(
                    409,
                    render_rules(
                        host,
                        flash='err:conflict',
                        raw_draft=raw,
                        expected_revision=expected_revision,
                    ),
                    'text/html; charset=utf-8',
                )
                return
            except TemplateConfigError:
                self.redirect('/admin/rules?msg=err:load_failed')
                return
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
                expected_revision = (
                    form.get('template_revision') or ['']
                )[0]
                try:
                    applied = apply_rule_pack_to_template(
                        pack,
                        expected_revision=expected_revision,
                    )
                except TemplateConflictError:
                    host = configured_public_host(
                        self.headers.get('Host', '127.0.0.1'),
                    )
                    self.send_response_body(
                        409,
                        render_rules(host, flash='err:conflict'),
                        'text/html; charset=utf-8',
                    )
                    return
                except TemplateConfigError:
                    self.redirect('/admin/rules?msg=err:load_failed')
                    return
                if not applied:
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
    load_meta()
    migrate_plaintext_passwords()
    migrate_admin_password()
    revocation_worker_stop = threading.Event()
    threading.Thread(
        target=_revocation_worker_loop,
        args=(revocation_worker_stop,),
        name='credential-revocation-retry',
        daemon=True,
    ).start()
    srv = BoundedThreadingHTTPServer(LISTEN, Handler)
    srv.serve_forever()
