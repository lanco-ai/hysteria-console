"""Bounded administrator account-mutation JSON transport tests."""

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
import subscription_service as ss
from account_mutation_service import AccountMutationResult
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices, LoginRequired

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


def _create_form(**overrides):
    form = {
        'user': 'fixture_new',
        'panel_password': 'fixture-panel-password',
        'password': 'fixture-proxy-password',
        'quota_gb': '10',
        'quota_extra_gb': '2',
        'expires_at': '2027-04-05',
        'note': 'fixture note',
        'guest': 'on',
        'tuic_enabled': 'on',
    }
    form.update(overrides)
    return form


def _update_form(revision, **overrides):
    form = {
        'user': 'alice',
        'user_revision': revision,
        'max_devices': '5',
        'quota_gb': '8',
        'quota_extra_gb': '3',
        'expires_at': '',
        'note': 'updated note',
        'landing_isp': '',
        'landing_region': 'new region',
        'landing_note': '',
        'landing_ip': '2001:db8::1',
        'panel_password': 'new-panel-password',
        'password': 'new-proxy-password',
        'guest': 'on',
    }
    form.update(overrides)
    return form


@pytest.fixture
def account_api_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
        'USAGE_FILE': tmp_path / 'usage.json',
        'USAGE_DAILY_FILE': tmp_path / 'usage_daily.json',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)

    admin_hash = ss.hash_secret('fixture-admin-password')
    alice_panel_hash = ss.hash_secret('old-panel-password')
    alice_proxy_hash = ss.hash_secret('old-proxy-password')
    alice = {
        'sub_token': 'alice-subscription-secret',
        'vless_uuid': 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
        'panel_pass_hash': alice_panel_hash,
        'password_hash': alice_proxy_hash,
        'monthly_quota_bytes': 1024,
        'quota_extra_bytes': 2048,
        'max_devices': 2,
        'used_bytes': 777,
        'disabled': True,
        'unrelated': {'keep': True},
    }
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': admin_hash,
            'admin_token': 'query-token-must-not-authorize',
        },
    )
    _write_json(paths['USERS_FILE'], {'alice': alice})
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    _write_json(paths['USAGE_FILE'], {'alice': {'tx': 12, 'rx': 34}})
    _write_json(paths['USAGE_DAILY_FILE'], {'2026-09-14': {'alice': {'total': 46}}})

    effects = []

    def sync(users, **_kwargs):
        effects.append(('sync', sorted(users)))
        return True, True

    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss, '_landing_registry_or_empty', lambda: {'nodes': {}})
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: effects.append('xray'))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: effects.append('tuic'))
    admin_sid = ss.create_session('admin', ss._credential_generation(admin_hash))
    user_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(alice_panel_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    with TestClient(create_app(LegacyPanelServices(ss), max_requests=2)) as client:
        yield SimpleNamespace(
            client=client,
            paths=paths,
            effects=effects,
            admin_sid=admin_sid,
            user_sid=user_sid,
            alice=alice,
            alice_panel_hash=alice_panel_hash,
            alice_proxy_hash=alice_proxy_hash,
        )


def _admin_headers(state):
    return {'Cookie': f'sid={state.admin_sid}', 'Origin': 'http://testserver'}


def _assert_security_headers(response):
    assert {name: response.headers[name] for name in SECURITY_HEADERS} == SECURITY_HEADERS


def test_create_account_uses_real_service_and_returns_only_public_identity(account_api_state):
    state = account_api_state
    obsolete = ss.create_user_session(
        'fixture_new',
        'obsolete-generation',
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    usage_before = {
        name: state.paths[name].read_bytes() for name in ('USAGE_FILE', 'USAGE_DAILY_FILE')
    }

    response = state.client.post(
        '/api/v1/admin/users/create',
        headers=_admin_headers(state),
        data=_create_form(),
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'outcome': 'created', 'user': 'fixture_new'}
    users = json.loads(state.paths['USERS_FILE'].read_text(encoding='utf-8'))
    created = users['fixture_new']
    assert ss.verify_secret('fixture-panel-password', created['panel_pass_hash'])
    assert ss.verify_secret('fixture-proxy-password', created['password_hash'])
    assert created['monthly_quota_bytes'] == 10 * 1024**3
    assert created['quota_extra_bytes'] == 2 * 1024**3
    assert created['sub_token'] and created['vless_uuid']
    assert users['alice'] == state.alice
    assert obsolete not in ss.get_user_sessions()
    assert state.user_sid in ss.get_user_sessions()
    assert state.effects == [('sync', ['alice', 'fixture_new']), 'xray', 'tuic']
    assert {name: state.paths[name].read_bytes() for name in usage_before} == usage_before
    for secret in (
        'fixture-panel-password',
        'fixture-proxy-password',
        created['panel_pass_hash'],
        created['password_hash'],
        created['sub_token'],
        created['vless_uuid'],
    ):
        assert secret not in response.text
    _assert_security_headers(response)


def test_update_account_uses_first_values_and_preserves_credentials_and_usage(account_api_state):
    state = account_api_state
    revision = ss.user_config_revision(state.alice)
    second_user_session = ss.create_user_session(
        'alice',
        ss._credential_generation(state.alice_panel_hash),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    body = urlencode(
        [
            ('user', 'alice'),
            ('user', 'missing'),
            ('user_revision', revision),
            ('user_revision', '0' * 64),
            ('max_devices', '5'),
            ('max_devices', '101'),
            ('quota_gb', '8'),
            ('quota_extra_gb', '3'),
            ('expires_at', ''),
            ('note', 'updated note'),
            ('landing_isp', ''),
            ('landing_region', 'new region'),
            ('landing_note', ''),
            ('landing_ip', '2001:db8::1'),
            ('panel_password', 'new-panel-password'),
            ('password', 'new-proxy-password'),
            ('guest', ''),
        ]
    )
    usage_before = {
        name: state.paths[name].read_bytes() for name in ('USAGE_FILE', 'USAGE_DAILY_FILE')
    }

    response = state.client.post(
        '/api/v1/admin/users/update',
        headers={
            **_admin_headers(state),
            'Content-Type': 'application/x-www-form-urlencoded',
        },
        content=body,
    )

    assert response.status_code == 200
    assert response.json() == {'ok': True, 'outcome': 'updated', 'user': 'alice'}
    saved = json.loads(state.paths['USERS_FILE'].read_text(encoding='utf-8'))['alice']
    assert saved['sub_token'] == state.alice['sub_token']
    assert saved['vless_uuid'] == state.alice['vless_uuid']
    assert saved['used_bytes'] == 777
    assert saved['disabled'] is True
    assert saved['unrelated'] == {'keep': True}
    assert saved['max_devices'] == 5
    assert saved['monthly_quota_bytes'] == 8 * 1024**3
    assert saved['quota_extra_bytes'] == 3 * 1024**3
    assert saved['note'] == 'updated note'
    assert ss.verify_secret('new-panel-password', saved['panel_pass_hash'])
    assert ss.verify_secret('new-proxy-password', saved['password_hash'])
    assert state.user_sid not in ss.get_user_sessions()
    assert second_user_session not in ss.get_user_sessions()
    assert state.effects == [('sync', ['alice']), 'xray', 'tuic']
    assert {name: state.paths[name].read_bytes() for name in usage_before} == usage_before
    for secret in (
        'new-panel-password',
        'new-proxy-password',
        saved['panel_pass_hash'],
        saved['password_hash'],
        saved['sub_token'],
        saved['vless_uuid'],
    ):
        assert secret not in response.text


@pytest.mark.parametrize(
    ('path', 'form', 'status', 'payload'),
    [
        (
            '/api/v1/admin/users/create',
            _create_form(user='bad user'),
            422,
            {
                'ok': False,
                'error': 'validation_error',
                'code': 'err:username_invalid',
                'field_id': 'create-user',
            },
        ),
        (
            '/api/v1/admin/users/create',
            _create_form(user='alice'),
            422,
            {
                'ok': False,
                'error': 'validation_error',
                'code': 'user_exists_use_reset_token',
                'field_id': 'create-user',
            },
        ),
        (
            '/api/v1/admin/users/update',
            _update_form('0' * 64),
            409,
            {'ok': False, 'error': 'revision_conflict'},
        ),
        (
            '/api/v1/admin/users/update',
            _update_form('0' * 64, user='missing'),
            404,
            {'ok': False, 'error': 'user_not_found'},
        ),
        (
            '/api/v1/admin/users/update',
            _update_form('0' * 64, max_devices='101'),
            422,
            {
                'ok': False,
                'error': 'validation_error',
                'code': 'err:max_devices_invalid',
                'field_id': '',
            },
        ),
    ],
)
def test_rejected_mutations_have_exact_status_and_never_return_submitted_secrets(
    account_api_state,
    path,
    form,
    status,
    payload,
):
    state = account_api_state
    form = {**form, 'panel_password': 'private-panel', 'password': 'private-proxy'}
    before = state.paths['USERS_FILE'].read_bytes()

    response = state.client.post(path, headers=_admin_headers(state), data=form)

    assert response.status_code == status
    assert response.json() == payload
    assert 'private-panel' not in response.text
    assert 'private-proxy' not in response.text
    assert 'alice-subscription-secret' not in response.text
    assert state.paths['USERS_FILE'].read_bytes() == before


def test_unavailable_egress_validation_code_is_preserved_but_draft_is_not_returned(
    account_api_state,
    monkeypatch,
):
    state = account_api_state
    monkeypatch.setattr(ss, '_landing_registry_or_empty', lambda: {'nodes': {}})

    response = state.client.post(
        '/api/v1/admin/users/create',
        headers=_admin_headers(state),
        data=_create_form(
            landing_initial_egress_id='vanished',
            panel_password='draft-panel-secret',
        ),
    )

    assert response.status_code == 422
    assert response.json() == {
        'ok': False,
        'error': 'validation_error',
        'code': '家宽出口已不可用，请重新选择',
        'field_id': 'create-landing-initial-egress',
    }
    assert 'draft' not in response.text
    assert 'draft-panel-secret' not in response.text


@pytest.mark.parametrize(
    ('cookie', 'query'),
    [
        ('', ''),
        ('sid=stale', ''),
        ('user', ''),
        ('', '?token=query-token-must-not-authorize'),
    ],
)
@pytest.mark.parametrize('action', ['create', 'update'])
def test_only_an_existing_admin_cookie_can_mutate_accounts(
    account_api_state,
    cookie,
    query,
    action,
):
    state = account_api_state
    if cookie == 'user':
        cookie = f'usid={state.user_sid}'
    before = state.paths['USERS_FILE'].read_bytes()

    response = state.client.post(
        f'/api/v1/admin/users/{action}{query}',
        headers={'Cookie': cookie},
        data=_create_form() if action == 'create' else _update_form('0' * 64),
    )

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
    assert state.paths['USERS_FILE'].read_bytes() == before


def test_update_revision_must_come_from_form_not_legacy_query_fallback(account_api_state):
    state = account_api_state
    revision = ss.user_config_revision(state.alice)

    response = state.client.post(
        f'/api/v1/admin/users/update?revision={revision}',
        headers=_admin_headers(state),
        data=_update_form('', user_revision=''),
    )

    assert response.status_code == 409
    assert response.json() == {'ok': False, 'error': 'revision_conflict'}


@pytest.mark.parametrize('path', ['/api/v1/admin/users/create', '/api/v1/admin/users/update'])
def test_account_routes_are_post_only_and_exact(account_api_state, path):
    state = account_api_state
    before = state.paths['USERS_FILE'].read_bytes()

    get_response = state.client.get(path)
    head_response = state.client.head(path)
    trailing = state.client.post(f'{path}/', data={})
    unknown = state.client.post(f'{path}-unknown', data={})

    assert get_response.status_code == 405
    assert get_response.json() == {'error': 'method_not_allowed'}
    assert head_response.status_code == 405
    assert head_response.content == b''
    assert trailing.status_code == 404
    assert trailing.json() == {'error': 'not_found'}
    assert unknown.status_code == 404
    assert unknown.json() == {'error': 'not_found'}
    assert state.paths['USERS_FILE'].read_bytes() == before
    _assert_security_headers(head_response)


class RecordingAccountServices:
    def __init__(self, *, result=None, error=None):
        self.calls = []
        self.result = result or AccountMutationResult(outcome='created', username='fixture')
        self.error = error

    def submit_account_mutation(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


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
    path='/api/v1/admin/users/create',
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


@pytest.mark.parametrize('path', ['/api/v1/admin/users/create', '/api/v1/admin/users/update'])
def test_cross_site_account_forms_are_rejected_before_body_receipt(path):
    services = RecordingAccountServices()

    async def forbidden_receive():
        raise AssertionError('cross-site input must not be received')

    messages = _run_exchange(
        create_app(services, max_requests=1),
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


@pytest.mark.parametrize(
    ('headers', 'events', 'status', 'error'),
    [
        ([(b'content-length', b'3'), (b'content-length', b'3')], None, 400, 'bad_request'),
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
def test_malformed_account_forms_never_dispatch(headers, events, status, error):
    services = RecordingAccountServices()
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

    messages = _run_exchange(
        create_app(services, max_requests=1),
        headers=all_headers,
        events=events,
        receive=receive,
    )

    assert _response_status(messages) == status
    assert _response_json(messages) == {'error': error}
    assert services.calls == []


def test_account_form_timeout_is_408_without_service_dispatch(monkeypatch):
    import web_api.requests as requests_module

    monkeypatch.setattr(requests_module, 'FORM_READ_TIMEOUT', 0.01)
    services = RecordingAccountServices()

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
    ('path', 'action'),
    [
        ('/api/v1/admin/users/create', 'create'),
        ('/api/v1/admin/users/update', 'update'),
    ],
)
def test_valid_forms_dispatch_fixed_action_path_first_values_and_peer(path, action):
    services = RecordingAccountServices(
        result=AccountMutationResult(outcome=f'{action}d', username='first')
    )
    body = b'user=first&user=second&user_revision=revision-one&user_revision=revision-two'

    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=path,
        headers=_form_headers(body),
        events=[{'type': 'http.request', 'body': body, 'more_body': False}],
        client=('203.0.113.9', 9876),
    )

    assert _response_status(messages) == 200
    assert services.calls == [
        {
            'headers': services.calls[0]['headers'],
            'path': path,
            'form': {
                'user': ['first', 'second'],
                'user_revision': ['revision-one', 'revision-two'],
            },
            'client_address': ('203.0.113.9', 9876),
            'action': action,
        }
    ]


def test_account_service_adapter_loads_metadata_then_admin_auth_and_calls_service_once():
    events = []

    class AccountService:
        @staticmethod
        def update(*, form, expected_revision):
            events.append(('update', form, expected_revision))
            return AccountMutationResult(outcome='updated', username='alice')

    class ServiceModule:
        state_store = ss.state_store
        request_multiplier_snapshot = staticmethod(lambda function: function)

        @staticmethod
        def load_meta():
            events.append('metadata')
            return {'complete': True}

        @staticmethod
        def is_logged_in(request):
            events.append(('admin_auth', request.path, request.client_address))
            return True

        @staticmethod
        def _account_mutation_service():
            events.append('service')
            return AccountService()

    form = {'user': ['alice'], 'user_revision': ['first', 'second']}
    result = LegacyPanelServices(ServiceModule()).submit_account_mutation(
        headers={'Cookie': 'sid=fixture'},
        path='/api/v1/admin/users/update?revision=query-fallback',
        form=form,
        client_address=('192.0.2.8', 1234),
        action='update',
    )

    assert result == AccountMutationResult(outcome='updated', username='alice')
    assert events == [
        'metadata',
        ('admin_auth', '/api/v1/admin/users/update', ('192.0.2.8', 1234)),
        'service',
        ('update', form, 'first'),
    ]


def test_account_service_adapter_rejects_invalid_action_before_touching_dependencies():
    class UntouchedModule:
        def __getattribute__(self, name):
            if not name.startswith('__'):
                raise AssertionError(f'unexpected dependency access: {name}')
            return super().__getattribute__(name)

    with pytest.raises(ValueError, match='invalid account mutation action'):
        LegacyPanelServices(UntouchedModule()).submit_account_mutation(
            headers={},
            path='/internal',
            form={},
            client_address=('', 0),
            action='delete',
        )


def test_account_service_adapter_does_not_construct_service_when_login_is_required():
    events = []

    class ServiceModule:
        state_store = ss.state_store
        request_multiplier_snapshot = staticmethod(lambda function: function)

        @staticmethod
        def load_meta():
            events.append('metadata')

        @staticmethod
        def is_logged_in(_request):
            events.append('admin_auth')
            return False

        @staticmethod
        def _account_mutation_service():
            raise AssertionError('unauthenticated request must not construct account service')

    with pytest.raises(LoginRequired):
        LegacyPanelServices(ServiceModule()).submit_account_mutation(
            headers={},
            path='/api/v1/admin/users/create',
            form={},
            client_address=('', 0),
            action='create',
        )
    assert events == ['metadata', 'admin_auth']


@pytest.mark.parametrize(
    ('action', 'result'),
    [
        ('create', AccountMutationResult(outcome='updated', username='alice')),
        ('update', AccountMutationResult(outcome='created', username='alice')),
        ('create', AccountMutationResult(outcome='created')),
        ('create', AccountMutationResult(outcome='created', username=123)),
        ('create', AccountMutationResult(outcome='created', username='alice', code='secret')),
        ('create', AccountMutationResult(outcome='created', username='alice', field_id='secret')),
        ('create', AccountMutationResult(outcome='created', username='alice', draft={})),
        ('create', AccountMutationResult(outcome='invalid', code='unexpected', field_id='')),
        (
            'create',
            AccountMutationResult(
                outcome='invalid',
                code='err:quota_invalid',
                field_id='unexpected',
            ),
        ),
        ('create', AccountMutationResult(outcome='unknown')),
    ],
)
def test_malformed_internal_account_results_are_sanitized_500(action, result):
    services = RecordingAccountServices(result=result)
    messages = _run_exchange(
        create_app(services, max_requests=1),
        path=f'/api/v1/admin/users/{action}',
        headers=_form_headers(b''),
    )

    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert 'secret' not in json.dumps(_response_json(messages))
    assert len(services.calls) == 1


def test_valid_validation_result_strips_draft_and_unrecognized_credentials():
    result = AccountMutationResult(
        outcome='invalid',
        username='internal-user-ignored',
        code='err:quota_invalid',
        field_id='',
        draft={
            'password': 'private-password',
            'password_hash': 'private-hash',
            'sub_token': 'private-token',
        },
    )
    messages = _run_exchange(
        create_app(RecordingAccountServices(result=result), max_requests=1),
        path='/api/v1/admin/users/update',
        headers=_form_headers(b''),
    )

    assert _response_status(messages) == 422
    assert _response_json(messages) == {
        'ok': False,
        'error': 'validation_error',
        'code': 'err:quota_invalid',
        'field_id': '',
    }
    for secret in ('internal-user-ignored', 'private-password', 'private-hash', 'private-token'):
        assert secret not in json.dumps(_response_json(messages))


def test_success_returns_without_a_post_commit_read_or_retry():
    services = RecordingAccountServices(
        result=AccountMutationResult(outcome='created', username='fixture')
    )
    messages = _run_exchange(create_app(services, max_requests=1), headers=_form_headers(b''))

    assert _response_status(messages) == 200
    assert _response_json(messages) == {'ok': True, 'outcome': 'created', 'user': 'fixture'}
    assert len(services.calls) == 1


@pytest.mark.parametrize(
    ('error', 'status', 'payload'),
    [
        (RuntimeError('private password hash and /private/path'), 500, {'error': 'internal_error'}),
        (LoginRequired(), 401, {'error': 'login_required'}),
    ],
)
def test_account_route_sanitizes_service_errors_without_retry(error, status, payload):
    services = RecordingAccountServices(error=error)
    messages = _run_exchange(create_app(services, max_requests=1), headers=_form_headers(b''))

    assert _response_status(messages) == status
    assert _response_json(messages) == payload
    assert 'private' not in json.dumps(_response_json(messages))
    assert len(services.calls) == 1


@pytest.mark.parametrize(
    ('path', 'legacy_path'),
    [
        ('/api/v1/admin/users/create', '/admin/add'),
        ('/api/v1/admin/users/update', '/admin/update'),
    ],
)
def test_malformed_metadata_is_503_and_classified_with_legacy_post_path(
    account_api_state,
    monkeypatch,
    path,
    legacy_path,
):
    state = account_api_state
    state.paths['META_FILE'].write_text('{broken', encoding='utf-8')
    classified = []

    def classify(exc, *, post_path=''):
        classified.append((type(exc), post_path))
        return False

    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', classify)
    response = state.client.post(path, data={})

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert len(classified) == 1
    assert issubclass(classified[0][0], ss.state_store.StateStoreError)
    assert classified[0][1] == legacy_path
    assert 'broken' not in response.text


def test_critical_account_state_failure_runs_fail_closed_policy(account_api_state, monkeypatch):
    state = account_api_state
    stopped = []

    class FailingAccountService:
        @staticmethod
        def create(*, form):
            del form
            raise ss.state_store.CriticalStateUnavailable('private critical state path')

    monkeypatch.setattr(ss, '_account_mutation_service', lambda: FailingAccountService())
    monkeypatch.setattr(ss, '_state_failure_requires_static_stop', lambda *_args, **_kwargs: True)
    monkeypatch.setattr(ss, '_fail_closed_static_access', stopped.append)

    response = state.client.post(
        '/api/v1/admin/users/create',
        headers=_admin_headers(state),
        data=_create_form(),
    )

    assert response.status_code == 503
    assert response.json() == {'error': 'state_unavailable'}
    assert len(stopped) == 1
    assert isinstance(stopped[0], ss.state_store.CriticalStateUnavailable)
    assert 'private' not in response.text


def test_cancelled_account_mutation_keeps_shared_capacity_until_worker_finishes():
    class BlockingServices(RecordingAccountServices):
        def __init__(self):
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def submit_account_mutation(self, **kwargs):
            self.calls.append(kwargs)
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError('test gate timed out')
            return self.result

    async def scenario():
        services = BlockingServices()
        app = create_app(services, max_requests=1)
        pending = asyncio.create_task(_asgi_exchange(app, headers=_form_headers(b'')))
        assert await asyncio.to_thread(services.entered.wait, 1)
        busy_before = await _asgi_exchange(
            app,
            path='/api/v1/admin/users/update',
            headers=_form_headers(b''),
        )
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
    assert _response_json(busy_before) == {'error': 'server_busy'}
    assert _response_status(busy_after) == 503
    assert _response_json(busy_after) == {'error': 'server_busy'}
    assert _response_status(recovered) == 200
    assert len(services.calls) == 2
