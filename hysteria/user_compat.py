"""User-config field accessors with backward-compat for legacy field names.

Centralizes the `metered` / `guest` alias mapping so callers don't hardcode the
fallback chain. See CONTEXT.md `### User types` for the canonical vocabulary.
"""
from datetime import date, datetime


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
