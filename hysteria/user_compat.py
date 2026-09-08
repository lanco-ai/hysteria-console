"""User-config field accessors with backward-compat for legacy field names.

Centralizes the `metered` / `guest` alias mapping so callers don't hardcode the
fallback chain. See CONTEXT.md `### User types` for the canonical vocabulary.
"""
from datetime import date, datetime
import ipaddress
import re
import uuid

_USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,64}$')


def configured_max_devices(cfg, default=2):
    """Stored nonnegative device cap; explicit zero means unlimited."""
    if not isinstance(cfg, dict):
        return default
    raw = cfg['max_devices'] if 'max_devices' in cfg else default
    if isinstance(raw, bool):
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def is_valid_username(value):
    return bool(
        isinstance(value, str)
        and _USERNAME_RE.fullmatch(value)
        and not value.endswith('.json')
    )


def authorization_config_error(cfg):
    """Return a reason when persisted auth/quota fields are structurally unsafe.

    Missing fields remain valid for legacy/unmetered users. Present fields are
    strict so malformed expiry or quota data can never degrade into unlimited
    access.
    """
    if not isinstance(cfg, dict):
        return 'user config must be an object'
    for field in (
        'disabled',
        'metered',
        'guest',
        'tuic_enabled',
        'panel_password_must_change',
    ):
        if field in cfg and not isinstance(cfg[field], bool):
            return f'{field} must be a boolean'
    for field in (
        'monthly_quota_bytes',
        'quota_extra_bytes',
        'max_devices',
    ):
        if field not in cfg:
            continue
        value = cfg[field]
        if isinstance(value, bool):
            return f'{field} must be a non-negative integer'
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return f'{field} must be a non-negative integer'
        if parsed < 0 or (
            isinstance(value, str) and str(parsed) != value.strip()
        ):
            return f'{field} must be a non-negative integer'
    for field in (
        'sub_token',
        'vless_uuid',
        'password_hash',
        'panel_pass_hash',
    ):
        if field in cfg and cfg[field] is not None and not isinstance(
            cfg[field], str
        ):
            return f'{field} must be a string'
    raw_uuid = cfg.get('vless_uuid')
    if raw_uuid not in (None, ''):
        try:
            uuid.UUID(str(raw_uuid))
        except (ValueError, AttributeError, TypeError):
            return 'vless_uuid must be a valid UUID'
    if cfg.get('expires_at') not in (None, '') and parse_expiry_date(
        cfg.get('expires_at')
    ) is None:
        return 'expires_at must be an ISO date'
    if cfg.get('disabled_until') not in (None, '') and parse_disabled_until(
        cfg.get('disabled_until')
    ) is None:
        return 'disabled_until must be an ISO datetime'
    return None


def is_metered(cfg):
    """Return True if this user is **metered** — quota-enforced, kicked over
    quota, eligible for quota alerts. Reads canonical `metered`, falls back
    to legacy `guest`. Missing/unknown both → False.
    """
    if not isinstance(cfg, dict):
        return False
    if 'metered' in cfg:
        return bool(cfg['metered'])
    return bool(cfg.get('guest', False))


def tuic_enabled(cfg):
    """Return whether this user may receive/use TUIC credentials.

    TUIC metering in this stack is protocol-level only, so metered users cannot
    be safely quota-enforced on TUIC. Missing config therefore means:
    non-metered users keep TUIC, metered users do not. Operators can override
    explicitly with `tuic_enabled`.
    """
    if not isinstance(cfg, dict):
        return False
    if 'tuic_enabled' in cfg:
        return bool(cfg.get('tuic_enabled'))
    return not is_metered(cfg)


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def quota_extra_bytes(cfg):
    if not isinstance(cfg, dict):
        return 0
    return max(0, _as_int(cfg.get('quota_extra_bytes'), 0))


def total_quota_bytes(cfg):
    if not isinstance(cfg, dict):
        return 0
    base = max(0, _as_int(cfg.get('monthly_quota_bytes'), 0))
    return base + quota_extra_bytes(cfg)


def parse_expiry_date(raw):
    if raw in (None, ''):
        return None
    try:
        return datetime.strptime(str(raw).strip(), '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def expiry_date(cfg):
    if not isinstance(cfg, dict):
        return None
    return parse_expiry_date(cfg.get('expires_at'))


def parse_disabled_until(raw):
    if raw in (None, ''):
        return None
    try:
        return datetime.fromisoformat(str(raw).strip())
    except (TypeError, ValueError):
        return None


def disabled_until(cfg):
    if not isinstance(cfg, dict):
        return None
    return parse_disabled_until(cfg.get('disabled_until'))


def temporary_disable_expired(cfg, now=None):
    until = disabled_until(cfg)
    if until is None:
        return False
    current = now or datetime.now(tz=until.tzinfo)
    if until.tzinfo is not None and current.tzinfo is None:
        current = current.replace(tzinfo=until.tzinfo)
    if until.tzinfo is None and current.tzinfo is not None:
        until = until.replace(tzinfo=current.tzinfo)
    return until <= current


def is_expired(cfg, today=None):
    exp = expiry_date(cfg)
    if exp is None:
        return False
    current = today or date.today()
    return exp < current


def is_inactive(cfg, today=None):
    if not isinstance(cfg, dict):
        return False
    return bool(cfg.get('disabled')) or is_expired(cfg, today=today)


# Display-only landing notes. Never consulted by authorization_config_error
# or the static-access plan — a malformed value here must not deny proxy
# access. Write-path validation lives in the admin update handler.
LANDING_FIELDS = (
    'landing_isp', 'landing_region', 'landing_note', 'landing_ip',
)
LANDING_MAX_LENGTH = 120


def _landing_control_chars(value):
    return any(ord(ch) < 32 for ch in value)


def _landing_has_markup(value):
    """Reject HTML-looking input without a parser or extra dependency."""
    return '<' in value or '>' in value


def parse_landing_write(raw):
    """Strict write-boundary check. Returns (value_or_None, error_or_None).

    Empty / missing → (None, None) meaning "clear the field".
    Invalid → (None, error_code) so the caller can reject the request.
    """
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, 'landing_invalid'
    text = raw.strip()
    if not text:
        return None, None
    if len(text) > LANDING_MAX_LENGTH:
        return None, 'landing_too_long'
    if _landing_control_chars(text) or _landing_has_markup(text):
        return None, 'landing_invalid'
    return text, None


def parse_landing_ip_write(raw):
    """Validate and canonicalize an optional display-only IP address."""
    if raw is None:
        return None, None
    if not isinstance(raw, str):
        return None, 'landing_ip_invalid'
    if _landing_control_chars(raw) or _landing_has_markup(raw) or '%' in raw:
        return None, 'landing_ip_invalid'
    text = raw.strip()
    if not text:
        return None, None
    try:
        return str(ipaddress.ip_address(text)), None
    except ValueError:
        return None, 'landing_ip_invalid'


def landing_field(cfg, name):
    """Lenient reader: non-str / overlong / control chars become empty."""
    if not isinstance(cfg, dict) or name not in LANDING_FIELDS:
        return ''
    value = cfg.get(name)
    if not isinstance(value, str):
        return ''
    text = value.strip()
    if not text or len(text) > LANDING_MAX_LENGTH:
        return ''
    if _landing_control_chars(text) or _landing_has_markup(text):
        return ''
    if name == 'landing_ip':
        if _landing_control_chars(value) or '%' in value:
            return ''
        try:
            return str(ipaddress.ip_address(text))
        except ValueError:
            return ''
    return text


def landing_fields(cfg):
    """All display fields. Empty dict when nothing is showable."""
    out = {name: landing_field(cfg, name) for name in LANDING_FIELDS}
    if not any(out.values()):
        return {}
    return out
