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
import admin_read_routes
import admin_console_routes
import public_page_routes
import user_panel_routes
import subscription_routes
import admin_config_routes
import admin_operations_routes
import auth_routes
import credential_routes
import landing_write_routes
import rule_pack_routes
import admin_traffic_routes
import admin_account_routes
import admin_user_status_routes
import admin_user_delete_routes
import auth_views
import web_assets
import user_views
import admin_views
import console_shell_views
import operations_views
import configuration_views
import template_store
import session_store
import login_throttle
import billing_service
import authorization_service
import credential_service
import revocation_service
import identity_service
import landing_views
import shared_views
import health_presentation
import user_state_service
import operational_service
import audit_log
import user_panel_data
import cost_calibrator
import cycle as cycle_util
import display as display_config
import health
import health_widgets
import hysteria_update
import http_utils
import incident_console
import landing_egress
import public_views
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
    return (
        tuple(
            map(
                str,
                (
                    USERS_FILE,
                    META_FILE,
                    USAGE_FILE,
                    USAGE_DAILY_FILE,
                ),
            )
        )
        == _LIVE_CORE_STATE_PATHS
    )


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


def _operational_service():
    return operational_service.OperationalService(
        CredentialActionResult=CredentialActionResult,
        DISPLAY_MULTIPLIER_STATE_FILE=DISPLAY_MULTIPLIER_STATE_FILE,
        HY_API_SECRET_FALLBACK=HY_API_SECRET_FALLBACK,
        HY_API_SECRET_FILE=HY_API_SECRET_FILE,
        HY_API_SECRET_PLACEHOLDER=HY_API_SECRET_PLACEHOLDER,
        HY_KICK_MAX_RESPONSE_BYTES=HY_KICK_MAX_RESPONSE_BYTES,
        HY_KICK_TIMEOUT_SECONDS=HY_KICK_TIMEOUT_SECONDS,
        META_FILE=META_FILE,
        MULTIPLIER_AUTO_POLICY_FILE=MULTIPLIER_AUTO_POLICY_FILE,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        USAGE_FILE=USAGE_FILE,
        USERS_FILE=USERS_FILE,
        _using_live_core_state=_using_live_core_state,
        current_display_multiplier=current_display_multiplier,
        load_json=load_json,
        local_now=local_now,
        parse_int_field=parse_int_field,
        summarize_cost_calibration=summarize_cost_calibration,
        secret_provider=get_hy_api_secret,
        restart_provider=restart_subscription_async,
    )


def _normalize_service_action(service, raw):
    return _operational_service()._normalize_service_action(service=service, raw=raw)


def _fail_closed_static_access(reason):
    return _operational_service()._fail_closed_static_access(reason=reason)


def _state_failure_requires_static_stop(exc, *, post_path=''):
    return _operational_service()._state_failure_requires_static_stop(exc=exc, post_path=post_path)


def _static_stop_confirmed(outcomes):
    return _operational_service()._static_stop_confirmed(outcomes=outcomes)


def get_hy_api_secret():
    return _operational_service().get_hy_api_secret()


def hy_kick(usernames):
    return _operational_service().hy_kick(usernames=usernames)


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


def _identity_service():
    return identity_service.IdentityService(
        META_FILE=META_FILE,
        USERS_FILE=USERS_FILE,
        PASSWORD_HASH_MAX_LENGTH=PASSWORD_HASH_MAX_LENGTH,
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        PASSWORD_MIN_LENGTH=PASSWORD_MIN_LENGTH,
        PBKDF2_DIGEST_BYTES=PBKDF2_DIGEST_BYTES,
        PBKDF2_ROUNDS_MAX=PBKDF2_ROUNDS_MAX,
        PBKDF2_ROUNDS_MIN=PBKDF2_ROUNDS_MIN,
        PBKDF2_SALT_BYTES=PBKDF2_SALT_BYTES,
        USER_SESSION_CREDENTIAL_KINDS=USER_SESSION_CREDENTIAL_KINDS,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        USER_SESSION_SUBSCRIPTION_TOKEN=USER_SESSION_SUBSCRIPTION_TOKEN,
        _credential_generation=_credential_generation,
        delete_session=delete_session,
        delete_user_session=delete_user_session,
        get_sessions=get_sessions,
        get_user_sessions=get_user_sessions,
        is_valid_username=is_valid_username,
        load_json=load_json,
        local_now=local_now,
        meta_lock=meta_lock,
        parse_cookies=parse_cookies,
        parse_query_params=parse_query_params,
        save_json=save_json,
        usage_lock=usage_lock,
    )


def _b64url_nopad(data):
    return _identity_service()._b64url_nopad(data=data)


def hash_secret(secret):
    return _identity_service().hash_secret(secret=secret)


def migrate_plaintext_passwords():
    return _identity_service().migrate_plaintext_passwords()


def _write_initial_admin_password(user, password):
    return _identity_service()._write_initial_admin_password(user=user, password=password)


def load_meta():
    return _identity_service().load_meta()


def ensure_meta():
    return _identity_service().ensure_meta()


def migrate_admin_password():
    return _identity_service().migrate_admin_password()


def _change_admin_password(current, new, confirm):
    return _identity_service()._change_admin_password(current=current, new=new, confirm=confirm)


SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX


def _billing_service():
    return billing_service.BillingService(
        load_meta,
        local_now,
        meta_lock,
        save_json,
        load_json,
        current_display_multiplier,
        META_FILE,
        USAGE_DAILY_FILE,
        USAGE_HOURLY_FILE,
        USAGE_PRESERVED_FILE,
    )


def get_settlement_day():
    return _billing_service().get_settlement_day()


def get_cycle_length_days():
    return _billing_service().get_cycle_length_days()


def _settlement_anchor_date(now, settlement_day):
    return _billing_service()._settlement_anchor_date(now=now, settlement_day=settlement_day)


def _update_cycle_meta(day, length=None, *, now=None):
    return _billing_service()._update_cycle_meta(day=day, length=length, now=now)


def get_cycle_anchor_date(now=None):
    return _billing_service().get_cycle_anchor_date(now=now)


def cycle_start_for(now, day=None, length=None, anchor=None):
    return _billing_service().cycle_start_for(now=now, day=day, length=length, anchor=anchor)


def month_key(now=None):
    return _billing_service().month_key(now=now)


def _cycle_days(now):
    return _billing_service()._cycle_days(now=now)


def _zero_cycle_daily_hourly_for(uids, *, now):
    return _billing_service()._zero_cycle_daily_hourly_for(uids=uids, now=now)


def _cycle_preserve_key(now):
    return _billing_service()._cycle_preserve_key(now=now)


def preserved_raw_for_cycle(*, now):
    return _billing_service().preserved_raw_for_cycle(now=now)


def add_preserved_for_user(username, tx, rx, total, *, now):
    return _billing_service().add_preserved_for_user(
        username=username, tx=tx, rx=rx, total=total, now=now
    )


def _cycle_raw_for_user(uid, daily, *, now):
    return _billing_service()._cycle_raw_for_user(uid=uid, daily=daily, now=now)


def _authorization_service():
    return authorization_service.AuthorizationService(
        CYCLE_LENGTH_MAX=CYCLE_LENGTH_MAX,
        CYCLE_LENGTH_MIN=CYCLE_LENGTH_MIN,
        DISPLAY_MULTIPLIER_STATE_FILE=DISPLAY_MULTIPLIER_STATE_FILE,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        USAGE_FILE=USAGE_FILE,
        _CYCLE_USAGE_CACHE_HARD_MAX=_CYCLE_USAGE_CACHE_HARD_MAX,
        _CYCLE_USAGE_CACHE_MIN=_CYCLE_USAGE_CACHE_MIN,
        _cycle_usage_cache=_cycle_usage_cache,
        _cycle_usage_cache_lock=_cycle_usage_cache_lock,
        _using_live_core_state=_using_live_core_state,
        is_valid_username=is_valid_username,
        load_json=load_json,
        load_meta=load_meta,
        local_now=local_now,
    )


def _critical_authorization_state(detail):
    return _authorization_service()._critical_authorization_state(detail=detail)


def _strict_usage_entry_total(entry, *, field):
    return _authorization_service()._strict_usage_entry_total(entry=entry, field=field)


def _cycle_usage_sum_strict(daily, cycle_days, username):
    return _authorization_service()._cycle_usage_sum_strict(
        daily=daily, cycle_days=cycle_days, username=username
    )


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
    return _authorization_service()._cycle_usage_cache_bound(user_count=user_count)


def _prune_cycle_usage_cache(max_entries):
    return _authorization_service()._prune_cycle_usage_cache(max_entries=max_entries)


def _usage_daily_file_version():
    return _authorization_service()._usage_daily_file_version()


def _cached_cycle_usage_sum(daily, cycle_days, username, *, version, max_entries):
    return _authorization_service()._cached_cycle_usage_sum(
        daily=daily,
        cycle_days=cycle_days,
        username=username,
        version=version,
        max_entries=max_entries,
    )


def _validate_authorization_meta(meta):
    return _authorization_service()._validate_authorization_meta(meta=meta)


def _build_static_access_plan(users, daily, meta, *, now=None, usage_version=None):
    return _authorization_service()._build_static_access_plan(
        users=users, daily=daily, meta=meta, now=now, usage_version=usage_version
    )


def _build_landing_access_plan(users, direct_plan, egress_nodes):
    return _authorization_service()._build_landing_access_plan(
        users=users, direct_plan=direct_plan, egress_nodes=egress_nodes
    )


def _sync_static_access_from_users(users, *, now=None):
    return _authorization_service()._sync_static_access_from_users(users=users, now=now)


def usage_for_user(username, usage_month=None, *, daily=None, now=None):
    return _billing_service().usage_for_user(
        username=username, usage_month=usage_month, daily=daily, now=now
    )


def scaled_usage_for_user(username, usage_month=None, *, daily=None, now=None):
    return _billing_service().scaled_usage_for_user(
        username=username, usage_month=usage_month, daily=daily, now=now
    )


def user_total_quota(user_cfg):
    return _billing_service().user_total_quota(user_cfg=user_cfg)


def base_quota_bytes(user_cfg):
    return _billing_service().base_quota_bytes(user_cfg=user_cfg)


def quota_extra_gb(user_cfg):
    return _billing_service().quota_extra_gb(user_cfg=user_cfg)


def user_expiry_state(user_cfg, *, today=None):
    return _billing_service().user_expiry_state(user_cfg=user_cfg, today=today)


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
        _subscription_profile_context(),
        username,
        auth_secret,
        profile=profile,
        generated_at=generated_at,
    )


def subscription_template_mtime():
    return profile_defs.template_mtime_iso(TEMPLATE_FILE)


def pct(used, total):
    if total <= 0:
        return 0.0
    return min(100.0, max(0.0, used * 100.0 / total))


def verify_secret(plain, stored_hash):
    return _identity_service().verify_secret(plain=plain, stored_hash=stored_hash)


# In-memory login failure tracker: {ip: [timestamp, ...]}
# Bounded so an attacker rotating through many source IPs can't grow this
# dict without limit; entries are also dropped when their timestamp list
# decays to empty so cleanly-decayed IPs don't linger as zero-cost ghosts.
_login_failures: dict = {}
_user_login_failures: dict = {}
_login_failures_lock = threading.Lock()
_login_attempts_inflight: dict = {}
_LOGIN_MAX = 3  # max failures
_LOGIN_WINDOW = 3600  # seconds (1 hour)
_LOGIN_FAILURES_MAX_IPS = 1024


def _login_throttle():
    return login_throttle.LoginThrottle(
        _login_failures,
        _login_attempts_inflight,
        _login_failures_lock,
        time.time,
        _LOGIN_MAX,
        _LOGIN_WINDOW,
        _LOGIN_FAILURES_MAX_IPS,
    )


def _prune_failures_locked(ip, failures, now):
    return _login_throttle()._prune_failures_locked(ip=ip, failures=failures, now=now)


def _is_rate_limited(ip, failures=None):
    return _login_throttle()._is_rate_limited(ip=ip, failures=failures)


def _record_failure(ip, failures=None):
    return _login_throttle()._record_failure(ip=ip, failures=failures)


def _begin_login_attempt(ip, failures=None):
    return _login_throttle()._begin_login_attempt(ip=ip, failures=failures)


def _finish_login_attempt(ip, succeeded, failures=None):
    return _login_throttle()._finish_login_attempt(ip=ip, succeeded=succeeded, failures=failures)


def check_user_token(user, token):
    return _identity_service().check_user_token(user=user, token=token)


def _credential_service():
    return credential_service.CredentialService(
        CredentialRotationCommitted=CredentialRotationCommitted,
        REVOCATION_QUEUE_FILE=REVOCATION_QUEUE_FILE,
        ROTATION_RECEIPTS_FILE=ROTATION_RECEIPTS_FILE,
        USAGE_FILE=USAGE_FILE,
        USERS_FILE=USERS_FILE,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        USER_SESSION_SUBSCRIPTION_TOKEN=USER_SESSION_SUBSCRIPTION_TOKEN,
        _credential_generation=_credential_generation,
        _safe_secret_equal=_safe_secret_equal,
        _sync_static_access_from_users=_sync_static_access_from_users,
        get_user_sessions=get_user_sessions,
        load_json=load_json,
        local_now=local_now,
        save_json=save_json,
        usage_lock=usage_lock,
    )


def _visible_rotation_matches(user, new_token, new_uuid):
    return _credential_service()._visible_rotation_matches(
        user=user, new_token=new_token, new_uuid=new_uuid
    )


def _save_users_for_rotation(
    users,
    *,
    user,
    new_token,
    new_uuid,
):
    return _credential_service()._save_users_for_rotation(
        users=users, user=user, new_token=new_token, new_uuid=new_uuid
    )


def _rotate_user_token_if_current(
    user,
    posted,
    *,
    today=None,
    include_config=False,
    new_token=None,
    new_uuid=None,
):
    return _credential_service()._rotate_user_token_if_current(
        user=user,
        posted=posted,
        today=today,
        include_config=include_config,
        new_token=new_token,
        new_uuid=new_uuid,
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
    return _identity_service()._safe_secret_equal(supplied=supplied, expected=expected)


def is_secure_request(handler):
    return http_utils.is_secure_request(handler)


def is_same_origin_post(handler):
    return http_utils.is_same_origin_post(handler)


def session_cookie(sid, *, max_age=SESSION_TTL, secure=False):
    return http_utils.session_cookie(sid, max_age=max_age, secure=secure)


def clear_session_cookie(*, secure=False):
    return http_utils.clear_session_cookie(secure=secure)


def _session_store():
    return session_store.SessionStore(
        load_json,
        save_json,
        state_store.file_lock,
        time.time,
        secrets.token_urlsafe,
        STATE_LOCK_TIMEOUT_SECONDS,
        SESSION_TTL,
        SESSION_MAX_PER_IDENTITY,
        SESSION_MAX_GLOBAL,
    )


def _session_lock_file(path):
    return session_store._session_lock_file(path=path)


def _credential_generation(stored_hash):
    return session_store._credential_generation(stored_hash=stored_hash)


def _alive_sessions(path):
    return _session_store()._alive_sessions(path=path)


def _get_sessions(path):
    return _session_store()._get_sessions(path=path)


def _create_session(
    path,
    username,
    credential_generation='',
    credential_kind='',
):
    return _session_store()._create_session(
        path=path,
        username=username,
        credential_generation=credential_generation,
        credential_kind=credential_kind,
    )


def _delete_session(path, sid):
    return _session_store()._delete_session(path=path, sid=sid)


def _delete_sessions_for(path, username):
    return _session_store()._delete_sessions_for(path=path, username=username)


def _replace_sessions_with_new(
    path,
    username,
    *,
    revoke_all=False,
    credential_generation='',
    credential_kind='',
):
    return _session_store()._replace_sessions_with_new(
        path=path,
        username=username,
        revoke_all=revoke_all,
        credential_generation=credential_generation,
        credential_kind=credential_kind,
    )


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


def _user_state_service():
    return user_state_service.UserStateService(
        USAGE_FILE=USAGE_FILE,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        USAGE_HOURLY_FILE=USAGE_HOURLY_FILE,
        USAGE_PRESERVED_FILE=USAGE_PRESERVED_FILE,
        ONLINE_FILE=ONLINE_FILE,
        DEVICE_ADMISSIONS_FILE=DEVICE_ADMISSIONS_FILE,
        USER_SESSIONS_FILE=USER_SESSIONS_FILE,
        USERS_FILE=USERS_FILE,
        _using_live_core_state=_using_live_core_state,
        load_json=load_json,
        save_json=save_json,
        _delete_sessions_for=_delete_sessions_for,
        usage_lock=usage_lock,
    )


def _scoped_state_path(path):
    return _user_state_service()._scoped_state_path(path=path)


RecoverableRotationResult = credential_service.RecoverableRotationResult


def _rotation_receipts_path():
    return _credential_service()._rotation_receipts_path()


def _revocation_queue_path():
    return _credential_service()._revocation_queue_path()


def _rotation_session_allows(sid, user, posted, cfg):
    return _credential_service()._rotation_session_allows(
        sid=sid, user=user, posted=posted, cfg=cfg
    )


def _recoverable_user_rotation(
    user,
    posted,
    *,
    request_id,
    session_id,
    today=None,
):
    return _credential_service()._recoverable_user_rotation(
        user=user, posted=posted, request_id=request_id, session_id=session_id, today=today
    )


def _revocation_service():
    return revocation_service.RevocationService(
        CredentialActionResult=CredentialActionResult,
        USERS_FILE=USERS_FILE,
        _DELETE_TARGET_GENERATION=_DELETE_TARGET_GENERATION,
        _WORKER_ERROR_LOG_INITIAL_SECONDS=_WORKER_ERROR_LOG_INITIAL_SECONDS,
        _WORKER_ERROR_LOG_LOCK=_WORKER_ERROR_LOG_LOCK,
        _WORKER_ERROR_LOG_MAX_SECONDS=_WORKER_ERROR_LOG_MAX_SECONDS,
        _WORKER_ERROR_LOG_STATE=_WORKER_ERROR_LOG_STATE,
        _credential_generation=_credential_generation,
        _delete_previous_generation=_delete_previous_generation,
        _fail_closed_static_access=_fail_closed_static_access,
        _is_delete_revocation_task=_is_delete_revocation_task,
        _normalize_service_action=_normalize_service_action,
        _purge_user_history_locked=_purge_user_history_locked,
        _revocation_queue_path=_revocation_queue_path,
        _rotation_receipts_path=_rotation_receipts_path,
        _state_failure_requires_static_stop=_state_failure_requires_static_stop,
        _sync_static_access_from_users=_sync_static_access_from_users,
        _using_live_core_state=_using_live_core_state,
        hy_kick=hy_kick,
        load_json=load_json,
        save_json=save_json,
        usage_lock=usage_lock,
    )


def _action_succeeded(result):
    return _revocation_service()._action_succeeded(result=result)


def _schedule_static_reload(service, *, changed):
    return _revocation_service()._schedule_static_reload(service=service, changed=changed)


def _record_static_retry(task_id, services):
    return _revocation_service()._record_static_retry(task_id=task_id, services=services)


def _record_kick_attempt(
    task_id,
    result,
    *,
    completed_static_services=(),
):
    return _revocation_service()._record_kick_attempt(
        task_id=task_id, result=result, completed_static_services=completed_static_services
    )


def _attempt_revocation_side_effects(
    task_id,
    username,
    *,
    xray_changed=False,
    tuic_changed=False,
    sync_error=None,
):
    return _revocation_service()._attempt_revocation_side_effects(
        task_id=task_id,
        username=username,
        xray_changed=xray_changed,
        tuic_changed=tuic_changed,
        sync_error=sync_error,
    )


def _process_one_revocation_task():
    return _revocation_service()._process_one_revocation_task()


def _prune_expired_rotation_receipts():
    return _revocation_service()._prune_expired_rotation_receipts()


def _reset_worker_error_log(category):
    return _revocation_service()._reset_worker_error_log(category=category)


def _log_worker_error_throttled(category, exc, message):
    return _revocation_service()._log_worker_error_throttled(
        category=category, exc=exc, message=message
    )


def _revocation_worker_loop(stop_event):
    return _revocation_service()._revocation_worker_loop(stop_event=stop_event)


def _clear_alert_dedup_for_users(usernames, *, quota_only):
    return _user_state_service()._clear_alert_dedup_for_users(
        usernames=usernames, quota_only=quota_only
    )


def _purge_user_history_locked(username):
    return _user_state_service()._purge_user_history_locked(username=username)


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
    return _identity_service().get_logged_in_user_context(handler=handler)


def get_logged_in_user(handler):
    return _identity_service().get_logged_in_user(handler=handler)


def user_panel_access_error(
    cfg,
    session_kind,
    *,
    today=None,
):
    return _identity_service().user_panel_access_error(
        cfg=cfg, session_kind=session_kind, today=today
    )


def is_logged_in(handler):
    return _identity_service().is_logged_in(handler=handler)


def render_panel_link_required():
    return console_shell_views.render_panel_link_required(
        _console_shell_views_context(),
    )


def _shared_views_context():
    return shared_views.Context(
        BASE_CSS_ETAG=BASE_CSS_ETAG,
        CYCLE_LENGTH_MAX=CYCLE_LENGTH_MAX,
        CYCLE_LENGTH_MIN=CYCLE_LENGTH_MIN,
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        SUBSCRIPTION_PROFILES=SUBSCRIPTION_PROFILES,
        SUBSCRIPTION_PROFILE_ORDER=SUBSCRIPTION_PROFILE_ORDER,
        _ICONS=_ICONS,
        subscription_profile_qr_path=subscription_profile_qr_path,
        subscription_profile_url=subscription_profile_url,
    )


def html_page(title, body, body_class=''):
    return shared_views.html_page(
        _shared_views_context(), title=title, body=body, body_class=body_class
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
    return shared_views.icon(_shared_views_context(), name=name)


def render_alert(msg, kind='flash', *, element_id=''):
    return shared_views.render_alert(
        _shared_views_context(), msg=msg, kind=kind, element_id=element_id
    )


def render_prefixed_alert(flash, msg_map):
    return shared_views.render_prefixed_alert(_shared_views_context(), flash=flash, msg_map=msg_map)


def back_to_admin(label='返回管理后台'):
    return shared_views.back_to_admin(_shared_views_context(), label=label)


def render_logout_confirmation(host, *, user_panel=False):
    return console_shell_views.render_logout_confirmation(
        _console_shell_views_context(),
        host=host,
        user_panel=user_panel,
    )


_SIDEBAR_NAV = [
    ('dashboard', '/admin', '总览', 'dashboard'),
    ('usage', '/admin/usage', '流量分析', 'traffic'),
    ('incidents', '/admin/incidents', '事故处理', 'pulse'),
    ('health', '/admin/health', '健康状态', 'pulse'),
    ('config', '/admin/config', '模板配置', 'config'),
    ('rules', '/admin/rules', '路由规则', 'rules'),
    ('landing-egresses', '/admin/landing-egresses', '家宽出口', 'rules'),
    ('logs', '/admin/logs', '清零日志', 'logs'),
    ('settings', '/admin/settings', '设置', 'lock'),
]


def _landing_registry_or_empty():
    return _user_state_service()._landing_registry_or_empty()


def _authorized_landing_nodes(cfg, registry=None):
    return _user_state_service()._authorized_landing_nodes(cfg=cfg, registry=registry)


def _enabled_landing_public_nodes(registry=None):
    return _user_state_service()._enabled_landing_public_nodes(registry=registry)


def _ensure_landing_vless_uuid(cfg, users):
    return _user_state_service()._ensure_landing_vless_uuid(cfg=cfg, users=users)


def _landing_views_context():
    return landing_views.Context(
        USERS_FILE=USERS_FILE,
        _authorized_landing_nodes=_authorized_landing_nodes,
        _landing_registry_or_empty=_landing_registry_or_empty,
        content_revision=content_revision,
        load_json=load_json,
        render_admin_shell=render_admin_shell,
        render_alert=render_alert,
        user_config_revision=user_config_revision,
    )


def render_landing_egress_selector(cfg, *, password_session):
    return landing_views.render_landing_egress_selector(
        _landing_views_context(), cfg=cfg, password_session=password_session
    )


def render_landing_egresses(host, flash=''):
    return landing_views.render_landing_egresses(_landing_views_context(), host=host, flash=flash)


def render_admin_shell(active, page_title, content, *, badge='', subtitle='', topbar_extra=''):
    return console_shell_views.render_admin_shell(
        _console_shell_views_context(),
        active=active,
        page_title=page_title,
        content=content,
        badge=badge,
        subtitle=subtitle,
        topbar_extra=topbar_extra,
    )


def flash_text(msg):
    return shared_views.flash_text(_shared_views_context(), msg=msg)


def render_home(host):
    return public_views.render_home(
        html_page=html_page,
        asset_version=HOME_JS_ETAG.strip('"'),
    )


def render_login(host, msg='', msg_kind='err', active_tab='admin', username=''):
    return auth_views.render_login(
        html_page=html_page,
        render_alert=render_alert,
        icon=icon,
        password_max_length=PASSWORD_MAX_LENGTH,
        msg=msg,
        msg_kind=msg_kind,
        active_tab=active_tab,
        username=username,
    )


def render_user_login(host, msg='', username=''):
    return auth_views.render_user_login(
        html_page=html_page,
        render_alert=render_alert,
        password_max_length=PASSWORD_MAX_LENGTH,
        msg=msg,
        username=username,
    )


def render_user_change_password(host, user, msg=''):
    return user_views.render_user_change_password(
        _user_views_context(),
        host=host,
        user=user,
        msg=msg,
    )


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
    return shared_views.render_subscription_profile_links(
        _shared_views_context(), base_url=base_url, user=user, token=token
    )


def _user_panel_data_context():
    return user_panel_data.Context(
        local_now=local_now,
        get_cycle_length_days=get_cycle_length_days,
        cycle_start_for=cycle_start_for,
        load_json=load_json,
        scaled_usage_for_user=scaled_usage_for_user,
        user_total_quota=user_total_quota,
        configured_max_devices=configured_max_devices,
        pct=pct,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        ONLINE_FILE=ONLINE_FILE,
    )


def _cycle_reset_info(now=None):
    return user_panel_data._cycle_reset_info(_user_panel_data_context(), now=now)


def _build_panel_json_payload(user, cfg, *, now=None):
    return user_panel_data._build_panel_json_payload(
        _user_panel_data_context(), user=user, cfg=cfg, now=now
    )


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
    return user_views.render_user_panel(
        _user_views_context(),
        host=host,
        base_url=base_url,
        user=user,
        token=token,
        cfg=cfg,
        session_auth=session_auth,
        session_kind=session_kind,
        notice=notice,
    )


def row_form(user, cfg, online, host, base_url, usage_month=None, daily=None, now=None):
    return admin_views.row_form(
        _admin_views_context(),
        user=user,
        cfg=cfg,
        online=online,
        host=host,
        base_url=base_url,
        usage_month=usage_month,
        daily=daily,
        now=now,
    )


def render_admin(host, base_url, flash='', *, create_draft=None, create_error_field=''):
    return admin_views.render_admin(
        _admin_views_context(),
        host=host,
        base_url=base_url,
        flash=flash,
        create_draft=create_draft,
        create_error_field=create_error_field,
    )


def _action_label(action):
    return shared_views._action_label(_shared_views_context(), action=action)


DAILY_RETENTION_DAYS = 30
LOCAL_TZ_LABEL = 'Asia/Shanghai · 滚动 7 天小时 / 30 天每日'


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
        ctx,
        username,
        cfg,
        online=online,
        daily=daily,
        now=now,
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
        _usage_context(),
        now=now,
        include_charts=include_charts,
    )


def _build_user_json_payload(uid, *, now, include_charts=True):
    return usage_dashboard.build_user_json_payload(
        _usage_context(),
        uid,
        now=now,
        include_charts=include_charts,
    )


def daily_window_for_user(uid, daily, *, days=30, today=None):
    return usage_dashboard.daily_window_for_user(
        _usage_context(),
        uid,
        daily,
        days=days,
        today=today,
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


def _health_presentation():
    return health_presentation.HealthPresentation(
        ONLINE_FILE=ONLINE_FILE,
        load_json=load_json,
        probe_systemd=probe_systemd,
        probe_certbot_renewal=probe_certbot_renewal,
        probe_cert=probe_cert,
        probe_panel_tls=probe_panel_tls,
        probe_disk=probe_disk,
        probe_cron_heartbeat=probe_cron_heartbeat,
        probe_auth_readiness=probe_auth_readiness,
        probe_online=probe_online,
        probe_xray_config_permissions=probe_xray_config_permissions,
        probe_hysteria_update=probe_hysteria_update,
        probe_recent_backup=probe_recent_backup,
        _health_card=_health_card,
    )


def _render_health_top_kpis():
    return _health_presentation()._render_health_top_kpis()


def _probe_overall_status():
    return _health_presentation()._probe_overall_status()


def _probe_online_services():
    return _health_presentation()._probe_online_services()


def _probe_https_cert():
    return _health_presentation()._probe_https_cert()


def _probe_disk_kpi():
    return _health_presentation()._probe_disk_kpi()


def _health_top_kpi_card(title, probe_result, is_text=False):
    return _health_presentation()._health_top_kpi_card(
        title=title, probe_result=probe_result, is_text=is_text
    )


def _render_health_cards():
    return _health_presentation()._render_health_cards()


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
    return _operational_service()._fire_test_alert(cfg=cfg, actor=actor)


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
    return operations_views.render_health(
        _operations_views_context(),
        host=host,
        flash=flash,
    )


def render_health_fragment():
    return _render_health_cards()


def restart_subscription_async():
    return _operational_service().restart_subscription_async()


def apply_suggested_display_multiplier(*, actor='admin', now=None):
    return _operational_service().apply_suggested_display_multiplier(actor=actor, now=now)


def save_multiplier_auto_policy_from_form(form):
    return _operational_service().save_multiplier_auto_policy_from_form(form=form)


_SETTINGS_FLASH = {
    'password changed': '管理员密码已更新',
    'password_wrong': '当前密码不正确',
    'password_mismatch': '两次输入的新密码不一致',
    'password_short': '新密码至少 8 位',
    'password_long': f'密码不能超过 {PASSWORD_MAX_LENGTH} 位',
}


def render_settings(host, flash=''):
    return operations_views.render_settings(
        _operations_views_context(),
        host=host,
        flash=flash,
    )


def render_reset_logs(host, limit=300):
    return operations_views.render_reset_logs(
        _operations_views_context(),
        host=host,
        limit=limit,
    )


def _template_store():
    return template_store.TemplateStore(
        TEMPLATE_FILE,
        template_lock,
        save_text_atomic,
        profile_defs.apply_rule_pack_to_clash_config,
    )


def _load_yaml_file(path):
    return template_store._load_yaml_file(path=path)


def _dump_yaml(data):
    return template_store._dump_yaml(data=data)


TemplateConfigError = template_store.TemplateConfigError


TemplateConflictError = template_store.TemplateConflictError


def _template_bytes_unlocked():
    return _template_store()._template_bytes_unlocked()


def _template_revision_unlocked():
    return _template_store()._template_revision_unlocked()


def _validate_template_revision_unlocked(expected_revision):
    return _template_store()._validate_template_revision_unlocked(
        expected_revision=expected_revision
    )


def load_template_config():
    return _template_store().load_template_config()


def load_template_config_snapshot():
    return _template_store().load_template_config_snapshot()


def save_template_config(data):
    return _template_store().save_template_config(data=data)


def replace_template_config(data, expected_revision=None):
    return _template_store().replace_template_config(data=data, expected_revision=expected_revision)


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
    return template_store.validate_template_config(data=data)


def validate_clash_rule(rule):
    return template_store.validate_clash_rule(rule=rule)


def render_config_editor(
    host,
    flash='',
    *,
    draft=None,
    expected_revision=None,
):
    return configuration_views.render_config_editor(
        _configuration_views_context(),
        host=host,
        flash=flash,
        draft=draft,
        expected_revision=expected_revision,
    )


def load_template_rules():
    return _template_store().load_template_rules()


def load_template_rules_snapshot():
    return _template_store().load_template_rules_snapshot()


def save_template_rules(rules):
    return _template_store().save_template_rules(rules=rules)


def add_template_rule(rule_str, expected_revision=None):
    return _template_store().add_template_rule(
        rule_str=rule_str, expected_revision=expected_revision
    )


def delete_template_rule(index, expected_revision=None, expected_rule=None):
    return _template_store().delete_template_rule(
        index=index, expected_revision=expected_revision, expected_rule=expected_rule
    )


def replace_template_rules(rules, expected_revision=None):
    return _template_store().replace_template_rules(
        rules=rules, expected_revision=expected_revision
    )


def apply_rule_pack_to_template(pack_key, expected_revision=None):
    return _template_store().apply_rule_pack_to_template(
        pack_key=pack_key, expected_revision=expected_revision
    )


def apply_rule_pack_to_user(username, pack_key):
    return _user_state_service().apply_rule_pack_to_user(username=username, pack_key=pack_key)


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
    'DOMAIN-SUFFIX': '域名后缀',
    'DOMAIN-KEYWORD': '域名关键词',
    'DOMAIN': '完整域名',
    'IP-CIDR': 'IP 段',
    'IP-CIDR6': 'IPv6 段',
    'GEOIP': 'GeoIP',
    'RULE-SET': '规则集',
    'MATCH': '兜底',
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
    return configuration_views.render_rules(
        _configuration_views_context(),
        host=host,
        flash=flash,
        raw_draft=raw_draft,
        expected_revision=expected_revision,
    )


def _handle_legacy_daily_redirect(handler):
    """Permanent redirect from old /admin/daily to /admin/usage."""
    handler.redirect('/admin/usage', status=301)


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
            b'\r\n' + body
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


def _build_health_read_snapshot():
    return {
        'rows': render_health_fragment(),
        'kpis': ''.join(
            _health_top_kpi_card(title, result, is_text=(title == '整体状态'))
            for title, result in _render_health_top_kpis().items()
        ),
        'update': hysteria_update.render_history(),
    }


def _admin_account_routes_context():
    return admin_account_routes.Context(
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        USERS_FILE=USERS_FILE,
        _ensure_landing_vless_uuid=_ensure_landing_vless_uuid,
        _landing_registry_or_empty=_landing_registry_or_empty,
        _sync_static_access_from_users=_sync_static_access_from_users,
        configured_public_host=configured_public_host,
        delete_user_sessions_for=delete_user_sessions_for,
        hash_secret=hash_secret,
        is_logged_in=is_logged_in,
        is_valid_username=is_valid_username,
        load_json=load_json,
        parse_bounded_int_field=parse_bounded_int_field,
        parse_date_field=parse_date_field,
        parse_note_field=parse_note_field,
        render_admin=render_admin,
        revision_matches=revision_matches,
        safe_base_url=safe_base_url,
        save_json=save_json,
        usage_lock=usage_lock,
    )


def _admin_user_status_routes_context():
    return admin_user_status_routes.Context(
        USERS_FILE=USERS_FILE,
        _build_overview_user=_build_overview_user,
        _json_request=_json_request,
        _static_reload_status=_static_reload_status,
        _sync_static_access_from_users=_sync_static_access_from_users,
        delete_user_sessions_for=delete_user_sessions_for,
        hy_kick=hy_kick,
        is_logged_in=is_logged_in,
        load_json=load_json,
        local_now=local_now,
        parse_int_field=parse_int_field,
        revision_matches=revision_matches,
        safe_admin_next=safe_admin_next,
        save_json=save_json,
        usage_lock=usage_lock,
        with_flash=with_flash,
    )


def _admin_user_delete_routes_context():
    return admin_user_delete_routes.Context(
        USERS_FILE=USERS_FILE,
        _DELETE_TARGET_GENERATION=_DELETE_TARGET_GENERATION,
        _attempt_revocation_side_effects=_attempt_revocation_side_effects,
        _delete_previous_generation=_delete_previous_generation,
        _json_request=_json_request,
        _purge_user_history_locked=_purge_user_history_locked,
        _revocation_queue_path=_revocation_queue_path,
        _static_reload_status=_static_reload_status,
        _sync_static_access_from_users=_sync_static_access_from_users,
        is_logged_in=is_logged_in,
        load_json=load_json,
        revision_matches=revision_matches,
        save_json=save_json,
        usage_lock=usage_lock,
        with_flash=with_flash,
    )


def _admin_traffic_context():
    return admin_traffic_routes.Context(
        CYCLE_LENGTH_MAX=CYCLE_LENGTH_MAX,
        CYCLE_LENGTH_MIN=CYCLE_LENGTH_MIN,
        USAGE_FILE=USAGE_FILE,
        USERS_FILE=USERS_FILE,
        _build_overview_json_payload=_build_overview_json_payload,
        _build_overview_user=_build_overview_user,
        _clear_alert_dedup_for_users=_clear_alert_dedup_for_users,
        _json_request=_json_request,
        _static_reload_status=_static_reload_status,
        _sync_static_access_from_users=_sync_static_access_from_users,
        _update_cycle_meta=_update_cycle_meta,
        _zero_cycle_daily_hourly_for=_zero_cycle_daily_hourly_for,
        add_preserved_for_user=add_preserved_for_user,
        is_logged_in=is_logged_in,
        load_json=load_json,
        local_now=local_now,
        month_key=month_key,
        revision_matches=revision_matches,
        save_json=save_json,
        usage_for_user=usage_for_user,
        usage_lock=usage_lock,
    )


def _landing_write_routes_context():
    return landing_write_routes.Context(
        USERS_FILE=USERS_FILE,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        _ensure_landing_vless_uuid=_ensure_landing_vless_uuid,
        _landing_registry_or_empty=_landing_registry_or_empty,
        _sync_static_access_from_users=_sync_static_access_from_users,
        content_revision=content_revision,
        get_logged_in_user_context=get_logged_in_user_context,
        is_logged_in=is_logged_in,
        load_json=load_json,
        local_now=local_now,
        revision_matches=revision_matches,
        save_json=save_json,
        usage_lock=usage_lock,
        user_panel_access_error=user_panel_access_error,
    )


def _rule_pack_routes_context():
    return rule_pack_routes.Context(
        RULE_PACKS=RULE_PACKS,
        TemplateConfigError=TemplateConfigError,
        TemplateConflictError=TemplateConflictError,
        apply_rule_pack_to_template=apply_rule_pack_to_template,
        apply_rule_pack_to_user=apply_rule_pack_to_user,
        configured_public_host=configured_public_host,
        is_logged_in=is_logged_in,
        render_rules=render_rules,
    )


def _credential_routes_context():
    return credential_routes.Context(
        CredentialRotationCommitted=CredentialRotationCommitted,
        USERS_FILE=USERS_FILE,
        USER_SESSION_SUBSCRIPTION_TOKEN=USER_SESSION_SUBSCRIPTION_TOKEN,
        _action_succeeded=_action_succeeded,
        _build_overview_user=_build_overview_user,
        _credential_generation=_credential_generation,
        _fail_closed_static_access=_fail_closed_static_access,
        _json_request=_json_request,
        _normalize_service_action=_normalize_service_action,
        _record_kick_attempt=_record_kick_attempt,
        _record_static_retry=_record_static_retry,
        _recoverable_user_rotation=_recoverable_user_rotation,
        _revocation_queue_path=_revocation_queue_path,
        _rotation_receipts_path=_rotation_receipts_path,
        _save_users_for_rotation=_save_users_for_rotation,
        _schedule_static_reload=_schedule_static_reload,
        _static_reload_status=_static_reload_status,
        _sync_static_access_from_users=_sync_static_access_from_users,
        _using_live_core_state=_using_live_core_state,
        configured_public_host=configured_public_host,
        create_user_session=create_user_session,
        html_page=html_page,
        hy_kick=hy_kick,
        is_logged_in=is_logged_in,
        is_secure_request=is_secure_request,
        load_json=load_json,
        local_now=local_now,
        parse_cookies=parse_cookies,
        render_user_panel=render_user_panel,
        revision_matches=revision_matches,
        safe_admin_next=safe_admin_next,
        safe_base_url=safe_base_url,
        usage_lock=usage_lock,
        user_session_cookie=user_session_cookie,
        with_flash=with_flash,
    )


def _auth_routes_context():
    return auth_routes.Context(
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        PASSWORD_MIN_LENGTH=PASSWORD_MIN_LENGTH,
        SESSIONS_FILE=SESSIONS_FILE,
        USERS_FILE=USERS_FILE,
        USER_SESSIONS_FILE=USER_SESSIONS_FILE,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        _LOGIN_WINDOW=_LOGIN_WINDOW,
        _begin_login_attempt=_begin_login_attempt,
        _change_admin_password=_change_admin_password,
        _credential_generation=_credential_generation,
        _finish_login_attempt=_finish_login_attempt,
        _replace_sessions_with_new=_replace_sessions_with_new,
        _user_login_failures=_user_login_failures,
        clear_session_cookie=clear_session_cookie,
        clear_user_session_cookie=clear_user_session_cookie,
        configured_public_host=configured_public_host,
        create_session=create_session,
        create_user_session=create_user_session,
        delete_session=delete_session,
        delete_user_session=delete_user_session,
        get_logged_in_user_context=get_logged_in_user_context,
        hash_secret=hash_secret,
        is_logged_in=is_logged_in,
        is_secure_request=is_secure_request,
        is_valid_username=is_valid_username,
        load_json=load_json,
        local_now=local_now,
        parse_cookies=parse_cookies,
        render_login=render_login,
        save_json=save_json,
        session_cookie=session_cookie,
        usage_lock=usage_lock,
        user_panel_access_error=user_panel_access_error,
        user_session_cookie=user_session_cookie,
        verify_secret=verify_secret,
    )


def _admin_operations_context():
    return admin_operations_routes.OperationsContext(
        is_logged_in=is_logged_in,
        load_alert_config=alerts.load_config,
        fire_test_alert=_fire_test_alert,
        json_request=_json_request,
        check_update=hysteria_update.check_and_record,
        schedule_update=hysteria_update.schedule_apply_async,
        update_public_status=hysteria_update.public_status,
        apply_multiplier=apply_suggested_display_multiplier,
        save_auto_policy=save_multiplier_auto_policy_from_form,
    )


def _admin_config_context():
    return admin_config_routes.ConfigContext(
        is_logged_in=is_logged_in,
        configured_public_host=configured_public_host,
        render_config_editor=render_config_editor,
        validate_template_config=validate_template_config,
        replace_template_config=replace_template_config,
        render_rules=render_rules,
        add_template_rule=add_template_rule,
        delete_template_rule=delete_template_rule,
        validate_clash_rule=validate_clash_rule,
        replace_template_rules=replace_template_rules,
        TemplateConflictError=TemplateConflictError,
        TemplateConfigError=TemplateConfigError,
    )


def _public_page_routes_context():
    return public_page_routes.Context(
        get_logged_in_user=get_logged_in_user,
        is_logged_in=is_logged_in,
        render_home=render_home,
        render_login=render_login,
        render_logout_confirmation=render_logout_confirmation,
    )


def _user_panel_routes_context():
    return user_panel_routes.Context(
        USERS_FILE=USERS_FILE,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        _build_panel_json_payload=_build_panel_json_payload,
        clear_user_session_cookie=clear_user_session_cookie,
        get_logged_in_user_context=get_logged_in_user_context,
        is_secure_request=is_secure_request,
        load_json=load_json,
        local_now=local_now,
        render_panel_link_required=render_panel_link_required,
        render_user_change_password=render_user_change_password,
        render_user_panel=render_user_panel,
        user_panel_access_error=user_panel_access_error,
    )


def _subscription_routes_context():
    return subscription_routes.Context(
        USER_SESSION_SUBSCRIPTION_TOKEN=USER_SESSION_SUBSCRIPTION_TOKEN,
        _build_panel_json_payload=_build_panel_json_payload,
        _credential_generation=_credential_generation,
        build_yaml=build_yaml,
        check_user_token=check_user_token,
        create_user_session=create_user_session,
        is_secure_request=is_secure_request,
        local_now=local_now,
        normalize_subscription_profile=normalize_subscription_profile,
        render_profile_qr_svg=render_profile_qr_svg,
        render_user_panel=render_user_panel,
        scaled_usage_for_user=scaled_usage_for_user,
        subscription_template_mtime=subscription_template_mtime,
        user_session_cookie=user_session_cookie,
        user_total_quota=user_total_quota,
    )


def _admin_console_context():
    return admin_console_routes.Context(
        _build_overview_json_payload=_build_overview_json_payload,
        _build_user_json_payload=_build_user_json_payload,
        _handle_legacy_daily_redirect=_handle_legacy_daily_redirect,
        _static_reload_status=_static_reload_status,
        back_to_admin=back_to_admin,
        build_incident_payload=build_incident_payload,
        is_logged_in=is_logged_in,
        local_now=local_now,
        render_admin=render_admin,
        render_admin_shell=render_admin_shell,
        render_config_editor=render_config_editor,
        render_incidents=render_incidents,
        render_landing_egresses=render_landing_egresses,
        render_reset_logs=render_reset_logs,
        render_rules=render_rules,
        render_settings=render_settings,
        render_user_detail_page=render_user_detail_page,
    )


def _user_views_context():
    return user_views.Context(
        ONLINE_FILE=ONLINE_FILE,
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        PASSWORD_MIN_LENGTH=PASSWORD_MIN_LENGTH,
        SUBSCRIPTION_PROFILES=SUBSCRIPTION_PROFILES,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        USER_SESSION_PANEL_PASSWORD=USER_SESSION_PANEL_PASSWORD,
        _cycle_reset_info=_cycle_reset_info,
        configured_max_devices=configured_max_devices,
        daily_window_for_user=daily_window_for_user,
        fmt_bytes=fmt_bytes,
        html_page=html_page,
        icon=icon,
        load_json=load_json,
        local_now=local_now,
        pct=pct,
        render_alert=render_alert,
        render_landing_egress_selector=render_landing_egress_selector,
        render_subscription_profile_links=render_subscription_profile_links,
        scaled_usage_for_user=scaled_usage_for_user,
        sparkline_svg=sparkline_svg,
        user_expiry_state=user_expiry_state,
        user_total_quota=user_total_quota,
    )


def _admin_views_context():
    return admin_views.Context(
        ADMIN_POLL_JS_ETAG=ADMIN_POLL_JS_ETAG,
        CYCLE_LENGTH_MAX=CYCLE_LENGTH_MAX,
        CYCLE_LENGTH_MIN=CYCLE_LENGTH_MIN,
        ONLINE_FILE=ONLINE_FILE,
        USAGE_DAILY_FILE=USAGE_DAILY_FILE,
        USERS_FILE=USERS_FILE,
        _enabled_landing_public_nodes=_enabled_landing_public_nodes,
        _landing_registry_or_empty=_landing_registry_or_empty,
        base_quota_bytes=base_quota_bytes,
        configured_max_devices=configured_max_devices,
        current_display_multiplier=current_display_multiplier,
        cycle_start_for=cycle_start_for,
        daily_window_for_user=daily_window_for_user,
        flash_text=flash_text,
        fmt_bytes=fmt_bytes,
        get_cycle_length_days=get_cycle_length_days,
        get_settlement_day=get_settlement_day,
        icon=icon,
        load_json=load_json,
        local_now=local_now,
        month_key=month_key,
        pct=pct,
        preserved_raw_for_cycle=preserved_raw_for_cycle,
        quota_extra_gb=quota_extra_gb,
        render_admin_shell=render_admin_shell,
        render_alert=render_alert,
        row_form=row_form,
        scaled_usage_for_user=scaled_usage_for_user,
        sparkline_svg=sparkline_svg,
        user_config_revision=user_config_revision,
        user_expiry_state=user_expiry_state,
        user_total_quota=user_total_quota,
    )


def _console_shell_views_context():
    return console_shell_views.Context(
        _SIDEBAR_NAV=_SIDEBAR_NAV,
        html_page=html_page,
        icon=icon,
    )


def _operations_views_context():
    return operations_views.Context(
        ADMIN_POLL_JS_ETAG=ADMIN_POLL_JS_ETAG,
        PASSWORD_MAX_LENGTH=PASSWORD_MAX_LENGTH,
        PASSWORD_MIN_LENGTH=PASSWORD_MIN_LENGTH,
        RESET_LOG_FILE=RESET_LOG_FILE,
        _HEALTH_FLASH=_HEALTH_FLASH,
        _SETTINGS_FLASH=_SETTINGS_FLASH,
        _action_label=_action_label,
        _health_card=_health_card,
        _health_top_kpi_card=_health_top_kpi_card,
        _render_health_cards=_render_health_cards,
        _render_health_top_kpis=_render_health_top_kpis,
        fmt_bytes=fmt_bytes,
        load_meta=load_meta,
        probe_cert=probe_cert,
        probe_certbot_renewal=probe_certbot_renewal,
        probe_hysteria_update=probe_hysteria_update,
        probe_online=probe_online,
        probe_panel_tls=probe_panel_tls,
        probe_recent_backup=probe_recent_backup,
        probe_xray_config_permissions=probe_xray_config_permissions,
        render_admin_shell=render_admin_shell,
        render_cost_calibrator=render_cost_calibrator,
        render_line_radar=render_line_radar,
        render_prefixed_alert=render_prefixed_alert,
    )


def _configuration_views_context():
    return configuration_views.Context(
        RULE_PACKS=RULE_PACKS,
        RULE_PACK_ORDER=RULE_PACK_ORDER,
        TEMPLATE_FILE=TEMPLATE_FILE,
        TemplateConfigError=TemplateConfigError,
        USERS_FILE=USERS_FILE,
        _ACTION_LABELS=_ACTION_LABELS,
        _CONFIG_FLASH=_CONFIG_FLASH,
        _RULES_FLASH=_RULES_FLASH,
        _RULE_TYPE_LABELS=_RULE_TYPE_LABELS,
        _parse_clash_rule=_parse_clash_rule,
        _template_revision_unlocked=_template_revision_unlocked,
        load_json=load_json,
        load_template_config_snapshot=load_template_config_snapshot,
        load_template_rules_snapshot=load_template_rules_snapshot,
        render_admin_shell=render_admin_shell,
        render_alert=render_alert,
        render_prefixed_alert=render_prefixed_alert,
        template_lock=template_lock,
    )


def _admin_read_context():
    return admin_read_routes.ReadContext(
        is_logged_in=is_logged_in,
        local_now=local_now,
        render_usage_page=render_usage_page,
        render_health=render_health,
        render_daily_table=_render_daily_table_collapsed,
        build_analytics=_build_analytics_json_payload,
        build_usage=_build_usage_json_payload,
        build_usage_csv=_build_usage_csv,
        render_health_fragment=render_health_fragment,
        build_health_snapshot=_build_health_read_snapshot,
    )


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

    def send_response_body(
        self, code, body, ctype='text/plain; charset=utf-8', send_body=True, extra_headers=None
    ):
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

    def _serve_static(
        self, payload_bytes, etag, ctype, send_payload, cache_control='public, max-age=86400'
    ):
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
        host = configured_public_host(self.headers.get('Host', '127.0.0.1'))
        content = shared_views.render_user_state_conflict(
            target,
            host,
            draft,
            render_admin_shell=render_admin_shell,
        )
        self.send_response_body(409, content, 'text/html; charset=utf-8')

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
                401,
                {'ok': False, 'reason': 'login_required'},
            )
        else:
            self.redirect('/login')

    def _mutation_user_not_found(self, username, next_to):
        if _json_request(self):
            self._send_mutation_json(
                404,
                {'ok': False, 'reason': 'user_not_found', 'username': username},
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
        audit_log.append_reset_log(
            RESET_LOG_FILE,
            actor,
            action,
            target,
            before,
            after,
            client_ip=http_utils.request_client_ip(self),
            month=month_key,
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
                204,
                '',
                'text/plain; charset=utf-8',
                send_payload,
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

        if send_payload and is_admin_ui_document(path):
            supplied_admin_token = (q.get('token') or [''])[0]
            if supplied_admin_token:
                meta = load_meta()
                expected_admin_token = str(meta.get('admin_token') or '')
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
                BASE_CSS_BYTES,
                BASE_CSS_ETAG,
                'text/css; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, BASE_CSS_ETAG),
            )
            return

        if path == '/static/admin-poll.js':
            self._serve_static(
                ADMIN_POLL_JS_BYTES,
                ADMIN_POLL_JS_ETAG,
                'application/javascript; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, ADMIN_POLL_JS_ETAG),
            )
            return

        if path == '/static/usage.js':
            self._serve_static(
                USAGE_JS_BYTES,
                USAGE_JS_ETAG,
                'application/javascript; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, USAGE_JS_ETAG),
            )
            return

        if path == '/static/home.js':
            self._serve_static(
                HOME_JS_BYTES,
                HOME_JS_ETAG,
                'application/javascript; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, HOME_JS_ETAG),
            )
            return

        page_asset = web_assets.ASSETS.get(path)
        if page_asset is not None:
            payload, etag = page_asset
            self._serve_static(
                payload,
                etag,
                'application/javascript; charset=utf-8',
                send_payload,
                cache_control=_static_asset_cache_control(q, etag),
            )
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

        if public_page_routes.handle_read(
            self,
            _public_page_routes_context(),
            path=path,
            query=q,
            host=host,
            base_url=base_url,
            send_payload=send_payload,
        ):
            return

        if user_panel_routes.handle_read(
            self,
            _user_panel_routes_context(),
            path=path,
            query=q,
            host=host,
            base_url=base_url,
            send_payload=send_payload,
        ):
            return

        if subscription_routes.handle_read(
            self,
            _subscription_routes_context(),
            path=path,
            query=q,
            host=host,
            base_url=base_url,
            send_payload=send_payload,
        ):
            return

        if admin_read_routes.handle_read(
            self,
            _admin_read_context(),
            path=path,
            query=q,
            host=host,
            send_payload=send_payload,
        ):
            return

        if admin_console_routes.handle_read(
            self,
            _admin_console_context(),
            path=path,
            query=q,
            host=host,
            base_url=base_url,
            send_payload=send_payload,
        ):
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
                        else '核心授权状态暂不可用；静态代理停止状态未完全确认，系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8',
                True,
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
                        else '核心授权状态暂不可用；静态代理停止状态未完全确认，系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8',
                False,
            )

    @request_multiplier_snapshot
    def do_POST(self):
        try:
            self._do_POST()
        except (state_store.StateStoreError, OSError) as exc:
            path = urlparse(self.path).path
            stopped = _state_failure_requires_static_stop(
                exc,
                post_path=path,
            )
            stop_outcomes = {}
            if stopped:
                stop_outcomes = _fail_closed_static_access(exc)
            self.send_response_body(
                503,
                (
                    (
                        '授权变更未能安全完成；为避免数据覆盖，已确认静态代理暂停'
                        if _static_stop_confirmed(stop_outcomes)
                        else '授权变更未能安全完成；为避免数据覆盖，'
                        '静态代理停止状态未完全确认，系统将继续重试'
                    )
                    if stopped
                    else '此功能依赖的状态暂不可用；代理服务未受影响'
                ),
                'text/plain; charset=utf-8',
                True,
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
        request_user_revision = (form.get('user_revision') or query.get('revision') or [''])[0]

        if auth_routes.handle_write(
            self,
            _auth_routes_context(),
            path=path,
            form=form,
            meta=meta,
        ):
            return

        if credential_routes.handle_write(
            self,
            _credential_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
            return

        if landing_write_routes.handle_write(
            self,
            _landing_write_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
            return

        if admin_account_routes.handle_write(
            self,
            _admin_account_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
            return

        if admin_traffic_routes.handle_write(
            self,
            _admin_traffic_context(),
            path=path,
            form=form,
            request_user_revision=request_user_revision,
        ):
            return

        if admin_operations_routes.handle_write(
            self,
            _admin_operations_context(),
            path=path,
            form=form,
        ):
            return

        if admin_user_status_routes.handle_write(
            self,
            _admin_user_status_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
            return

        if admin_user_delete_routes.handle_write(
            self,
            _admin_user_delete_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
            return

        if admin_config_routes.handle_write(
            self,
            _admin_config_context(),
            path=path,
            form=form,
        ):
            return

        if rule_pack_routes.handle_write(
            self,
            _rule_pack_routes_context(),
            path=path,
            form=form,
            query=query,
            request_user_revision=request_user_revision,
        ):
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
