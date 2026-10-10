"""Alert dispatcher: telegram + signed webhook, fire-and-forget, dedup-aware.

The dispatcher silently no-ops when alerts.json is missing. State is kept in a
small JSON file so that quota crossings dedupe per billing month and anomaly
events dedupe per day.
"""
import hashlib
import hmac
import http.client
import io
import ipaddress
import json
import logging
import math
import re
import secrets
import socket
import ssl
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


# Console-managed settings (设置 · 告警通知). Secrets are write-only: the console
# sees whether a channel is configured, never its token, URL or signing secret.
CONFIG_LOCK_FILE = Path('/root/hysteria/state/alerts-config.lock')
TELEGRAM_TOKEN_RE = re.compile(r'^\d{5,16}:[A-Za-z0-9_-]{30,64}$')
TELEGRAM_CHAT_RE = re.compile(r'^(-?\d{1,20}|@[A-Za-z][A-Za-z0-9_]{4,63})$')
Z_THRESHOLD_LIMITS = (1.0, 10.0)
MIN_GIB_LIMITS = (0.0, 1024.0)


class AlertConfigError(ValueError):
    """A console alert setting is invalid; ``code`` names the field."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _section(cfg, name):
    value = cfg.get(name) if isinstance(cfg, dict) else None
    return value if isinstance(value, dict) else {}


def _webhook_host(url):
    try:
        return urllib.parse.urlsplit(url).hostname or ''
    except ValueError:
        return ''


def public_config(cfg):
    """Channel status and thresholds for the console, without any secret."""
    cfg = cfg if isinstance(cfg, dict) else {}
    telegram = _section(cfg, 'telegram')
    webhook = _section(cfg, 'webhook')
    try:
        z_threshold = float(cfg.get('anomaly_z_threshold', DEFAULT_Z_THRESHOLD))
        min_bytes = int(cfg.get('anomaly_min_bytes', DEFAULT_MIN_BYTES))
    except (TypeError, ValueError):
        z_threshold, min_bytes = DEFAULT_Z_THRESHOLD, DEFAULT_MIN_BYTES
    return {
        'telegram': {
            'configured': bool(telegram.get('bot_token') and telegram.get('chat_id')),
            'chat_id': str(telegram.get('chat_id') or ''),
        },
        'webhook': {
            'configured': bool(webhook.get('url')),
            'host': _webhook_host(str(webhook.get('url') or '')),
            'signed': bool(webhook.get('secret')),
        },
        'anomaly_z_threshold': z_threshold,
        'anomaly_min_gib': round(min_bytes / (1 << 30), 2),
    }


def _public_address(address):
    """True for a globally routable unicast address (IPv4-mapped IPv6 judged as IPv4)."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def webhook_url_problem(url):
    """Return why ``url`` cannot receive alerts, or '' when it can.

    Alerts are POSTed from the server itself, so a webhook must not point at
    this host or a private network. Host names are checked against what they
    resolve to when an alert is sent (see _WebhookTransport).
    """
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return 'webhook_url_invalid'
    if (len(url) > 2048 or parsed.scheme != 'https' or not parsed.hostname or port == 0
            or parsed.username is not None or parsed.password is not None
            or any(char.isspace() for char in url)):
        return 'webhook_url_invalid'
    host = parsed.hostname.rstrip('.')
    if host == 'localhost' or host.endswith('.localhost'):
        return 'webhook_url_private'
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return ''
    return '' if _public_address(address) else 'webhook_url_private'


def config_revision(cfg, secret):
    """Compare-and-swap revision for alerts.json that cannot confirm a guessed secret.

    The file holds write-only credentials, so a plain content hash handed to
    the console would let anyone holding it test guesses offline. Keying the
    hash with a server-side secret keeps it opaque while any edit still
    changes it.
    """
    key = hmac.new(
        str(secret).encode('utf-8'), b'hy2 alerts config revision', hashlib.sha256,
    ).digest()
    payload = json.dumps(
        cfg if isinstance(cfg, dict) else {},
        ensure_ascii=False, sort_keys=True, separators=(',', ':'),
    ).encode('utf-8')
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _bounded_number(raw, limits, code):
    try:
        value = float(str(raw).strip())
    except ValueError:
        raise AlertConfigError(code) from None
    if not math.isfinite(value) or not limits[0] <= value <= limits[1]:
        raise AlertConfigError(code)
    return value


def updated_config(cfg, values):
    """Merge console form values into the alert config.

    A blank token, URL or secret keeps the stored value, so saving never needs
    them re-entered; turning a channel off removes it. Keys the console does
    not manage are preserved. Raises AlertConfigError naming the bad field.
    """
    current = cfg if isinstance(cfg, dict) else {}
    managed = ('telegram', 'webhook', 'anomaly_z_threshold', 'anomaly_min_bytes')
    result = {key: value for key, value in current.items() if key not in managed}
    if values.get('telegram_enabled'):
        stored = _section(current, 'telegram')
        token = str(values.get('telegram_bot_token') or '').strip() or str(stored.get('bot_token') or '')
        chat_id = str(values.get('telegram_chat_id') or '').strip()
        if not TELEGRAM_TOKEN_RE.fullmatch(token):
            raise AlertConfigError('telegram_token_invalid')
        if not TELEGRAM_CHAT_RE.fullmatch(chat_id):
            raise AlertConfigError('telegram_chat_invalid')
        result['telegram'] = {'bot_token': token, 'chat_id': chat_id}
    if values.get('webhook_enabled'):
        stored = _section(current, 'webhook')
        url = str(values.get('webhook_url') or '').strip() or str(stored.get('url') or '')
        problem = webhook_url_problem(url)
        if problem:
            raise AlertConfigError(problem)
        if values.get('webhook_secret_clear'):
            secret = ''
        else:
            secret = str(values.get('webhook_secret') or '').strip() or str(stored.get('secret') or '')
        if len(secret) > 256:
            raise AlertConfigError('webhook_secret_invalid')
        result['webhook'] = {'url': url, **({'secret': secret} if secret else {})}
    result['anomaly_z_threshold'] = _bounded_number(
        values.get('anomaly_z_threshold', DEFAULT_Z_THRESHOLD), Z_THRESHOLD_LIMITS, 'z_threshold_invalid')
    min_gib = _bounded_number(
        values.get('anomaly_min_gib', DEFAULT_MIN_BYTES / (1 << 30)), MIN_GIB_LIMITS, 'min_gib_invalid')
    result['anomaly_min_bytes'] = int(min_gib * (1 << 30))
    return result


def save_config(cfg, path=None):
    """Atomically write alerts.json, readable by root only."""
    target = Path(path) if path is not None else CONFIG_FILE
    state_store.save_json(target, cfg)
    target.chmod(0o600)


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
    if kind == 'hysteria_update':
        return (
            f"Hysteria 更新 {details.get('status', 'unknown')} · "
            f"{details.get('previous_version', '?')} → "
            f"{details.get('version', '?')}"
        )
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


def _resolved_public(address):
    try:
        return _public_address(ipaddress.ip_address(address.split('%', 1)[0]))
    except ValueError:
        return False


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to addresses vetted in advance; TLS still verifies the URL's host name."""

    def __init__(self, host, port, *, addresses, timeout):
        super().__init__(host, port, timeout=timeout)
        self._addresses = list(addresses)
        self._tls = ssl.create_default_context()

    def connect(self):
        # Try each vetted address in turn, as socket.create_connection does for a name.
        error = OSError('webhook host has no address')
        for address in self._addresses:
            try:
                sock = socket.create_connection((address, self.port), self.timeout)
            except OSError as exc:
                error = exc
                continue
            try:
                self.sock = self._tls.wrap_socket(sock, server_hostname=self.host)
            except BaseException:
                sock.close()
                raise
            return
        raise error


class _WebhookTransport:
    """POST a webhook without proxies or redirects, only to a public address.

    Every address the host name resolves to must be public, and the request
    goes only to the addresses that were checked, so a later DNS answer cannot
    switch the target to this host or a private network. A redirect is a
    failed delivery rather than a second request.
    """

    def __init__(self, resolve=socket.getaddrinfo, connection=_PinnedHTTPSConnection):
        self._resolve = resolve
        self._connection = connection

    def urlopen(self, request, timeout):
        parts = urllib.parse.urlsplit(request.full_url)
        port = parts.port or 443
        answers = self._resolve(parts.hostname, port, type=socket.SOCK_STREAM)
        addresses = list(dict.fromkeys(answer[4][0] for answer in answers))
        if not addresses or not all(_resolved_public(address) for address in addresses):
            raise OSError('webhook destination is not a public address')
        connection = self._connection(parts.hostname, port, addresses=addresses, timeout=timeout)
        try:
            path = (parts.path or '/') + (f'?{parts.query}' if parts.query else '')
            connection.request(
                request.get_method(), path, body=request.data, headers=dict(request.header_items()),
            )
            response = connection.getresponse()
            body = response.read(64 * 1024)
            if not 200 <= response.status < 300:
                raise OSError(f'webhook answered HTTP {response.status}')
            return io.BytesIO(body)
        finally:
            connection.close()


_WEBHOOK_TRANSPORT = _WebhookTransport()


def _post_webhook(cfg, event, *, opener):
    """Return True on a successful POST, False on transport failure."""
    url = cfg.get('url')
    if not url:
        return False
    # Also covers a hand-written alerts.json that the console never validated.
    if webhook_url_problem(str(url)):
        log.warning('webhook alert refused: destination not allowed')
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
            webhook_transport = opener if opener is not None else _WEBHOOK_TRANSPORT
            if not _post_webhook(cfg['webhook'], event, opener=webhook_transport):
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
