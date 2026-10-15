"""The loopback-only intent endpoint the network service's LancoAgent calls."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from web_api.agent_intent import AgentIntentError, complete_agent_intent
from web_api.agent_intent_routes import MAX_REQUEST_BYTES, register_agent_intent_routes
from web_api.chat_service import ChatSettingsStore

TOKEN = 'k' * 48
PAYLOAD = {
    'message': '让 alice 走 OpenAI 规则包',
    'target_user': 'alice',
    'context': {'packs': ['openai'], 'rules': ['DOMAIN,a.example,DIRECT'], 'global_rules': []},
}


async def dispatch(function, request, *, prepare=None):
    return function(headers={}, path=request.scope['path'])


def app_with(tmp_path, completer, token=TOKEN):
    token_file = tmp_path / 'internal-token'
    if token is not None:
        token_file.write_text(token + '\n', encoding='utf-8')
    app = FastAPI()
    register_agent_intent_routes(
        app,
        dispatch,
        settings_store=ChatSettingsStore(tmp_path / 'chat-settings.json'),
        completer=completer,
        token_file=token_file,
    )
    return app


def post(client, body=PAYLOAD, *, token=TOKEN, headers=None):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    sent = {'Content-Type': 'application/json'}
    if token is not None:
        sent['X-Lancoai-Internal-Token'] = token
    sent.update(headers or {})
    return client.post('/internal/v1/agent-intent', content=raw, headers=sent)


def loopback(app):
    return TestClient(app, client=('127.0.0.1', 40000))


def test_success_returns_the_typed_intent(tmp_path):
    seen = {}

    def completer(settings, **kwargs):
        seen.update(kwargs, settings=settings)
        return {'action': 'apply_pack', 'pack': 'openai', 'rule': '', 'explanation': '应用规则包'}

    with loopback(app_with(tmp_path, completer)) as client:
        response = post(client)
    assert response.status_code == 200
    assert response.json() == {
        'ok': True,
        'intent': {
            'action': 'apply_pack',
            'pack': 'openai',
            'rule': '',
            'explanation': '应用规则包',
        },
    }
    assert seen['message'] == PAYLOAD['message'] and seen['target_user'] == 'alice'
    assert seen['context'] == PAYLOAD['context']
    assert isinstance(seen['settings'], ChatSettingsStore)


def test_only_direct_loopback_callers_see_the_endpoint(tmp_path):
    def completer(settings, **kwargs):
        raise AssertionError('must not run')

    app = app_with(tmp_path, completer)
    with TestClient(app, client=('203.0.113.9', 40000)) as remote:
        assert post(remote).status_code == 404
    with loopback(app) as client:
        for header in ('X-Forwarded-For', 'X-Real-IP', 'Forwarded'):
            assert post(client, headers={header: '203.0.113.9'}).status_code == 404


@pytest.mark.parametrize(
    'token, status, error', [(None, 403, 'forbidden'), ('x' * 48, 403, 'forbidden')]
)
def test_the_shared_token_is_required(tmp_path, token, status, error):
    with loopback(app_with(tmp_path, lambda settings, **kwargs: {})) as client:
        response = post(client, token=token)
    assert (response.status_code, response.json()['error']) == (status, error)


@pytest.mark.parametrize('stored', [None, 'short-token'])
def test_a_missing_or_weak_token_file_fails_closed(tmp_path, stored):
    with loopback(app_with(tmp_path, lambda settings, **kwargs: {}, token=stored)) as client:
        response = post(client, token=stored or TOKEN)
    assert response.status_code == 503
    assert response.json()['error'] == 'internal_token_unavailable'


@pytest.mark.parametrize(
    'body',
    [
        b'not json',
        json.dumps(['list']).encode(),
        json.dumps({**PAYLOAD, 'message': ''}).encode(),
        json.dumps({**PAYLOAD, 'message': 'x' * (16 * 1024 + 1)}).encode(),
        json.dumps({**PAYLOAD, 'target_user': 7}).encode(),
        json.dumps({**PAYLOAD, 'context': {'packs': [1]}}).encode(),
        json.dumps({**PAYLOAD, 'context': {'rules': ['r'] * 1001}}).encode(),
        b'{"message": "' + b'x' * MAX_REQUEST_BYTES + b'"}',
    ],
)
def test_malformed_or_oversized_requests_are_rejected(tmp_path, body):
    def completer(settings, **kwargs):
        raise AssertionError('must not run')

    with loopback(app_with(tmp_path, completer)) as client:
        response = post(client, body)
    assert response.status_code == 400


@pytest.mark.parametrize(
    'code, status',
    [
        ('settings_unavailable', 503),
        ('upstream_unavailable', 502),
        ('invalid_model_response', 502),
        ('unsupported_action', 422),
        ('invalid_rule', 422),
    ],
)
def test_completion_failures_keep_the_agent_error_codes(tmp_path, code, status):
    def completer(settings, **kwargs):
        raise AgentIntentError(code)

    with loopback(app_with(tmp_path, completer)) as client:
        response = post(client)
    assert response.status_code == status
    assert response.json() == {'ok': False, 'error': code}


def test_missing_model_settings_surface_as_settings_unavailable(tmp_path):
    # The real completer with an unreadable settings file never reaches a model.
    (tmp_path / 'chat-settings.json').write_text('{not json', encoding='utf-8')
    with loopback(app_with(tmp_path, complete_agent_intent)) as client:
        response = post(client)
    assert response.status_code == 503
    assert response.json() == {'ok': False, 'error': 'settings_unavailable'}


def test_completion_reads_the_chat_settings_store_and_uses_the_agent_model(tmp_path, monkeypatch):
    import web_api.agent_intent as agent_intent

    settings_path = tmp_path / 'chat-settings.json'
    settings_path.write_text(
        json.dumps(
            {'base_url': 'https://chat.example.test/v1', 'api_key': 'test-key', 'temperature': 0.2}
        ),
        encoding='utf-8',
    )
    seen = {}

    def fake_forward(settings, messages, **kwargs):
        seen['messages'] = messages
        seen['kwargs'] = kwargs
        assert settings.api_key == 'test-key'
        return {
            'choices': [{'message': {'content': '{"action":"inspect","explanation":"已读取"}'}}]
        }

    monkeypatch.setattr(agent_intent, 'forward_chat', fake_forward)
    result = agent_intent.complete_agent_intent(
        ChatSettingsStore(settings_path),
        message='查看当前用户规则',
        target_user='alice',
        context={'packs': (), 'rules': []},
    )
    assert result['action'] == 'inspect'
    assert seen['kwargs']['model'] == 'gemini-3.8-flash-high'
    assert 'alice' in seen['messages'][0]['content']
