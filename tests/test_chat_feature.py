import json
import urllib.error

from fastapi.testclient import TestClient

import web_api.chat_routes as chat_routes
from web_api import create_app
from web_api.chat_service import (
    ChatSettings,
    ChatSettingsStore,
    ChatUpstreamError,
    build_upstream_request,
    chat_models_url,
    chat_completions_url,
    forward_chat,
    list_chat_models,
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
    assert '/api/chat/models' in paths
    assert '/api/chat/test' in paths


def test_settings_store_masks_keys_and_enforces_file_mode(tmp_path):
    store = ChatSettingsStore(tmp_path / 'chat' / 'settings.json')
    result = store.update(
        base_url='https://example.test/v1',
        api_key='sk-super-secret-value',
        temperature=0.4,
    )

    assert result['api_key_configured'] is True
    assert result['api_key_masked'] == 'sk-…alue'
    assert 'model' not in result
    assert 'reasoning_enabled' not in result
    assert 'reasoning_effort' not in result
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
        ChatSettings('https://example.test/v1', 'sk-secret', 0.7),
        [{'role': 'user', 'content': 'hello'}],
        model='model-x',
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


def test_chat_models_url_normalizes_common_base_url_shapes():
    assert chat_models_url('https://example.test') == 'https://example.test/models'
    assert chat_models_url('https://example.test/v1') == 'https://example.test/v1/models'
    assert chat_models_url('https://example.test/v1/v1') == 'https://example.test/v1/models'
    assert chat_models_url('https://example.test/v1/v1/models') == 'https://example.test/v1/models'
    assert chat_models_url('https://example.test/models') == 'https://example.test/models'
    assert chat_models_url('https://example.test/v1/models/') == 'https://example.test/v1/models'
    assert chat_models_url('https://example.test/v1/chat/completions') == 'https://example.test/v1/models'


def test_list_chat_models_builds_safe_get_request():
    seen = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"data":[{"id":"model-a","owned_by":"provider","context_length":8192},{"id":"model-b","name":"Friendly"}]}'

    def opener(request, timeout):
        seen['url'] = request.full_url
        seen['method'] = request.method
        seen['authorization'] = request.get_header('Authorization')
        seen['timeout'] = timeout
        return Response()

    result = list_chat_models(
        ChatSettings('https://example.test/v1', 'sk-secret', 0.7),
        opener=opener,
    )

    assert result == [
        {'id': 'model-a', 'name': 'model-a', 'context_window': 8192},
        {'id': 'model-b', 'name': 'Friendly'},
    ]
    assert seen == {
        'url': 'https://example.test/v1/models',
        'method': 'GET',
        'authorization': 'Bearer sk-secret',
        'timeout': 20,
    }


def test_reasoning_effort_is_opt_in_and_omits_auto():
    base = ChatSettings('https://example.test/v1', 'sk-secret', 0.7)
    disabled_request = build_upstream_request(base, [{'role': 'user', 'content': 'hello'}], model='model-x')
    assert 'reasoning_effort' not in json.loads(disabled_request.data)

    high_request = build_upstream_request(base, [{'role': 'user', 'content': 'hello'}], model='model-x', reasoning_effort='high')
    assert json.loads(high_request.data)['reasoning_effort'] == 'high'

    auto_request = build_upstream_request(base, [{'role': 'user', 'content': 'hello'}], model='model-x', reasoning_effort='auto')
    assert 'reasoning_effort' not in json.loads(auto_request.data)


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
            ChatSettings('https://example.test/v1', 'sk-secret', 0.7),
            [{'role': 'user', 'content': 'hello'}],
            model='model-x',
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
    monkeypatch.setattr(chat_routes, 'forward_chat', lambda settings, messages, **kwargs: {
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
                'temperature': 0.7,
            },
        )
        assert response.status_code == 200
        assert 'sk-secret-value' not in response.text
        assert response.json()['api_key_configured'] is True
        completion = client.post(
            '/api/chat/completions',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={'messages': [{'role': 'user', 'content': 'hello'}], 'model': 'model-x'},
        )
        assert completion.status_code == 200
        assert completion.json()['choices'][0]['message']['content'] == 'HELLO'


def test_chat_settings_rejects_removed_default_model_fields(tmp_path, monkeypatch):
    store = ChatSettingsStore(tmp_path / 'settings.json')
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)
    with TestClient(create_app(_Services())) as client:
        response = client.put(
            '/api/chat/settings',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={'base_url': 'http://127.0.0.1:8088/v1', 'api_key': 'sk-secret', 'model': 'legacy'},
        )
        assert response.status_code == 400
        assert not store.path.exists()


def test_chat_completion_requires_ephemeral_model_and_accepts_optional_reasoning(tmp_path, monkeypatch):
    store = ChatSettingsStore(tmp_path / 'settings.json')
    store.update(base_url='http://127.0.0.1:8088/v1', api_key='sk-secret', temperature=0.7)
    seen = []
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)
    monkeypatch.setattr(chat_routes, 'forward_chat', lambda settings, messages, **kwargs: (seen.append(kwargs) or {'choices': [{'message': {'content': 'ok'}}]}))
    with TestClient(create_app(_Services())) as client:
        headers = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}
        missing = client.post('/api/chat/completions', headers=headers, json={'messages': [{'role': 'user', 'content': 'hi'}]})
        assert missing.status_code == 422
        response = client.post('/api/chat/completions', headers=headers, json={
            'messages': [{'role': 'user', 'content': 'hi'}], 'model': 'model-x', 'reasoning_effort': 'high',
        })
        assert response.status_code == 200
        assert seen == [{'model': 'model-x', 'reasoning_effort': 'high'}]


def test_chat_settings_legacy_disk_fields_are_ignored(tmp_path):
    path = tmp_path / 'settings.json'
    path.write_text(json.dumps({
        'base_url': 'http://127.0.0.1:8088/v1', 'api_key': 'sk-secret', 'model': 'legacy',
        'temperature': 0.5, 'reasoning_enabled': True, 'reasoning_effort': 'high',
    }))
    store = ChatSettingsStore(path)
    settings = store.read()
    assert not hasattr(settings, 'model')
    assert store.public() == {
        'base_url': 'http://127.0.0.1:8088/v1', 'temperature': 0.5,
        'api_key_configured': True, 'api_key_masked': 'sk-…cret',
    }


def test_reasoning_error_can_be_classified_without_retaining_upstream_body():
    body = '{"error":{"message":"unsupported parameter reasoning_effort: secret details"}}'.encode()

    class Error(urllib.error.HTTPError):
        def __init__(self):
            super().__init__('https://example.test/v1/chat/completions', 400, 'bad request', {'Retry-After': '1'}, None)

        def read(self, _limit=-1):
            return body

    def opener(_request, **_kwargs):
        raise Error()

    try:
        forward_chat(ChatSettings('https://example.test/v1', 'sk-secret', 0.7), [{'role': 'user', 'content': 'hello'}], model='model-x', reasoning_effort='high', opener=opener)
    except ChatUpstreamError as error:
        assert error.reasoning_unsupported is True
        assert 'secret details' not in str(error)
    else:
        raise AssertionError('expected reasoning classification')


def test_forward_chat_retries_without_reasoning_when_upstream_rejects_it():
    requests = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    class Error(urllib.error.HTTPError):
        def __init__(self):
            super().__init__('https://example.test/v1/chat/completions', 400, 'bad request', {}, None)

        def read(self, _limit=-1):
            return b'{"error":{"message":"unsupported parameter reasoning_effort"}}'

    def opener(request, **_kwargs):
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            raise Error()
        return Response()

    result = forward_chat(
        ChatSettings('https://example.test/v1', 'sk-secret', 0.7),
        [{'role': 'user', 'content': 'hello'}],
        model='model-x', reasoning_effort='high', opener=opener,
    )
    assert result['choices'][0]['message']['content'] == 'ok'
    assert result['chat_notice'] == 'reasoning_unsupported'
    assert requests[0]['reasoning_effort'] == 'high'
    assert 'reasoning_effort' not in requests[1]


def test_chat_models_and_connection_test_are_admin_only_and_sanitized(tmp_path, monkeypatch):
    store = ChatSettingsStore(tmp_path / 'settings.json')
    store.update(
        base_url='https://example.test/v1',
        api_key='sk-secret-value',
        temperature=0.7,
    )
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)
    monkeypatch.setattr(chat_routes, 'list_chat_models', lambda settings: [
        {'id': 'model-x', 'name': 'model-x'},
    ])
    with TestClient(create_app(_Services())) as client:
        assert client.get('/api/chat/models').status_code == 401
        models = client.get('/api/chat/models', headers={'Cookie': 'sid=admin'})
        assert models.status_code == 200
        assert models.json() == [{'id': 'model-x', 'name': 'model-x'}]
        assert 'sk-secret-value' not in models.text

        test = client.post(
            '/api/chat/test',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={},
        )
        assert test.status_code == 200
        assert test.json() == {'ok': True, 'message': 'Connected', 'models_count': 1}


def test_chat_connection_test_maps_upstream_errors_without_body(tmp_path, monkeypatch):
    store = ChatSettingsStore(tmp_path / 'settings.json')
    store.update(base_url='https://example.test/v1', api_key='sk-secret', temperature=0.7)
    monkeypatch.setattr(chat_routes, 'ChatSettingsStore', lambda: store)
    monkeypatch.setattr(
        chat_routes,
        'list_chat_models',
        lambda _settings: (_ for _ in ()).throw(ChatUpstreamError(401)),
    )
    with TestClient(create_app(_Services())) as client:
        response = client.post(
            '/api/chat/test',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'},
            json={},
        )
    assert response.status_code == 502
    assert response.json() == {'ok': False, 'error': 'authentication_failed'}
    assert 'sk-secret' not in response.text
