"""Credential operation API authentication, allowlists, and pending outcomes."""

from types import SimpleNamespace

import pytest
import subscription_service as ss
from admin_credential_service import CredentialMutationResult
from web_api import create_app

from tests.test_admin_credential_services import stopped
from tests.test_web_api_operations import (
    SECURITY_HEADERS,
    RecordingOperationServices,
    _admin_headers,
    _form_headers,
    _response_json,
    _response_status,
    _run_exchange,
)
from tests.test_web_api_operations import operation_api_state as operation_api_state

CODES = [
    ('rotate-token', 'rotated'),
    ('rotate-token', 'err:rotated_pending'),
    ('rotate-token', 'err:rotated_static_pending'),
    ('rotate-token', 'err:rotated_retry'),
    ('delete', 'deleted'),
    ('delete', 'err:deleted_retry'),
]


def path(action):
    return '/api/v1/admin/operations/' + action


@pytest.fixture
def state(operation_api_state, monkeypatch):
    state = operation_api_state
    monkeypatch.setattr(
        ss, 'REVOCATION_QUEUE_FILE', state.paths['USERS_FILE'].parent / 'queue.json'
    )
    monkeypatch.setattr(ss, '_using_live_core_state', lambda: False)
    return state


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
def test_real_mutation_returns_minimal_result_without_post_commit_reads(state, monkeypatch, action):
    old_token = state.user['sub_token']
    forbidden = lambda *_a, **_k: pytest.fail('no post-commit presentation read')
    monkeypatch.setattr(ss, '_static_reload_status', forbidden)
    monkeypatch.setattr(ss, '_build_overview_json_payload', forbidden)
    response = state.client.post(
        path(action),
        headers={**_admin_headers(state), 'Content-Type': 'application/x-www-form-urlencoded'},
        content='user=fixture&user=ignored&user_revision='
        + ss.user_config_revision(state.user)
        + '&user_revision=stale',
    )
    assert response.status_code == 200
    assert response.json() == {
        'ok': True,
        'action': action,
        'user': 'fixture',
        'code': 'rotated' if action == 'rotate-token' else 'deleted',
    }
    users = ss.load_json(ss.USERS_FILE, {})
    if action == 'rotate-token':
        assert users['fixture']['sub_token'] != old_token
        assert users['fixture']['sub_token'] not in response.text
        assert not ss.check_user_token('fixture', old_token)
    else:
        assert 'fixture' not in users
    assert {key: response.headers[key] for key in SECURITY_HEADERS} == SECURITY_HEADERS


@pytest.mark.parametrize(('action', 'code'), CODES)
def test_success_and_pending_codes_are_allowlisted_without_token_or_followup(action, code):
    result = CredentialMutationResult('success', 'fixture', code, 'private-new-token')
    services = RecordingOperationServices(result=result)
    messages = _run_exchange(create_app(services), path=path(action), headers=_form_headers(b''))
    assert _response_status(messages) == 200
    assert _response_json(messages) == {
        'ok': True,
        'action': action,
        'user': 'fixture',
        'code': code,
    }
    assert len(services.calls) == 1 and services.calls[0][0] == 'write'


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
@pytest.mark.parametrize('credential', ['anonymous', 'stale', 'user', 'query'])
def test_new_actions_require_admin_cookie_before_writes(state, action, credential):
    cookies = {'anonymous': '', 'stale': 'sid=stale', 'user': f'usid={state.user_sid}', 'query': ''}
    before = state.paths['USERS_FILE'].read_bytes()
    response = state.client.post(
        path(action) + ('?token=query-token-must-not-authorize' if credential == 'query' else ''),
        headers={'Cookie': cookies[credential], 'Origin': 'http://testserver'},
        data={'user': 'fixture', 'user_revision': ss.user_config_revision(state.user)},
    )
    assert response.status_code == 401 and response.json() == {'error': 'login_required'}
    assert state.paths['USERS_FILE'].read_bytes() == before
    assert not ss.REVOCATION_QUEUE_FILE.exists()


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
@pytest.mark.parametrize(
    ('form', 'status', 'error'),
    [
        ({'user': 'fixture', 'user_revision': 'stale'}, 409, 'revision_conflict'),
        ({'user': 'missing'}, 404, 'user_not_found'),
        ({}, 404, 'user_not_found'),
    ],
)
def test_rejected_credentials_preserve_state(state, action, form, status, error):
    before = state.paths['USERS_FILE'].read_bytes()
    response = state.client.post(path(action), headers=_admin_headers(state), data=form)
    assert response.status_code == status and response.json() == {'ok': False, 'error': error}
    assert state.paths['USERS_FILE'].read_bytes() == before
    assert not ss.REVOCATION_QUEUE_FILE.exists()


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
def test_exact_post_paths_and_shared_origin_size_protection(state, action):
    assert state.client.get(path(action)).status_code == 405
    head = state.client.head(path(action))
    assert head.status_code == 405 and head.content == b''
    assert state.client.post(path(action) + '/').status_code == 404
    assert state.client.post(path(action) + '-unknown').status_code == 404
    cross_site = state.client.post(
        path(action), headers={'Origin': 'https://attacker.test'}, data={}
    )
    assert cross_site.status_code == 403
    large = state.client.post(
        path(action), headers={'Content-Length': str(256 * 1024 + 1)}, content=b''
    )
    assert large.status_code == 413


@pytest.mark.parametrize(
    ('action', 'result'),
    [
        ('rotate-token', CredentialMutationResult('success', 'fixture', 'deleted')),
        ('delete', CredentialMutationResult('success', 'fixture', 'rotated')),
        ('delete', CredentialMutationResult('success', '', 'deleted')),
        ('delete', CredentialMutationResult('success', 7, 'deleted')),
        ('delete', CredentialMutationResult('success', 'fixture', None)),
        ('delete', CredentialMutationResult('unknown', 'fixture')),
        ('delete', CredentialMutationResult('conflict', '')),
        ('delete', CredentialMutationResult('not_found', 'fixture', 'deleted')),
        ('delete', SimpleNamespace(outcome='success', username='fixture', code='deleted')),
        ('reset-usage', CredentialMutationResult('success', 'fixture', 'deleted')),
    ],
)
def test_malformed_results_are_sanitized_without_retry(action, result):
    services = RecordingOperationServices(result=result)
    messages = _run_exchange(create_app(services), path=path(action), headers=_form_headers(b''))
    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert len(services.calls) == 1


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
def test_state_errors_use_legacy_failure_path(state, monkeypatch, action):
    state.paths['META_FILE'].write_text('{broken-private-state')
    calls = []
    monkeypatch.setattr(
        ss, '_state_failure_requires_static_stop', lambda exc, **kw: calls.append(kw) or False
    )
    response = state.client.post(path(action), data={})
    assert response.status_code == 503 and response.json() == {'error': 'state_unavailable'}
    assert calls == [{'post_path': '/admin/' + action}]


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
def test_unexpected_service_errors_are_sanitized_and_not_retried(action):
    services = RecordingOperationServices(error=RuntimeError('private-token /private/path'))
    messages = _run_exchange(create_app(services), path=path(action), headers=_form_headers(b''))
    assert _response_status(messages) == 500
    assert _response_json(messages) == {'error': 'internal_error'}
    assert len(services.calls) == 1


@pytest.mark.parametrize('action', ['rotate-token', 'delete'])
def test_visible_save_uncertainty_is_success_without_api_retry(state, monkeypatch, action):
    real_save = ss.save_json
    writes = []

    def save(file, value):
        real_save(file, value)
        if file == ss.USERS_FILE:
            writes.append(file)
            raise ss.state_store.AtomicReplaceDurabilityUncertain(file)

    monkeypatch.setattr(ss, 'save_json', save)
    monkeypatch.setattr(
        ss,
        '_fail_closed_static_access',
        lambda _error: {service: stopped(service) for service in ss.static_access.SERVICES},
    )
    response = state.client.post(
        path(action),
        headers=_admin_headers(state),
        data={'user': 'fixture', 'user_revision': ss.user_config_revision(state.user)},
    )
    assert response.status_code == 200
    assert response.json() == {
        'ok': True,
        'action': action,
        'user': 'fixture',
        'code': 'err:rotated_pending' if action == 'rotate-token' else 'err:deleted_retry',
    }
    assert writes == [ss.USERS_FILE]
    assert len(ss.load_json(ss.REVOCATION_QUEUE_FILE, {})) == 1


def test_api_rotation_captures_actor_after_auth_and_before_service_mutation(state, monkeypatch):
    events = []
    real_factory = ss._admin_credential_service
    real_actor = ss._admin_actor
    real_save = ss.save_json

    def actor(request):
        assert request.path == path('rotate-token')
        assert ss.is_logged_in(request)
        assert 'save' not in events
        events.append('actor')
        return real_actor(request)

    def save(file, value):
        if file == ss.USERS_FILE:
            assert events == ['actor', 'factory']
            events.append('save')
        return real_save(file, value)

    def factory(audit):
        events.append('factory')
        return real_factory(audit)

    monkeypatch.setattr(ss, '_admin_actor', actor)
    monkeypatch.setattr(ss, 'save_json', save)
    monkeypatch.setattr(ss, '_admin_credential_service', factory)
    response = state.client.post(
        path('rotate-token') + '?token=ignored',
        headers=_admin_headers(state),
        data={'user': 'fixture', 'user_revision': ss.user_config_revision(state.user)},
    )
    assert response.status_code == 200 and events == ['actor', 'factory', 'save']


def test_legacy_rotation_captures_actor_before_write_and_keeps_authorized_links(state, monkeypatch):
    events = []
    payloads = []
    real_save = ss.save_json

    def save(file, value):
        if file == ss.USERS_FILE:
            assert events == ['actor']
            events.append('save')
        return real_save(file, value)

    monkeypatch.setattr(ss, 'save_json', save)
    monkeypatch.setattr(ss, '_build_overview_user', lambda *_a, **_k: {'username': 'fixture'})
    handler = SimpleNamespace(
        path='/admin/rotate-token',
        headers={**_admin_headers(state), 'Accept': 'application/json'},
        get_admin_actor=lambda: events.append('actor') or 'operator',
        write_reset_log=lambda actor, *args: events.append(('audit', actor, args)),
        _send_mutation_json=lambda status, payload: payloads.append((status, payload)),
    )
    import credential_routes

    credential_routes.handle_write(
        handler,
        ss._credential_routes_context(),
        path='/admin/rotate-token',
        form={'user': ['fixture']},
        query={},
        request_user_revision=ss.user_config_revision(state.user),
    )
    assert events == ['actor', 'save', ('audit', 'operator', ('rotate_token', 'fixture', {}, {}))]
    assert payloads[0][0] == 200
    token = ss.load_json(ss.USERS_FILE, {})['fixture']['sub_token']
    assert payloads[0][1]['links']['sub'].endswith('?token=' + token)
