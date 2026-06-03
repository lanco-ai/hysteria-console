"""user_compat — metered/guest fallback accessor."""
from datetime import date

import user_compat


def test_canonical_metered_true():
    assert user_compat.is_metered({'metered': True}) is True


def test_canonical_metered_false():
    assert user_compat.is_metered({'metered': False}) is False


def test_legacy_guest_true_when_metered_absent():
    assert user_compat.is_metered({'guest': True}) is True


def test_legacy_guest_false_when_metered_absent():
    assert user_compat.is_metered({'guest': False}) is False


def test_canonical_metered_overrides_legacy_guest():
    """If both keys present, `metered` is authoritative."""
    assert user_compat.is_metered({'metered': True, 'guest': False}) is True
    assert user_compat.is_metered({'metered': False, 'guest': True}) is False


def test_missing_both_returns_false():
    assert user_compat.is_metered({}) is False


def test_non_dict_input_returns_false():
    assert user_compat.is_metered(None) is False
    assert user_compat.is_metered('string') is False
    assert user_compat.is_metered(0) is False


def test_truthy_values_coerce_to_bool():
    assert user_compat.is_metered({'metered': 1}) is True
    assert user_compat.is_metered({'metered': 'yes'}) is True
    assert user_compat.is_metered({'guest': 1}) is True


def test_total_quota_bytes_adds_extra_package():
    cfg = {'monthly_quota_bytes': 100, 'quota_extra_bytes': 25}
    assert user_compat.total_quota_bytes(cfg) == 125


def test_total_quota_bytes_ignores_negative_extra():
    cfg = {'monthly_quota_bytes': 100, 'quota_extra_bytes': -25}
    assert user_compat.total_quota_bytes(cfg) == 100


def test_expiry_date_parses_iso_date():
    assert user_compat.expiry_date({'expires_at': '2026-06-30'}) == date(2026, 6, 30)


def test_is_expired_only_after_expiry_date():
    cfg = {'expires_at': '2026-06-30'}
    assert user_compat.is_expired(cfg, today=date(2026, 6, 30)) is False
    assert user_compat.is_expired(cfg, today=date(2026, 7, 1)) is True


def test_is_inactive_covers_disabled_or_expired():
    assert user_compat.is_inactive({'disabled': True}, today=date(2026, 6, 1)) is True
    assert user_compat.is_inactive({'expires_at': '2026-05-31'}, today=date(2026, 6, 1)) is True
    assert user_compat.is_inactive({'expires_at': '2026-06-30'}, today=date(2026, 6, 1)) is False
