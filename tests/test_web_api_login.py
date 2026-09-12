"""Bounded JSON login transport contract tests."""

import asyncio
import http.client
import importlib
import json
import threading
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from login_service import LoginResult
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginReply

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))
ADMIN_HASH = 'fixture-admin-hash'
USER_HASH = 'fixture-user-hash'
INVARIANT_SECURITY_HEADERS = {
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
def login_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)
    monkeypatch.setattr(
        ss,
        'verify_secret',
        lambda plain, stored: (
            (plain, stored) in {('fixture-correct', ADMIN_HASH), ('user-correct', USER_HASH)}
        ),
    )
    ss._login_failures.clear()
    ss._user_login_failures.clear()
    ss._login_attempts_inflight.clear()
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
def login_client(login_state):
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield client


@contextmanager
def _legacy_server():
    server = ThreadingHTTPServer(('127.0.0.1', 0), ss.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _legacy_request(server, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    request_headers = {'Host': 'panel.test', **(headers or {})}
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    result = SimpleNamespace(
        status=response.status,
        headers={name.lower(): value for name, value in response.getheaders()},
        body=response.read(),
    )
    conn.close()
    return result


def _assert_api_security_headers(response):
    assert response.headers['cache-control'] == 'no-store'
    assert {
        name: response.headers[name] for name in INVARIANT_SECURITY_HEADERS
    } == INVARIANT_SECURITY_HEADERS


def test_admin_login_returns_allowlisted_json_and_generation_bound_cookie(
    login_client,
    login_state,
):
    response = login_client.post(
        '/api/v1/login',
        data={
            'admin_username': ' admin ',
            'admin_password': 'fixture-correct',
        },
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/admin?msg=login+success'}
    cookie = response.headers['set-cookie']
    assert cookie.startswith('sid=')
    assert '; Path=/; Max-Age=86400; HttpOnly; SameSite=Lax' in cookie
    sessions = json.loads(login_state['SESSIONS_FILE'].read_text(encoding='utf-8'))
    assert len(sessions) == 1
    stored = next(iter(sessions.values()))
    assert stored['user'] == 'admin'
    assert stored['credential_generation'] == ss._credential_generation(ADMIN_HASH)
    assert ADMIN_HASH not in response.text
    assert 'fixture-correct' not in response.text
    _assert_api_security_headers(response)


def test_admin_login_preserves_first_value_precedence_and_ignores_redirect_input(
    login_client,
):
    body = urlencode(
        [
            ('admin_username', ' admin '),
            ('admin_username', 'intruder'),
            ('admin_password', 'fixture-correct'),
            ('admin_password', 'wrong'),
            ('user_username', 'alice'),
            ('user_password', 'user-correct'),
            ('redirect_to', 'https://attacker.test/steal'),
        ]
    )

    response = login_client.post(
        '/api/v1/login',
        content=body,
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': '/admin?msg=login+success'}
    assert response.headers['set-cookie'].startswith('sid=')
    assert 'attacker' not in response.text


def test_secure_request_adds_secure_to_the_existing_cookie(login_client):
    response = login_client.post(
        '/api/v1/login',
        data={'admin_username': 'admin', 'admin_password': 'fixture-correct'},
        headers={'X-Forwarded-Proto': 'https'},
    )

    assert response.status_code == 200
    assert response.headers['set-cookie'].endswith('; Secure')


@pytest.mark.parametrize(
    ('data', 'expected'),
    [
        (
            {'admin_username': 'admin', 'admin_password': 'wrong'},
            {'ok': False, 'message': '用户名或密码错误'},
        ),
        ({'admin_password': 'fixture-correct'}, {'ok': False, 'message': '请输入用户名和密码'}),
    ],
)
def test_admin_login_failure_is_visible_but_never_sets_a_cookie(login_client, data, expected):
    response = login_client.post('/api/v1/login', data=data)

    assert response.status_code == 200
    assert response.json() == expected
    assert 'set-cookie' not in response.headers
    assert 'fixture-correct' not in response.text
    _assert_api_security_headers(response)


@pytest.mark.parametrize(
    ('account_change', 'expected_redirect'),
    [({}, '/user/panel'), ({'panel_password_must_change': True}, '/user/change-password')],
)
def test_compatibility_user_login_uses_existing_destinations_and_cookie(
    login_client,
    login_state,
    account_change,
    expected_redirect,
):
    users = json.loads(login_state['USERS_FILE'].read_text(encoding='utf-8'))
    users['alice'].update(account_change)
    _write_json(login_state['USERS_FILE'], users)

    response = login_client.post(
        '/api/v1/login',
        data={'user_username': ' alice ', 'user_password': 'user-correct'},
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'redirect_to': expected_redirect}
    assert response.headers['set-cookie'].startswith('usid=')
    sessions = json.loads(login_state['USER_SESSIONS_FILE'].read_text(encoding='utf-8'))
    stored = next(iter(sessions.values()))
    assert stored['user'] == 'alice'
    assert stored['credential_generation'] == ss._credential_generation(USER_HASH)
    assert stored['credential_kind'] == ss.USER_SESSION_PANEL_PASSWORD


@pytest.mark.parametrize('account_change', [{'disabled': True}, {'expires_at': '2026-09-11'}])
def test_compatibility_user_ineligible_failure_is_neutral(
    login_client,
    login_state,
    account_change,
):
    users = json.loads(login_state['USERS_FILE'].read_text(encoding='utf-8'))
    users['alice'].update(account_change)
    _write_json(login_state['USERS_FILE'], users)

    response = login_client.post(
        '/api/v1/login',
        data={'user_username': 'alice', 'user_password': 'user-correct'},
    )

    assert response.status_code == 200
    assert response.json() == {'ok': False, 'message': '请使用管理员账号登录控制台。'}
    assert 'set-cookie' not in response.headers
    assert 'alice' not in response.text


def test_compatibility_user_invalid_and_throttled_failures_are_neutral(
    login_client,
    monkeypatch,
):
    monkeypatch.setattr(ss, '_LOGIN_MAX', 1)
    form = {'user_username': 'alice', 'user_password': 'wrong'}

    invalid = login_client.post('/api/v1/login', data=form)
    throttled = login_client.post('/api/v1/login', data=form)

    expected = {'ok': False, 'message': '请使用管理员账号登录控制台。'}
    assert invalid.status_code == 200
    assert invalid.json() == expected
    assert throttled.status_code == 429
    assert throttled.json() == expected
    assert throttled.headers['retry-after'] == '3600'
    assert 'alice' not in invalid.text
    assert 'alice' not in throttled.text


def test_login_throttle_uses_existing_status_message_and_retry_header(
    login_client,
    monkeypatch,
):
    monkeypatch.setattr(ss, '_LOGIN_MAX', 1)
    form = {'admin_username': 'admin', 'admin_password': 'wrong'}

    first = login_client.post('/api/v1/login', data=form)
    throttled = login_client.post('/api/v1/login', data=form)

    assert first.status_code == 200
    assert first.json() == {'ok': False, 'message': '用户名或密码错误'}
    assert throttled.status_code == 429
    assert throttled.json() == {'ok': False, 'message': '登录尝试过于频繁，请 1 小时后再试'}
    assert throttled.headers['retry-after'] == '3600'
    assert 'set-cookie' not in throttled.headers
    _assert_api_security_headers(throttled)


def test_login_is_post_only_and_head_body_is_suppressed(login_client, login_state):
    before = login_state['SESSIONS_FILE'].read_text(encoding='utf-8')

    get_response = login_client.get('/api/v1/login')
    head_response = login_client.head('/api/v1/login')

    assert get_response.status_code == 405
    assert get_response.json() == {'error': 'method_not_allowed'}
    assert head_response.status_code == 405
    assert head_response.content == b''
    assert login_state['SESSIONS_FILE'].read_text(encoding='utf-8') == before
    _assert_api_security_headers(head_response)


def test_api_security_headers_exactly_match_real_legacy_invariants(
    login_client,
    login_state,
):
    del login_state
    with _legacy_server() as server:
        legacy = _legacy_request(server, 'GET', '/login')
    api = login_client.post('/api/v1/login', data={})

    assert legacy.status == 200
    assert {name: legacy.headers[name] for name in INVARIANT_SECURITY_HEADERS} == (
        INVARIANT_SECURITY_HEADERS
    )
    assert {name: api.headers[name] for name in INVARIANT_SECURITY_HEADERS} == {
        name: legacy.headers[name] for name in INVARIANT_SECURITY_HEADERS
    }
    assert api.headers['cache-control'] == 'no-store'


def test_legacy_login_keeps_its_cross_site_origin_exception(login_state):
    del login_state
    body = urlencode({'admin_username': 'admin', 'admin_password': 'fixture-correct'}).encode()
    with _legacy_server() as server:
        response = _legacy_request(
            server,
            'POST',
            '/login',
            body=body,
            headers={
                'Origin': 'https://attacker.test',
                'Content-Type': 'application/x-www-form-urlencoded',
                'Content-Length': str(len(body)),
            },
        )

    assert response.status == 302
    assert response.headers['location'] == '/admin?msg=login+success'


class RecordingLoginServices:
    def __init__(self, result=None, *, cookie=None, error=None):
        self.calls = []
        self.result = result or LoginResult(outcome='missing')
        self.cookie = cookie
        self.error = error

    def submit_login(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(result=self.result, cookie=self.cookie)


def test_internal_login_reply_is_frozen_and_hides_cookie_from_repr():
    reply = LoginReply(
        result=LoginResult(outcome='missing'),
        cookie='sid=private-session-secret',
    )

    assert reply.cookie == 'sid=private-session-secret'
    assert 'private-session-secret' not in repr(reply)
    with pytest.raises(FrozenInstanceError):
        reply.cookie = 'replacement'


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
    path='/api/v1/login',
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


def test_request_headers_preserve_repetitions_first_value_and_latin1():
    try:
        module = importlib.import_module('web_api.requests')
    except ModuleNotFoundError:
        pytest.fail('web_api.requests must provide the shared raw-header adapter')

    headers = module.RequestHeaders(
        [
            (b'X-Name', b'first'),
            (b'x-name', b'second'),
            (b'X-Latin', b'caf\xe9'),
        ]
    )

    assert headers['X-NAME'] == 'first'
    assert headers.get_all('x-name') == ['first', 'second']
    assert headers.get_all('missing') is None
    assert headers.get_all('missing', []) == []
    assert headers['x-latin'] == 'caf\xe9'
    assert dict(module.RequestHeaders({'Mixed-Case': 'value'})) == {'mixed-case': 'value'}


def test_cross_site_login_is_rejected_before_receipt_and_service_call():
    services = RecordingLoginServices()
    app = create_app(services, max_requests=1)

    async def forbidden_receive():
        raise AssertionError('cross-site input must not be received')

    messages = _run_exchange(
        app,
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


@pytest.mark.parametrize(
    ('headers', 'expected_status', 'expected_error'),
    [
        ([(b'content-length', b'3'), (b'content-length', b'3')], 400, 'bad_request'),
        ([(b'content-length', b'3'), (b'content-length', b'4')], 400, 'bad_request'),
        ([(b'content-length', b'-1')], 400, 'bad_request'),
        ([(b'content-length', b'3x')], 400, 'bad_request'),
        ([], 400, 'bad_request'),
        (
            [(b'content-length', b'3'), (b'transfer-encoding', b'chunked')],
            400,
            'bad_request',
        ),
        (
            [(b'content-length', b'3'), (b'content-type', b'application/json')],
            400,
            'bad_request',
        ),
        ([(b'content-length', str(256 * 1024 + 1).encode())], 413, 'request_too_large'),
    ],
)
def test_invalid_raw_form_headers_are_rejected_without_receipt_or_state_call(
    headers,
    expected_status,
    expected_error,
):
    services = RecordingLoginServices()
    app = create_app(services, max_requests=1)

    async def forbidden_receive():
        raise AssertionError('invalid headers must be rejected before receiving')

    messages = _run_exchange(
        app,
        headers=[(b'host', b'panel.test'), *headers],
        receive=forbidden_receive,
    )

    assert _response_status(messages) == expected_status
    assert _response_json(messages) == {'error': expected_error}
    assert services.calls == []


@pytest.mark.parametrize(
    ('claimed', 'events', 'expected_status', 'expected_error'),
    [
        (
            4,
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
        (
            2,
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
        (
            5,
            [
                {'type': 'http.request', 'body': b'a=', 'more_body': True},
                {'type': 'http.request', 'body': b'1&b', 'more_body': False},
            ],
            400,
            'bad_request',
        ),
        (
            256 * 1024,
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
    ],
)
def test_incomplete_overlong_and_oversized_raw_bodies_are_rejected(
    claimed,
    events,
    expected_status,
    expected_error,
):
    services = RecordingLoginServices()
    app = create_app(services, max_requests=1)
    headers = [
        (b'host', b'panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
        (b'content-length', str(claimed).encode()),
    ]

    messages = _run_exchange(app, headers=headers, events=events)

    assert _response_status(messages) == expected_status
    assert _response_json(messages) == {'error': expected_error}
    assert services.calls == []


@pytest.mark.parametrize(
    'body',
    [
        b'admin_username=\xff',
        b'admin_username=%ZZ',
        b'admin_username',
        b'&'.join(f'field{i}=x'.encode() for i in range(129)),
    ],
)
def test_malformed_form_bodies_are_bad_requests_without_service_calls(body):
    services = RecordingLoginServices()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(body),
        events=[{'type': 'http.request', 'body': body, 'more_body': False}],
    )

    assert _response_status(messages) == 400
    assert _response_json(messages) == {'error': 'bad_request'}
    assert services.calls == []


def test_multichunk_form_and_actual_peer_are_passed_to_service():
    services = RecordingLoginServices()
    body = b'admin_username=admin&admin_password=correct'
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(body, (b'x-name', b'first'), (b'x-name', b'second')),
        events=[
            {'type': 'http.request', 'body': body[:12], 'more_body': True},
            {'type': 'http.request', 'body': body[12:], 'more_body': False},
        ],
        client=('203.0.113.9', 9876),
    )

    assert _response_status(messages) == 200
    assert _response_json(messages) == {'ok': False, 'message': '请输入用户名和密码'}
    assert len(services.calls) == 1
    assert services.calls[0]['form'] == {
        'admin_username': ['admin'],
        'admin_password': ['correct'],
    }
    assert services.calls[0]['client_address'] == ('203.0.113.9', 9876)
    assert services.calls[0]['headers'].get_all('x-name') == ['first', 'second']


def test_explicit_empty_input_is_a_missing_login_not_a_parser_failure():
    services = RecordingLoginServices()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(b''),
        events=[{'type': 'http.request', 'body': b'', 'more_body': False}],
    )

    assert _response_status(messages) == 200
    assert _response_json(messages) == {'ok': False, 'message': '请输入用户名和密码'}
    assert services.calls[0]['form'] == {}


@pytest.mark.parametrize(
    ('body', 'expected_status', 'expected_error'),
    [
        (b'a=1', 400, 'bad_request'),
        (b'x' * (256 * 1024 + 1), 413, 'request_too_large'),
    ],
    ids=['nonempty', 'oversized'],
)
def test_zero_claim_rejects_queued_body_without_authentication(
    body,
    expected_status,
    expected_error,
):
    services = RecordingLoginServices()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(b''),
        events=[{'type': 'http.request', 'body': body, 'more_body': False}],
    )

    assert _response_status(messages) == expected_status
    assert _response_json(messages) == {'error': expected_error}
    assert services.calls == []


def test_disconnect_during_body_receipt_is_bad_request_and_releases_capacity():
    services = RecordingLoginServices()
    app = create_app(services, max_requests=1)
    body = b'a=1'
    disconnected = _run_exchange(
        app,
        headers=_form_headers(body),
        events=[{'type': 'http.disconnect'}],
    )
    recovered = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(disconnected) == 400
    assert _response_json(disconnected) == {'error': 'bad_request'}
    assert len(services.calls) == 1
    assert _response_status(recovered) == 200


def test_body_receipt_timeout_is_408_and_does_not_call_service(monkeypatch):
    requests_module = importlib.import_module('web_api.requests')
    monkeypatch.setattr(requests_module, 'FORM_READ_TIMEOUT', 0.01)
    services = RecordingLoginServices()

    async def never_receive():
        await asyncio.Event().wait()

    app = create_app(services, max_requests=1)
    messages = _run_exchange(
        app,
        headers=_form_headers(b'a=1'),
        receive=never_receive,
    )
    recovered = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(messages) == 408
    assert _response_json(messages) == {'error': 'request_timeout'}
    assert _response_status(recovered) == 200
    assert len(services.calls) == 1


def test_service_timeout_error_is_sanitized_500_not_body_timeout():
    services = RecordingLoginServices(error=TimeoutError('verifier timed out with secret'))
    app = create_app(services, max_requests=1)
    messages = _run_exchange(
        app,
        headers=_form_headers(b''),
    )
    services.error = None
    recovered = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert _response_status(recovered) == 200


def test_state_failure_uses_login_policy_path_and_never_mutates_sessions(
    login_state,
    monkeypatch,
):
    login_state['META_FILE'].write_text('{broken', encoding='utf-8')
    before = login_state['SESSIONS_FILE'].read_bytes()
    classified = []

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    app = create_app(LegacyPanelServices(ss), max_requests=1)

    messages = _run_exchange(app, headers=_form_headers(b''))

    assert _response_status(messages) == 503
    assert _response_json(messages) == {'error': 'state_unavailable'}
    assert len(classified) == 1
    assert issubclass(classified[0][0], ss.state_store.StateStoreError)
    assert classified[0][1] == '/login'
    assert login_state['SESSIONS_FILE'].read_bytes() == before


def test_actual_peer_and_locally_forwarded_ip_use_separate_throttle_buckets(
    login_state,
    monkeypatch,
):
    del login_state
    monkeypatch.setattr(ss, '_LOGIN_MAX', 1)
    app = create_app(LegacyPanelServices(ss), max_requests=1)
    body = b'admin_username=admin&admin_password=wrong'
    forged_headers = _form_headers(body, (b'x-real-ip', b'203.0.113.77'))
    request_event = [{'type': 'http.request', 'body': body, 'more_body': False}]

    direct_first = _run_exchange(
        app,
        headers=forged_headers,
        events=request_event,
        client=('198.51.100.8', 1000),
    )
    direct_second = _run_exchange(
        app,
        headers=forged_headers,
        events=request_event,
        client=('198.51.100.8', 1001),
    )
    forwarded_first = _run_exchange(
        app,
        headers=forged_headers,
        events=request_event,
        client=('127.0.0.1', 1002),
    )

    assert _response_status(direct_first) == 200
    assert _response_status(direct_second) == 429
    assert _response_status(forwarded_first) == 200
    assert set(ss._login_failures) == {'198.51.100.8', '203.0.113.77'}


def test_pending_body_owns_admission_and_cancellation_frees_it():
    async def scenario():
        services = RecordingLoginServices()
        app = create_app(services, max_requests=1)
        entered = asyncio.Event()

        async def pending_receive():
            entered.set()
            await asyncio.Event().wait()

        pending = asyncio.create_task(
            _asgi_exchange(app, headers=_form_headers(b'a=1'), receive=pending_receive)
        )
        await entered.wait()
        busy = await _asgi_exchange(app, headers=_form_headers(b''))
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        recovered = await _asgi_exchange(app, headers=_form_headers(b''))
        return services, busy, recovered

    services, busy, recovered = asyncio.run(scenario())
    assert _response_status(busy) == 503
    assert _response_json(busy) == {'error': 'server_busy'}
    assert _response_status(recovered) == 200
    assert len(services.calls) == 1


def test_cancelled_sync_verifier_retains_admission_until_worker_finishes():
    class BlockingServices(RecordingLoginServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_login(self, **kwargs):
            self.calls.append(kwargs)
            self.entered.set()
            self.release.wait(timeout=5)
            return SimpleNamespace(result=self.result, cookie=None)

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        pending = asyncio.create_task(_asgi_exchange(app, headers=_form_headers(b'')))
        while not services.entered.is_set():
            await asyncio.sleep(0)
        pending.cancel()
        await asyncio.sleep(0)
        busy = await _asgi_exchange(app, headers=_form_headers(b''))
        marker = []
        await asyncio.sleep(0)
        marker.append('responsive')
        services.release.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        recovered = await _asgi_exchange(app, headers=_form_headers(b''))
        return services, busy, marker, recovered

    services, busy, marker, recovered = asyncio.run(scenario())
    assert _response_status(busy) == 503
    assert marker == ['responsive']
    assert _response_status(recovered) == 200
    assert len(services.calls) == 2
