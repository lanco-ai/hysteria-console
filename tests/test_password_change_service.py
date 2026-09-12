"""Password-change service behavior shared by legacy and JSON adapters."""

import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
import subscription_service as ss


def test_password_change_service_module_exposes_the_shared_boundary():
    try:
        module = importlib.import_module('password_change_service')
    except ModuleNotFoundError:
        pytest.fail('password_change_service must provide the shared boundary')

    assert module.PasswordChangeService
    assert module.PasswordChangeResult


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def password_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    admin_hash = ss.hash_secret('old-admin-password')
    alice_hash = ss.hash_secret('old-alice-password')
    bob_hash = ss.hash_secret('old-bob-password')
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': admin_hash,
            'admin_token': 'admin-token',
            'settings': {'keep': True},
        },
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'alice': {
                'panel_pass_hash': alice_hash,
                'panel_password_must_change': True,
                'sub_token': 'alice-token',
                'monthly_quota_bytes': 1000,
                'used_bytes': 200,
                'max_devices': 3,
                'proxy': {'host': 'private.example'},
            },
            'bob': {
                'panel_pass_hash': bob_hash,
                'sub_token': 'bob-token',
                'monthly_quota_bytes': 2000,
            },
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    return {**paths, 'admin_hash': admin_hash, 'alice_hash': alice_hash, 'bob_hash': bob_hash}


def test_password_change_result_is_frozen_slotted_and_hides_the_session_identifier():
    module = importlib.import_module('password_change_service')
    result = module.PasswordChangeResult(outcome='success', session_id='private-session-secret')

    assert result.session_id == 'private-session-secret'
    assert 'private-session-secret' not in repr(result)
    assert not hasattr(result, '__dict__')
    with pytest.raises(FrozenInstanceError):
        result.session_id = 'replacement'


def test_admin_change_delegates_existing_policy_and_replaces_all_admin_sessions(password_state):
    first = ss.create_session('admin', ss._credential_generation(password_state['admin_hash']))
    second = ss.create_session('admin', ss._credential_generation(password_state['admin_hash']))
    user_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    result = ss._password_change_service().change_admin(
        form={
            'current': ['old-admin-password'],
            'new': ['new-admin-password'],
            'confirm': ['new-admin-password'],
        }
    )

    assert result.outcome == 'success'
    meta = json.loads(password_state['META_FILE'].read_text(encoding='utf-8'))
    assert ss.verify_secret('new-admin-password', meta['admin_pass_hash'])
    assert meta['admin_token'] == 'admin-token'
    assert meta['settings'] == {'keep': True}
    sessions = ss.get_sessions()
    assert first not in sessions
    assert second not in sessions
    assert sessions[result.session_id]['credential_generation'] == ss._credential_generation(
        meta['admin_pass_hash']
    )
    assert user_sid in ss.get_user_sessions()


@pytest.mark.parametrize(
    ('form', 'code'),
    [
        ({}, 'password_wrong'),
        (
            {'current': ['wrong'], 'new': ['short'], 'confirm': ['different']},
            'password_wrong',
        ),
        (
            {'current': ['old-admin-password'], 'new': ['short'], 'confirm': ['short']},
            'password_short',
        ),
        (
            {
                'current': ['old-admin-password'],
                'new': ['x' * 257],
                'confirm': ['x' * 257],
            },
            'password_long',
        ),
        (
            {
                'current': ['old-admin-password'],
                'new': ['new-admin-password'],
                'confirm': ['different-password'],
            },
            'password_mismatch',
        ),
    ],
)
def test_admin_validation_codes_and_order_never_write_credentials_or_sessions(
    password_state,
    form,
    code,
):
    sid = ss.create_session('admin', ss._credential_generation(password_state['admin_hash']))
    before_meta = password_state['META_FILE'].read_bytes()
    before_sessions = password_state['SESSIONS_FILE'].read_bytes()

    result = ss._password_change_service().change_admin(form=form)

    assert result.outcome == 'invalid'
    assert result.code == code
    assert result.session_id == ''
    assert password_state['META_FILE'].read_bytes() == before_meta
    assert password_state['SESSIONS_FILE'].read_bytes() == before_sessions
    assert sid in ss.get_sessions()


def test_admin_policy_still_allows_reusing_the_current_password(password_state):
    old_sid = ss.create_session('admin', ss._credential_generation(password_state['admin_hash']))

    result = ss._password_change_service().change_admin(
        form={
            'current': ['old-admin-password'],
            'new': ['old-admin-password'],
            'confirm': ['old-admin-password'],
        }
    )

    assert result.outcome == 'success'
    assert old_sid not in ss.get_sessions()
    assert result.session_id in ss.get_sessions()


def test_user_change_preserves_account_fields_and_replaces_only_target_sessions(password_state):
    admin_sid = ss.create_session('admin', ss._credential_generation(password_state['admin_hash']))
    first = ss.create_user_session(
        'alice',
        ss._credential_generation(password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    second = ss.create_user_session(
        'alice',
        ss._credential_generation(password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    token_session = ss.create_user_session(
        'alice',
        ss._credential_generation('alice-token'),
        ss.USER_SESSION_SUBSCRIPTION_TOKEN,
    )
    bob_sid = ss.create_user_session(
        'bob',
        ss._credential_generation(password_state['bob_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    result = ss._password_change_service().change_user(
        username='alice',
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
        form={
            'current': ['old-alice-password'],
            'new': ['new-alice-password'],
            'confirm': ['new-alice-password'],
        },
    )

    assert result.outcome == 'success'
    users = json.loads(password_state['USERS_FILE'].read_text(encoding='utf-8'))
    alice = users['alice']
    assert ss.verify_secret('new-alice-password', alice['panel_pass_hash'])
    assert 'panel_password_must_change' not in alice
    assert alice['sub_token'] == 'alice-token'
    assert alice['monthly_quota_bytes'] == 1000
    assert alice['used_bytes'] == 200
    assert alice['max_devices'] == 3
    assert alice['proxy'] == {'host': 'private.example'}
    assert users['bob']['panel_pass_hash'] == password_state['bob_hash']
    sessions = ss.get_user_sessions()
    assert first not in sessions
    assert second not in sessions
    assert token_session not in sessions
    assert bob_sid in sessions
    assert sessions[result.session_id]['credential_kind'] == ss.USER_SESSION_PANEL_PASSWORD
    assert sessions[result.session_id]['credential_generation'] == ss._credential_generation(
        alice['panel_pass_hash']
    )
    assert admin_sid in ss.get_sessions()


@pytest.mark.parametrize(
    ('form', 'code'),
    [
        ({}, 'new password short'),
        (
            {'current': ['wrong'], 'new': ['short'], 'confirm': ['different']},
            'new password short',
        ),
        (
            {'current': ['old-alice-password'], 'new': ['short'], 'confirm': ['short']},
            'new password short',
        ),
        (
            {
                'current': ['old-alice-password'],
                'new': ['x' * 257],
                'confirm': ['x' * 257],
            },
            'new password long',
        ),
        (
            {
                'current': ['old-alice-password'],
                'new': ['new-alice-password'],
                'confirm': ['different-password'],
            },
            'new password mismatch',
        ),
        (
            {
                'current': ['wrong-alice-password'],
                'new': ['new-alice-password'],
                'confirm': ['new-alice-password'],
            },
            'current password wrong',
        ),
        (
            {
                'current': ['old-alice-password'],
                'new': ['old-alice-password'],
                'confirm': ['old-alice-password'],
            },
            'new password same',
        ),
    ],
)
def test_user_validation_codes_and_order_never_write_credentials_or_sessions(
    password_state,
    form,
    code,
):
    sid = ss.create_user_session(
        'alice',
        ss._credential_generation(password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    before_users = password_state['USERS_FILE'].read_bytes()
    before_sessions = password_state['USER_SESSIONS_FILE'].read_bytes()

    result = ss._password_change_service().change_user(
        username='alice',
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
        form=form,
    )

    assert result.outcome == 'invalid'
    assert result.code == code
    assert result.session_id == ''
    assert password_state['USERS_FILE'].read_bytes() == before_users
    assert password_state['USER_SESSIONS_FILE'].read_bytes() == before_sessions
    assert sid in ss.get_user_sessions()


@pytest.mark.parametrize('username', ['', 'missing'])
@pytest.mark.parametrize('session_kind', ['', ss.USER_SESSION_SUBSCRIPTION_TOKEN])
def test_user_missing_or_non_password_identity_returns_before_reading_users(
    password_state,
    monkeypatch,
    username,
    session_kind,
):
    del password_state

    def forbidden_read(*_args, **_kwargs):
        raise AssertionError('identity rejection must happen before user state reads')

    monkeypatch.setattr(ss, 'load_json', forbidden_read)
    result = ss._password_change_service().change_user(
        username=username,
        session_kind=session_kind,
        form={'current': ['secret'], 'new': ['new-password'], 'confirm': ['new-password']},
    )

    assert result.outcome == 'login_required'


@pytest.mark.parametrize(
    ('account_change', 'outcome'),
    [
        (None, 'forbidden'),
        ({'disabled': True}, 'disabled'),
        ({'expires_at': '2000-01-01'}, 'expired'),
    ],
)
def test_user_initial_lifecycle_rejection_happens_before_form_validation(
    password_state,
    account_change,
    outcome,
):
    users = json.loads(password_state['USERS_FILE'].read_text(encoding='utf-8'))
    if account_change is None:
        del users['alice']
    else:
        users['alice'].pop('panel_password_must_change', None)
        users['alice'].update(account_change)
    _write_json(password_state['USERS_FILE'], users)
    before = password_state['USERS_FILE'].read_bytes()

    result = ss._password_change_service().change_user(
        username='alice',
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
        form={},
    )

    assert result.outcome == outcome
    assert password_state['USERS_FILE'].read_bytes() == before


@pytest.mark.parametrize(
    ('state_change', 'outcome'),
    [('missing', 'forbidden'), ('disabled', 'disabled'), ('expired', 'expired')],
)
def test_user_locked_recheck_observes_a_state_change_without_writing_a_password(
    password_state,
    monkeypatch,
    state_change,
    outcome,
):
    original_load = ss.load_json
    reads = 0
    initial = original_load(password_state['USERS_FILE'], {})
    initial['alice'].pop('panel_password_must_change', None)
    ss.save_json(password_state['USERS_FILE'], initial)
    old_hash = initial['alice']['panel_pass_hash']

    def change_before_locked_read(path, default, **kwargs):
        nonlocal reads
        result = original_load(path, default, **kwargs)
        if Path(path) == password_state['USERS_FILE']:
            reads += 1
            if reads == 1:
                changed = original_load(path, {})
                if state_change == 'missing':
                    del changed['alice']
                elif state_change == 'disabled':
                    changed['alice']['disabled'] = True
                else:
                    changed['alice']['expires_at'] = '2000-01-01'
                ss.save_json(path, changed)
        return result

    monkeypatch.setattr(ss, 'load_json', change_before_locked_read)
    result = ss._password_change_service().change_user(
        username='alice',
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
        form={
            'current': ['old-alice-password'],
            'new': ['new-alice-password'],
            'confirm': ['new-alice-password'],
        },
    )

    assert reads == 2
    assert result.outcome == outcome
    users = original_load(password_state['USERS_FILE'], {})
    if state_change == 'missing':
        assert 'alice' not in users
    else:
        assert users['alice']['panel_pass_hash'] == old_hash


def test_must_change_priority_still_allows_change_when_disabled_is_also_set(password_state):
    users = json.loads(password_state['USERS_FILE'].read_text(encoding='utf-8'))
    users['alice']['disabled'] = True
    _write_json(password_state['USERS_FILE'], users)

    result = ss._password_change_service().change_user(
        username='alice',
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
        form={
            'current': ['old-alice-password'],
            'new': ['new-alice-password'],
            'confirm': ['new-alice-password'],
        },
    )

    assert result.outcome == 'success'
    changed = json.loads(password_state['USERS_FILE'].read_text(encoding='utf-8'))['alice']
    assert changed['disabled'] is True
    assert 'panel_password_must_change' not in changed


def test_replacement_failure_keeps_new_hash_authoritative_and_old_session_rejects(
    password_state,
    monkeypatch,
):
    old_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    def replacement_failure(*_args, **_kwargs):
        raise OSError('private session storage path')

    monkeypatch.setattr(ss, '_replace_sessions_with_new', replacement_failure)
    with pytest.raises(OSError, match='private session storage path'):
        ss._password_change_service().change_user(
            username='alice',
            session_kind=ss.USER_SESSION_PANEL_PASSWORD,
            form={
                'current': ['old-alice-password'],
                'new': ['new-alice-password'],
                'confirm': ['new-alice-password'],
            },
        )

    cfg = json.loads(password_state['USERS_FILE'].read_text(encoding='utf-8'))['alice']
    assert ss.verify_secret('new-alice-password', cfg['panel_pass_hash'])
    assert old_sid in json.loads(password_state['USER_SESSIONS_FILE'].read_text(encoding='utf-8'))
    request = type(
        'Request',
        (),
        {'headers': {'Cookie': f'usid={old_sid}'}, 'path': '/user/panel'},
    )()
    assert ss.get_logged_in_user_context(request) == ('', '')
    assert old_sid not in ss.get_user_sessions()
