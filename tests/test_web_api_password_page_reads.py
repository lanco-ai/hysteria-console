"""Authentication and allowlist contracts for password-page reads."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginRequired

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def password_page_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)

    admin_hash = ss.hash_secret('fixture-admin-password')
    user_hash = ss.hash_secret('fixture-user-password')
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'fixture-admin',
            'admin_pass_hash': admin_hash,
            'admin_token': 'must-not-leak',
            'private_settings': {'secret': True},
        },
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'fixture-user': {
                'panel_pass_hash': user_hash,
                'panel_password_must_change': True,
                'sub_token': 'subscription-secret',
                'private_note': 'must-not-leak',
            }
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    admin_sid = ss.create_session('fixture-admin', ss._credential_generation(admin_hash))
    user_sid = ss.create_user_session(
        'fixture-user',
        ss._credential_generation(user_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield {
            'client': client,
            'paths': paths,
            'admin_sid': admin_sid,
            'user_sid': user_sid,
            'admin_hash': admin_hash,
            'user_hash': user_hash,
        }


def test_admin_settings_returns_only_authoritative_password_page_metadata(password_page_state):
    state = password_page_state
    before_meta = state['paths']['META_FILE'].read_bytes()
    before_sessions = state['paths']['SESSIONS_FILE'].read_bytes()

    response = state['client'].get(
        '/api/v1/admin/settings',
        headers={'Cookie': f'sid={state["admin_sid"]}'},
    )

    assert response.status_code == 200
    assert response.json() == {
        'username': 'fixture-admin',
        'password_min_length': ss.PASSWORD_MIN_LENGTH,
        'password_max_length': ss.PASSWORD_MAX_LENGTH,
    }
    assert set(response.json()) == {'username', 'password_min_length', 'password_max_length'}
    assert 'must-not-leak' not in response.text
    assert state['admin_hash'] not in response.text
    assert state['paths']['META_FILE'].read_bytes() == before_meta
    assert state['paths']['SESSIONS_FILE'].read_bytes() == before_sessions


def test_user_password_read_allows_password_kind_session_that_must_change(password_page_state):
    state = password_page_state

    response = state['client'].get(
        '/api/v1/user/password',
        headers={'Cookie': f'usid={state["user_sid"]}'},
    )

    assert response.status_code == 200
    assert response.json() == {
        'username': 'fixture-user',
        'password_min_length': ss.PASSWORD_MIN_LENGTH,
        'password_max_length': ss.PASSWORD_MAX_LENGTH,
    }
    assert set(response.json()) == {'username', 'password_min_length', 'password_max_length'}
    assert 'subscription-secret' not in response.text
    assert state['user_hash'] not in response.text


def test_subscription_token_session_cannot_read_password_page(password_page_state):
    state = password_page_state
    token_sid = ss.create_user_session(
        'fixture-user',
        ss._credential_generation('subscription-secret'),
        ss.USER_SESSION_SUBSCRIPTION_TOKEN,
    )

    response = state['client'].get(
        '/api/v1/user/password',
        headers={'Cookie': f'usid={token_sid}'},
    )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


@pytest.mark.parametrize('path', ['/api/v1/admin/settings', '/api/v1/user/password'])
def test_password_page_reads_require_their_own_cookie_realm(password_page_state, path):
    state = password_page_state
    anonymous = state['client'].get(path)
    wrong_realm_cookie = (
        f'usid={state["user_sid"]}' if path.endswith('settings') else f'sid={state["admin_sid"]}'
    )
    wrong_realm = state['client'].get(path, headers={'Cookie': wrong_realm_cookie})
    assert anonymous.status_code == wrong_realm.status_code == 401
    assert anonymous.json() == wrong_realm.json() == {'error': 'login_required'}


@pytest.mark.parametrize(
    ('change', 'code'),
    [({'disabled': True}, 'disabled'), ({'expires_at': '2026-09-11'}, 'expired')],
)
def test_user_password_read_retains_disabled_and_expired_codes(
    password_page_state,
    change,
    code,
):
    state = password_page_state
    users = json.loads(state['paths']['USERS_FILE'].read_text(encoding='utf-8'))
    users['fixture-user'].pop('panel_password_must_change', None)
    users['fixture-user'].update(change)
    _write_json(state['paths']['USERS_FILE'], users)

    response = state['client'].get(
        '/api/v1/user/password', headers={'Cookie': f'usid={state["user_sid"]}'}
    )

    assert response.status_code == 403
    assert response.json() == {'error': code}
    assert response.headers.get('set-cookie') is None


def test_must_change_priority_allows_password_page_even_if_user_is_disabled(password_page_state):
    state = password_page_state
    users = json.loads(state['paths']['USERS_FILE'].read_text(encoding='utf-8'))
    users['fixture-user']['disabled'] = True
    _write_json(state['paths']['USERS_FILE'], users)

    response = state['client'].get(
        '/api/v1/user/password', headers={'Cookie': f'usid={state["user_sid"]}'}
    )

    assert response.status_code == 200
    assert response.json()['username'] == 'fixture-user'


def test_disappearing_user_returns_forbidden_and_clears_only_user_cookie(
    password_page_state,
    monkeypatch,
):
    state = password_page_state
    original = ss.load_json
    user_reads = 0

    def disappear_after_identity(path, default, **kwargs):
        nonlocal user_reads
        result = original(path, default, **kwargs)
        if Path(path) == state['paths']['USERS_FILE']:
            user_reads += 1
            if user_reads == 2:
                return {}
        return result

    monkeypatch.setattr(ss, 'load_json', disappear_after_identity)
    response = state['client'].get(
        '/api/v1/user/password', headers={'Cookie': f'usid={state["user_sid"]}'}
    )

    assert user_reads == 2
    assert response.status_code == 403
    assert response.json() == {'error': 'forbidden'}
    assert response.headers['set-cookie'].startswith('usid=; ')
    assert 'sid=' not in response.headers['set-cookie'].removeprefix('usid=')


@pytest.mark.parametrize(('stored', 'expected'), [('', ''), (None, 'None')])
def test_admin_username_preserves_legacy_present_value_semantics(
    password_page_state,
    stored,
    expected,
):
    state = password_page_state
    meta = json.loads(state['paths']['META_FILE'].read_text(encoding='utf-8'))
    meta['admin_user'] = stored
    _write_json(state['paths']['META_FILE'], meta)

    response = state['client'].get(
        '/api/v1/admin/settings', headers={'Cookie': f'sid={state["admin_sid"]}'}
    )

    assert response.status_code == 200
    assert response.json()['username'] == expected


def test_admin_identity_rejection_happens_before_page_metadata_read():
    touched = []
    service = SimpleNamespace(
        request_multiplier_snapshot=lambda function: function,
        is_logged_in=lambda _request: False,
        load_meta=lambda: touched.append('metadata'),
        state_store=SimpleNamespace(StateStoreError=RuntimeError),
    )

    with pytest.raises(LoginRequired):
        LegacyPanelServices(service).read_admin_settings(headers={}, path='/api/v1/admin/settings')

    assert touched == []


@pytest.mark.parametrize(
    'payload',
    [
        {'username': 'admin', 'password_min_length': True, 'password_max_length': 256},
        {'username': 7, 'password_min_length': 8, 'password_max_length': 256},
        {'username': 'admin', 'password_min_length': 0, 'password_max_length': 256},
        {'username': 'admin', 'password_min_length': 9, 'password_max_length': 8},
    ],
)
def test_password_page_model_rejects_non_strict_or_invalid_bounds(payload):
    class BadServices:
        def read_admin_settings(self, *, headers, path):
            del headers, path
            return payload

    with TestClient(create_app(BadServices(), max_requests=1)) as client:
        response = client.get('/api/v1/admin/settings')

    assert response.status_code == 500
    assert response.json() == {'error': 'internal_error'}


def test_password_page_model_drops_unknown_service_fields():
    class ExtraServices:
        def read_admin_settings(self, *, headers, path):
            del headers, path
            return {
                'username': 'admin',
                'password_min_length': 8,
                'password_max_length': 256,
                'admin_token': 'secret',
            }

    with TestClient(create_app(ExtraServices(), max_requests=1)) as client:
        response = client.get('/api/v1/admin/settings')

    assert response.status_code == 200
    assert response.json() == {
        'username': 'admin',
        'password_min_length': 8,
        'password_max_length': 256,
    }
    assert 'secret' not in response.text


@pytest.mark.parametrize('path', ['/api/v1/admin/settings', '/api/v1/user/password'])
def test_password_page_read_method_path_headers_and_head_contract(password_page_state, path):
    state = password_page_state
    cookie = (
        f'sid={state["admin_sid"]}' if path.endswith('settings') else f'usid={state["user_sid"]}'
    )
    get_response = state['client'].get(path, headers={'Cookie': cookie})
    head_response = state['client'].head(path, headers={'Cookie': cookie})
    post_response = state['client'].post(path, headers={'Cookie': cookie})
    trailing_response = state['client'].get(
        path + '/', headers={'Cookie': cookie}, follow_redirects=False
    )

    assert get_response.status_code == head_response.status_code == 200
    assert dict(get_response.headers) == dict(head_response.headers)
    assert head_response.content == b''
    assert post_response.status_code == 405
    assert post_response.json() == {'error': 'method_not_allowed'}
    assert trailing_response.status_code == 404
    for response in (get_response, head_response, post_response, trailing_response):
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'


def test_user_state_failure_is_sanitized(password_page_state):
    state = password_page_state
    state['paths']['USERS_FILE'].write_text('{broken', encoding='utf-8')

    response = state['client'].get(
        '/api/v1/user/password', headers={'Cookie': f'usid={state["user_sid"]}'}
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert str(state['paths']['USERS_FILE']) not in response.text
