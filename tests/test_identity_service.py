"""Password and identity lifecycle can be used without HTTP routes."""

import importlib.util

import subscription_service as ss


def test_identity_service_module_exists():
    assert importlib.util.find_spec('identity_service') is not None


def test_password_round_trip_uses_existing_hash_format():
    import identity_service

    service = ss._identity_service()
    assert isinstance(service, identity_service.IdentityService)
    encoded = service.hash_secret('a-test-password')
    assert encoded.startswith('pbkdf2_sha256$200000$')
    assert service.verify_secret('a-test-password', encoded)
    assert not service.verify_secret('wrong-password', encoded)
