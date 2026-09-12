"""Bounded administrator and user password-change JSON transport tests."""

import asyncio
import json
import threading
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from password_change_service import PasswordChangeResult
from web_api import create_app
from web_api.services import LegacyPanelServices, PasswordChangeReply

SECURITY_HEADERS = {
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
    'x-frame-options': 'DENY',
    'cross-origin-opener-policy': 'same-origin',
    'permissions-policy': 'camera=(), microphone=(), geolocation=(), payment=(), usb=()',
    'content-security-policy': (
        "default-src 'self'; base-uri 'none'; object-src 'none'; "
        "frame-ancestors 'none'; form-action 'self'; img-src 'self' data:; "
        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
        "connect-src 'self'"
    ),
}


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def password_client(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    admin_hash = ss.hash_secret('old-password')
    alice_hash = ss.hash_secret('old-user-password')
    bob_hash = ss.hash_secret('old-bob-password')
    _write_json(
        paths['META_FILE'],
        {'admin_user': 'admin', 'admin_pass_hash': admin_hash, 'admin_token': 'preserved'},
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'alice': {
                'panel_pass_hash': alice_hash,
                'panel_password_must_change': True,
                'sub_token': 'alice-token',
                'monthly_quota_bytes': 1024,
                'used_bytes': 77,
                'proxy': {'host': 'private.example'},
            },
            'bob': {
                'panel_pass_hash': bob_hash,
                'sub_token': 'bob-token',
                'monthly_quota_bytes': 2048,
            },
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    sid = ss.create_session('admin', ss._credential_generation(admin_hash))
    user_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(alice_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield SimpleNamespace(
            client=client,
            paths=paths,
            admin_hash=admin_hash,
            alice_hash=alice_hash,
            bob_hash=bob_hash,
            admin_sid=sid,
            user_sid=user_sid,
        )


def test_admin_password_change_returns_the_fixed_success_contract(password_client):
    state = password_client
    old_second = ss.create_session('admin', ss._credential_generation(state.admin_hash))
    bob_sid = ss.create_user_session(
        'bob',
        ss._credential_generation(state.bob_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    response = state.client.post(
        '/api/v1/admin/change-password',
        headers={
            'Cookie': f'sid={state.admin_sid}; usid={state.user_sid}',
            'Origin': 'http://testserver',
        },
        data={
            'current': 'old-password',
            'new': 'new-password',
            'confirm': 'new-password',
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        'ok': True,
        'redirect_to': '/admin/settings?msg=password+changed',
    }
    cookie = response.headers['set-cookie']
    assert cookie.startswith('sid=')
    assert '; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax' in cookie
    new_sid = cookie.split('sid=', 1)[1].split(';', 1)[0]
    meta = json.loads(state.paths['META_FILE'].read_text(encoding='utf-8'))
    assert ss.verify_secret('new-password', meta['admin_pass_hash'])
    assert meta['admin_token'] == 'preserved'
    sessions = ss.get_sessions()
    assert state.admin_sid not in sessions
    assert old_second not in sessions
    assert sessions[new_sid]['credential_generation'] == ss._credential_generation(
        meta['admin_pass_hash']
    )
    assert state.user_sid in ss.get_user_sessions()
    assert bob_sid in ss.get_user_sessions()
    assert 'old-password' not in response.text
    assert meta['admin_pass_hash'] not in response.text
    assert {name: response.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


def test_user_password_change_returns_fixed_success_and_preserves_other_state(password_client):
    state = password_client
    alice_second = ss.create_user_session(
        'alice',
        ss._credential_generation(state.alice_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    bob_sid = ss.create_user_session(
        'bob',
        ss._credential_generation(state.bob_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={state.user_sid}; sid={state.admin_sid}'},
        data={
            'current': 'old-user-password',
            'new': 'new-user-password',
            'confirm': 'new-user-password',
        },
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/user/panel'}
    cookie = response.headers['set-cookie']
    assert cookie.startswith('usid=')
    new_sid = cookie.split('usid=', 1)[1].split(';', 1)[0]
    users = json.loads(state.paths['USERS_FILE'].read_text(encoding='utf-8'))
    alice = users['alice']
    assert ss.verify_secret('new-user-password', alice['panel_pass_hash'])
    assert 'panel_password_must_change' not in alice
    assert alice['sub_token'] == 'alice-token'
    assert alice['monthly_quota_bytes'] == 1024
    assert alice['used_bytes'] == 77
    assert alice['proxy'] == {'host': 'private.example'}
    assert users['bob']['panel_pass_hash'] == state.bob_hash
    sessions = ss.get_user_sessions()
    assert state.user_sid not in sessions
    assert alice_second not in sessions
    assert bob_sid in sessions
    assert sessions[new_sid]['credential_kind'] == ss.USER_SESSION_PANEL_PASSWORD
    assert state.admin_sid in ss.get_sessions()


@pytest.mark.parametrize(
    ('path', 'cookie', 'form', 'code'),
    [
        (
            '/api/v1/admin/change-password',
            'admin',
            {'current': 'wrong', 'new': 'new-password', 'confirm': 'new-password'},
            'password_wrong',
        ),
        (
            '/api/v1/admin/change-password',
            'admin',
            {'current': 'old-password', 'new': 'short', 'confirm': 'short'},
            'password_short',
        ),
        (
            '/api/v1/admin/change-password',
            'admin',
            {'current': 'old-password', 'new': 'x' * 257, 'confirm': 'x' * 257},
            'password_long',
        ),
        (
            '/api/v1/admin/change-password',
            'admin',
            {'current': 'old-password', 'new': 'new-password', 'confirm': 'different'},
            'password_mismatch',
        ),
        ('/api/v1/admin/change-password', 'admin', {}, 'password_wrong'),
        (
            '/api/v1/user/change-password',
            'user',
            {'current': 'wrong', 'new': 'new-password', 'confirm': 'new-password'},
            'current password wrong',
        ),
        (
            '/api/v1/user/change-password',
            'user',
            {'current': 'old-user-password', 'new': 'short', 'confirm': 'short'},
            'new password short',
        ),
        (
            '/api/v1/user/change-password',
            'user',
            {'current': 'old-user-password', 'new': 'x' * 257, 'confirm': 'x' * 257},
            'new password long',
        ),
        (
            '/api/v1/user/change-password',
            'user',
            {'current': 'old-user-password', 'new': 'new-password', 'confirm': 'different'},
            'new password mismatch',
        ),
        (
            '/api/v1/user/change-password',
            'user',
            {
                'current': 'old-user-password',
                'new': 'old-user-password',
                'confirm': 'old-user-password',
            },
            'new password same',
        ),
        ('/api/v1/user/change-password', 'user', {}, 'new password short'),
    ],
)
def test_validation_codes_are_exact_and_never_set_a_cookie(
    password_client,
    path,
    cookie,
    form,
    code,
):
    state = password_client
    cookies = {
        'admin': f'sid={state.admin_sid}',
        'user': f'usid={state.user_sid}',
    }
    before_meta = state.paths['META_FILE'].read_bytes()
    before_users = state.paths['USERS_FILE'].read_bytes()
    before_admin_sessions = state.paths['SESSIONS_FILE'].read_bytes()
    before_user_sessions = state.paths['USER_SESSIONS_FILE'].read_bytes()

    response = state.client.post(path, headers={'Cookie': cookies[cookie]}, data=form)

    assert response.status_code == 200
    assert response.json() == {'ok': False, 'code': code}
    assert 'set-cookie' not in response.headers
    assert state.paths['META_FILE'].read_bytes() == before_meta
    assert state.paths['USERS_FILE'].read_bytes() == before_users
    assert state.paths['SESSIONS_FILE'].read_bytes() == before_admin_sessions
    assert state.paths['USER_SESSIONS_FILE'].read_bytes() == before_user_sessions


@pytest.mark.parametrize(
    ('path', 'cookie'),
    [
        ('/api/v1/admin/change-password', ''),
        ('/api/v1/admin/change-password', 'sid=stale'),
        ('/api/v1/admin/change-password', 'usid=opposite'),
        ('/api/v1/user/change-password', ''),
        ('/api/v1/user/change-password', 'usid=stale'),
        ('/api/v1/user/change-password', 'sid=opposite'),
    ],
)
def test_missing_stale_and_opposite_realm_only_cookies_require_login(
    password_client,
    path,
    cookie,
):
    response = password_client.client.post(path, headers={'Cookie': cookie}, data={})

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
    assert 'set-cookie' not in response.headers


def test_subscription_token_session_cannot_change_user_password(password_client):
    state = password_client
    token_sid = ss.create_user_session(
        'alice',
        ss._credential_generation('alice-token'),
        ss.USER_SESSION_SUBSCRIPTION_TOKEN,
    )

    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={token_sid}'},
        data={'current': 'old-user-password', 'new': 'new-password', 'confirm': 'new-password'},
    )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
    assert token_sid in ss.get_user_sessions()


@pytest.mark.parametrize(
    ('state_change', 'expected', 'clears_cookie'),
    [
        ('missing', 'forbidden', True),
        ('disabled', 'disabled', False),
        ('expired', 'expired', False),
    ],
)
def test_user_lifecycle_access_errors_have_exact_status_and_cookie_behavior(
    password_client,
    state_change,
    expected,
    clears_cookie,
):
    state = password_client
    users = json.loads(state.paths['USERS_FILE'].read_text(encoding='utf-8'))
    users['alice'].pop('panel_password_must_change', None)
    if state_change == 'missing':
        del users['alice']
        sid = ss.create_user_session('alice')
    else:
        users['alice'].update(
            {'disabled': True} if state_change == 'disabled' else {'expires_at': '2000-01-01'}
        )
        sid = state.user_sid
    _write_json(state.paths['USERS_FILE'], users)

    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={sid}'},
        data={'current': 'old-user-password', 'new': 'new-password', 'confirm': 'new-password'},
    )

    assert response.status_code == 403
    assert response.json() == {'error': expected}
    if clears_cookie:
        assert response.headers['set-cookie'].startswith('usid=; ')
    else:
        assert 'set-cookie' not in response.headers


@pytest.mark.parametrize(
    ('path', 'cookie_name'),
    [
        ('/api/v1/admin/change-password', 'sid'),
        ('/api/v1/user/change-password', 'usid'),
    ],
)
def test_success_cookie_uses_existing_secure_attributes(password_client, path, cookie_name):
    state = password_client
    current = 'old-password' if cookie_name == 'sid' else 'old-user-password'
    sid = state.admin_sid if cookie_name == 'sid' else state.user_sid

    response = state.client.post(
        path,
        headers={'Cookie': f'{cookie_name}={sid}', 'X-Forwarded-Proto': 'https'},
        data={'current': current, 'new': 'new-password', 'confirm': 'new-password'},
    )

    assert response.status_code == 200
    assert response.headers['set-cookie'].endswith('; Secure')


def test_first_form_value_wins_and_untrusted_fields_cannot_change_admin_destination(
    password_client,
):
    state = password_client
    body = urlencode(
        [
            ('current', 'old-password'),
            ('current', 'wrong'),
            ('new', 'new-password'),
            ('new', 'short'),
            ('confirm', 'new-password'),
            ('confirm', 'different'),
            ('realm', 'user'),
            ('redirect_to', 'https://attacker.test/steal'),
        ]
    )

    response = state.client.post(
        '/api/v1/admin/change-password?realm=user',
        headers={
            'Cookie': f'sid={state.admin_sid}',
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.json() == {
        'ok': True,
        'redirect_to': '/admin/settings?msg=password+changed',
    }
    assert 'attacker' not in response.text


def test_internal_password_reply_is_frozen_and_hides_cookie_and_session_from_repr():
    result = PasswordChangeResult(outcome='success', session_id='private-session-secret')
    reply = PasswordChangeReply(result=result, cookie='sid=private-cookie-secret')

    assert reply.cookie == 'sid=private-cookie-secret'
    assert 'private-cookie-secret' not in repr(reply)
    assert 'private-session-secret' not in repr(reply)
    with pytest.raises(FrozenInstanceError):
        reply.cookie = 'replacement'


@pytest.mark.parametrize(
    'path',
    ['/api/v1/admin/change-password', '/api/v1/user/change-password'],
)
def test_password_change_is_post_only_and_unknown_variants_do_not_fall_back(
    password_client,
    path,
):
    before_meta = password_client.paths['META_FILE'].read_bytes()
    before_users = password_client.paths['USERS_FILE'].read_bytes()

    get_response = password_client.client.get(path)
    head_response = password_client.client.head(path)
    trailing = password_client.client.post(f'{path}/', data={})
    unknown = password_client.client.post(f'{path}-unknown', data={})

    assert get_response.status_code == 405
    assert get_response.json() == {'error': 'method_not_allowed'}
    assert head_response.status_code == 405
    assert head_response.content == b''
    assert trailing.status_code == 404
    assert trailing.json() == {'error': 'not_found'}
    assert unknown.status_code == 404
    assert unknown.json() == {'error': 'not_found'}
    assert password_client.paths['META_FILE'].read_bytes() == before_meta
    assert password_client.paths['USERS_FILE'].read_bytes() == before_users
    assert {name: head_response.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


class RecordingPasswordServices:
    def __init__(self, *, result=None, cookie=None, error=None):
        self.calls = []
        self.result = result or PasswordChangeResult(outcome='login_required')
        self.cookie = cookie
        self.error = error

    def submit_password_change(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return PasswordChangeReply(result=self.result, cookie=self.cookie)


def _form_headers(body, *extra):
    return [
        (b'host', b'panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
        (b'content-length', str(len(body)).encode('ascii')),
        *extra,
    ]


async def _asgi_exchange(
    app,
    *,
    method='POST',
    path='/api/v1/admin/change-password',
    headers=None,
    events=None,
    receive=None,
    client=('198.51.100.10', 4321),
):
    messages = []
    if receive is None:
        queued = iter(events or [{'type': 'http.request', 'body': b'', 'more_body': False}])

        async def receive():
            return next(queued)

    async def send(message):
        messages.append(message)

    await app(
        {
            'type': 'http',
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': method,
            'scheme': 'http',
            'path': path,
            'raw_path': path.encode('ascii'),
            'query_string': b'',
            'root_path': '',
            'headers': headers or [],
            'client': client,
            'server': ('panel.test', 80),
        },
        receive,
        send,
    )
    return messages


def _run_exchange(*args, **kwargs):
    return asyncio.run(_asgi_exchange(*args, **kwargs))


def _response_status(messages):
    return next(
        message['status'] for message in messages if message['type'] == 'http.response.start'
    )


def _response_headers(messages):
    start = next(message for message in messages if message['type'] == 'http.response.start')
    return {
        name.decode('latin-1').lower(): value.decode('latin-1') for name, value in start['headers']
    }


def _response_json(messages):
    raw = b''.join(
        message.get('body', b'') for message in messages if message['type'] == 'http.response.body'
    )
    return json.loads(raw)


@pytest.mark.parametrize(
    'path',
    ['/api/v1/admin/change-password', '/api/v1/user/change-password'],
)
def test_cross_site_password_change_is_rejected_before_receipt_and_service_call(path):
    services = RecordingPasswordServices()
    app = create_app(services, max_requests=1)

    async def forbidden_receive():
        raise AssertionError('cross-site input must not be received')

    messages = _run_exchange(
        app,
        path=path,
        headers=[
            (b'host', b'panel.test'),
            (b'origin', b'https://attacker.test'),
            (b'content-length', b'3'),
        ],
        receive=forbidden_receive,
    )

    assert _response_status(messages) == 403
    assert _response_json(messages) == {'error': 'cross_site_request'}
    assert services.calls == []
    assert all(
        _response_headers(messages)[name] == value for name, value in SECURITY_HEADERS.items()
    )


@pytest.mark.parametrize(
    'path',
    ['/api/v1/admin/change-password', '/api/v1/user/change-password'],
)
@pytest.mark.parametrize(
    ('headers', 'events', 'status', 'error'),
    [
        (
            [(b'content-length', b'3'), (b'content-length', b'3')],
            None,
            400,
            'bad_request',
        ),
        (
            [(b'content-length', str(256 * 1024 + 1).encode('ascii'))],
            None,
            413,
            'request_too_large',
        ),
        (
            [(b'content-length', b'4')],
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
    ],
    ids=['duplicate-length', 'oversized', 'truncated'],
)
def test_malformed_password_forms_never_dispatch_to_state(path, headers, events, status, error):
    services = RecordingPasswordServices()
    app = create_app(services, max_requests=1)
    all_headers = [
        (b'host', b'panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
    ]
    all_headers.extend(headers)

    if events is None:

        async def receive():
            raise AssertionError('invalid headers must be rejected before receiving')

    else:
        receive = None
    messages = _run_exchange(app, path=path, headers=all_headers, events=events, receive=receive)

    assert _response_status(messages) == status
    assert _response_json(messages) == {'error': error}
    assert services.calls == []


@pytest.mark.parametrize(
    'path',
    ['/api/v1/admin/change-password', '/api/v1/user/change-password'],
)
def test_password_form_timeout_is_408_without_service_dispatch(path, monkeypatch):
    import web_api.requests as requests_module

    monkeypatch.setattr(requests_module, 'FORM_READ_TIMEOUT', 0.01)
    services = RecordingPasswordServices()

    async def never_receive():
        await asyncio.Event().wait()

    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=path,
        headers=_form_headers(b'a=1'),
        receive=never_receive,
    )

    assert _response_status(messages) == 408
    assert _response_json(messages) == {'error': 'request_timeout'}
    assert services.calls == []


@pytest.mark.parametrize(
    ('path', 'realm'),
    [
        ('/api/v1/admin/change-password', 'admin'),
        ('/api/v1/user/change-password', 'user'),
    ],
)
def test_valid_form_dispatches_fixed_realm_values_and_peer(path, realm):
    services = RecordingPasswordServices()
    body = b'current=one&new=two&confirm=three'
    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=path,
        headers=_form_headers(body),
        events=[{'type': 'http.request', 'body': body, 'more_body': False}],
        client=('203.0.113.9', 9876),
    )

    assert _response_status(messages) == 401
    assert services.calls == [
        {
            'headers': services.calls[0]['headers'],
            'path': path,
            'form': {'current': ['one'], 'new': ['two'], 'confirm': ['three']},
            'client_address': ('203.0.113.9', 9876),
            'realm': realm,
        }
    ]


def test_password_route_resolves_the_service_method_lazily():
    services = RecordingPasswordServices()
    app = create_app(services, max_requests=1)
    late_calls = []

    def replacement(**kwargs):
        late_calls.append(kwargs)
        return PasswordChangeReply(result=PasswordChangeResult(outcome='login_required'))

    services.submit_password_change = replacement
    messages = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(messages) == 401
    assert len(late_calls) == 1
    assert services.calls == []


def test_invalid_internal_realm_is_rejected_before_any_service_side_effect():
    class UntouchedModule:
        def __getattribute__(self, name):
            if not name.startswith('__'):
                raise AssertionError(f'unexpected side effect: {name}')
            return super().__getattribute__(name)

    services = LegacyPanelServices(UntouchedModule())

    with pytest.raises(ValueError, match='invalid password-change realm'):
        services.submit_password_change(
            headers={},
            path='/internal',
            form={},
            client_address=('', 0),
            realm='intruder',
        )


@pytest.mark.parametrize('realm', ['admin', 'user'])
def test_service_loads_metadata_then_authenticates_only_the_fixed_realm(realm):
    events = []

    class PasswordService:
        def change_admin(self, *, form):
            events.append(('change_admin', form))
            return PasswordChangeResult(outcome='invalid', code='password_wrong')

        def change_user(self, *, username, session_kind, form):
            events.append(('change_user', username, session_kind, form))
            return PasswordChangeResult(outcome='invalid', code='new password short')

    class ServiceModule:
        request_multiplier_snapshot = staticmethod(lambda function: function)
        session_cookie = staticmethod(lambda sid, *, secure: f'sid={sid}; secure={secure}')
        user_session_cookie = staticmethod(lambda sid, *, secure: f'usid={sid}; secure={secure}')
        clear_user_session_cookie = staticmethod(lambda *, secure: f'usid=; secure={secure}')

        @staticmethod
        def load_meta():
            events.append('metadata')
            return {'complete': True}

        @staticmethod
        def _password_change_service():
            events.append('service')
            return PasswordService()

        @staticmethod
        def is_logged_in(_request):
            events.append('admin_auth')
            return True

        @staticmethod
        def get_logged_in_user_context(_request):
            events.append('user_auth')
            return 'alice', 'panel_password'

    result = LegacyPanelServices(ServiceModule()).submit_password_change(
        headers={},
        path=f'/{realm}/change-password',
        form={'new': ['short']},
        client_address=('', 0),
        realm=realm,
    )

    if realm == 'admin':
        assert events == [
            'metadata',
            'admin_auth',
            'service',
            ('change_admin', {'new': ['short']}),
        ]
        assert result.result.code == 'password_wrong'
    else:
        assert events == [
            'metadata',
            'user_auth',
            'service',
            ('change_user', 'alice', 'panel_password', {'new': ['short']}),
        ]
        assert result.result.code == 'new password short'


@pytest.mark.parametrize(
    ('result', 'cookie'),
    [
        (PasswordChangeResult(outcome='invalid', code='new password short'), None),
        (PasswordChangeResult(outcome='forbidden'), None),
        (
            PasswordChangeResult(
                outcome='success',
                code='private-current',
                session_id='secret',
            ),
            'sid=private-cookie',
        ),
        (
            PasswordChangeResult(outcome='invalid', code='password_wrong'),
            'sid=private-cookie',
        ),
    ],
)
def test_response_builder_rejects_invalid_admin_outcome_code_combinations(result, cookie):
    services = RecordingPasswordServices(result=result, cookie=cookie)
    messages = _run_exchange(create_app(services, max_requests=1), headers=_form_headers(b''))

    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert 'set-cookie' not in _response_headers(messages)


@pytest.mark.parametrize(
    ('path', 'legacy_path'),
    [
        ('/api/v1/admin/change-password', '/admin/change-password'),
        ('/api/v1/user/change-password', '/user/change-password'),
    ],
)
def test_unavailable_metadata_is_503_before_auth_and_uses_legacy_policy_path(
    password_client,
    monkeypatch,
    path,
    legacy_path,
):
    state = password_client
    state.paths['META_FILE'].write_text('{broken', encoding='utf-8')
    classified = []

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    response = state.client.post(path, data={})

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert len(classified) == 1
    assert issubclass(classified[0][0], ss.state_store.StateStoreError)
    assert classified[0][1] == legacy_path


def test_user_hash_write_failure_is_503_without_cookie_or_session_replacement(
    password_client,
    monkeypatch,
):
    state = password_client
    before_sessions = state.paths['USER_SESSIONS_FILE'].read_bytes()
    original_save = ss.save_json

    def fail_user_write(path, value):
        if Path(path) == state.paths['USERS_FILE']:
            raise ss.state_store.StateStoreError('private user credential path')
        return original_save(path, value)

    monkeypatch.setattr(ss, 'save_json', fail_user_write)
    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={state.user_sid}'},
        data={
            'current': 'old-user-password',
            'new': 'new-user-password',
            'confirm': 'new-user-password',
        },
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert state.paths['USER_SESSIONS_FILE'].read_bytes() == before_sessions


def test_user_state_read_failure_is_503_without_reflecting_private_paths(
    password_client,
    monkeypatch,
):
    state = password_client
    original_load = ss.load_json

    def fail_user_read(path, default, **kwargs):
        if Path(path) == state.paths['USERS_FILE']:
            raise ss.state_store.StateStoreError('private users path')
        return original_load(path, default, **kwargs)

    monkeypatch.setattr(ss, 'load_json', fail_user_read)
    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={state.user_sid}'},
        data={},
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert 'private' not in response.text


def test_post_write_session_failure_is_503_but_new_hash_invalidates_old_generation(
    password_client,
    monkeypatch,
):
    state = password_client

    def fail_replacement(*_args, **_kwargs):
        raise ss.state_store.StateStoreError('private user session path')

    monkeypatch.setattr(ss, '_replace_sessions_with_new', fail_replacement)
    response = state.client.post(
        '/api/v1/user/change-password',
        headers={'Cookie': f'usid={state.user_sid}'},
        data={
            'current': 'old-user-password',
            'new': 'new-user-password',
            'confirm': 'new-user-password',
        },
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    cfg = json.loads(state.paths['USERS_FILE'].read_text(encoding='utf-8'))['alice']
    assert ss.verify_secret('new-user-password', cfg['panel_pass_hash'])
    request = SimpleNamespace(headers={'Cookie': f'usid={state.user_sid}'}, path='/user/panel')
    assert ss.get_logged_in_user_context(request) == ('', '')


def test_critical_password_state_failure_runs_recording_fail_closed_double(
    password_client,
    monkeypatch,
):
    stopped = []

    def fail_change(*_args, **_kwargs):
        raise ss.state_store.CriticalStateUnavailable('private critical path')

    monkeypatch.setattr(ss, '_change_admin_password', fail_change)
    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ss, '_fail_closed_static_access', stopped.append)
    response = password_client.client.post(
        '/api/v1/admin/change-password',
        headers={'Cookie': f'sid={password_client.admin_sid}'},
        data={'current': 'old-password', 'new': 'new-password', 'confirm': 'new-password'},
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert len(stopped) == 1
    assert isinstance(stopped[0], ss.state_store.CriticalStateUnavailable)


def test_unexpected_password_error_is_sanitized_and_capacity_recovers():
    services = RecordingPasswordServices(error=RuntimeError('private password and hash'))
    app = create_app(services, max_requests=1)
    failed = _run_exchange(app, headers=_form_headers(b''))
    services.error = None
    recovered = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(failed) == 500
    assert _response_json(failed) == {'error': 'internal_error'}
    assert 'set-cookie' not in _response_headers(failed)
    assert _response_status(recovered) == 401


def test_password_snapshot_is_request_local_and_resets_after_failure_and_success(
    password_client,
    monkeypatch,
):
    values = iter((2.0, 3.0))
    observed = []
    calls = 0

    monkeypatch.setattr(
        ss.display_config,
        'effective_display_multiplier_strict',
        lambda **_kwargs: next(values),
    )

    def change_once(*, form):
        nonlocal calls
        del form
        calls += 1
        observed.extend((ss.current_display_multiplier(), ss.current_display_multiplier()))
        if calls == 1:
            raise RuntimeError('private first failure')
        return PasswordChangeResult(outcome='invalid', code='password_wrong')

    class PasswordService:
        change_admin = staticmethod(change_once)

    monkeypatch.setattr(ss, '_password_change_service', lambda: PasswordService())
    first = password_client.client.post(
        '/api/v1/admin/change-password',
        headers={'Cookie': f'sid={password_client.admin_sid}'},
        data={},
    )
    second = password_client.client.post(
        '/api/v1/admin/change-password',
        headers={'Cookie': f'sid={password_client.admin_sid}'},
        data={},
    )

    assert first.status_code == 500
    assert second.status_code == 200
    assert observed == [2.0, 2.0, 3.0, 3.0]


def test_cancelled_password_mutation_retains_capacity_until_worker_completion():
    class BlockingServices(RecordingPasswordServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_password_change(self, **kwargs):
            self.calls.append(kwargs)
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test gate timed out')
            return PasswordChangeReply(result=self.result)

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        pending = asyncio.create_task(_asgi_exchange(app, headers=_form_headers(b'')))
        assert await asyncio.to_thread(services.entered.wait, 1)
        busy_before = await _asgi_exchange(app, headers=_form_headers(b''))
        pending.cancel()
        await asyncio.sleep(0)
        busy_after = await _asgi_exchange(app, headers=_form_headers(b''))
        services.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        recovered = await _asgi_exchange(app, headers=_form_headers(b''))
        return services, busy_before, busy_after, recovered

    services, busy_before, busy_after, recovered = asyncio.run(scenario())
    assert _response_status(busy_before) == 503
    assert _response_status(busy_after) == 503
    assert _response_status(recovered) == 401
    assert len(services.calls) == 2
