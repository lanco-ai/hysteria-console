"""Static authorization rejects corrupt accounting before creating access."""

import importlib.util

import pytest
import state_store
import subscription_service as ss


def test_authorization_service_module_exists():
    assert importlib.util.find_spec('authorization_service') is not None


def test_corrupt_usage_is_rejected_by_domain_service():
    import authorization_service

    service = ss._authorization_service()
    assert isinstance(service, authorization_service.AuthorizationService)
    with pytest.raises(state_store.CriticalStateUnavailable):
        service._cycle_usage_sum_strict(
            {'2026-09-08': {'alice': {'tx': 10, 'rx': 20, 'total': 0}}},
            ['2026-09-08'],
            'alice',
        )
