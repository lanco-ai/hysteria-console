"""HTTP contract tests for the read-only FastAPI boundary."""

import asyncio
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import subscription_service as ss
import web_api.app as web_api_app
import web_api.services as web_api_services
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices

FIXED_NOW = datetime(2026, 9, 12, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def real_state(tmp_path, monkeypatch):
    paths = {
        'USERS_FILE': tmp_path / 'users.json',
        'META_FILE': tmp_path / 'meta.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_FILE': tmp_path / 'usage.json',
        'USAGE_DAILY_FILE': tmp_path / 'usage_daily.json',
        'USAGE_HOURLY_FILE': tmp_path / 'usage_hourly.json',
        'USAGE_PRESERVED_FILE': tmp_path / 'usage_preserved.json',
        'ONLINE_FILE': tmp_path / 'online.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
        'DISPLAY_MULTIPLIER_STATE_FILE': tmp_path / 'display_multiplier.json',
        'RESET_LOG_FILE': tmp_path / 'usage_reset.log',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)

    user = {
        'sub_token': 'subscription-secret',
        'panel_pass_hash': 'current-user-password-hash',
        'monthly_quota_bytes': 1000,
        'max_devices': 3,
        'note': 'private operator note',
        'proxy_password': 'proxy-secret',
    }
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': 'current-admin-password-hash',
            'admin_token': 'query-token-must-not-authorize',
            'settlement_day': 1,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-09-01',
        },
    )
    _write_json(paths['USERS_FILE'], {'demo_alex': user})
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    _write_json(paths['USAGE_FILE'], {})
    _write_json(
        paths['USAGE_DAILY_FILE'],
        {'2026-09-12': {'demo_alex': {'tx': 10, 'rx': 15, 'total': 25}}},
    )
    _write_json(paths['USAGE_HOURLY_FILE'], {})
    _write_json(
        paths['USAGE_PRESERVED_FILE'],
        {'2026-09-01': {'retired': {'tx': 2, 'rx': 3, 'total': 5}}},
    )
    _write_json(paths['ONLINE_FILE'], {'demo_alex': 2})
    _write_json(
        paths['DISPLAY_MULTIPLIER_STATE_FILE'],
        {'enabled': True, 'multiplier': 2.0},
    )
    return {'paths': paths, 'user': user}


@pytest.fixture
def api_client(real_state):
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=4)) as client:
        yield client


def _admin_cookie():
    generation = ss._credential_generation('current-admin-password-hash')
    return {'Cookie': f'sid={ss.create_session("admin", generation)}'}


def _user_cookie(*, credential_kind=ss.USER_SESSION_PANEL_PASSWORD):
    credential = (
        'subscription-secret'
        if credential_kind == ss.USER_SESSION_SUBSCRIPTION_TOKEN
        else 'current-user-password-hash'
    )
    sid = ss.create_user_session(
        'demo_alex',
        ss._credential_generation(credential),
        credential_kind,
    )
    return {'Cookie': f'usid={sid}'}


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


def _assert_security_headers(response):
    for name, expected in SECURITY_HEADERS.items():
        assert response.headers[name] == expected


def test_overview_requires_admin(api_client):
    response = api_client.get('/api/v1/admin/overview')
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_session_requires_a_valid_existing_cookie(api_client):
    response = api_client.get('/api/v1/session')
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_unknown_api_does_not_fall_back(api_client):
    response = api_client.get('/api/v1/missing')
    assert response.status_code == 404
    assert response.headers['content-type'].startswith('application/json')


def test_query_credentials_do_not_authorize_reads(api_client):
    for path in (
        '/api/v1/session?token=query-token-must-not-authorize',
        '/api/v1/admin/overview?token=query-token-must-not-authorize',
        '/api/v1/admin/logs?token=query-token-must-not-authorize',
    ):
        response = api_client.get(path)
        assert response.status_code == 401
        assert response.json() == {'error': 'login_required'}


def test_admin_session_wins_over_a_user_session(api_client):
    admin = _admin_cookie()['Cookie']
    user = _user_cookie()['Cookie']
    response = api_client.get(
        '/api/v1/session',
        headers={'Cookie': f'{admin}; {user}'},
    )
    assert response.status_code == 200
    assert response.json() == {'role': 'admin'}


def test_user_session_returns_only_the_public_identity(api_client):
    response = api_client.get('/api/v1/session', headers=_user_cookie())
    assert response.status_code == 200
    assert response.json() == {'role': 'user', 'username': 'demo_alex'}
    assert 'subscription-secret' not in response.text
    assert 'current-user-password-hash' not in response.text


@pytest.mark.parametrize(
    ('account_change', 'expected_error'),
    [
        ({'disabled': True}, 'disabled'),
        ({'expires_at': '2026-09-11'}, 'expired'),
        ({'panel_password_must_change': True}, 'password_change_required'),
    ],
)
def test_user_lifecycle_failures_retain_existing_error_codes(
    api_client,
    real_state,
    account_change,
    expected_error,
):
    headers = _user_cookie()
    users = json.loads(real_state['paths']['USERS_FILE'].read_text(encoding='utf-8'))
    users['demo_alex'].update(account_change)
    _write_json(real_state['paths']['USERS_FILE'], users)

    response = api_client.get('/api/v1/session', headers=headers)
    assert response.status_code == 403
    assert response.json() == {'error': expected_error}


def test_expired_session_is_not_accepted(api_client, real_state):
    _write_json(
        real_state['paths']['SESSIONS_FILE'],
        {'expired-admin': {'user': 'admin', 'exp': 1}},
    )
    response = api_client.get(
        '/api/v1/session',
        headers={'Cookie': 'sid=expired-admin'},
    )
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_admin_credential_replacement_invalidates_existing_session(
    api_client,
    real_state,
):
    headers = _admin_cookie()
    meta = json.loads(real_state['paths']['META_FILE'].read_text(encoding='utf-8'))
    meta['admin_pass_hash'] = 'replacement-admin-password-hash'
    _write_json(real_state['paths']['META_FILE'], meta)

    response = api_client.get('/api/v1/admin/overview', headers=headers)
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_subscription_token_rotation_invalidates_existing_user_session(
    api_client,
    real_state,
):
    headers = _user_cookie(credential_kind=ss.USER_SESSION_SUBSCRIPTION_TOKEN)
    users = json.loads(real_state['paths']['USERS_FILE'].read_text(encoding='utf-8'))
    users['demo_alex']['sub_token'] = 'rotated-subscription-secret'
    _write_json(real_state['paths']['USERS_FILE'], users)

    response = api_client.get('/api/v1/session', headers=headers)
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_user_session_cannot_read_admin_overview(api_client):
    response = api_client.get(
        '/api/v1/admin/overview',
        headers=_user_cookie(),
    )
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}


def test_logs_require_admin_and_reject_user_session(api_client):
    anonymous = api_client.get('/api/v1/admin/logs')
    user = api_client.get('/api/v1/admin/logs', headers=_user_cookie())

    assert anonymous.status_code == 401
    assert anonymous.json() == {'error': 'login_required'}
    assert user.status_code == 401
    assert user.json() == {'error': 'login_required'}


def test_logs_return_latest_safe_rows_from_real_temporary_log(api_client, real_state):
    records = [
        {
            'time': '2026-09-11 09:08:07',
            'actor': 'older-admin',
            'ip': '192.0.2.1',
            'action': 'reset_usage_all',
            'target': 'all',
            'month': '2026-09',
            'before': {'total': 1024},
            'after': {'total': 2048},
        },
        ['non-object-secret', {'password': 'never-return-this'}],
        {
            'time': '<script>alert(1)</script>',
            'actor': '<b>newer-admin</b>',
            'ip': '198.51.100.7',
            'action': 'rotate_token',
            'target': 'demo_alex',
            'month': '2026-10',
            'before': {},
            'after': {'total': 9000},
            'password_hash': 'private-password-hash',
            'proxy_password': 'private-proxy-password',
        },
    ]
    lines = [json.dumps(records[0]), '{malformed', json.dumps(records[1]), json.dumps(records[2])]
    real_state['paths']['RESET_LOG_FILE'].write_text('\n'.join(lines) + '\n', encoding='utf-8')

    response = api_client.get('/api/v1/admin/logs', headers=_admin_cookie())

    assert response.status_code == 200
    assert response.json() == {
        'limit': 300,
        'rows': [
            {
                'time': '<script>alert(1)</script>',
                'actor': '<b>newer-admin</b>',
                'ip': '198.51.100.7',
                'action': '重置订阅令牌',
                'target': 'demo_alex',
                'month': '2026-10',
                'detail': '',
            },
            {
                'time': '2026-09-11 09:08:07',
                'actor': 'older-admin',
                'ip': '192.0.2.1',
                'action': '清空全部流量',
                'target': 'all',
                'month': '2026-09',
                'detail': '1.00 KB → 2.00 KB',
            },
        ],
    }
    assert 'private-password-hash' not in response.text
    assert 'private-proxy-password' not in response.text
    assert 'non-object-secret' not in response.text


def test_logs_ignore_query_limit_and_keep_the_bounded_contract(api_client, real_state):
    real_state['paths']['RESET_LOG_FILE'].write_text(
        json.dumps({'time': 'newest'}) + '\n',
        encoding='utf-8',
    )

    response = api_client.get(
        '/api/v1/admin/logs?limit=999999',
        headers=_admin_cookie(),
    )

    assert response.status_code == 200
    assert response.json()['limit'] == 300


def test_logs_response_model_drops_unknown_and_secret_fields(api_client, monkeypatch):
    monkeypatch.setattr(
        web_api_services,
        'read_reset_logs',
        lambda *args, **kwargs: {
            'limit': 300,
            'admin_token': 'top-level-secret',
            'rows': [
                {
                    'time': 'now',
                    'actor': 'admin',
                    'ip': '127.0.0.1',
                    'action': 'action',
                    'target': 'target',
                    'month': 'month',
                    'detail': 'detail',
                    'before': {'password_hash': 'nested-secret'},
                    'proxy_password': 'row-secret',
                }
            ],
        },
    )

    response = api_client.get('/api/v1/admin/logs', headers=_admin_cookie())

    assert response.status_code == 200
    assert set(response.json()) == {'limit', 'rows'}
    assert set(response.json()['rows'][0]) == {
        'time',
        'actor',
        'ip',
        'action',
        'target',
        'month',
        'detail',
    }
    assert 'secret' not in response.text


def test_log_io_failure_is_not_reported_as_successful_empty_data(
    api_client,
    real_state,
    monkeypatch,
):
    original = Path.open
    reset_log_path = real_state['paths']['RESET_LOG_FILE']

    def fail_reset_log(self, *args, **kwargs):
        if self == reset_log_path:
            raise PermissionError('private log path')
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, 'open', fail_reset_log)

    response = api_client.get('/api/v1/admin/logs', headers=_admin_cookie())

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'private log path' not in response.text


def test_overview_matches_hand_derived_values_and_legacy_builder(api_client):
    response = api_client.get(
        '/api/v1/admin/overview',
        headers=_admin_cookie(),
    )
    assert response.status_code == 200
    assert response.json() == {
        'ts': '2026-09-12T10:30:45+08:00',
        'total_used': 60,
        'users': [
            {
                'user': 'demo_alex',
                'tx': 20,
                'rx': 30,
                'used': 50,
                'total': 1000,
                'percent': 5.0,
                'online': 2,
                'revision': '99908dc76ea3ae87a4d6b13d13734a6a1c996224f9fdfef6b1be2e57405e895b',
                'disabled': False,
            }
        ],
    }
    legacy = ss.request_multiplier_snapshot(
        lambda: ss._build_overview_json_payload(now=FIXED_NOW)
    )()
    assert response.json() == legacy


def test_overview_models_exclude_unknown_and_secret_shaped_fields(
    api_client,
    monkeypatch,
):
    original = ss._build_overview_json_payload

    def builder_with_secrets(*, now):
        payload = original(now=now)
        payload['admin_token'] = 'top-level-secret'
        payload['users'][0].update(
            {
                'password_hash': 'password-secret',
                'sub_token': 'subscription-secret',
                'proxy_password': 'proxy-secret',
            }
        )
        return payload

    monkeypatch.setattr(ss, '_build_overview_json_payload', builder_with_secrets)
    response = api_client.get(
        '/api/v1/admin/overview',
        headers=_admin_cookie(),
    )
    assert response.status_code == 200
    assert set(response.json()) == {'ts', 'total_used', 'users'}
    assert set(response.json()['users'][0]) == {
        'user',
        'tx',
        'rx',
        'used',
        'total',
        'percent',
        'online',
        'revision',
        'disabled',
    }
    assert 'secret' not in response.text


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/session',
        '/api/v1/admin/overview',
        '/api/v1/admin/logs',
        '/api/v1/missing',
    ],
)
def test_every_response_has_read_boundary_security_headers(api_client, path):
    response = api_client.get(path)
    _assert_security_headers(response)


@pytest.mark.parametrize('path', ['/docs', '/redoc', '/openapi.json'])
def test_automatic_documentation_is_disabled(api_client, path):
    response = api_client.get(path)
    assert response.status_code == 404
    assert response.headers['content-type'].startswith('application/json')


def test_read_routes_do_not_redirect_trailing_slashes(api_client):
    response = api_client.get('/api/v1/session/', follow_redirects=False)
    assert response.status_code == 404
    assert 'location' not in response.headers


def test_post_to_read_route_returns_json_405(api_client):
    response = api_client.post('/api/v1/session')
    assert response.status_code == 405
    assert response.headers['content-type'].startswith('application/json')
    _assert_security_headers(response)


@pytest.mark.parametrize(
    'path',
    ['/api/v1/session', '/api/v1/admin/overview', '/api/v1/admin/logs'],
)
def test_get_and_head_have_identical_status_and_headers(api_client, path):
    request_headers = _admin_cookie()
    get_response = api_client.get(path, headers=request_headers)
    head_response = api_client.head(path, headers=request_headers)
    assert head_response.status_code == get_response.status_code
    assert dict(head_response.headers) == dict(get_response.headers)
    assert head_response.content == b''


def _direct_asgi_exchange(app, method, path, *, query_string=b''):
    async def exchange():
        messages = []

        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}

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
                'query_string': query_string,
                'root_path': '',
                'headers': [],
                'client': ('127.0.0.1', 1234),
                'server': ('test', 80),
            },
            receive,
            send,
        )
        return messages

    return asyncio.run(exchange())


def _asgi_status(messages):
    return next(item['status'] for item in messages if item['type'] == 'http.response.start')


def test_malformed_raw_query_does_not_leak_capacity_or_enter_legacy_path():
    seen_paths = []

    class RecordingServices:
        def read_session(self, *, headers, path):
            del headers
            seen_paths.append(path)
            return {'role': 'admin'}

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    app = create_app(RecordingServices(), max_requests=1)
    responses = [
        _direct_asgi_exchange(
            app,
            'GET',
            '/api/v1/session',
            query_string=b'bad=\xff',
        ),
        _direct_asgi_exchange(app, 'GET', '/api/v1/session'),
        _direct_asgi_exchange(app, 'GET', '/api/v1/session'),
    ]
    assert [_asgi_status(response) for response in responses] == [200, 200, 200]
    assert seen_paths == ['/api/v1/session'] * 3


def test_pre_worker_preparation_exception_releases_capacity(monkeypatch):
    original = web_api_app._request_headers
    attempts = 0

    def fail_once(request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError('pre-worker preparation failed')
        return original(request)

    class SuccessfulServices:
        def read_session(self, *, headers, path):
            del headers, path
            return {'role': 'admin'}

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    monkeypatch.setattr(web_api_app, '_request_headers', fail_once)
    app = create_app(SuccessfulServices(), max_requests=1)
    responses = [_direct_asgi_exchange(app, 'GET', '/api/v1/session') for _ in range(3)]
    assert [_asgi_status(response) for response in responses] == [500, 200, 200]


@pytest.mark.parametrize(
    ('path', 'outcome', 'expected_status'),
    [
        ('/api/v1/session', {'role': 'admin'}, 200),
        ('/api/v1/session', 'login', 401),
        ('/api/v1/session', 'state', 503),
        ('/api/v1/session', 'internal', 500),
        ('/api/v1/missing', None, 404),
    ],
)
def test_head_suppresses_body_at_asgi_boundary_with_get_equivalent_headers(
    path,
    outcome,
    expected_status,
):
    class DirectServices:
        def read_session(self, *, headers, path):
            del headers, path
            if outcome == 'login':
                from web_api.services import LoginRequired

                raise LoginRequired
            if outcome == 'state':
                from web_api.services import StateUnavailable

                raise StateUnavailable
            if outcome == 'internal':
                raise RuntimeError('private exception text')
            return outcome

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    app = create_app(DirectServices(), max_requests=1)
    get_messages = _direct_asgi_exchange(app, 'GET', path)
    head_messages = _direct_asgi_exchange(app, 'HEAD', path)
    get_start = next(item for item in get_messages if item['type'] == 'http.response.start')
    head_start = next(item for item in head_messages if item['type'] == 'http.response.start')
    get_body = b''.join(
        item.get('body', b'') for item in get_messages if item['type'] == 'http.response.body'
    )
    head_body = b''.join(
        item.get('body', b'') for item in head_messages if item['type'] == 'http.response.body'
    )
    assert get_start['status'] == head_start['status'] == expected_status
    assert get_start['headers'] == head_start['headers']
    assert get_body
    assert head_body == b''


def test_factory_rejects_invalid_capacity_without_touching_services():
    class UntouchedServices:
        def __getattribute__(self, name):
            if name.startswith('read_'):
                raise AssertionError('factory touched a service')
            return super().__getattribute__(name)

    for invalid in (True, False, 0, -1, 1.5, '2'):
        with pytest.raises(ValueError, match='positive integer'):
            create_app(UntouchedServices(), max_requests=invalid)


def test_factory_construction_performs_no_io_or_service_calls(monkeypatch):
    class UntouchedServices:
        def read_session(self, *, headers, path):
            raise AssertionError('service called during construction')

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('service called during construction')

    def unexpected_io(*args, **kwargs):
        raise AssertionError('IO occurred during factory construction')

    monkeypatch.setattr(Path, 'read_text', unexpected_io)
    monkeypatch.setattr(socket, 'socket', unexpected_io)
    monkeypatch.setattr(threading.Thread, 'start', unexpected_io)
    app = create_app(UntouchedServices(), max_requests=1)
    assert app is not None


def test_state_failure_is_sanitized_and_runs_existing_fail_closed_policy(
    api_client,
    real_state,
    monkeypatch,
):
    headers = _admin_cookie()
    real_state['paths']['USERS_FILE'].write_text(
        '{"broken":',
        encoding='utf-8',
    )
    stopped = []
    monkeypatch.setattr(ss, '_fail_closed_static_access', stopped.append)

    response = api_client.get('/api/v1/admin/overview', headers=headers)
    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert len(stopped) == 1
    assert 'broken' not in response.text
    assert str(real_state['paths']['USERS_FILE']) not in response.text


def test_noncritical_state_failure_does_not_stop_static_access(
    api_client,
    real_state,
    monkeypatch,
):
    headers = _admin_cookie()
    real_state['paths']['ONLINE_FILE'].write_text('{"broken":', encoding='utf-8')
    stopped = []
    monkeypatch.setattr(ss, '_fail_closed_static_access', stopped.append)

    response = api_client.get('/api/v1/admin/overview', headers=headers)
    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert stopped == []


def test_os_error_is_sanitized_as_state_unavailable(
    api_client,
    monkeypatch,
):
    def unavailable_builder(*, now):
        del now
        raise OSError('private filesystem path')

    monkeypatch.setattr(ss, '_build_overview_json_payload', unavailable_builder)
    response = api_client.get(
        '/api/v1/admin/overview',
        headers=_admin_cookie(),
    )
    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert 'private' not in response.text


def test_multiplier_snapshot_is_request_local_and_resets_after_failure(
    api_client,
    monkeypatch,
):
    values = iter((2.0, 3.0))
    observed = []
    calls = 0

    def read_multiplier(**_kwargs):
        return next(values)

    def builder(*, now):
        nonlocal calls
        calls += 1
        observed.extend((ss.current_display_multiplier(), ss.current_display_multiplier()))
        if calls == 1:
            raise RuntimeError('first request fails')
        return {'ts': now.isoformat(), 'total_used': 0, 'users': []}

    monkeypatch.setattr(
        ss.display_config,
        'effective_display_multiplier_strict',
        read_multiplier,
    )
    monkeypatch.setattr(ss, '_build_overview_json_payload', builder)
    headers = _admin_cookie()
    failed = api_client.get('/api/v1/admin/overview', headers=headers)
    recovered = api_client.get('/api/v1/admin/overview', headers=headers)
    assert failed.status_code == 500
    assert recovered.status_code == 200
    assert observed == [2.0, 2.0, 3.0, 3.0]


def test_unexpected_exception_is_sanitized_and_capacity_recovers(real_state):
    class FailOnceServices:
        def __init__(self):
            self.calls = 0

        def read_session(self, *, headers, path):
            del headers, path
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError('password=secret at /root/private/state.json')
            return {'role': 'admin'}

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    with TestClient(create_app(FailOnceServices(), max_requests=1)) as client:
        failed = client.get('/api/v1/session')
        recovered = client.get('/api/v1/session')
    assert failed.status_code == 500
    assert failed.json() == {'error': 'internal_error'}
    assert 'secret' not in failed.text
    assert '/root' not in failed.text
    assert recovered.status_code == 200
    assert recovered.json() == {'role': 'admin'}


def test_sync_service_execution_is_off_the_event_loop_thread(real_state):
    class ThreadCheckingServices:
        def __init__(self):
            self.no_running_loop = False

        def read_session(self, *, headers, path):
            del headers, path
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                self.no_running_loop = True
            return {'role': 'admin'}

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    services = ThreadCheckingServices()
    with TestClient(create_app(services, max_requests=1)) as client:
        response = client.get('/api/v1/session')
    assert response.status_code == 200
    assert services.no_running_loop is True


def test_admission_rejects_overload_then_recovers(real_state):
    class BlockingServices:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()

        def read_session(self, *, headers, path):
            del headers, path
            self.entered.set()
            if not self.release.wait(timeout=3):
                raise RuntimeError('test gate timed out')
            return {'role': 'admin'}

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    services = BlockingServices()
    with TestClient(create_app(services, max_requests=1)) as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(client.get, '/api/v1/session')
            assert services.entered.wait(timeout=1)
            busy = client.get('/api/v1/session')
            services.release.set()
            completed = first.result(timeout=2)
        recovered = client.get('/api/v1/session')

    assert busy.status_code == 503
    assert busy.json() == {'error': 'server_busy'}
    assert busy.headers['retry-after'] == '1'
    assert completed.status_code == 200
    assert recovered.status_code == 200


def test_cancelled_nonabandoning_worker_keeps_capacity_until_io_finishes(real_state):
    class BlockingServices:
        def __init__(self):
            self.entered = threading.Event()
            self.release = threading.Event()
            self.finished = threading.Event()

        def read_session(self, *, headers, path):
            del headers, path
            self.entered.set()
            try:
                self.release.wait(timeout=3)
                return {'role': 'admin'}
            finally:
                self.finished.set()

        def read_admin_overview(self, *, headers, path):
            raise AssertionError('unexpected route')

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            first = asyncio.create_task(client.get('/api/v1/session'))
            for _ in range(100):
                if services.entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert services.entered.is_set()
            first.cancel()
            await asyncio.sleep(0)
            busy = await asyncio.wait_for(client.get('/api/v1/session'), timeout=0.5)
            services.release.set()
            with pytest.raises(asyncio.CancelledError):
                await first
            for _ in range(100):
                if services.finished.is_set():
                    break
                await asyncio.sleep(0.01)
            assert services.finished.is_set()
            recovered = await client.get('/api/v1/session')
        return busy, recovered

    busy, recovered = asyncio.run(scenario())
    assert busy.status_code == 503
    assert busy.json() == {'error': 'server_busy'}
    assert recovered.status_code == 200
