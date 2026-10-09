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
        chat = next(item for item in data['profiles'] if item['id'] == 'chat-primary')
        assert 'api_key' not in chat

        response = client.put(
            f'{endpoint}/chat-primary', headers=HEADERS,
            json={'revision': data['revision'], 'api_key': 'chat-test-secret'},
        )
        assert response.status_code == 200
        assert 'chat-test-secret' not in response.text
        assert response.json()['revision'] != data['revision']
        profiles = client.get(endpoint, headers=HEADERS).json()['profiles']
        assert [(item['id'], item['api_key_configured']) for item in profiles] == [
            ('chat-primary', True), ('media-primary', False),
        ]

        cross_site = client.put(
            f'{endpoint}/chat-primary',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'},
            json={'revision': response.json()['revision'], 'name': 'Rejected'},
        )
        assert cross_site.status_code == 403
        stale = client.put(
            f'{endpoint}/chat-primary', headers=HEADERS,
            json={'revision': data['revision'], 'name': 'stale'},
        )
        assert stale.status_code == 409


def test_removed_gemini_service_endpoints_answer_unknown_service(tmp_path):
    store = make_store(tmp_path)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        revision = client.get('/api/ai/services', headers=HEADERS).json()['revision']
        responses = [
            client.put('/api/ai/services/gemini-primary', headers=HEADERS, json={'revision': revision, 'name': 'Gemini'}),
            client.get('/api/ai/services/gemini-primary/models', headers=HEADERS),
            client.post('/api/ai/services/gemini-primary/test', headers=HEADERS, json={}),
            client.post(
                '/api/ai/services/gemini-primary/test/generation', headers=HEADERS,
                json={'feature': 'plan_assistant', 'model_id': 'gemini-test'},
            ),
        ]
    assert [(item.status_code, item.json()['error']) for item in responses] == [(404, 'unknown_service')] * 4


def test_binding_is_admin_only_same_origin_and_protocol_checked(tmp_path):
    store = make_store(tmp_path)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        endpoint = '/api/ai/service-bindings'
        assert client.put(endpoint, json={}).status_code == 401
        initial = client.get('/api/ai/services', headers=HEADERS).json()
        assert client.put(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
        changed = client.put(endpoint, headers=HEADERS, json={
            'revision': initial['revision'], 'feature': 'plan_assistant', 'profile_id': 'chat-primary',
        })
        assert changed.status_code == 200
        bad_binding = client.put(endpoint, headers=HEADERS, json={
            'revision': changed.json()['revision'], 'feature': 'image_generation', 'profile_id': 'chat-primary',
        })
        assert bad_binding.status_code == 422
        removed = client.put(endpoint, headers=HEADERS, json={
            'revision': changed.json()['revision'], 'feature': 'video_assistant', 'profile_id': 'gemini-primary',
        })
        assert removed.status_code == 422


def test_chat_connection_test_persists_only_safe_model_metadata(tmp_path):
    store = make_store(tmp_path)
    seen = []

    def fetch_models(settings):
        seen.append(settings)
        return [{'id': 'chat-test', 'name': 'Chat Test', 'input_token_limit': 64000}]

    with TestClient(create_app(Sessions(), ai_services_store=store, openai_models_fetcher=fetch_models)) as client:
        endpoint = '/api/ai/services/chat-primary/test'
        initial = client.get('/api/ai/services', headers=HEADERS).json()
        client.put('/api/ai/services/chat-primary', headers=HEADERS, json={
            'revision': initial['revision'], 'base_url': 'https://provider.test/v1',
            'api_key': 'chat-secret-not-returned',
        })
        response = client.post(endpoint, headers=HEADERS, json={})
        assert response.status_code == 200
        result = response.json()
        assert result['ok'] is True
        assert result['test_level'] == 'A'
        assert result['tested_at']
        assert result['models_count'] == 1
        assert result['models'][0]['id'] == 'chat-test'
        assert 'chat-secret-not-returned' not in response.text
        assert seen[0].api_key == 'chat-secret-not-returned'
        assert seen[0].base_url == 'https://provider.test/v1'
        assert client.get('/api/ai/services/chat-primary/models', headers=HEADERS).json()['models'][0]['id'] == 'chat-test'
        assert store.profile('chat-primary')['models'] == result['models']


def test_assistant_text_and_structured_capability_tests_are_explicit_and_ephemeral(tmp_path, monkeypatch):
    import web_api.ai.routes as ai_routes

    store = make_store(tmp_path)
    initial = store.public()
    saved = store.update_profile('chat-primary', revision=initial['revision'], base_url='https://provider.test/v1', api_key='test-chat-key')
    catalog = store.update_catalog(
        'chat-primary', [{'id': 'listed-first'}, {'id': 'chosen-model'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    bound = store.set_binding(
        'plan_assistant', 'chat-primary', model_id='chosen-model', revision=catalog['revision'],
    )
    called = []

    def text_test(profile, model, prompt):
        called.append(('text', profile['protocol'], model, prompt))
        return 'ok'

    def structured_test(profile, model, prompt, schema):
        called.append(('structured', profile['protocol'], model, prompt))
        return ({'summary': '测试', 'suggestions': [{
            'title': '测试任务', 'notes': '', 'quadrant': 'important',
            'start_time': '', 'estimate_minutes': 30,
            'reminder_offset_minutes': 0, 'reason': '结构测试',
        }]}, 'json_schema')

    monkeypatch.setattr(ai_routes, 'generate_assistant_text', text_test, raising=False)
    monkeypatch.setattr(ai_routes, 'generate_assistant_json', structured_test, raising=False)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        text_response = client.post(
            '/api/ai/services/chat-primary/test/generation', headers=HEADERS,
            json={'feature': 'plan_assistant', 'model_id': 'chosen-model'},
        )
        structured_response = client.post(
            '/api/ai/services/chat-primary/test/structured', headers=HEADERS,
            json={'feature': 'plan_assistant', 'model_id': 'chosen-model'},
        )

    assert text_response.status_code == 200
    assert text_response.json()['level'] == 'B'
    assert text_response.json()['model_id'] == 'chosen-model'
    assert text_response.json()['revision'] == bound['revision']
    assert structured_response.status_code == 200
    assert structured_response.json()['level'] == 'C'
    assert structured_response.json()['structured_output'] == 'json_schema'
    assert structured_response.json()['revision'] == bound['revision']
    assert [item[:3] for item in called] == [
        ('text', 'openai_compatible', 'chosen-model'),
        ('structured', 'openai_compatible', 'chosen-model'),
    ]


def test_video_assistant_capability_test_uses_video_structure_without_persisting_projects(tmp_path, monkeypatch):
    import web_api.ai.routes as ai_routes

    store = make_store(tmp_path)
    initial = store.public()
    saved = store.update_profile('chat-primary', revision=initial['revision'], base_url='https://provider.test/v1', api_key='test-chat-key')
    catalog = store.update_catalog(
        'chat-primary', [{'id': 'listed-first'}, {'id': 'chosen-video-model'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    bound = store.set_binding(
        'video_assistant', 'chat-primary', model_id='chosen-video-model', revision=catalog['revision'],
    )
    observed = []

    def generate(profile, model, prompt, schema):
        observed.append((profile['protocol'], model, schema['properties']['shots']['items']['properties']['duration']))
        return ({
            'title': '测试分镜', 'rewritten_text': '故事草稿', 'style_prompt': '柔和光线',
            'aspect_ratio': '16:9', 'shots': [{
                'title': '镜头一', 'script': '人物抬头', 'shot_type': '近景',
                'character': '人物', 'scene': '房间', 'duration': 5,
                'image_prompt': '明亮房间', 'motion_prompt': '缓慢推进', 'dialogue': '',
            }],
        }, 'json_schema')

    monkeypatch.setattr(ai_routes, 'generate_assistant_json', generate, raising=False)
    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        response = client.post(
            '/api/ai/services/chat-primary/test/structured', headers=HEADERS,
            json={'feature': 'video_assistant', 'model_id': 'chosen-video-model'},
        )
    assert response.status_code == 200, response.text
    assert response.json()['level'] == 'C'
    assert response.json()['revision'] == bound['revision']
    assert response.json()['structured_output'] == 'json_schema'
    assert observed == [('openai_compatible', 'chosen-video-model', {'type': 'INTEGER'})]


def test_video_assistant_capability_test_sanitizes_invalid_structured_result(tmp_path, monkeypatch):
    import web_api.ai.routes as ai_routes

    store = make_store(tmp_path)
    initial = store.public()
    saved = store.update_profile('chat-primary', revision=initial['revision'], base_url='https://provider.test/v1', api_key='test-chat-key')
    catalog = store.update_catalog(
        'chat-primary', [{'id': 'chosen-video-model'}], capabilities=['chat'],
        checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    store.set_binding('video_assistant', 'chat-primary', model_id='chosen-video-model', revision=catalog['revision'])
    monkeypatch.setattr(ai_routes, 'generate_assistant_json', lambda *_args, **_kwargs: ({}, 'json_schema'))

    with TestClient(create_app(Sessions(), ai_services_store=store)) as client:
        response = client.post(
            '/api/ai/services/chat-primary/test/structured', headers=HEADERS,
            json={'feature': 'video_assistant', 'model_id': 'chosen-video-model'},
        )

    assert response.status_code == 502
    assert response.json() == {'error': 'structured_result_invalid'}
    assert 'ValidationError' not in response.text


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


def test_shared_chat_settings_no_longer_write_service_url_or_key(tmp_path):
    from web_api.ai.compat import ChatSettingsAdapter

    store = make_store(tmp_path)
    adapter = ChatSettingsAdapter(store)
    public = adapter.update(temperature=0.9)
    assert public['temperature'] == 0.9
    try:
        adapter.update(base_url='https://new.example/v1')
    except Exception as exc:
        assert 'Service Center' in str(exc)
    else:
        raise AssertionError('the legacy Chat settings API must not change the shared service URL')
