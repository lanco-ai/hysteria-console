"""Operational side effects have explicit runtime dependencies."""

import importlib.util

import subscription_service as ss


def test_operational_service_module_exists():
    assert importlib.util.find_spec('operational_service') is not None


def test_empty_kick_is_success_without_attempting_connection():
    import operational_service

    service = ss._operational_service()
    assert isinstance(service, operational_service.OperationalService)
    result = service.hy_kick([])
    assert result.ok is True
    assert result.attempted is False
    assert result.code == 'not_needed'
