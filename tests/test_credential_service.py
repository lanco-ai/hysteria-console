"""Credential rotation belongs to a domain service, not the HTTP handler."""

import importlib.util

import subscription_service as ss


def test_credential_service_module_exists():
    assert importlib.util.find_spec('credential_service') is not None


def test_invalid_rotation_id_is_rejected_without_reading_state(monkeypatch):
    import credential_service

    def unexpected_read(*args, **kwargs):
        raise AssertionError('invalid request must not read credential state')

    monkeypatch.setattr(ss, 'load_json', unexpected_read)
    service = ss._credential_service()
    result = service._recoverable_user_rotation(
        'alice', 'old-token', request_id='invalid', session_id='session'
    )
    assert isinstance(result, credential_service.RecoverableRotationResult)
    assert result.status == 'bad_request'
