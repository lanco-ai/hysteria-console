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
