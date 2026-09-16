import json

import pytest
from fastapi.testclient import TestClient

import web_api.chat_routes as chat_routes
from web_api import create_app
from web_api.chat_service import ChatSettings, ChatSettingsStore, build_upstream_request
from web_api.services import LoginRequired


SENTINEL_API_KEY = 'sk-workbench-security-sentinel-9f7e'
SAME_ORIGIN_HEADERS = {
    'Sec-Fetch-Site': 'same-origin',
    'Content-Type': 'application/json',
}


class _SessionServices:
    """Small session double that exercises the real route boundary."""

    def read_session(self, *, headers, path):
        del path
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        raise LoginRequired


@pytest.mark.parametrize(
    ('method', 'path', 'payload'),
    [
        ('get', '/api/chat/settings', None),
        ('get', '/api/chat/models', None),
        ('put', '/api/chat/settings', {
            'base_url': 'https://api.example.test/v1',
            'api_key': SENTINEL_API_KEY,
            'temperature': 0.7,
        }),
        ('post', '/api/chat/test', {}),
        ('post', '/api/chat/completions', {
            'messages': [{'role': 'user', 'content': 'hello'}],
            'model': 'model-x',
        }),
    ],
)
def test_anonymous_chat_endpoints_return_login_required(method, path, payload):
    """Every browser-facing chat endpoint must keep the admin-session boundary."""
    with TestClient(create_app(_SessionServices())) as client:
        request = getattr(client, method)
        if payload is None:
            response = request(path)
        else:
            response = request(path, headers=SAME_ORIGIN_HEADERS, json=payload)

    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
    assert response.headers['cache-control'] == 'no-store'


def test_settings_response_only_exposes_masked_credential_metadata(tmp_path, monkeypatch):
    """The key stays in the server store even when an admin reads settings."""
    store = ChatSettingsStore(tmp_path / 'chat-settings.json')
    store.update(
        base_url='https://api.example.test/v1',
        api_key=SENTINEL_API_KEY,
        temperature=0.4,
    )
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)

    with TestClient(create_app(_SessionServices())) as client:
        response = client.get('/api/chat/settings', headers={'Cookie': 'sid=admin'})

    assert response.status_code == 200
    assert response.json() == {
        'base_url': 'https://api.example.test/v1',
        'temperature': 0.4,
        'api_key_configured': True,
        'api_key_masked': 'sk-…9f7e',
    }
    assert SENTINEL_API_KEY not in response.text


def test_auto_reasoning_is_omitted_from_the_server_upstream_payload():
    """Auto must leave provider-specific reasoning fields out of the request."""
    request = build_upstream_request(
        ChatSettings('https://api.example.test/v1', SENTINEL_API_KEY, 0.7),
        [{'role': 'user', 'content': 'hello'}],
        model='model-x',
        reasoning_effort='auto',
    )

    payload = json.loads(request.data)
    assert 'reasoning_effort' not in payload
    assert SENTINEL_API_KEY not in request.data.decode('utf-8')
