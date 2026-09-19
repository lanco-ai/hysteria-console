import json

from fastapi.testclient import TestClient

from web_api import create_app
from web_api.ai.compat import ChatSettingsAdapter, VideoSettingsAdapter
from web_api.ai.service_store import AIServiceStore
from web_api.services import LoginRequired


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


class GeminiStub:
    def __init__(self):
        self.profile = None

    def list_models(self, profile):
        self.profile = profile
        return [{'id': 'gemini-test', 'name': 'Gemini Test', 'input_token_limit': 64000}]

    def generate_chat(self, profile, model, messages, **kwargs):
        self.chat_call = {'profile': profile, 'model': model, 'messages': messages, **kwargs}
        return {'choices': [{'message': {'role': 'assistant', 'content': 'Gemini replied'}}]}

    def stream_chat(self, profile, model, messages, **kwargs):
        self.chat_call = {'profile': profile, 'model': model, 'messages': messages, **kwargs}

        async def events():
            yield b'data: {"type":"delta","text":"Gemini "}\n\n'
            yield b'data: {"type":"delta","text":"stream"}\n\n'
            yield b'data: {"type":"done"}\n\n'

        return events()


def make_store(tmp_path):
    return AIServiceStore(
        tmp_path / 'ai' / 'registry.json',
        chat_legacy_path=tmp_path / 'chat.json',
        video_legacy_path=tmp_path / 'video.json',
        backup_dir=tmp_path / 'ai' / 'migration-backup',
    )


def test_ai_service_api_is_admin_only_and_updates_secrets_without_returning_them(tmp_path):
    store = make_store(tmp_path)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        endpoint = '/api/ai/services'
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers={'Cookie': 'sid=user'}).status_code == 403

        result = client.get(endpoint, headers=HEADERS)
        assert result.status_code == 200
        data = result.json()
        gemini = next(item for item in data['profiles'] if item['id'] == 'gemini-primary')
        assert 'api_key' not in gemini

        response = client.put(
            f'{endpoint}/gemini-primary', headers=HEADERS,
            json={'revision': data['revision'], 'api_key': 'gemini-test-secret'},
        )
        assert response.status_code == 200
        assert 'gemini-test-secret' not in response.text
        assert response.json()['revision'] != data['revision']
        assert client.get(endpoint, headers=HEADERS).json()['profiles'][0]['api_key_configured'] is False

        cross_site = client.put(
            f'{endpoint}/gemini-primary',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'},
            json={'revision': response.json()['revision'], 'name': 'Rejected'},
        )
        assert cross_site.status_code == 403
        stale = client.put(
            f'{endpoint}/gemini-primary', headers=HEADERS,
            json={'revision': data['revision'], 'name': 'stale'},
        )
        assert stale.status_code == 409


def test_binding_is_admin_only_same_origin_and_protocol_checked(tmp_path):
    store = make_store(tmp_path)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        endpoint = '/api/ai/service-bindings'
        assert client.put(endpoint, json={}).status_code == 401
        initial = client.get('/api/ai/services', headers=HEADERS).json()
        assert client.put(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
        changed = client.put(endpoint, headers=HEADERS, json={
            'revision': initial['revision'], 'feature': 'plan_assistant', 'profile_id': 'gemini-primary',
        })
        assert changed.status_code == 200
        bad_binding = client.put(endpoint, headers=HEADERS, json={
            'revision': changed.json()['revision'], 'feature': 'image_generation', 'profile_id': 'gemini-primary',
        })
        assert bad_binding.status_code == 422


def test_gemini_connection_test_persists_only_safe_model_metadata(tmp_path):
    store = make_store(tmp_path)
    adapter = GeminiStub()
    with TestClient(create_app(Sessions(), ai_services_store=store, gemini_adapter=adapter)) as client:
        endpoint = '/api/ai/services/gemini-primary/test'
        initial = client.get('/api/ai/services', headers=HEADERS).json()
        updated = client.put('/api/ai/services/gemini-primary', headers=HEADERS, json={
            'revision': initial['revision'], 'api_key': 'gemini-secret-not-returned',
        })
        response = client.post(endpoint, headers=HEADERS, json={})
        assert response.status_code == 200
        result = response.json()
        assert result['ok'] is True
        assert result['models_count'] == 1
        assert result['models'][0]['id'] == 'gemini-test'
        assert 'gemini-secret-not-returned' not in response.text
        assert adapter.profile['api_key'] == 'gemini-secret-not-returned'
        assert client.get('/api/ai/services/gemini-primary/models', headers=HEADERS).json()['models'][0]['id'] == 'gemini-test'
        assert store.profile('gemini-primary')['models'] == result['models']


def test_legacy_chat_and_video_api_contracts_read_and_write_the_shared_registry(tmp_path):
    chat_path = tmp_path / 'chat.json'
    video_path = tmp_path / 'video.json'
    chat_path.write_text(json.dumps({'base_url': 'https://chat.example/v1', 'api_key': 'chat-migrated-secret', 'temperature': 0.6}))
    video_path.write_text(json.dumps({'base_url': 'https://media.example/v1', 'api_key': 'media-migrated-secret', 'provider': 'grok'}))
    store = AIServiceStore(
        tmp_path / 'ai' / 'registry.json', chat_legacy_path=chat_path,
        video_legacy_path=video_path, backup_dir=tmp_path / 'ai' / 'migration-backup',
    )
    chat_adapter = ChatSettingsAdapter(store)
    video_adapter = VideoSettingsAdapter(store)
    with TestClient(create_app(
        Sessions(), ai_services_store=store,
        chat_settings_store=chat_adapter, video_settings_store=video_adapter,
    )) as client:
        chat = client.get('/api/chat/settings', headers=HEADERS)
        video = client.get('/api/video/settings', headers=HEADERS)
        assert chat.status_code == video.status_code == 200
        assert chat.json()['base_url'] == 'https://chat.example/v1'
        assert video.json()['base_url'] == 'https://media.example/v1'
        assert 'chat-migrated-secret' not in chat.text
        assert 'media-migrated-secret' not in video.text

        chat_update = client.put('/api/chat/settings', headers=HEADERS, json={'temperature': 0.9})
        video_update = client.put('/api/video/settings', headers=HEADERS, json={'base_url': 'https://media2.example/v1'})
        assert chat_update.status_code == video_update.status_code == 200
        assert store.profile('chat-primary')['temperature'] == 0.9
        assert store.profile('chat-primary')['api_key'] == 'chat-migrated-secret'
        assert store.profile('media-primary')['base_url'] == 'https://media2.example/v1'
        assert store.profile('media-primary')['api_key'] == 'media-migrated-secret'


def test_chat_can_bind_to_native_gemini_for_nonstreaming_and_streaming(tmp_path):
    from web_api.ai.compat import ChatSettingsAdapter

    store = make_store(tmp_path)
    adapter = GeminiStub()
    with TestClient(create_app(
        Sessions(), ai_services_store=store,
        chat_settings_store=ChatSettingsAdapter(store, adapter),
        gemini_adapter=adapter,
    )) as client:
        initial = client.get('/api/ai/services', headers=HEADERS).json()
        saved = client.put('/api/ai/services/gemini-primary', headers=HEADERS, json={
            'revision': initial['revision'], 'api_key': 'native-gemini-secret',
        })
        assert saved.status_code == 200
        binding = client.put('/api/ai/service-bindings', headers=HEADERS, json={
            'revision': saved.json()['revision'], 'feature': 'chat', 'profile_id': 'gemini-primary',
        })
        assert binding.status_code == 200

        message = {'role': 'user', 'content': 'hello'}
        regular = client.post('/api/chat/completions', headers=HEADERS, json={
            'messages': [message], 'model': 'gemini-test',
        })
        assert regular.status_code == 200
        assert regular.json()['choices'][0]['message']['content'] == 'Gemini replied'
        assert adapter.chat_call['profile']['api_key'] == 'native-gemini-secret'

        streamed = client.post('/api/chat/completions', headers=HEADERS, json={
            'messages': [message], 'model': 'gemini-test', 'stream': True,
        })
        assert streamed.status_code == 200
        assert streamed.headers['content-type'].startswith('text/event-stream')
        assert '"text":"Gemini "' in streamed.text
        assert '"text":"stream"' in streamed.text
        assert 'native-gemini-secret' not in streamed.text


def test_shared_chat_settings_no_longer_write_service_url_or_key(tmp_path):
    from web_api.ai.compat import ChatSettingsAdapter

    store = make_store(tmp_path)
    adapter = ChatSettingsAdapter(store, GeminiStub())
    public = adapter.update(temperature=0.9)
    assert public['temperature'] == 0.9
    try:
        adapter.update(base_url='https://new.example/v1')
    except Exception as exc:
        assert 'Service Center' in str(exc)
    else:
        raise AssertionError('the legacy Chat settings API must not change the shared service URL')
