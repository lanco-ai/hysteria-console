"""Billing storage must remain independent of HTTP routing."""

import importlib.util
from datetime import datetime

import subscription_service as ss


def test_billing_service_module_exists():
    assert importlib.util.find_spec('billing_service') is not None


def test_preserved_refresh_is_additive_within_cycle(tmp_path, monkeypatch):
    import billing_service

    monkeypatch.setattr(ss, 'META_FILE', tmp_path / 'meta.json')
    monkeypatch.setattr(ss, 'USAGE_PRESERVED_FILE', tmp_path / 'preserved.json')
    ss.save_json(
        ss.META_FILE,
        {'settlement_day': 1, 'cycle_length_days': 30, 'cycle_anchor_date': '2026-09-01'},
    )
    service = ss._billing_service()
    assert isinstance(service, billing_service.BillingService)
    now = datetime(2026, 9, 8)
    service.add_preserved_for_user('alice', 10, 20, 30, now=now)
    service.add_preserved_for_user('alice', 5, 7, 12, now=now)
    assert service.preserved_raw_for_cycle(now=now) == 42
    assert service.preserved_raw_for_cycle(now=datetime(2026, 10, 8)) == 0
