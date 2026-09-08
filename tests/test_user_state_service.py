"""User lifecycle helpers preserve other identities while cleaning state."""

import importlib.util

import subscription_service as ss


def test_user_state_service_module_exists():
    assert importlib.util.find_spec('user_state_service') is not None


def test_landing_identity_does_not_reuse_existing_uuid():
    import user_state_service

    service = ss._user_state_service()
    assert isinstance(service, user_state_service.UserStateService)
    occupied = '01234567-89ab-4cde-8fab-0123456789ab'
    cfg = {'vless_uuid': occupied}
    generated = service._ensure_landing_vless_uuid(cfg, {'alice': cfg})
    assert generated != occupied
    assert cfg['landing_vless_uuid'] == generated
    assert service._ensure_landing_vless_uuid(cfg, {'alice': cfg}) == generated
