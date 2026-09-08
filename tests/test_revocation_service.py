"""Revocation retries can run independently of HTTP request handling."""

import importlib.util

import subscription_service as ss


def test_revocation_service_module_exists():
    assert importlib.util.find_spec('revocation_service') is not None


def test_empty_revocation_queue_does_not_attempt_network(tmp_path, monkeypatch):
    import revocation_service

    monkeypatch.setattr(ss, 'USAGE_FILE', tmp_path / 'usage.json')

    def unexpected_kick(*args, **kwargs):
        raise AssertionError('empty queue must not kick users')

    monkeypatch.setattr(ss, 'hy_kick', unexpected_kick)
    service = ss._revocation_service()
    assert isinstance(service, revocation_service.RevocationService)
    assert service._process_one_revocation_task() is False
