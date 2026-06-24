import json

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


def test_sync_user_plan_ignores_empty_transient_input(tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    config_file.write_text(json.dumps({'users': {'uuid-A': 'alice:tok-A'}}))
    monkeypatch.setattr(tc, 'LOCKED_USER_FILE', tmp_path / 'locked.json')

    changed = tc.sync_user_plan({}, {}, path=config_file)

    assert changed is False
    assert json.loads(config_file.read_text())['users'] == {'uuid-A': 'alice:tok-A'}
    assert not (tmp_path / 'locked.json').exists()
