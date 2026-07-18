"""Alert dispatcher: telegram + signed webhook, fire-and-forget, dedup-aware.

The dispatcher silently no-ops when alerts.json is missing. State is kept in a
small JSON file so that quota crossings dedupe per billing month and anomaly
events dedupe per day.
"""
import hashlib
import hmac
import json
import logging
import math
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from pathlib import Path

import state_store

CONFIG_FILE = Path('/root/hysteria/alerts.json')
STATE_FILE = Path('/root/hysteria/state/alert_state.json')

DEFAULT_Z_THRESHOLD = 3.0
DEFAULT_MIN_BYTES = 1 << 30
CLAIM_TTL_SECONDS = 60.0
STATE_LOCK_TIMEOUT_SECONDS = 5.0

log = logging.getLogger('hy2.alerts')

_STATE_KEYS = ('quota_80', 'quota_100', 'anomaly', 'expiry_soon', 'expiry_expired')
_CLAIM_KEYS = frozenset(('key', 'token', 'claimed_at'))


def load_config(path=None):
    """Return parsed alerts.json or None if absent/unreadable."""
    p = Path(path) if path is not None else CONFIG_FILE
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        log.warning('alerts: cannot read %s: %s', p, e)
        return None


def _empty_state():
    return {k: {} for k in _STATE_KEYS}


def _invalid_state(path, detail):
    raise state_store.InvalidJsonState(
        f'invalid alert state schema: {path}: {detail}'
    )


def _validate_entry(value, *, path, field):
    if isinstance(value, str):
        if not value:
            _invalid_state(path, f'{field} must not be empty')
        return
    if not isinstance(value, dict) or set(value) != _CLAIM_KEYS:
        _invalid_state(path, f'{field} must be a dedup key or claim object')
    key = value.get('key')
    token = value.get('token')
    claimed_at = value.get('claimed_at')
    if not isinstance(key, str) or not key:
        _invalid_state(path, f'{field}.key must be a non-empty string')
    if not isinstance(token, str) or not token:
        _invalid_state(path, f'{field}.token must be a non-empty string')
    if (
        isinstance(claimed_at, bool)
        or not isinstance(claimed_at, (int, float))
        or not math.isfinite(claimed_at)
        or claimed_at < 0
    ):
        _invalid_state(
            path, f'{field}.claimed_at must be a finite non-negative number'
        )


def _validate_state(data, *, path):
    if not isinstance(data, dict):
        _invalid_state(path, 'top-level value must be an object')
    for kind, bucket in data.items():
        if not isinstance(kind, str) or not kind:
            _invalid_state(path, 'alert kind keys must be non-empty strings')
        if not isinstance(bucket, dict):
            _invalid_state(path, f'{kind} must be an object')
        for user, value in bucket.items():
            if not isinstance(user, str) or not user:
                _invalid_state(
                    path, f'{kind} user keys must be non-empty strings'
                )
            _validate_entry(value, path=path, field=f'{kind}.{user}')
    return data


def load_state(path=None):
    p = Path(path) if path is not None else STATE_FILE
    data = state_store.load_json_strict(p, {})
    _validate_state(data, path=p)
    for k in _STATE_KEYS:
        data.setdefault(k, {})
    return data


def save_state(state, path=None):
    """Durably replace alert state via state_store's unique sibling tempfile."""
    p = Path(path) if path is not None else STATE_FILE
    _validate_state(state, path=p)
    state_store.save_json(p, state)


def state_lock_path(path=None):
    p = Path(path) if path is not None else STATE_FILE
    return p.with_name(p.name + '.lock')


def mutate_state(mutator, path=None):
    """Apply one alert-state read/modify/write transaction under ``.lock``.

    Existing corrupt state raises ``InvalidJsonState`` before ``mutator`` is
    called, so an optional alert feature can degrade without overwriting the
    operator-repairable file. The callback may return any result.
    """
    p = Path(path) if path is not None else STATE_FILE
    with state_store.file_lock(
        state_lock_path(p), timeout=STATE_LOCK_TIMEOUT_SECONDS
    ):
        state = load_state(p)
        before = deepcopy(state)
        result = mutator(state)
        _validate_state(state, path=p)
        if state != before:
            save_state(state, p)
        return result


def already_alerted(state, kind, user, key):
    """Return True if (kind, user) was last alerted at exactly `key`."""
    return state.get(kind, {}).get(user) == key


def mark_alerted(state, kind, user, key):
    state.setdefault(kind, {})[user] = key


def clear_quota_dedup_for(state, usernames):
    """Drop quota_80 / quota_100 dedup entries for `usernames` so subsequent
    quota crossings within the same cycle re-fire alerts. Anomaly entries are
    NOT touched — they're day-scoped and self-reset on the next calendar day.
    See docs/adr/0001-manual-reset-clears-alert-dedup.md.
    """
    for kind in ('quota_80', 'quota_100'):
        bucket = state.get(kind, {})
        for u in usernames:
            bucket.pop(u, None)


def clear_quota_dedup_transaction(usernames, path=None):
    """Atomically clear quota dedup entries for the selected users."""
    selected = tuple(usernames)
    return mutate_state(
        lambda state: clear_quota_dedup_for(state, selected),
        path,
    )


def clear_user_dedup_transaction(usernames, path=None):
    """Atomically remove every alert/claim belonging to selected users."""
    selected = frozenset(usernames)

    def remove(state):
        for bucket in state.values():
            for user in selected:
                bucket.pop(user, None)

    return mutate_state(remove, path)


def _claim_value(value):
    return value if isinstance(value, dict) and set(value) == _CLAIM_KEYS else None


def claim_alert(kind, user, key, *, path=None, now=None, ttl=CLAIM_TTL_SECONDS):
    """Reserve one dedup key and return an opaque claim token, or ``None``.

    Delivered string entries and unexpired claims suppress concurrent sends.
    A process that dies while dispatching leaves a claim that becomes
    retryable after ``ttl`` seconds.
    """
    if kind not in _STATE_KEYS:
        raise ValueError(f'unsupported alert kind: {kind!r}')
    if not isinstance(user, str) or not user:
        raise ValueError('alert user must be a non-empty string')
    if not isinstance(key, str) or not key:
        raise ValueError('alert dedup key must be a non-empty string')
    claimed_at = time.time() if now is None else float(now)
    ttl = max(0.0, float(ttl))
    token = secrets.token_urlsafe(24)

    def claim(state):
        bucket = state[kind]
        current = bucket.get(user)
        if current == key:
            return None
        pending = _claim_value(current)
        pending_age = (
            claimed_at - float(pending.get('claimed_at'))
            if pending is not None else None
        )
        if (
            pending is not None
            and pending.get('key') == key
            and 0 <= pending_age < ttl
        ):
            return None
        bucket[user] = {
            'key': key,
            'token': token,
            'claimed_at': claimed_at,
        }
        return token

    return mutate_state(claim, path)


def finish_alert_claim(
    kind, user, key, token, *, delivered, path=None
):
    """CAS-complete a claim, preserving a concurrent clear or replacement."""
    if kind not in _STATE_KEYS:
        raise ValueError(f'unsupported alert kind: {kind!r}')

    def finish(state):
        bucket = state[kind]
        current = _claim_value(bucket.get(user))
        if (
            current is None
            or current.get('key') != key
            or not hmac.compare_digest(str(current.get('token')), str(token))
        ):
            return False
        if delivered:
            bucket[user] = key
        else:
            bucket.pop(user, None)
        return True

    return mutate_state(finish, path)


def dispatch_once(event, key, *, config=None, opener=None, path=None):
    """Claim, dispatch outside the lock, then CAS-complete or release.

    Delivery is at-least-once across transport/process failures: all configured
    transport attempts must succeed before the dedup key is committed. A
    failed attempt releases its claim immediately for the next timer tick.
    """
    kind = event.get('kind')
    user = event.get('user')
    token = claim_alert(kind, user, key, path=path)
    if token is None:
        return {'attempted': [], 'failed': []}
    result = dispatch(event, config=config, opener=opener)
    attempted = result.get('attempted') or []
    delivered = bool(attempted) and not (result.get('failed') or [])
    finish_alert_claim(
        kind, user, key, token, delivered=delivered, path=path
    )
    return result


def format_message(event):
    kind = event.get('kind')
    user = event.get('user', '?')
    details = event.get('details') or {}
    if kind == 'quota_80':
        return (f"\U0001F7E1 {user} 已用 80% "
                f"({details.get('used_human','?')} / {details.get('total_human','?')}) "
                f"· 周期 {details.get('cycle','?')}")
    if kind == 'quota_100':
        return (f"\U0001F534 {user} 已耗尽 "
                f"({details.get('used_human','?')} / {details.get('total_human','?')}) "
                f"· 周期 {details.get('cycle','?')}")
    if kind == 'anomaly':
        z = details.get('z', 0.0)
        return (f"⚠️ {user} 今日 {details.get('today_human','?')} "
                f"(基线 {details.get('mean_human','?')}, z={z:.1f})")
    if kind == 'expiry_soon':
        return (f"⏳ {user} 将于 {details.get('expires_at','?')} 到期 "
                f"· 剩余 {details.get('days_left','?')} 天")
    if kind == 'expiry_expired':
        return f"⛔ {user} 已于 {details.get('expires_at','?')} 到期"
    if kind == 'test':
        return f"✅ 测试告警 · 来自管理面板（{user}）"
    return f"{kind}: {user}"


def _post_telegram(cfg, message, *, opener):
    """Return True on a successful POST, False on transport failure."""
    bot = cfg.get('bot_token')
    chat = cfg.get('chat_id')
    if not bot or not chat:
        return False
    url = f'https://api.telegram.org/bot{bot}/sendMessage'
    body = urllib.parse.urlencode({'chat_id': chat, 'text': message}).encode('utf-8')
    req = urllib.request.Request(url, data=body, method='POST')
    try:
        opener.urlopen(req, timeout=5).read()
        return True
    except (urllib.error.URLError, OSError) as exc:
        # The request URL contains the bot token. Log only the exception type;
        # urllib exception strings may echo a credential-bearing URL.
        log.warning(
            'telegram alert failed (%s)', type(exc).__name__,
        )
        return False


def _post_webhook(cfg, event, *, opener):
    """Return True on a successful POST, False on transport failure."""
    url = cfg.get('url')
    if not url:
        return False
    body = json.dumps(event, ensure_ascii=True).encode('utf-8')
    headers = {'Content-Type': 'application/json'}
    secret = cfg.get('secret')
    if secret:
        sig = hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()
        headers['X-Hy2-Signature'] = f'sha256={sig}'
    req = urllib.request.Request(url, data=body, method='POST', headers=headers)
    try:
        opener.urlopen(req, timeout=5).read()
        return True
    except (urllib.error.URLError, OSError) as exc:
        # Webhook URLs may contain operator-managed secret query parameters.
        log.warning(
            'webhook alert failed (%s)', type(exc).__name__,
        )
        return False


def dispatch(event, *, config=None, opener=None):
    """Fire `event` to every configured channel. Never raises.

    Returns a result dict `{'attempted': [...], 'failed': [...]}` naming the
    channels that were tried and those whose transport failed. Cron callers
    ignore the return value, so this stays backward compatible.
    """
    result = {'attempted': [], 'failed': []}
    try:
        cfg = config if config is not None else load_config()
        if not isinstance(cfg, dict):
            return result
        transport = opener if opener is not None else urllib.request
        msg = format_message(event)
        if cfg.get('telegram'):
            result['attempted'].append('telegram')
            if not _post_telegram(cfg['telegram'], msg, opener=transport):
                result['failed'].append('telegram')
        if cfg.get('webhook'):
            result['attempted'].append('webhook')
            if not _post_webhook(cfg['webhook'], event, opener=transport):
                result['failed'].append('webhook')
    except Exception as exc:
        # A programmer/configuration error inside a transport is still a
        # delivery failure. Without this, an exception raised after a channel
        # was appended to ``attempted`` could be mistaken for success and
        # permanently commit the dedup key even though nothing was sent.
        for channel in result['attempted']:
            if channel not in result['failed']:
                result['failed'].append(channel)
        # Do not include the exception message/traceback: third-party opener
        # errors can embed the credential-bearing destination URL.
        log.error(
            'dispatch failed unexpectedly (%s)', type(exc).__name__,
        )
    return result
