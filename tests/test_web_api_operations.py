"""Bounded administrator overview-operation API contract tests."""

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
import subscription_service as ss
from fastapi.testclient import TestClient
from overview_mutation_result import OverviewMutationResult
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginRequired

FIXED_NOW = datetime(2026, 9, 14, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))
ACTION_PATHS = {
    'cycle': '/api/v1/admin/operations/cycle',
    'reset-usage': '/api/v1/admin/operations/reset-usage',
    'refresh-usage': '/api/v1/admin/operations/refresh-usage',
    'reset-usage-all': '/api/v1/admin/operations/reset-usage-all',
    'pause-user': '/api/v1/admin/operations/pause-user',
    'toggle-user': '/api/v1/admin/operations/toggle-user',
}
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
def operation_api_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_FILE': tmp_path / 'usage.json',
        'USAGE_DAILY_FILE': tmp_path / 'usage_daily.json',
        'USAGE_HOURLY_FILE': tmp_path / 'usage_hourly.json',
        'USAGE_PRESERVED_FILE': tmp_path / 'usage_preserved.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
        'RESET_LOG_FILE': tmp_path / 'usage_reset.log',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)
    admin_hash = ss.hash_secret('fixture-admin-password')
    user_hash = ss.hash_secret('fixture-user-password')
    user = {
        'panel_pass_hash': user_hash,
        'sub_token': 'private-subscription-token',
        'disabled': False,
        'monthly_quota_bytes': 1000,
    }
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': admin_hash,
            'admin_token': 'query-token-must-not-authorize',
            'settlement_day': 1,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-09-01',
        },
    )
    _write_json(paths['USERS_FILE'], {'fixture': user})
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    _write_json(paths['USAGE_FILE'], {'2026-09': {'fixture': {'total': 25}}})
    _write_json(
        paths['USAGE_DAILY_FILE'],
        {'2026-09-14': {'fixture': {'tx': 10, 'rx': 15, 'total': 25}}},
    )
    _write_json(
        paths['USAGE_HOURLY_FILE'],
        {'2026-09-14T10': {'fixture': {'tx': 10, 'rx': 15, 'total': 25}}},
    )
    _write_json(paths['USAGE_PRESERVED_FILE'], {'2026-09-01': {}})
    monkeypatch.setattr(ss, '_clear_alert_dedup_for_users', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        ss, '_sync_static_access_from_users', lambda *_args, **_kwargs: (False, False)
    )
    monkeypatch.setattr(ss, 'hy_kick', lambda _users: None)
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: None)
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: None)
    xray_config = tmp_path / 'xray.json'
    tuic_config = tmp_path / 'tuic.json'
    _write_json(xray_config, {})
    _write_json(tuic_config, {})
    monkeypatch.setattr(ss.xray_config, 'CONFIG_FILE', xray_config)
    monkeypatch.setattr(ss.tuic_config, 'CONFIG_FILE', tuic_config)
    ss.xray_config._mark_reload_pending(xray_config)
    admin_sid = ss.create_session('admin', ss._credential_generation(admin_hash))
    user_sid = ss.create_user_session(
        'fixture', ss._credential_generation(user_hash), ss.USER_SESSION_PANEL_PASSWORD
    )
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield SimpleNamespace(
            client=client,
            paths=paths,
            user=user,
            admin_sid=admin_sid,
            user_sid=user_sid,
            xray_config=xray_config,
            tuic_config=tuic_config,
        )


def _admin_headers(state):
    return {'Cookie': f'sid={state.admin_sid}', 'Origin': 'http://testserver'}


def _form_for(state, action):
    revision = ss.user_config_revision(state.user)
    forms = {
        'cycle': {'day': '12', 'length': '15'},
        'reset-usage': {'user': 'fixture', 'user_revision': revision},
        'refresh-usage': {'user': 'fixture', 'user_revision': revision},
        'reset-usage-all': {},
        'pause-user': {'user': 'fixture', 'user_revision': revision, 'minutes': '30'},
        'toggle-user': {
            'user': 'fixture',
            'user_revision': revision,
            'desired': 'disabled',
        },
    }
    return forms[action]


@pytest.mark.parametrize('action', ACTION_PATHS)
def test_each_operation_uses_real_state_and_returns_only_the_minimal_success(
    operation_api_state,
    action,
):
    state = operation_api_state

    response = state.client.post(
        ACTION_PATHS[action],
        headers=_admin_headers(state),
        data=_form_for(state, action),
    )

    expected = {
        'ok': True,
        'action': action,
        'user': 'fixture'
        if action in {'reset-usage', 'refresh-usage', 'pause-user', 'toggle-user'}
        else '',
        'day': 12 if action == 'cycle' else None,
        'disabled_until': ('2026-09-14T11:00:45+08:00' if action == 'pause-user' else ''),
    }
    assert response.status_code == 200
    assert response.json() == expected
    assert 'private-subscription-token' not in response.text
    if action == 'cycle':
        assert json.loads(state.paths['META_FILE'].read_text())['settlement_day'] == 12
    elif action in {'reset-usage', 'refresh-usage', 'reset-usage-all'}:
        assert (
            json.loads(state.paths['USAGE_DAILY_FILE'].read_text())['2026-09-14']['fixture'][
                'total'
            ]
            == 0
        )
    else:
        assert json.loads(state.paths['USERS_FILE'].read_text())['fixture']['disabled'] is True
    assert {name: response.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


@pytest.mark.parametrize('action', ACTION_PATHS)
@pytest.mark.parametrize('credential', ['anonymous', 'stale', 'user', 'query'])
def test_operations_require_existing_admin_cookie_before_writes(
    operation_api_state,
    action,
    credential,
):
    state = operation_api_state
    cookie = ''
    query = ''
    if credential == 'stale':
        cookie = 'sid=stale'
    elif credential == 'user':
        cookie = f'usid={state.user_sid}'
    elif credential == 'query':
        query = '?token=query-token-must-not-authorize'
    before = {name: path.read_bytes() for name, path in state.paths.items() if path.exists()}

    response = state.client.post(
        ACTION_PATHS[action] + query,
        headers={'Cookie': cookie, 'Origin': 'http://testserver'},
        data=_form_for(state, action),
    )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
    assert {
        name: path.read_bytes() for name, path in state.paths.items() if name in before
    } == before


@pytest.mark.parametrize(
    ('action', 'form', 'status', 'payload'),
    [
        (
            'cycle',
            {'day': '29'},
            422,
            {'ok': False, 'error': 'validation_error', 'code': 'err:settlement_invalid'},
        ),
        (
            'cycle',
            {'day': '1', 'length': '91'},
            422,
            {'ok': False, 'error': 'validation_error', 'code': 'err:cycle_length_invalid'},
        ),
        (
            'toggle-user',
            {'user': 'fixture', 'desired': 'banana'},
            422,
            {'ok': False, 'error': 'validation_error', 'code': 'invalid_desired'},
        ),
        ('reset-usage', {'user': 'missing'}, 404, {'ok': False, 'error': 'user_not_found'}),
        (
            'pause-user',
            {'user': 'fixture', 'user_revision': 'stale'},
            409,
            {'ok': False, 'error': 'revision_conflict'},
        ),
    ],
)
def test_operation_rejections_have_exact_public_shapes(
    operation_api_state,
    action,
    form,
    status,
    payload,
):
    state = operation_api_state
    before_users = state.paths['USERS_FILE'].read_bytes()
    before_usage = state.paths['USAGE_FILE'].read_bytes()

    response = state.client.post(ACTION_PATHS[action], headers=_admin_headers(state), data=form)

    assert response.status_code == status
    assert response.json() == payload
    assert state.paths['USERS_FILE'].read_bytes() == before_users
    assert state.paths['USAGE_FILE'].read_bytes() == before_usage


@pytest.mark.parametrize('action', ['reset-usage', 'refresh-usage', 'pause-user', 'toggle-user'])
def test_empty_per_user_target_preserves_not_found_outcome(operation_api_state, action):
    state = operation_api_state
    form = {'desired': 'disabled'} if action == 'toggle-user' else {}

    response = state.client.post(ACTION_PATHS[action], headers=_admin_headers(state), data=form)

    assert response.status_code == 404
    assert response.json() == {'ok': False, 'error': 'user_not_found'}


@pytest.mark.parametrize('action', ACTION_PATHS)
def test_operation_routes_are_post_only_and_exact(operation_api_state, action):
    state = operation_api_state
    path = ACTION_PATHS[action]

    get_response = state.client.get(path)
    head_response = state.client.head(path)
    trailing = state.client.post(path + '/', data={})
    unknown = state.client.post(path + '-unknown', data={})

    assert get_response.status_code == 405
    assert get_response.json() == {'error': 'method_not_allowed'}
    assert head_response.status_code == 405 and head_response.content == b''
    assert trailing.status_code == unknown.status_code == 404
    assert trailing.json() == unknown.json() == {'error': 'not_found'}


def test_reload_status_is_authenticated_bool_only_read_and_head(operation_api_state):
    state = operation_api_state
    marker = ss.xray_config._reload_pending_path(state.xray_config)
    before = marker.read_bytes()

    denied = state.client.get('/api/v1/admin/reload-status')
    response = state.client.get('/api/v1/admin/reload-status', headers=_admin_headers(state))
    head = state.client.head('/api/v1/admin/reload-status', headers=_admin_headers(state))

    assert denied.status_code == 401 and denied.json() == {'error': 'login_required'}
    assert response.status_code == 200
    assert response.json() == {'pending': True, 'xray': True, 'tuic': False}
    assert all(type(value) is bool for value in response.json().values())
    assert head.status_code == 200 and head.content == b''
    assert marker.read_bytes() == before
    assert not ss.tuic_config._reload_pending_path(state.tuic_config).exists()
    assert {name: head.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


class RecordingOperationServices:
    def __init__(self, *, result=None, reload_status=None, error=None):
        self.calls = []
        self.result = result or OverviewMutationResult(outcome='success', username='fixture')
        self.reload_status = reload_status or {'pending': False, 'xray': False, 'tuic': False}
        self.error = error

    def submit_overview_operation(self, **kwargs):
        self.calls.append(('write', kwargs))
        if self.error is not None:
            raise self.error
        return self.result

    def read_admin_reload_status(self, **kwargs):
        self.calls.append(('read', kwargs))
        if self.error is not None:
            raise self.error
        return self.reload_status


def _form_headers(body, *extra):
    return [
        (b'host', b'panel.test'),
        (b'origin', b'http://panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
        (b'content-length', str(len(body)).encode('ascii')),
        *extra,
    ]


async def _asgi_exchange(
    app,
    *,
    method='POST',
    path=ACTION_PATHS['reset-usage'],
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


def _response_json(messages):
    body = b''.join(
        message.get('body', b'') for message in messages if message['type'] == 'http.response.body'
    )
    return json.loads(body)


def test_valid_operation_form_dispatches_first_values_and_fixed_action():
    services = RecordingOperationServices()
    body = urlencode(
        [
            ('user', 'fixture'),
            ('user_revision', 'first-revision'),
            ('user_revision', 'second-revision'),
            ('desired', 'disabled'),
            ('desired', 'enabled'),
        ]
    ).encode()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=ACTION_PATHS['toggle-user'],
        headers=_form_headers(body),
        events=[{'type': 'http.request', 'body': body, 'more_body': False}],
        client=('203.0.113.9', 9876),
    )

    assert _response_status(messages) == 200
    assert services.calls == [
        (
            'write',
            {
                'headers': services.calls[0][1]['headers'],
                'path': ACTION_PATHS['toggle-user'],
                'form': {
                    'user': ['fixture'],
                    'user_revision': ['first-revision', 'second-revision'],
                    'desired': ['disabled', 'enabled'],
                },
                'client_address': ('203.0.113.9', 9876),
                'action': 'toggle-user',
            },
        )
    ]


@pytest.mark.parametrize(
    ('headers', 'events', 'status', 'error'),
    [
        (
            [(b'origin', b'https://attacker.test'), (b'content-length', b'3')],
            None,
            403,
            'cross_site_request',
        ),
        (
            [(b'content-length', b'3'), (b'content-length', b'3')],
            None,
            400,
            'bad_request',
        ),
        ([(b'content-length', str(256 * 1024 + 1).encode())], None, 413, 'request_too_large'),
        (
            [(b'content-length', b'4')],
            [{'type': 'http.request', 'body': b'a=1', 'more_body': False}],
            400,
            'bad_request',
        ),
    ],
)
def test_rejected_operation_forms_never_dispatch(headers, events, status, error):
    services = RecordingOperationServices()
    all_headers = [
        (b'host', b'panel.test'),
        (b'content-type', b'application/x-www-form-urlencoded'),
    ]
    all_headers.extend(headers)
    if events is None:

        async def receive():
            raise AssertionError('rejected input must not be received')

    else:
        receive = None
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=all_headers,
        events=events,
        receive=receive,
    )

    assert _response_status(messages) == status
    assert _response_json(messages) == {'error': error}
    assert services.calls == []


def test_operation_form_timeout_is_408_without_service_dispatch(monkeypatch):
    import web_api.requests as requests_module

    monkeypatch.setattr(requests_module, 'FORM_READ_TIMEOUT', 0.01)
    services = RecordingOperationServices()

    async def never_receive():
        await asyncio.Event().wait()

    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(b'a=1'),
        receive=never_receive,
    )

    assert _response_status(messages) == 408
    assert _response_json(messages) == {'error': 'request_timeout'}
    assert services.calls == []


@pytest.mark.parametrize(
    ('action', 'result'),
    [
        ('cycle', OverviewMutationResult(outcome='success', day=None)),
        ('cycle', OverviewMutationResult(outcome='success', day=12, username='fixture')),
        ('reset-usage', OverviewMutationResult(outcome='success')),
        ('reset-usage', OverviewMutationResult(outcome='success', username=0)),
        ('reset-usage', OverviewMutationResult(outcome='success', username='fixture', code=None)),
        ('pause-user', OverviewMutationResult(outcome='success', username='fixture')),
        (
            'toggle-user',
            OverviewMutationResult(outcome='success', username='fixture', disabled_until='secret'),
        ),
        ('reset-usage', OverviewMutationResult(outcome='invalid', code='invalid_desired')),
        ('cycle', OverviewMutationResult(outcome='invalid', code='unknown')),
        ('reset-usage', OverviewMutationResult(outcome='conflict', username='')),
        ('reset-usage', OverviewMutationResult(outcome='unknown')),
    ],
)
def test_malformed_internal_operation_results_are_sanitized_500(action, result):
    services = RecordingOperationServices(result=result)
    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=ACTION_PATHS[action],
        headers=_form_headers(b''),
    )

    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert 'secret' not in json.dumps(_response_json(messages))
    assert len(services.calls) == 1


@pytest.mark.parametrize(
    'payload',
    [
        {'pending': 0, 'xray': False, 'tuic': False},
        {'pending': False, 'xray': False},
        {'pending': False, 'xray': False, 'tuic': False, 'secret': True},
    ],
)
def test_invalid_reload_status_payload_is_sanitized_500(payload):
    services = RecordingOperationServices(reload_status=payload)
    messages = _run_exchange(
        create_app(services, max_requests=1),
        method='GET',
        path='/api/v1/admin/reload-status',
    )

    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert len(services.calls) == 1


def test_operation_service_adapter_is_metadata_auth_first_query_free_and_single_call():
    events = []

    class TrafficService:
        def __init__(self, audit):
            self.audit = audit

        def reset_user(self, *, form, expected_revision):
            events.append(('reset', form, expected_revision))
            self.audit('reset_usage_user', 'fixture', {'total': 1}, {'total': 0})
            return OverviewMutationResult(outcome='success', username='fixture')

    class ServiceModule:
        state_store = ss.state_store
        request_multiplier_snapshot = staticmethod(lambda function: function)

        @staticmethod
        def load_meta():
            events.append('metadata')

        @staticmethod
        def is_logged_in(request):
            events.append(('auth', request.path, request.client_address))
            return True

        @staticmethod
        def _traffic_mutation_service(audit):
            events.append(('factory', audit))
            return TrafficService(audit)

        @staticmethod
        def _admin_actor(request):
            events.append(('actor', request.path))
            return 'operator'

        @staticmethod
        def _write_reset_log(*args):
            events.append(('audit', args))

    form = {'user': ['fixture'], 'user_revision': ['first', 'second']}
    result = LegacyPanelServices(ServiceModule()).submit_overview_operation(
        headers={'Cookie': 'sid=fixture'},
        path=ACTION_PATHS['reset-usage'] + '?token=query-token',
        form=form,
        client_address=('192.0.2.8', 1234),
        action='reset-usage',
    )

    assert result == OverviewMutationResult(outcome='success', username='fixture')
    assert events[:7] == [
        'metadata',
        ('auth', ACTION_PATHS['reset-usage'], ('192.0.2.8', 1234)),
        ('factory', events[2][1]),
        ('reset', form, 'first'),
        ('actor', ACTION_PATHS['reset-usage']),
        (
            'audit',
            (
                events[5][1][0],
                'operator',
                'reset_usage_user',
                'fixture',
                {'total': 1},
                {'total': 0},
            ),
        ),
    ]
    assert [event for event in events if isinstance(event, tuple) and event[0] == 'reset'] == [
        ('reset', form, 'first')
    ]


def test_operation_adapter_rejects_login_before_constructing_domain_service():
    events = []

    class ServiceModule:
        state_store = ss.state_store
        request_multiplier_snapshot = staticmethod(lambda function: function)

        @staticmethod
        def load_meta():
            events.append('metadata')

        @staticmethod
        def is_logged_in(_request):
            events.append('auth')
            return False

        @staticmethod
        def _traffic_mutation_service(_audit):
            raise AssertionError('unauthenticated operation must not construct service')

    with pytest.raises(LoginRequired):
        LegacyPanelServices(ServiceModule()).submit_overview_operation(
            headers={},
            path=ACTION_PATHS['cycle'],
            form={},
            client_address=('', 0),
            action='cycle',
        )
    assert events == ['metadata', 'auth']


def test_reload_status_adapter_authenticates_inside_read_boundary_before_helper():
    events = []

    class ServiceModule:
        state_store = ss.state_store
        request_multiplier_snapshot = staticmethod(lambda function: function)

        @staticmethod
        def is_logged_in(request):
            events.append(('auth', request.path))
            return True

        @staticmethod
        def _static_reload_status():
            events.append('status')
            return {'pending': False, 'xray': False, 'tuic': False}

    result = LegacyPanelServices(ServiceModule()).read_admin_reload_status(
        headers={'Cookie': 'sid=fixture'},
        path='/api/v1/admin/reload-status?token=ignored',
    )

    assert result == {'pending': False, 'xray': False, 'tuic': False}
    assert events == [('auth', '/api/v1/admin/reload-status'), 'status']


@pytest.mark.parametrize('action', ['delete', '', 'reset_usage'])
def test_operation_adapter_rejects_unknown_actions_before_dependency_access(action):
    class UntouchedModule:
        def __getattribute__(self, name):
            if not name.startswith('__'):
                raise AssertionError(f'unexpected dependency access: {name}')
            return super().__getattribute__(name)

    with pytest.raises(ValueError, match='invalid overview operation action'):
        LegacyPanelServices(UntouchedModule()).submit_overview_operation(
            headers={}, path='/internal', form={}, client_address=('', 0), action=action
        )


def test_operation_success_returns_without_post_commit_read_or_retry():
    services = RecordingOperationServices()
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(b'user=fixture'),
        events=[{'type': 'http.request', 'body': b'user=fixture', 'more_body': False}],
    )

    assert _response_status(messages) == 200
    assert len(services.calls) == 1 and services.calls[0][0] == 'write'


@pytest.mark.parametrize(
    ('error', 'status', 'payload'),
    [
        (RuntimeError('private /path token'), 500, {'error': 'internal_error'}),
        (LoginRequired(), 401, {'error': 'login_required'}),
    ],
)
def test_operation_route_sanitizes_service_errors_without_retry(error, status, payload):
    services = RecordingOperationServices(error=error)
    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=_form_headers(b''),
    )

    assert _response_status(messages) == status
    assert _response_json(messages) == payload
    assert 'private' not in json.dumps(_response_json(messages))
    assert len(services.calls) == 1


@pytest.mark.parametrize(
    ('action', 'legacy_path'),
    [
        ('cycle', '/admin/cycle-config'),
        ('reset-usage', '/admin/reset-usage'),
        ('refresh-usage', '/admin/refresh-usage'),
        ('reset-usage-all', '/admin/reset-usage-all'),
        ('pause-user', '/admin/pause-user'),
        ('toggle-user', '/admin/toggle-user'),
    ],
)
def test_malformed_metadata_is_503_and_classified_with_exact_legacy_path(
    operation_api_state,
    monkeypatch,
    action,
    legacy_path,
):
    state = operation_api_state
    state.paths['META_FILE'].write_text('{broken', encoding='utf-8')
    classified = []

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    response = state.client.post(ACTION_PATHS[action], data={})

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert len(classified) == 1
    assert issubclass(classified[0][0], ss.state_store.StateStoreError)
    assert classified[0][1] == legacy_path
    assert 'broken' not in response.text


def test_reload_status_state_failure_is_sanitized_503(operation_api_state):
    state = operation_api_state
    state.paths['META_FILE'].write_text('{broken', encoding='utf-8')

    response = state.client.get('/api/v1/admin/reload-status')

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}


def test_operation_and_reload_read_share_the_same_capacity_bound():
    class BlockingServices(RecordingOperationServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_overview_operation(self, **kwargs):
            self.calls.append(('write', kwargs))
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test gate timed out')
            return self.result

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        pending = asyncio.create_task(_asgi_exchange(app, headers=_form_headers(b'')))
        assert await asyncio.to_thread(services.entered.wait, 1)
        busy = await _asgi_exchange(
            app,
            method='GET',
            path='/api/v1/admin/reload-status',
        )
        services.release.set()
        await pending
        recovered = await _asgi_exchange(
            app,
            method='GET',
            path='/api/v1/admin/reload-status',
        )
        return services, busy, recovered

    services, busy, recovered = asyncio.run(scenario())
    assert _response_status(busy) == 503
    assert _response_json(busy) == {'error': 'server_busy'}
    assert _response_status(recovered) == 200
    assert [kind for kind, _kwargs in services.calls] == ['write', 'read']
