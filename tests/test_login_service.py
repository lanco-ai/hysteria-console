"""Shared login decisions are transport-free and reservation-safe."""

import threading
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from pathlib import Path

import pytest
from login_service import LoginResult, LoginService
from login_throttle import LoginThrottle

CLIENT_IP = '198.51.100.8'
ADMIN_HASH = 'admin-fixture-hash'
USER_HASH = 'alice-fixture-hash'


def _service(*, users=None, max_attempts=3, **overrides):
    failures = {}
    user_failures = {}
    inflight = {}
    throttle = LoginThrottle(
        failures,
        inflight,
        threading.Lock(),
        lambda: 100.0,
        max_attempts,
        3600,
        32,
    )
    calls = {'verified': [], 'admin_sessions': [], 'user_sessions': []}
    stored_users = (
        {
            'alice': {
                'panel_pass_hash': USER_HASH,
                'monthly_quota_bytes': 1024,
            }
        }
        if users is None
        else users
    )

    def verify_secret(plain, stored_hash):
        calls['verified'].append((plain, stored_hash))
        return plain == 'correct-password' and stored_hash in {ADMIN_HASH, USER_HASH}

    def create_session(username, generation):
        calls['admin_sessions'].append((username, generation))
        return 'admin-session-secret'

    def create_user_session(username, generation):
        calls['user_sessions'].append((username, generation))
        return 'user-session-secret'

    dependencies = {
        'PASSWORD_MAX_LENGTH': 32,
        'USERS_FILE': Path('/fictional/users.json'),
        '_LOGIN_WINDOW': 3600,
        '_begin_login_attempt': throttle._begin_login_attempt,
        '_finish_login_attempt': throttle._finish_login_attempt,
        '_user_login_failures': user_failures,
        '_credential_generation': lambda stored_hash: f'generation:{stored_hash}',
        'create_session': create_session,
        'create_user_session': create_user_session,
        'is_valid_username': lambda username: username == 'alice',
        'load_json': lambda path, default: stored_users,
        'local_now': lambda: datetime(2026, 9, 12, 12, 0, 0),
        'verify_secret': verify_secret,
    }
    dependencies.update(overrides)
    return (
        LoginService(**dependencies),
        throttle,
        failures,
        user_failures,
        calls,
    )


def _admin_form(password='correct-password', username='admin'):
    return {'admin_username': [username], 'admin_password': [password]}


def _user_form(password='correct-password', username='alice'):
    return {'user_username': [username], 'user_password': [password]}


def _meta():
    return {'admin_user': 'admin', 'admin_pass_hash': ADMIN_HASH}


def test_login_result_is_typed_immutable_and_hides_session_id_from_repr():
    result = LoginResult(
        outcome='success',
        realm='admin',
        username='admin',
        session_id='internal-session-secret',
        redirect_to='/admin?msg=login+success',
    )

    assert result.outcome == 'success'
    assert result.session_id == 'internal-session-secret'
    assert 'internal-session-secret' not in repr(result)
    assert 'password' not in result.__slots__
    assert 'hash' not in result.__slots__
    assert 'password' not in repr(result)
    assert 'fixture-hash' not in repr(result)
    with pytest.raises(FrozenInstanceError):
        result.outcome = 'invalid'


def test_admin_first_values_are_used_after_username_trim_and_take_precedence():
    service, throttle, failures, user_failures, calls = _service()
    result = service.authenticate(
        form={
            'admin_username': [' admin ', 'intruder'],
            'admin_password': ['correct-password', 'wrong'],
            'user_username': ['alice'],
            'user_password': ['correct-password'],
        },
        meta=_meta(),
        client_ip=CLIENT_IP,
    )

    assert result == LoginResult(
        outcome='success',
        realm='admin',
        username='admin',
        session_id='admin-session-secret',
        redirect_to='/admin?msg=login+success',
    )
    assert calls['verified'] == [('correct-password', ADMIN_HASH)]
    assert calls['admin_sessions'] == [('admin', f'generation:{ADMIN_HASH}')]
    assert calls['user_sessions'] == []
    assert failures == {}
    assert user_failures == {}
    assert throttle.inflight == {}


def test_missing_username_does_not_reserve_or_verify():
    service, throttle, failures, user_failures, calls = _service()

    result = service.authenticate(
        form={'admin_password': ['correct-password']},
        meta=_meta(),
        client_ip=CLIENT_IP,
    )

    assert result == LoginResult(outcome='missing')
    assert calls['verified'] == []
    assert failures == {}
    assert user_failures == {}
    assert throttle.inflight == {}


@pytest.mark.parametrize(
    ('form', 'expected_realm', 'expected_username', 'failure_bucket'),
    [
        (_admin_form(password='wrong'), 'admin', 'admin', 'admin'),
        (_user_form(password='wrong'), 'user', 'alice', 'user'),
    ],
)
def test_invalid_credentials_record_one_failure(
    form, expected_realm, expected_username, failure_bucket
):
    service, throttle, failures, user_failures, calls = _service()

    result = service.authenticate(form=form, meta=_meta(), client_ip=CLIENT_IP)

    assert result == LoginResult(
        outcome='invalid', realm=expected_realm, username=expected_username
    )
    selected = failures if failure_bucket == 'admin' else user_failures
    other = user_failures if failure_bucket == 'admin' else failures
    assert selected == {CLIENT_IP: [100.0]}
    assert other == {}
    assert calls['admin_sessions'] == []
    assert calls['user_sessions'] == []
    assert throttle.inflight == {}


@pytest.mark.parametrize('form', [_admin_form(password='x' * 33), _user_form(password='x' * 33)])
def test_overlong_password_is_invalid_without_verification(form):
    service, throttle, failures, user_failures, calls = _service()

    result = service.authenticate(form=form, meta=_meta(), client_ip=CLIENT_IP)

    assert result.outcome == 'invalid'
    assert calls['verified'] == []
    assert throttle.inflight == {}
    assert sum(map(len, failures.values())) + sum(map(len, user_failures.values())) == 1


def test_admin_and_user_throttle_buckets_are_independent():
    service, throttle, failures, user_failures, _calls = _service(max_attempts=1)
    admin_invalid = service.authenticate(
        form=_admin_form(password='wrong'), meta=_meta(), client_ip=CLIENT_IP
    )
    admin_blocked = service.authenticate(
        form=_admin_form(password='wrong'), meta=_meta(), client_ip=CLIENT_IP
    )
    user_invalid = service.authenticate(
        form=_user_form(password='wrong'), meta=_meta(), client_ip=CLIENT_IP
    )

    assert admin_invalid.outcome == 'invalid'
    assert admin_blocked == LoginResult(
        outcome='throttled',
        realm='admin',
        username='admin',
        retry_after=3600,
    )
    assert user_invalid.outcome == 'invalid'
    assert failures == {CLIENT_IP: [100.0]}
    assert user_failures == {CLIENT_IP: [100.0]}
    assert throttle.inflight == {}


def test_throttled_attempt_does_not_release_another_requests_reservation():
    service, throttle, failures, _user_failures, calls = _service(max_attempts=1)
    assert throttle._begin_login_attempt(CLIENT_IP, failures)

    result = service.authenticate(form=_admin_form(), meta=_meta(), client_ip=CLIENT_IP)

    assert result.outcome == 'throttled'
    assert result.retry_after == 3600
    assert calls['verified'] == []
    assert throttle.inflight == {(id(failures), CLIENT_IP): 1}
    throttle._finish_login_attempt(CLIENT_IP, None, failures)


@pytest.mark.parametrize(
    ('account_state', 'expected_outcome'),
    [({'disabled': True}, 'disabled'), ({'expires_at': '2026-09-11'}, 'expired')],
)
def test_correct_but_ineligible_user_preserves_prior_failures(account_state, expected_outcome):
    users = {
        'alice': {
            'panel_pass_hash': USER_HASH,
            'monthly_quota_bytes': 1024,
            **account_state,
        }
    }
    service, throttle, _failures, user_failures, calls = _service(users=users)
    user_failures[CLIENT_IP] = [90.0]

    result = service.authenticate(form=_user_form(), meta=_meta(), client_ip=CLIENT_IP)

    assert result == LoginResult(outcome=expected_outcome, realm='user', username='alice')
    assert user_failures == {CLIENT_IP: [90.0]}
    assert calls['user_sessions'] == []
    assert throttle.inflight == {}


@pytest.mark.parametrize(
    ('must_change', 'expected_redirect'),
    [(False, '/user/panel'), (True, '/user/change-password')],
)
def test_user_success_uses_generation_and_default_session_creator_contract(
    must_change, expected_redirect
):
    users = {
        'alice': {
            'panel_pass_hash': USER_HASH,
            'panel_password_must_change': must_change,
        }
    }
    service, throttle, _failures, user_failures, calls = _service(users=users)
    user_failures[CLIENT_IP] = [90.0]

    result = service.authenticate(
        form={
            'user_username': [' alice ', 'intruder'],
            'user_password': ['correct-password', 'wrong'],
        },
        meta=_meta(),
        client_ip=CLIENT_IP,
    )

    assert result == LoginResult(
        outcome='success',
        realm='user',
        username='alice',
        session_id='user-session-secret',
        redirect_to=expected_redirect,
    )
    assert calls['user_sessions'] == [('alice', f'generation:{USER_HASH}')]
    assert user_failures == {}
    assert throttle.inflight == {}


@pytest.mark.parametrize('realm', ['admin', 'user'])
def test_verifier_exception_releases_reservation_and_preserves_history(realm):
    def raise_from_verifier(_plain, _stored_hash):
        raise RuntimeError(f'{realm} verifier failed')

    service, throttle, failures, user_failures, calls = _service(verify_secret=raise_from_verifier)
    selected = failures if realm == 'admin' else user_failures
    selected[CLIENT_IP] = [90.0]
    form = _admin_form() if realm == 'admin' else _user_form()

    with pytest.raises(RuntimeError, match=f'{realm} verifier failed'):
        service.authenticate(form=form, meta=_meta(), client_ip=CLIENT_IP)

    assert selected == {CLIENT_IP: [90.0]}
    assert throttle.inflight == {}
    assert calls['admin_sessions'] == []
    assert calls['user_sessions'] == []
    assert throttle._begin_login_attempt(CLIENT_IP, selected)
    throttle._finish_login_attempt(CLIENT_IP, None, selected)


def test_user_state_exception_releases_reservation_and_preserves_history():
    def raise_from_load(_path, _default):
        raise OSError('fixture unavailable')

    service, throttle, _failures, user_failures, _calls = _service(load_json=raise_from_load)
    user_failures[CLIENT_IP] = [90.0]

    with pytest.raises(OSError, match='fixture unavailable'):
        service.authenticate(form=_user_form(), meta=_meta(), client_ip=CLIENT_IP)

    assert user_failures == {CLIENT_IP: [90.0]}
    assert throttle.inflight == {}
    assert throttle._begin_login_attempt(CLIENT_IP, user_failures)
    throttle._finish_login_attempt(CLIENT_IP, None, user_failures)


@pytest.mark.parametrize('realm', ['admin', 'user'])
def test_session_creation_exception_occurs_after_success_clears_failures(realm):
    override_name = 'create_session' if realm == 'admin' else 'create_user_session'
    service, throttle, failures, user_failures, _calls = _service()
    selected = failures if realm == 'admin' else user_failures
    selected[CLIENT_IP] = [90.0]
    form = _admin_form() if realm == 'admin' else _user_form()

    def raise_from_session(*_args):
        assert selected == {}
        assert throttle.inflight == {}
        raise OSError(f'{realm} session unavailable')

    service = replace(service, **{override_name: raise_from_session})

    with pytest.raises(OSError, match=f'{realm} session unavailable'):
        service.authenticate(form=form, meta=_meta(), client_ip=CLIENT_IP)

    assert selected == {}
    assert throttle.inflight == {}


def test_login_service_dependencies_are_frozen():
    service, _throttle, _failures, _user_failures, _calls = _service()

    with pytest.raises(FrozenInstanceError):
        service.PASSWORD_MAX_LENGTH = 64
