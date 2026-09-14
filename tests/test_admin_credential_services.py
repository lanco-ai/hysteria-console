"""Administrator credential mutations preserve durable work and lock boundaries."""

import importlib
import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from uuid import UUID

import pytest
import revocation_queue
import state_store
import static_access
import subscription_service as ss


def read(path):
    return json.loads(path.read_text())


@pytest.fixture
def credential_state(tmp_path, monkeypatch):
    for name in (
        'USERS_FILE',
        'USAGE_FILE',
        'USAGE_DAILY_FILE',
        'USAGE_HOURLY_FILE',
        'USAGE_PRESERVED_FILE',
        'USER_SESSIONS_FILE',
        'REVOCATION_QUEUE_FILE',
        'ROTATION_RECEIPTS_FILE',
        'USAGE_LOCK_FILE',
    ):
        monkeypatch.setattr(ss, name, tmp_path / (name.lower() + '.json'))
    user = {'sub_token': 'old-private-token', 'vless_uuid': str(UUID(int=1)), 'marker': 'keep'}
    ss.save_json(ss.USERS_FILE, {'fixture': user, 'other': {'sub_token': 'other'}})
    for path in (ss.USAGE_FILE, ss.USAGE_DAILY_FILE, ss.USAGE_HOURLY_FILE):
        ss.save_json(path, {'2026-09': {'fixture': {'total': 8}, 'other': {'total': 4}}})
    ss.save_json(ss.USER_SESSIONS_FILE, {})
    events = []
    held = False
    real_lock = ss.usage_lock
    real_save = ss.save_json
    real_prepare = revocation_queue.prepare
    real_purge = ss._purge_user_history_locked

    @contextmanager
    def lock():
        nonlocal held
        with real_lock():
            held = True
            events.append('lock')
            try:
                yield
            finally:
                held = False
                events.append('unlock')

    def prepare(*args, **kwargs):
        assert held
        events.append('wal')
        return real_prepare(*args, **kwargs)

    def save(path, value):
        if path == ss.USERS_FILE:
            assert held and read(ss.REVOCATION_QUEUE_FILE)
            events.append('save')
        return real_save(path, value)

    def sync(_users):
        assert held
        events.append('sync')
        return True, True

    def kick(users):
        assert not held and users == ['fixture']
        events.append('kick')

    def audit(*args):
        assert not held
        events.append(('audit', args))

    def purge(username):
        assert held
        events.append('history')
        real_purge(username)

    monkeypatch.setattr(ss, 'usage_lock', lock)
    monkeypatch.setattr(ss, 'save_json', save)
    monkeypatch.setattr(revocation_queue, 'prepare', prepare)
    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss, 'hy_kick', kick)
    monkeypatch.setattr(ss, '_using_live_core_state', lambda: False)
    monkeypatch.setattr(ss, '_purge_user_history_locked', purge)
    return SimpleNamespace(user=user, events=events, audit=audit, save=save, held=lambda: held)


def invoke(state, action, *, user=' fixture ', revision=None):
    if revision is None:
        revision = ss.user_config_revision(state.user)
    service = (
        ss._admin_credential_service(state.audit)
        if action == 'rotate'
        else ss._user_deletion_service()
    )
    assert not hasattr(service, '__dict__')
    return getattr(service, action)(form={'user': [user, 'other']}, expected_revision=revision)


@pytest.mark.parametrize('action', ['rotate', 'delete'])
def test_mutations_commit_after_wal_and_effects_outside_lock(credential_state, action):
    state = credential_state
    result = invoke(state, action)
    assert result.outcome == 'success' and result.username == 'fixture'
    assert result.code == ('rotated' if action == 'rotate' else 'deleted')
    assert state.events[:4] == ['lock', 'wal', 'save', 'sync']
    assert state.events.index('unlock') < state.events.index('kick')
    tasks = list(read(ss.REVOCATION_QUEUE_FILE).values())
    assert len(tasks) == 1 and tasks[0]['user'] == 'fixture'
    assert tasks[0]['previous_generation'] == (
        ss._credential_generation('old-private-token')
        if action == 'rotate'
        else 'user-revision-v1:' + ss.user_config_revision(state.user)
    )
    users = read(ss.USERS_FILE)
    if action == 'rotate':
        assert users['fixture']['marker'] == 'keep'
        assert users['fixture']['sub_token'] != 'old-private-token'
        assert UUID(users['fixture']['vless_uuid']) != UUID(int=1)
        assert result.subscription_token == users['fixture']['sub_token']
        assert result.subscription_token not in repr(result)
        assert tasks[0]['target_generation'] == ss._credential_generation(result.subscription_token)
        assert state.events[-1] == ('audit', ('rotate_token', 'fixture', {}, {}))
    else:
        assert 'fixture' not in users and 'other' in users
        assert tasks[0]['target_generation'] == ss._DELETE_TARGET_GENERATION
        assert 'history' in state.events
        assert result.subscription_token == ''
        for history in (ss.USAGE_FILE, ss.USAGE_DAILY_FILE, ss.USAGE_HOURLY_FILE):
            assert read(history) == {'2026-09': {'other': {'total': 4}}}
    assert not hasattr(result, '__dict__')
    with pytest.raises(FrozenInstanceError):
        result.username = 'other'


@pytest.mark.parametrize('action', ['rotate', 'delete'])
@pytest.mark.parametrize(
    ('target', 'revision', 'outcome'),
    [
        ('missing', '', 'not_found'),
        ('fixture', 'stale', 'conflict'),
        ('wrong-type', '', 'not_found'),
    ],
)
def test_rejected_targets_leave_state_and_effects_untouched(
    credential_state,
    action,
    target,
    revision,
    outcome,
):
    state = credential_state
    if target == 'wrong-type':
        state_store.save_json(ss.USERS_FILE, {'wrong-type': []})
    before = ss.USERS_FILE.read_bytes()
    result = invoke(state, action, user=target, revision=revision)
    assert result.outcome == outcome and result.code == ''
    assert ss.USERS_FILE.read_bytes() == before
    assert not ss.REVOCATION_QUEUE_FILE.exists()
    assert state.events == ['lock', 'unlock']


def stopped(service, *, ok=True, confirmed=True):
    return static_access.ServiceActionResult(
        service,
        'stop_fail_closed',
        True,
        ok,
        confirmed,
        True,
        'fixture',
        not ok,
    )


@pytest.mark.parametrize(
    ('failure', 'code'),
    [
        ('durability', 'err:rotated_pending'),
        ('critical-sync', 'err:rotated_pending'),
        ('stop-failed', 'err:rotated_retry'),
        ('reload', 'err:rotated_static_pending'),
        ('reload-stop-failed', 'err:rotated_retry'),
        ('retry-record', 'err:rotated_retry'),
        ('kick-record', 'err:rotated_retry'),
        ('kick', 'err:rotated_retry'),
        ('pending-kick-record', 'err:rotated_retry'),
    ],
)
def test_rotation_pending_outcome_precedence_and_retained_tasks(
    credential_state,
    monkeypatch,
    failure,
    code,
):
    state = credential_state

    def fail_sync(_users):
        raise state_store.CriticalStateUnavailable('private state')

    def uncertain_save(path, value):
        state.save(path, value)
        if path == ss.USERS_FILE:
            raise state_store.AtomicReplaceDurabilityUncertain('private durability')

    def fail_closed(_error):
        assert not state.held()
        return {
            service: stopped(
                service,
                ok=failure not in ('stop-failed', 'retry-record'),
                confirmed=failure != 'stop-failed',
            )
            for service in static_access.SERVICES
        }

    monkeypatch.setattr(ss, '_fail_closed_static_access', fail_closed)
    if failure == 'durability':
        monkeypatch.setattr(ss, 'save_json', uncertain_save)
    if failure in ('critical-sync', 'stop-failed', 'retry-record', 'pending-kick-record'):
        monkeypatch.setattr(ss, '_sync_static_access_from_users', fail_sync)
    if failure.startswith('reload'):
        monkeypatch.setattr(
            ss, '_schedule_static_reload', lambda *_a, **_k: SimpleNamespace(ok=False)
        )
        monkeypatch.setattr(
            static_access,
            'stop_fail_closed',
            lambda service, **_kw: stopped(
                service, ok=failure == 'reload', confirmed=failure == 'reload'
            ),
        )
    if failure == 'retry-record':
        monkeypatch.setattr(
            revocation_queue,
            'add_static_services',
            lambda *_a: (_ for _ in ()).throw(OSError('record')),
        )
    if failure in ('kick-record', 'pending-kick-record'):
        monkeypatch.setattr(
            revocation_queue,
            'complete_attempt',
            lambda *_a, **_k: (_ for _ in ()).throw(OSError('record')),
        )
    if failure == 'kick':
        monkeypatch.setattr(ss, 'hy_kick', lambda _u: False)
    result = invoke(state, 'rotate')
    assert result.outcome == 'success' and result.code == code
    assert read(ss.USERS_FILE)['fixture']['sub_token'] == result.subscription_token
    assert len(read(ss.REVOCATION_QUEUE_FILE)) == 1
    if failure == 'durability':
        assert 'sync' not in state.events


@pytest.mark.parametrize('failure', ['save', 'durability', 'sync', 'history', 'kick'])
def test_deletion_errors_retain_durable_tasks_without_rollback(
    credential_state, monkeypatch, failure
):
    state = credential_state

    def fail_save(path, value):
        if failure == 'durability':
            state.save(path, value)
            raise state_store.AtomicReplaceDurabilityUncertain(path)
        raise OSError('private save error')

    def fail(*_args, **_kwargs):
        raise OSError('private state error')

    if failure in ('save', 'durability'):
        monkeypatch.setattr(ss, 'save_json', fail_save)
    elif failure == 'sync':
        monkeypatch.setattr(ss, '_sync_static_access_from_users', fail)
    elif failure == 'history':
        monkeypatch.setattr(ss, '_purge_user_history_locked', fail)
    else:
        monkeypatch.setattr(ss, 'hy_kick', lambda _u: False)
    result = invoke(state, 'delete')
    assert result.outcome == 'success' and result.code == 'err:deleted_retry'
    assert len(read(ss.REVOCATION_QUEUE_FILE)) == 1
    assert ('fixture' in read(ss.USERS_FILE)) is (failure == 'save')
    if failure in ('save', 'durability'):
        assert 'sync' not in state.events and 'history' not in state.events


@pytest.mark.parametrize('failure', ['wal', 'save', 'sync', 'kick', 'audit'])
def test_rotation_unhandled_exceptions_propagate_once(credential_state, monkeypatch, failure):
    state = credential_state
    calls = []

    def fail(*_args, **_kwargs):
        calls.append(failure)
        raise RuntimeError(failure)

    if failure == 'wal':
        monkeypatch.setattr(revocation_queue, 'prepare', fail)
    elif failure == 'save':
        monkeypatch.setattr(ss, 'save_json', fail)
    elif failure == 'sync':
        monkeypatch.setattr(ss, '_sync_static_access_from_users', fail)
    elif failure == 'kick':
        monkeypatch.setattr(ss, 'hy_kick', fail)
    else:
        state.audit = fail
    with pytest.raises(RuntimeError, match=failure):
        invoke(state, 'rotate')
    assert calls == [failure]
    assert (read(ss.USERS_FILE)['fixture']['sub_token'] == 'old-private-token') is (
        failure in ('wal', 'save')
    )


def test_services_have_no_http_dependency():
    for name in ('admin_credential_service', 'user_deletion_service'):
        module = importlib.import_module(name)
        assert 'subscription_service' not in vars(module)
