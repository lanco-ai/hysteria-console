import json
import urllib.error

from fastapi.testclient import TestClient

import web_api.chat_routes as chat_routes
from web_api import create_app
from web_api.chat_service import (
    ChatSettings,
    ChatSettingsStore,
    ChatUpstreamError,
    chat_completions_url,
    forward_chat,
)
from web_api.services import LoginRequired


class _Services:
    """Route registration only needs a service object until a request runs."""

    def read_session(self, *, headers, path):
        del path
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        raise LoginRequired


def test_chat_routes_are_registered():
    app = create_app(_Services())
    paths = {getattr(route, 'path', None) for route in app.routes}

    assert '/api/chat/settings' in paths
    assert '/api/chat/completions' in paths


def test_settings_store_masks_keys_and_enforces_file_mode(tmp_path):
    store = ChatSettingsStore(tmp_path / 'chat' / 'settings.json')
    result = store.update(
        base_url='https://example.test/v1',
        api_key='sk-super-secret-value',
        model='gemini-test',
        temperature=0.4,
    )

    assert result['api_key_configured'] is True
    assert result['api_key_masked'] == 'sk-…alue'
    assert 'sk-super-secret-value' not in json.dumps(result)
    assert oct(store.path.stat().st_mode & 0o777) == '0o600'


def test_forward_chat_builds_openai_request_without_logging_credentials():
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def opener(request, timeout):
        seen['url'] = request.full_url
        seen['authorization'] = request.get_header('Authorization')
        seen['body'] = json.loads(request.data)
        seen['timeout'] = timeout
        return Response()

    result = forward_chat(
        ChatSettings('https://example.test/v1', 'sk-secret', 'model-x', 0.7),
        [{'role': 'user', 'content': 'hello'}],
        opener=opener,
    )

    assert result['choices'][0]['message']['content'] == 'ok'
    assert seen['url'] == 'https://example.test/v1/chat/completions'
    assert seen['authorization'] == 'Bearer sk-secret'
    assert seen['body']['stream'] is False
    assert seen['timeout'] == 120


def test_chat_completions_url_normalizes_common_base_url_shapes():
    assert chat_completions_url('https://example.test/v1') == 'https://example.test/v1/chat/completions'
    assert chat_completions_url('https://example.test/v1/') == 'https://example.test/v1/chat/completions'
    assert chat_completions_url('https://example.test/v1/v1') == 'https://example.test/v1/chat/completions'
    assert chat_completions_url('https://example.test/v1/chat/completions') == 'https://example.test/v1/chat/completions'
    assert chat_completions_url('https://example.test/v1/chat/completions/') == 'https://example.test/v1/chat/completions'


def test_forward_chat_sanitizes_upstream_http_errors():
    def opener(_request, **_kwargs):
        raise urllib.error.HTTPError(
            'https://example.test/v1/chat/completions',
            429,
            'rate limited: secret body omitted',
            {'Retry-After': '9'},
            None,
        )

    try:
        forward_chat(
            ChatSettings('https://example.test/v1', 'sk-secret', 'model-x', 0.7),
            [{'role': 'user', 'content': 'hello'}],
            opener=opener,
        )
    except ChatUpstreamError as error:
        assert error.status == 429
        assert error.retry_after == '9'
        assert 'secret body' not in str(error)
    else:
        raise AssertionError('expected sanitized upstream error')


def test_chat_routes_require_admin_and_never_return_raw_key(tmp_path, monkeypatch):
    store = ChatSettingsStore(tmp_path / 'settings.json')
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)
    monkeypatch.setattr(chat_routes, 'forward_chat', lambda settings, messages: {
        'choices': [{'message': {'content': messages[-1]['content'].upper()}}],
    })
    with TestClient(create_app(_Services())) as client:
        assert client.get('/api/chat/settings').status_code == 401
        response = client.put(
            '/api/chat/settings',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={
                'base_url': 'https://example.test/v1',
                'api_key': 'sk-secret-value',
                'model': 'model-x',
                'temperature': 0.7,
            },
        )
        assert response.status_code == 200
        assert 'sk-secret-value' not in response.text
        assert response.json()['api_key_configured'] is True
        completion = client.post(
            '/api/chat/completions',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={'messages': [{'role': 'user', 'content': 'hello'}]},
        )
        assert completion.status_code == 200
        assert completion.json()['choices'][0]['message']['content'] == 'HELLO'
