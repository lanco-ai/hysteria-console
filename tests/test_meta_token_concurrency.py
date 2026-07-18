import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import pytest

import state_store
import subscription_service as ss


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


def _configure_meta(tmp_path, monkeypatch, password='old-password'):
    meta_path = tmp_path / 'subscription_meta.json'
    monkeypatch.setattr(ss, 'META_FILE', meta_path)
    old_hash = ss.hash_secret(password)
    _write_json(meta_path, {
        'admin_user': 'admin',
        'admin_token': 'admin-token',
        'admin_pass_hash': old_hash,
        'settlement_day': 12,
        'cycle_length_days': 30,
    })
    return meta_path, old_hash


def _configure_users(tmp_path, monkeypatch, user):
    users_path = tmp_path / 'users.json'
    monkeypatch.setattr(ss, 'USERS_FILE', users_path)
    monkeypatch.setattr(ss, 'USAGE_LOCK_FILE', tmp_path / 'usage.lock')
    _write_json(users_path, {'alice': user})
    return users_path


def test_runtime_meta_is_required_but_first_deploy_initializer_is_explicit(
        tmp_path, monkeypatch):
    meta_path = tmp_path / 'subscription_meta.json'
    monkeypatch.setattr(ss, 'META_FILE', meta_path)

    with pytest.raises(state_store.InvalidJsonState, match='missing required'):
        ss.load_meta()
    with pytest.raises(state_store.InvalidJsonState, match='missing required'):
        ss.get_settlement_day()

    initialized = ss.ensure_meta()

    assert meta_path.exists()
    assert initialized == ss.load_meta()
    assert initialized['admin_token']
    assert initialized['admin_pass_hash']


def test_cycle_update_waits_for_password_rekey_and_preserves_both_changes(
        tmp_path, monkeypatch):
    meta_path, old_hash = _configure_meta(tmp_path, monkeypatch)
    real_save_json = ss.save_json
    password_save_entered = threading.Event()
    allow_password_save = threading.Event()
    cycle_started = threading.Event()
    cycle_finished = threading.Event()

    def blocking_save_json(path, data):
        if (
            Path(path) == meta_path
            and data.get('admin_pass_hash') != old_hash
            and data.get('settlement_day') == 12
        ):
            password_save_entered.set()
            assert allow_password_save.wait(2)
        return real_save_json(path, data)

    def update_cycle():
        cycle_started.set()
        try:
            return ss._update_cycle_meta(
                18, 15, now=datetime(2026, 7, 18, 12, 0, 0),
            )
        finally:
            cycle_finished.set()

    monkeypatch.setattr(ss, 'save_json', blocking_save_json)
    with ThreadPoolExecutor(max_workers=2) as pool:
        password_future = pool.submit(
            ss._change_admin_password,
            'old-password',
            'new-password',
            'new-password',
        )
        assert password_save_entered.wait(2)
        cycle_future = pool.submit(update_cycle)
        assert cycle_started.wait(2)
        try:
            assert not cycle_finished.wait(0.1)
        finally:
            allow_password_save.set()
        assert password_future.result(timeout=2)[0] == 'ok'
        cycle_future.result(timeout=2)

    stored = json.loads(meta_path.read_text(encoding='utf-8'))
    assert stored['settlement_day'] == 18
    assert stored['cycle_length_days'] == 15
    assert ss.verify_secret('new-password', stored['admin_pass_hash'])
    assert not ss.verify_secret('old-password', stored['admin_pass_hash'])


def test_plaintext_migration_and_cycle_update_share_meta_lock(
        tmp_path, monkeypatch):
    meta_path = tmp_path / 'subscription_meta.json'
    monkeypatch.setattr(ss, 'META_FILE', meta_path)
    _write_json(meta_path, {
        'admin_user': 'admin',
        'admin_token': 'admin-token',
        'admin_pass': 'legacy-password',
        'settlement_day': 12,
    })
    real_save_json = ss.save_json
    migration_save_entered = threading.Event()
    allow_migration_save = threading.Event()
    cycle_started = threading.Event()
    cycle_finished = threading.Event()

    def blocking_save_json(path, data):
        if Path(path) == meta_path and 'admin_pass_hash' in data:
            migration_save_entered.set()
            assert allow_migration_save.wait(2)
        return real_save_json(path, data)

    def update_cycle():
        cycle_started.set()
        try:
            ss._update_cycle_meta(
                20, now=datetime(2026, 7, 20, 12, 0, 0),
            )
        finally:
            cycle_finished.set()

    monkeypatch.setattr(ss, 'save_json', blocking_save_json)
    with ThreadPoolExecutor(max_workers=2) as pool:
        migration_future = pool.submit(ss.migrate_admin_password)
        assert migration_save_entered.wait(2)
        cycle_future = pool.submit(update_cycle)
        assert cycle_started.wait(2)
        try:
            assert not cycle_finished.wait(0.1)
        finally:
            allow_migration_save.set()
        migration_future.result(timeout=2)
        cycle_future.result(timeout=2)

    stored = json.loads(meta_path.read_text(encoding='utf-8'))
    assert stored['settlement_day'] == 20
    assert 'admin_pass' not in stored
    assert ss.verify_secret('legacy-password', stored['admin_pass_hash'])


def test_only_one_concurrent_request_can_rotate_the_same_current_token(
        tmp_path, monkeypatch):
    users_path = _configure_users(tmp_path, monkeypatch, {
        'sub_token': 'old-token',
        'vless_uuid': '11111111-1111-4111-8111-111111111111',
    })
    synced = []
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda users: synced.append(users) or (True, True),
    )
    start = threading.Barrier(2)

    def rotate():
        start.wait()
        return ss._rotate_user_token_if_current(
            'alice', 'old-token', today=date(2026, 7, 18),
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: rotate(), range(2)))

    successes = [result for result in results if result[0] == 'ok']
    denied = [result for result in results if result[0] == 'forbidden']
    assert len(successes) == 1
    assert len(denied) == 1
    stored = json.loads(users_path.read_text(encoding='utf-8'))
    assert stored['alice']['sub_token'] == successes[0][1]
    assert stored['alice']['sub_token'] != 'old-token'
    assert stored['alice']['vless_uuid'] != (
        '11111111-1111-4111-8111-111111111111'
    )
    assert len(synced) == 1
    assert synced[0]['alice']['sub_token'] == successes[0][1]


@pytest.mark.parametrize(
    ('extra', 'expected'),
    [
        ({'disabled': True}, 'disabled'),
        ({'expires_at': '2026-07-17'}, 'expired'),
    ],
)
def test_rotate_rechecks_latest_inactive_state_under_user_lock(
        tmp_path, monkeypatch, extra, expected):
    users_path = _configure_users(tmp_path, monkeypatch, {
        'sub_token': 'current-token',
        'vless_uuid': '11111111-1111-4111-8111-111111111111',
        **extra,
    })
    sync_calls = []
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda users: sync_calls.append(users) or (False, False),
    )

    result = ss._rotate_user_token_if_current(
        'alice', 'current-token', today=date(2026, 7, 18),
    )

    assert result == (expected, '', False, False)
    stored = json.loads(users_path.read_text(encoding='utf-8'))
    assert stored['alice']['sub_token'] == 'current-token'
    assert sync_calls == []
