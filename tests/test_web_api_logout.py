"""Bounded administrator and user logout JSON transport tests."""

import asyncio
import json
import threading
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices, LogoutReply

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))
ADMIN_HASH = 'current-admin-password-hash'
USER_HASH = 'current-user-password-hash'
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
def logout_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)
    _write_json(
        paths['META_FILE'],
        {'admin_user': 'admin', 'admin_pass_hash': ADMIN_HASH},
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'alice': {
                'panel_pass_hash': USER_HASH,
                'monthly_quota_bytes': 1024,
            }
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    return paths


@pytest.fixture
def logout_client(logout_state):
    del logout_state
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield client


def _admin_session():
    return ss.create_session('admin', ss._credential_generation(ADMIN_HASH))


def _user_session():
    return ss.create_user_session(
        'alice',
        ss._credential_generation(USER_HASH),
        ss.USER_SESSION_PANEL_PASSWORD,
    )


def _assert_security_headers(response):
    assert {name: response.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


@pytest.mark.parametrize(
    ('path', 'cookie_name', 'create_session', 'get_sessions'),
    [
        ('/api/v1/logout', 'sid', _admin_session, ss.get_sessions),
        ('/api/v1/user/logout', 'usid', _user_session, ss.get_user_sessions),
    ],
)
def test_logout_revokes_only_the_supplied_current_device(
    logout_client,
    path,
    cookie_name,
    create_session,
    get_sessions,
):
    current = create_session()
    other_device = create_session()

    response = logout_client.post(
        path,
        content=b'',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': '0',
            'Cookie': f'{cookie_name}={current}',
        },
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/login'}
    assert current not in get_sessions()
    assert other_device in get_sessions()
    assert current not in response.text
    assert other_device not in response.text
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ('path', 'target_cookie', 'other_cookie'),
    [
        ('/api/v1/logout', 'sid', 'usid'),
        ('/api/v1/user/logout', 'usid', 'sid'),
    ],
)
def test_logout_with_both_cookies_never_switches_the_fixed_realm(
    logout_client,
    path,
    target_cookie,
    other_cookie,
):
    admin_current = _admin_session()
    admin_other = _admin_session()
    user_current = _user_session()
    user_other = _user_session()
    cookie_values = {'sid': admin_current, 'usid': user_current}

    response = logout_client.post(
        path,
        content=b'',
        headers={
            'Content-Type': 'application/x-www-form-urlencoded',
            'Content-Length': '0',
            'Cookie': (
                f'{target_cookie}={cookie_values[target_cookie]}; '
                f'{other_cookie}={cookie_values[other_cookie]}'
            ),
        },
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/login'}
    if target_cookie == 'sid':
        assert admin_current not in ss.get_sessions()
        assert user_current in ss.get_user_sessions()
    else:
        assert user_current not in ss.get_user_sessions()
        assert admin_current in ss.get_sessions()
    assert admin_other in ss.get_sessions()
    assert user_other in ss.get_user_sessions()
    assert logout_client.get(
        '/api/v1/session',
        headers={'Cookie': f'sid={admin_other}'},
    ).json() == {'role': 'admin'}
    assert logout_client.get(
        '/api/v1/session',
        headers={'Cookie': f'usid={user_other}'},
    ).json() == {'role': 'user', 'username': 'alice'}


@pytest.mark.parametrize(
    ('path', 'cookie_name', 'other_cookie_name', 'create_session', 'sessions_file'),
    [
        ('/api/v1/logout', 'sid', 'usid', _admin_session, 'SESSIONS_FILE'),
        ('/api/v1/user/logout', 'usid', 'sid', _user_session, 'USER_SESSIONS_FILE'),
    ],
)
@pytest.mark.parametrize('cookie_state', ['missing', 'stale', 'expired', 'repeated', 'opposite'])
def test_logout_without_a_live_matching_cookie_still_clears_only_its_cookie(
    logout_client,
    logout_state,
    path,
    cookie_name,
    other_cookie_name,
    create_session,
    sessions_file,
    cookie_state,
):
    live = create_session()
    request_cookie = 'unrelated=value'
    if cookie_state == 'stale':
        request_cookie = f'{cookie_name}=stale-session-secret'
    elif cookie_state == 'expired':
        expired = f'expired-{cookie_name}-secret'
        state = json.loads(logout_state[sessions_file].read_text(encoding='utf-8'))
        state[expired] = {'user': 'admin' if cookie_name == 'sid' else 'alice', 'exp': 1}
        _write_json(logout_state[sessions_file], state)
        request_cookie = f'{cookie_name}={expired}'
    elif cookie_state == 'repeated':
        repeated = create_session()
        request_cookie = f'{cookie_name}={repeated}'
        first = logout_client.post(path, data={}, headers={'Cookie': request_cookie})
        assert first.status_code == 200
    elif cookie_state == 'opposite':
        opposite = _user_session() if other_cookie_name == 'usid' else _admin_session()
        request_cookie = f'{other_cookie_name}={opposite}'

    response = logout_client.post(path, data={}, headers={'Cookie': request_cookie})

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/login'}
    assert response.headers['set-cookie'].startswith(f'{cookie_name}=; ')
    assert not response.headers['set-cookie'].startswith(f'{other_cookie_name}=')
    assert live in (ss.get_sessions() if cookie_name == 'sid' else ss.get_user_sessions())


@pytest.mark.parametrize(
    ('path', 'cookie_name', 'create_session', 'target_sessions', 'other_sessions'),
    [
        ('/api/v1/logout', 'sid', _admin_session, ss.get_sessions, ss.get_user_sessions),
        (
            '/api/v1/user/logout',
            'usid',
            _user_session,
            ss.get_user_sessions,
            ss.get_sessions,
        ),
    ],
)
def test_form_and_query_cannot_change_the_endpoint_realm(
    logout_client,
    path,
    cookie_name,
    create_session,
    target_sessions,
    other_sessions,
):
    target = create_session()
    opposite = _user_session() if cookie_name == 'sid' else _admin_session()

    response = logout_client.post(
        f'{path}?realm={"user" if cookie_name == "sid" else "admin"}',
        data={'realm': 'user' if cookie_name == 'sid' else 'admin'},
        headers={'Cookie': f'{cookie_name}={target}'},
    )

    assert response.status_code == 200
    assert target not in target_sessions()
    assert opposite in other_sessions()


@pytest.mark.parametrize(
    ('path', 'cookie_name'),
    [('/api/v1/logout', 'sid'), ('/api/v1/user/logout', 'usid')],
)
@pytest.mark.parametrize('secure', [False, True])
def test_logout_uses_the_existing_matching_clear_cookie(logout_client, path, cookie_name, secure):
    headers = {
        'Cookie': f'{cookie_name}=not-live',
        **({'X-Forwarded-Proto': 'https'} if secure else {}),
    }

    response = logout_client.post(path, data={}, headers=headers)

    expected = f'{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
    if secure:
        expected += '; Secure'
    assert response.headers['set-cookie'] == expected


def test_internal_logout_reply_is_frozen_and_hides_cookie_from_repr():
    reply = LogoutReply(cookie='sid=private-session-secret')

    assert reply.cookie == 'sid=private-session-secret'
    assert 'private-session-secret' not in repr(reply)
    with pytest.raises(FrozenInstanceError):
        reply.cookie = 'replacement'


@pytest.mark.parametrize('path', ['/api/v1/logout', '/api/v1/user/logout'])
def test_logout_is_post_only_and_unknown_variants_do_not_fall_back(logout_client, path):
    get_response = logout_client.get(path)
    head_response = logout_client.head(path)
    trailing = logout_client.post(f'{path}/', data={})
    unknown = logout_client.post(f'{path}-unknown', data={})

    assert get_response.status_code == 405
    assert get_response.json() == {'error': 'method_not_allowed'}
    assert head_response.status_code == 405
    assert head_response.content == b''
    assert trailing.status_code == 404
    assert trailing.json() == {'error': 'not_found'}
    assert unknown.status_code == 404
    assert unknown.json() == {'error': 'not_found'}
    _assert_security_headers(head_response)


class RecordingLogoutServices:
    def __init__(self, *, error=None):
        self.calls = []
        self.error = error

    def submit_logout(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        cookie_name = 'sid' if kwargs['realm'] == 'admin' else 'usid'
        return LogoutReply(cookie=f'{cookie_name}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')


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
    path='/api/v1/logout',
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


@pytest.mark.parametrize('path', ['/api/v1/logout', '/api/v1/user/logout'])
def test_cross_site_logout_is_rejected_before_receipt_and_service_call(path):
    services = RecordingLogoutServices()
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


@pytest.mark.parametrize('path', ['/api/v1/logout', '/api/v1/user/logout'])
@pytest.mark.parametrize(
    ('headers', 'expected_status', 'expected_error'),
    [
        ([(b'content-length', b'3'), (b'content-length', b'3')], 400, 'bad_request'),
        (
            [(b'content-length', b'3'), (b'transfer-encoding', b'chunked')],
            400,
            'bad_request',
        ),
    ],
)
def test_invalid_logout_framing_is_rejected_without_receipt_or_service_call(
    path,
    headers,
    expected_status,
    expected_error,
):
    services = RecordingLogoutServices()
    app = create_app(services, max_requests=1)

    async def forbidden_receive():
        raise AssertionError('invalid headers must be rejected before receiving')

    messages = _run_exchange(
        app,
        path=path,
        headers=[(b'host', b'panel.test'), *headers],
        receive=forbidden_receive,
    )

    assert _response_status(messages) == expected_status
    assert _response_json(messages) == {'error': expected_error}
    assert services.calls == []


@pytest.mark.parametrize('path', ['/api/v1/logout', '/api/v1/user/logout'])
@pytest.mark.parametrize(
    ('body', 'claimed', 'events', 'expected_status', 'expected_error'),
    [
        (
            b'field=\xff',
            7,
            [{'type': 'http.request', 'body': b'field=\xff', 'more_body': False}],
            400,
            'bad_request',
        ),
        (
            b'field=%ZZ',
            9,
            [{'type': 'http.request', 'body': b'field=%ZZ', 'more_body': False}],
            400,
            'bad_request',
        ),
        (
            b'a=1',
            0,
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
        (
            b'x' * (256 * 1024 + 1),
            0,
            [
                {
                    'type': 'http.request',
                    'body': b'x' * (256 * 1024 + 1),
                    'more_body': False,
                }
            ],
            413,
            'request_too_large',
        ),
        (
            b'a=1',
            4,
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
    ],
    ids=['utf8', 'form', 'zero-nonempty', 'zero-oversized', 'truncated'],
)
def test_invalid_logout_bodies_never_dispatch_to_state(
    path,
    body,
    claimed,
    events,
    expected_status,
    expected_error,
):
    del body
    services = RecordingLogoutServices()
    headers = [
        (b'host', b'panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
        (b'content-length', str(claimed).encode('ascii')),
    ]

    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=path,
        headers=headers,
        events=events,
    )

    assert _response_status(messages) == expected_status
    assert _response_json(messages) == {'error': expected_error}
    assert services.calls == []


@pytest.mark.parametrize(
    ('path', 'realm'),
    [('/api/v1/logout', 'admin'), ('/api/v1/user/logout', 'user')],
)
def test_valid_empty_logout_form_dispatches_fixed_realm_and_peer(path, realm):
    services = RecordingLogoutServices()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=path,
        headers=_form_headers(b''),
        client=('203.0.113.9', 9876),
    )

    assert _response_status(messages) == 200
    assert _response_json(messages) == {'ok': True, 'redirect_to': '/login'}
    assert services.calls[0]['realm'] == realm
    assert services.calls[0]['form'] == {}
    assert services.calls[0]['client_address'] == ('203.0.113.9', 9876)


@pytest.mark.parametrize('path', ['/api/v1/logout', '/api/v1/user/logout'])
def test_logout_body_timeout_is_408_without_service_dispatch(path, monkeypatch):
    import web_api.requests as requests_module

    monkeypatch.setattr(requests_module, 'FORM_READ_TIMEOUT', 0.01)
    services = RecordingLogoutServices()

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
    ('path', 'cookie_name', 'create_session', 'sessions_file', 'legacy_path'),
    [
        ('/api/v1/logout', 'sid', _admin_session, 'SESSIONS_FILE', '/logout'),
        (
            '/api/v1/user/logout',
            'usid',
            _user_session,
            'USER_SESSIONS_FILE',
            '/user/logout',
        ),
    ],
)
def test_unavailable_metadata_prevents_deletion_and_uses_legacy_failure_path(
    logout_client,
    logout_state,
    monkeypatch,
    path,
    cookie_name,
    create_session,
    sessions_file,
    legacy_path,
):
    current = create_session()
    before = logout_state[sessions_file].read_bytes()
    logout_state['META_FILE'].write_text('{broken', encoding='utf-8')
    classified = []

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    response = logout_client.post(path, data={}, headers={'Cookie': f'{cookie_name}={current}'})

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert logout_state[sessions_file].read_bytes() == before
    assert len(classified) == 1
    assert issubclass(classified[0][0], ss.state_store.StateStoreError)
    assert classified[0][1] == legacy_path
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ('path', 'cookie_name', 'delete_name', 'legacy_path'),
    [
        ('/api/v1/logout', 'sid', 'delete_session', '/logout'),
        ('/api/v1/user/logout', 'usid', 'delete_user_session', '/user/logout'),
    ],
)
@pytest.mark.parametrize(
    'error',
    [OSError('private deletion path'), ss.state_store.StateStoreError('private session state')],
    ids=['os-error', 'state-store'],
)
def test_deletion_state_failures_are_503_without_a_clearing_cookie(
    logout_client,
    monkeypatch,
    path,
    cookie_name,
    delete_name,
    legacy_path,
    error,
):
    classified = []

    def unavailable(_sid):
        raise error

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, delete_name, unavailable)
    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)

    response = logout_client.post(
        path,
        data={},
        headers={'Cookie': f'{cookie_name}=private-session-secret'},
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert 'private' not in response.text
    assert classified == [(type(error), legacy_path)]


def test_critical_state_failure_runs_recording_fail_closed_double(monkeypatch, logout_client):
    stopped = []
    classified = []

    def unavailable(_sid):
        raise ss.state_store.CriticalStateUnavailable('private critical path')

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return True

    monkeypatch.setattr(ss, 'delete_session', unavailable)
    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    monkeypatch.setattr(ss, '_fail_closed_static_access', stopped.append)

    response = logout_client.post(
        '/api/v1/logout',
        data={},
        headers={'Cookie': 'sid=private-session-secret'},
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'set-cookie' not in response.headers
    assert classified == [(ss.state_store.CriticalStateUnavailable, '/logout')]
    assert len(stopped) == 1
    assert isinstance(stopped[0], ss.state_store.CriticalStateUnavailable)


def test_unexpected_deletion_error_is_sanitized_and_capacity_recovers(
    monkeypatch,
    logout_client,
):
    original = ss.delete_session
    calls = 0

    def fail_once(sid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f'private sid={sid} at /private/session.json')
        return original(sid)

    monkeypatch.setattr(ss, 'delete_session', fail_once)
    failed = logout_client.post(
        '/api/v1/logout',
        data={},
        headers={'Cookie': 'sid=private-session-secret'},
    )
    recovered = logout_client.post(
        '/api/v1/logout',
        data={},
        headers={'Cookie': 'sid=private-session-secret'},
    )

    assert failed.status_code == 500
    assert failed.json() == {'error': 'internal_error'}
    assert 'set-cookie' not in failed.headers
    assert 'private' not in failed.text
    assert recovered.status_code == 200


def test_logout_snapshot_is_request_local_and_resets_after_success_and_failure(
    monkeypatch,
    logout_client,
):
    values = iter((2.0, 3.0))
    observed = []
    calls = 0

    monkeypatch.setattr(
        ss.display_config,
        'effective_display_multiplier_strict',
        lambda **_kwargs: next(values),
    )

    def deletion(_sid):
        nonlocal calls
        calls += 1
        observed.extend((ss.current_display_multiplier(), ss.current_display_multiplier()))
        if calls == 1:
            raise RuntimeError('first deletion failed')

    monkeypatch.setattr(ss, 'delete_session', deletion)
    failed = logout_client.post('/api/v1/logout', data={})
    succeeded = logout_client.post('/api/v1/logout', data={})

    assert failed.status_code == 500
    assert succeeded.status_code == 200
    assert observed == [2.0, 2.0, 3.0, 3.0]


def test_service_loads_metadata_before_cookie_parsing_and_deletion():
    calls = []

    class OrderedService:
        state_store = SimpleNamespace(StateStoreError=ss.state_store.StateStoreError)

        @staticmethod
        def request_multiplier_snapshot(function):
            return function

        @staticmethod
        def load_meta():
            calls.append('meta')
            return {}

        @staticmethod
        def parse_cookies(_request):
            calls.append('cookies')
            return {'sid': 'admin-current'}

        @staticmethod
        def delete_session(sid):
            calls.append(('delete', sid))

        @staticmethod
        def clear_session_cookie(*, secure=False):
            calls.append(('clear', secure))
            return 'sid=cleared'

        @staticmethod
        def _state_failure_requires_static_stop(_exc, *, post_path=''):
            raise AssertionError(post_path)

        @staticmethod
        def _fail_closed_static_access(_exc):
            raise AssertionError('unexpected fail-closed')

    reply = LegacyPanelServices(OrderedService).submit_logout(
        headers={},
        path='/api/v1/logout',
        form={'realm': ['user']},
        client_address=('203.0.113.1', 1),
        realm='admin',
    )

    assert reply.cookie == 'sid=cleared'
    assert calls == ['meta', 'cookies', ('delete', 'admin-current'), ('clear', False)]


def test_service_rejects_an_unknown_realm_before_any_side_effect():
    class UntouchedService:
        def __getattribute__(self, name):
            raise AssertionError(f'service touched: {name}')

    with pytest.raises(ValueError, match='invalid logout realm'):
        LegacyPanelServices(UntouchedService()).submit_logout(
            headers={},
            path='/api/v1/logout',
            form={},
            client_address=('', 0),
            realm='intruder',
        )


def test_cancelled_logout_retains_capacity_until_the_worker_finishes():
    class BlockingServices(RecordingLogoutServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_logout(self, **kwargs):
            self.calls.append(kwargs)
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test gate timed out')
            return LogoutReply(cookie='sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        pending = asyncio.create_task(
            _asgi_exchange(app, path='/api/v1/logout', headers=_form_headers(b''))
        )
        assert await asyncio.to_thread(services.entered.wait, 1)
        busy_before_cancel = await _asgi_exchange(
            app,
            path='/api/v1/logout',
            headers=_form_headers(b''),
        )
        pending.cancel()
        await asyncio.sleep(0)
        busy_after_cancel = await _asgi_exchange(
            app,
            path='/api/v1/logout',
            headers=_form_headers(b''),
        )
        services.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        recovered = await _asgi_exchange(
            app,
            path='/api/v1/logout',
            headers=_form_headers(b''),
        )
        return services, busy_before_cancel, busy_after_cancel, recovered

    services, busy_before_cancel, busy_after_cancel, recovered = asyncio.run(scenario())
    assert _response_status(busy_before_cancel) == 503
    assert _response_status(busy_after_cancel) == 503
    assert _response_status(recovered) == 200
    assert len(services.calls) == 2


def test_unrelated_read_remains_responsive_below_capacity_while_logout_blocks():
    class BlockingServices(RecordingLogoutServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_logout(self, **kwargs):
            self.calls.append(kwargs)
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test gate timed out')
            return LogoutReply(cookie='sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')

        def read_session(self, *, headers, path):
            del headers, path
            return {'role': 'admin'}

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=2)
        pending = asyncio.create_task(
            _asgi_exchange(app, path='/api/v1/logout', headers=_form_headers(b''))
        )
        assert await asyncio.to_thread(services.entered.wait, 1)
        session = await _asgi_exchange(
            app,
            method='GET',
            path='/api/v1/session',
            headers=[(b'host', b'panel.test')],
        )
        services.release.set()
        completed = await pending
        return session, completed

    session, completed = asyncio.run(scenario())
    assert _response_status(session) == 200
    assert _response_json(session) == {'role': 'admin'}
    assert _response_status(completed) == 200


def test_logout_worker_error_releases_capacity():
    services = RecordingLogoutServices(error=RuntimeError('private worker failure'))
    app = create_app(services, max_requests=1)

    failed = _run_exchange(app, path='/api/v1/logout', headers=_form_headers(b''))
    services.error = None
    recovered = _run_exchange(app, path='/api/v1/logout', headers=_form_headers(b''))

    assert _response_status(failed) == 500
    assert _response_json(failed) == {'error': 'internal_error'}
    assert _response_status(recovered) == 200
