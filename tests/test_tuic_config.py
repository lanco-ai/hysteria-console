import json
from concurrent.futures import ThreadPoolExecutor

import pytest

import state_store
import tuic_config as tc


def test_render_from_users_uses_real_tuic_users(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')
    cfg = tc.render_from_users({
        'alice': {'vless_uuid': 'uuid-A', 'sub_token': 'tok-A'},
        'bob': {'vless_uuid': 'uuid-B', 'sub_token': 'tok-B', 'disabled': True},
    })

    assert cfg['users'] == {'uuid-A': 'alice:tok-A'}
    assert not (tmp_path / 'locked.json').exists()


def test_render_from_users_skips_metered_users_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')
    cfg = tc.render_from_users({
        'alice': {'vless_uuid': 'uuid-A', 'sub_token': 'tok-A', 'metered': True},
        'bob': {'vless_uuid': 'uuid-B', 'sub_token': 'tok-B'},
        'carol': {
            'vless_uuid': 'uuid-C', 'sub_token': 'tok-C',
            'metered': True, 'tuic_enabled': True,
        },
    })

    assert cfg['users'] == {
        'uuid-B': 'bob:tok-B',
        'uuid-C': 'carol:tok-C',
    }


def test_render_from_users_adds_locked_user_when_empty(tmp_path, monkeypatch):
    secret_file = tmp_path / 'state' / 'locked.json'
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', secret_file)

    cfg = tc.render_from_users({})

    assert set(cfg['users']) == {tc.LOCKED_USER_UUID}
    assert cfg['users'][tc.LOCKED_USER_UUID]
    assert json.loads(secret_file.read_text())['password'] == cfg['users'][tc.LOCKED_USER_UUID]


def test_render_from_user_plan_adds_locked_user_when_all_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')

    cfg = tc.render_from_user_plan(
        {'alice': {'sub_token': 'tok-A'}},
        {'alice': None},
    )

    assert set(cfg['users']) == {tc.LOCKED_USER_UUID}


def test_sync_user_plan_empty_exact_plan_revokes_stale_credentials(
        tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    config_file.write_text(json.dumps({'users': {'uuid-A': 'alice:tok-A'}}))
    locked_file = tmp_path / 'locked.json'
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', locked_file)

    changed = tc.sync_user_plan({}, {}, path=config_file)

    assert changed is True
    assert set(json.loads(config_file.read_text())['users']) == {
        tc.LOCKED_USER_UUID,
    }
    assert locked_file.exists()
    assert tc._reload_pending_path(config_file).exists()


def test_sync_user_plan_preserves_operator_runtime_settings(
        tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    config_file.write_text(json.dumps({
        'server': '127.0.0.1:19443',
        'users': {'stale-uuid': 'stale:token'},
        'custom_operator_setting': {'enabled': True},
    }))
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')
    uid = '11111111-1111-4111-8111-111111111111'

    changed = tc.sync_user_plan(
        {'alice': {'vless_uuid': uid, 'sub_token': 'new-token'}},
        {'alice': uid},
        path=config_file,
    )

    assert changed is True
    cfg = json.loads(config_file.read_text())
    assert cfg['server'] == '127.0.0.1:19443'
    assert cfg['custom_operator_setting'] == {'enabled': True}
    assert cfg['users'] == {uid: 'alice:new-token'}


def test_concurrent_config_syncs_leave_complete_valid_json(tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    locked_file = tmp_path / 'locked.json'
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', locked_file)
    users = {
        f'user-{index}': {
            'vless_uuid': f'uuid-{index}',
            'sub_token': f'token-{index}',
        }
        for index in range(30)
    }

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(
            lambda _index: tc.sync_all(users=users, path=config_file),
            range(30),
        ))

    assert any(results)
    cfg = json.loads(config_file.read_text())
    assert cfg['users'] == {
        f'uuid-{index}': f'user-{index}:token-{index}'
        for index in range(30)
    }
    assert not list(tmp_path.glob('tuic.json.*.tmp'))


def test_sync_all_fails_closed_when_canonical_users_are_corrupt(
    tmp_path, monkeypatch
):
    users_file = tmp_path / 'users.json'
    config_file = tmp_path / 'tuic.json'
    users_file.write_text('{"broken":', encoding='utf-8')
    original = '{"users":{"uuid-A":"alice:token"}}\n'
    config_file.write_text(original, encoding='utf-8')
    monkeypatch.setattr(tc, 'USERS_FILE', users_file)
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')

    with pytest.raises(state_store.InvalidJsonState):
        tc.sync_all(path=config_file)

    assert config_file.read_text(encoding='utf-8') == original
    assert not tc._reload_pending_path(config_file).exists()


@pytest.mark.parametrize('corrupt_runtime', ['{"users":', '["not-an-object"]'])
def test_sync_preserves_existing_corrupt_runtime_config(
    tmp_path, monkeypatch, corrupt_runtime
):
    config_file = tmp_path / 'tuic.json'
    original = corrupt_runtime.encode('utf-8')
    config_file.write_bytes(original)
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')

    with pytest.raises(state_store.InvalidJsonState):
        tc.sync_all(
            users={
                'alice': {
                    'vless_uuid': '11111111-1111-4111-8111-111111111111',
                    'sub_token': 'token-A',
                },
            },
            path=config_file,
        )

    assert config_file.read_bytes() == original
    assert not tc._reload_pending_path(config_file).exists()


def metered_config():
    return {
        'inbounds': [{'type': 'tuic', 'tag': 'tuic-in', 'listen': '::',
                      'listen_port': 9443, 'users': [],
                      'congestion_control': 'bbr', 'zero_rtt_handshake': False,
                      'tls': {'enabled': True, 'alpn': ['h3']}}],
        'experimental': {'v2ray_api': {
            'listen': '127.0.0.1:10086',
            'stats': {'enabled': True, 'users': []},
        }},
    }


@pytest.mark.parametrize('use_plan', [False, True])
def test_metered_sync_replaces_named_users_and_statistics_together(tmp_path, use_plan):
    path = tmp_path / 'tuic.json'
    cfg = metered_config()
    cfg['inbounds'][0]['users'] = [{'name': 'stale', 'uuid': 'old', 'password': 'old'}]
    cfg['experimental']['v2ray_api']['stats']['users'] = ['stale']
    path.write_text(json.dumps(cfg))
    users = {'alice': {'vless_uuid': '11111111-1111-4111-8111-111111111111', 'sub_token': 'new'},
             'bob': {'vless_uuid': '22222222-2222-4222-8222-222222222222', 'sub_token': 'secret', 'disabled': True}}
    if use_plan:
        tc.sync_user_plan(users, {'alice': '11111111-1111-4111-8111-111111111111'}, path=path)
    else:
        tc.sync_all(users=users, path=path)
    actual = json.loads(path.read_text())
    assert 'server' not in actual
    assert actual['inbounds'][0]['users'] == [
        {'name': 'alice', 'uuid': '11111111-1111-4111-8111-111111111111', 'password': 'alice:new'},
    ]
    assert actual['experimental']['v2ray_api']['stats']['users'] == ['alice']
    assert actual['inbounds'][0]['listen_port'] == 9443
    tc.sync_user_plan({}, {}, path=path)
    actual = json.loads(path.read_text())
    assert actual['inbounds'][0]['users'] == []
    assert actual['experimental']['v2ray_api']['stats']['users'] == []


def test_metered_sync_rejects_missing_or_public_statistics(tmp_path):
    cfg = metered_config()
    cfg['experimental']['v2ray_api']['listen'] = '0.0.0.0:10086'
    path = tmp_path / 'tuic.json'
    path.write_text(json.dumps(cfg))
    with pytest.raises(state_store.StateStoreError):
        tc.sync_all(users={}, path=path)


def test_migration_maps_protocol_security_and_ipv6_udp_policy():
    cfg = tc.render_metered_runtime({
        'server': '[::]:9443', 'users': {}, 'certificate': '/tmp/cert',
        'private_key': '/tmp/key', 'congestion_control': 'bbr',
        'alpn': ['h3'], 'zero_rtt_handshake': False, 'dual_stack': True,
        'udp_relay_ipv6': False, 'auth_timeout': '3s', 'log_level': 'warn',
    }, {'alice': {'vless_uuid': 'uuid-a', 'sub_token': 'secret'}}, {'alice': 'uuid-a'})
    inbound = cfg['inbounds'][0]
    assert inbound['listen'] == '::' and inbound['listen_port'] == 9443
    assert inbound['zero_rtt_handshake'] is False
    assert inbound['tls']['alpn'] == ['h3']
    assert inbound['users'][0]['password'] == 'alice:secret'
    assert cfg['route']['rules'] == [{'network': 'udp', 'ip_version': 6, 'action': 'reject'}]


def test_migration_rejects_unmapped_tuning():
    with pytest.raises(state_store.StateStoreError, match='send_window'):
        tc.render_metered_runtime({'server': '[::]:9443', 'send_window': 123}, {}, {})


def test_default_legacy_tuning_requires_explicit_runtime_default_acknowledgment():
    with pytest.raises(state_store.StateStoreError, match='send_window'):
        tc.render_metered_runtime(tc._base_config(), {}, {})
    cfg = tc.render_metered_runtime(tc._base_config(), {}, {}, accept_runtime_defaults=True)
    assert cfg['inbounds'][0]['listen_port'] == 9443
    custom = tc._base_config()
    custom['send_window'] = 123
    with pytest.raises(state_store.StateStoreError, match='send_window'):
        tc.render_metered_runtime(custom, {}, {}, accept_runtime_defaults=True)


def test_fault_marker_blocks_named_auth_after_unrelated_sync(tmp_path):
    path = tmp_path / 'tuic.json'
    path.write_text(json.dumps(metered_config()))
    tc.accounting_fault_path(path).write_text('{}')
    tc.sync_user_plan({'alice': {'sub_token': 'secret'}}, {'alice': 'uuid-a'}, path=path)
    cfg = json.loads(path.read_text())
    assert cfg['inbounds'][0]['users'] == []


def test_metered_sync_without_quota_plan_is_conservative(tmp_path):
    path = tmp_path / 'tuic.json'
    path.write_text(json.dumps(metered_config()))
    base = {'vless_uuid': '11111111-1111-4111-8111-111111111111', 'sub_token': 'fixture'}
    tc.sync_all(users={
        'expired': {**base, 'expires_at': '2000-01-01'},
        'metered': {**base, 'metered': True, 'tuic_enabled': True},
        'malformed': {**base, 'expires_at': 'invalid'},
        'active': base,
    }, path=path)
    actual = json.loads(path.read_text())
    assert [u['name'] for u in actual['inbounds'][0]['users']] == ['active']


@pytest.mark.parametrize('names', [[], ['alice', 'alice'], ['bob']])
def test_metered_config_cannot_authenticate_users_without_stats(names):
    cfg = metered_config()
    cfg['inbounds'][0]['users'] = [{'name': 'alice', 'uuid': 'uuid-a', 'password': 'alice:fixture'}]
    cfg['experimental']['v2ray_api']['stats']['users'] = names
    with pytest.raises(state_store.StateStoreError):
        tc.metered_parts(cfg)


@pytest.mark.parametrize('field,value', [('uuid', 'other-uuid'), ('password', 'bob:fixture'), ('name', 'bob')])
def test_named_runtime_credentials_must_match_canonical_identity(field, value):
    cfg = metered_config()
    row = {'name': 'alice', 'uuid': 'alice-uuid', 'password': 'alice:fixture'}
    row[field] = value
    cfg['inbounds'][0]['users'] = [row]
    cfg['experimental']['v2ray_api']['stats']['users'] = [row['name']]
    with pytest.raises(state_store.StateStoreError):
        tc.validate_named_identities(cfg, {'alice': {'vless_uuid': 'alice-uuid', 'sub_token': 'fixture'}})


def test_active_mode_sync_refuses_missing_runtime_config(tmp_path):
    import tuic_user_meter as meter
    meter.initialize(tmp_path / 'state', activated_at='2026-10-02')
    with pytest.raises(state_store.StateStoreError):
        tc.sync_all(users={}, path=tmp_path / 'tuic.json')
    assert not (tmp_path / 'tuic.json').exists()


def test_offline_migration_does_not_consult_live_activation_or_fault_state(tmp_path, monkeypatch):
    live_config = tmp_path / 'tuic.json'
    monkeypatch.setattr(tc, 'CONFIG_FILE', live_config)
    tc.accounting_fault_path(live_config).write_text('{}')
    def forbidden(*args):
        raise AssertionError('offline rendering must not inspect live accounting mode')
    monkeypatch.setattr(tc.tuic_user_meter, 'accounting_mode', forbidden)
    cfg = tc.render_metered_runtime({}, {'alice': {'vless_uuid': 'uuid-a', 'sub_token': 'fixture'}}, {'alice': 'uuid-a'})
    assert cfg['inbounds'][0]['users'][0]['name'] == 'alice'
