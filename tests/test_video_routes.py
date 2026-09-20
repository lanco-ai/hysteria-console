import json
import threading

import pytest
from fastapi.testclient import TestClient

import web_api.video_routes as video_routes
from web_api import create_app
from web_api.ai.service_store import AIServiceStore
from web_api.video_provider import ProviderError
from web_api.video_service import VideoSettingsError, VideoSettingsStore
from web_api.services import LoginRequired


class _Services:
    def read_session(self, *, headers, path):
        del path
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        raise LoginRequired


def _admin_headers():
    return {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


class _RunService:
    def __init__(self, run):
        self.run = run
        self.tick_calls = 0

    def get(self, run_id):
        return self.run if run_id == self.run['id'] else None

    def list(self):
        return [self.run]

    def tick(self, run_id):
        self.tick_calls += 1
        return self.get(run_id)

    def resume_pending(self):
        return []


class _Media:
    status_code = 206
    content_type = 'video/mp4'
    content_length = '4'
    content_range = 'bytes 0-3/4'

    def __init__(self):
        self.closed = False

    def iter_bytes(self):
        yield b'mp4!'

    def close(self):
        self.closed = True


def _completed_run():
    return {
        'id': 'run-1', 'workflow_id': 'workflow-1', 'state': 'succeeded',
        'node_status': {'video-node': {'state': 'succeeded'}},
        'assets': {'video-node': 'http://provider.test/v1/media/videos/asset_123'},
        'workflow': {'nodes': [], 'edges': []},
    }


def test_video_settings_requires_admin_and_masks_key(tmp_path):
    store = VideoSettingsStore(tmp_path / 'video' / 'settings.json')
    app = create_app(_Services(), video_settings_store=store)
    with TestClient(app) as client:
        assert client.get('/api/video/settings').status_code == 401
        response = client.put(
            '/api/video/settings',
            headers=_admin_headers(),
            json={'base_url': 'https://provider.test/v1', 'api_key': 'secret', 'provider': 'grok'},
        )
        assert response.status_code == 200
        assert 'secret' not in response.text
        assert response.json()['api_key_configured'] is True
        assert oct(store.path.stat().st_mode & 0o777) == '0o600'
        assert json.loads(store.path.read_text())['api_key'] == 'secret'


def test_video_state_directory_is_private_when_preexisting(tmp_path):
    video_dir = tmp_path / 'video'
    video_dir.mkdir(mode=0o755)
    video_dir.chmod(0o755)

    VideoSettingsStore(video_dir / 'settings.json')

    assert oct(video_dir.stat().st_mode & 0o777) == '0o700'


def test_video_settings_reject_cross_site_write(tmp_path):
    store = VideoSettingsStore(tmp_path / 'settings.json')
    app = create_app(_Services(), video_settings_store=store)
    with TestClient(app) as client:
        response = client.put(
            '/api/video/settings',
            headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'},
            json={'base_url': 'https://provider.test/v1', 'api_key': 'secret'},
        )
    assert response.status_code == 403


@pytest.mark.parametrize('base_url', [
    'http://127.0.0.1:13004/v1',
    'http://localhost:13004/v1',
    'http://[::1]:13004/v1',
])
def test_video_settings_allow_loopback_http_for_local_provider(tmp_path, base_url):
    store = VideoSettingsStore(tmp_path / 'settings.json')
    result = store.update(base_url=base_url, api_key='secret')
    assert result['base_url'] == base_url


def test_video_settings_reject_plain_http_to_remote_provider(tmp_path):
    store = VideoSettingsStore(tmp_path / 'settings.json')
    with pytest.raises(VideoSettingsError, match='HTTPS or loopback HTTP'):
        store.update(base_url='http://provider.test/v1', api_key='secret')


def test_video_capabilities_are_sanitized(tmp_path, monkeypatch):
    store = VideoSettingsStore(tmp_path / 'settings.json')
    store.update(base_url='https://provider.test/v1', api_key='secret')

    class Provider:
        def capabilities(self, settings):
            assert settings.api_key == 'secret'
            return type('Caps', (), {
                'image_models': ['grok-imagine-image'],
                'video_models': ['grok-imagine-video'],
                'first_last_frame': type('Capability', (), {'supported': False, 'reason': 'unverified'})(),
                'video_composition': type('Capability', (), {'supported': False, 'reason': 'unverified'})(),
            })()

    monkeypatch.setattr(video_routes, 'GrokVideoProvider', lambda: Provider())
    app = create_app(_Services(), video_settings_store=store)
    with TestClient(app) as client:
        response = client.get('/api/video/capabilities', headers=_admin_headers())
    assert response.status_code == 200
    assert 'Authorization' not in response.text
    assert 'api_key' not in response.text
    assert response.json()['video_models'] == ['grok-imagine-video']


def test_video_connection_test_returns_sanitized_model_count(tmp_path, monkeypatch):
    store = VideoSettingsStore(tmp_path / 'settings.json')
    store.update(base_url='https://provider.test/v1', api_key='secret')

    class Provider:
        def capabilities(self, settings):
            return type('Caps', (), {
                'image_models': ['image'], 'video_models': ['video'],
                'first_last_frame': type('Capability', (), {'supported': False})(),
                'video_composition': type('Capability', (), {'supported': False})(),
            })()

    monkeypatch.setattr(video_routes, 'GrokVideoProvider', lambda: Provider())
    app = create_app(_Services(), video_settings_store=store)
    with TestClient(app) as client:
        response = client.post('/api/video/connection/test', headers=_admin_headers(), json={})
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'models_count': 2}


class _VideoAssistantGemini:
    def __init__(self):
        self.calls = []

    def list_models(self, profile):
        assert profile['api_key'] == 'gemini-server-secret'
        return [{'id': 'gemini-preview-fast', 'name': 'Gemini Preview Fast'}]

    def generate_json(self, profile, model, prompt, schema):
        self.calls.append({'model': model, 'prompt': prompt, 'schema': schema})
        return {
            'title': '小小探险家', 'rewritten_text': '孩子在花园找到一颗发光的种子。',
            'style_prompt': '温暖的 3D 动画，柔和晨光。', 'aspect_ratio': '9:16',
            'shots': [{
                'title': '发现种子', 'script': '孩子蹲下发现种子。', 'shot_type': '近景',
                'character': '小朋友', 'scene': '晨光花园', 'duration': 5,
                'image_prompt': '温暖的花园里，小朋友发现一颗发光的种子。',
                'motion_prompt': '镜头缓慢推进，小朋友好奇地拾起种子。', 'dialogue': '这是什么？',
            }],
        }


def _ai_store(tmp_path):
    store = AIServiceStore(
        tmp_path / 'ai' / 'registry.json',
        chat_legacy_path=tmp_path / 'chat.json',
        video_legacy_path=tmp_path / 'video.json',
        backup_dir=tmp_path / 'ai' / 'migration-backup',
    )
    snapshot = store.public()
    snapshot = store.update_profile('gemini-primary', revision=snapshot['revision'], api_key='gemini-server-secret')
    snapshot = store.update_catalog(
        'gemini-primary', [{'id': 'listed-first'}, {'id': 'gemini-preview-fast'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=snapshot['revision'],
    )
    store.set_binding(
        'video_assistant', 'gemini-primary', model_id='gemini-preview-fast', revision=snapshot['revision'],
    )
    return store


def test_video_assistant_drafts_storyboard_without_running_paid_media_jobs(tmp_path):
    gemini = _VideoAssistantGemini()
    app = create_app(
        _Services(), video_settings_store=VideoSettingsStore(tmp_path / 'video.json'),
        ai_services_store=_ai_store(tmp_path), gemini_adapter=gemini,
    )
    with TestClient(app) as client:
        response = client.post('/api/video/assistant/draft', headers=_admin_headers(), json={
            'idea': '一个孩子和会发光的种子', 'style_prompt': '温暖 3D 动画',
            'aspect_ratio': '9:16', 'shot_count': 1, 'shot_duration': 5,
        })
    assert response.status_code == 200
    result = response.json()
    assert result['model'] == 'gemini-preview-fast'
    assert result['structured_output'] == 'gemini_native_schema'
    assert result['shots'][0]['image_prompt'].startswith('温暖的花园')
    assert 'gemini-server-secret' not in response.text
    assert 'gemini-server-secret' not in gemini.calls[0]['prompt']
    assert len(gemini.calls) == 1


def test_video_assistant_uses_the_saved_chat_service_and_exact_model(tmp_path, monkeypatch):
    import web_api.video_routes as video_routes

    ai_store = _ai_store(tmp_path)
    initial = ai_store.public()
    saved = ai_store.update_profile(
        'chat-primary', revision=initial['revision'],
        base_url='https://provider.test/v1', api_key='test-chat-secret',
    )
    catalog = ai_store.update_catalog(
        'chat-primary', [{'id': 'listed-first'}, {'id': 'chosen-video-model'}],
        capabilities=['chat'], checked_at='2026-09-20T00:00:00Z', revision=saved['revision'],
    )
    ai_store.set_binding(
        'video_assistant', 'chat-primary', model_id='chosen-video-model', revision=catalog['revision'],
    )
    observed = {}
    valid_result = _VideoAssistantGemini().generate_json({}, 'chosen-video-model', '', {})

    def generate(profile, model, prompt, schema, *, gemini_adapter):
        observed.update(protocol=profile['protocol'], model=model, api_key=profile['api_key'])
        return valid_result, 'json_text_fallback'

    monkeypatch.setattr(video_routes, 'generate_assistant_json', generate, raising=False)
    app = create_app(
        _Services(), video_settings_store=VideoSettingsStore(tmp_path / 'video.json'),
        ai_services_store=ai_store,
    )
    with TestClient(app) as client:
        response = client.post('/api/video/assistant/draft', headers=_admin_headers(), json={
            'idea': '一个孩子和会发光的种子', 'style_prompt': '温暖 3D 动画',
            'aspect_ratio': '9:16', 'shot_count': 1, 'shot_duration': 5,
        })
    assert response.status_code == 200
    assert response.json()['model'] == 'chosen-video-model'
    assert response.json()['structured_output'] == 'json_text_fallback'
    assert observed == {'protocol': 'openai_compatible', 'model': 'chosen-video-model', 'api_key': 'test-chat-secret'}


def test_video_assistant_requires_admin_same_origin_and_rejects_invalid_draft(tmp_path):
    class InvalidGemini(_VideoAssistantGemini):
        def generate_json(self, profile, model, prompt, schema):
            return {'title': 'bad', 'shots': [{'title': 'bad', 'duration': 999}]}

    app = create_app(
        _Services(), video_settings_store=VideoSettingsStore(tmp_path / 'video.json'),
        ai_services_store=_ai_store(tmp_path), gemini_adapter=InvalidGemini(),
    )
    endpoint = '/api/video/assistant/draft'
    payload = {'idea': '一段短故事', 'style_prompt': '', 'aspect_ratio': '9:16', 'shot_count': 2, 'shot_duration': 5}
    with TestClient(app) as client:
        assert client.post(endpoint, json=payload).status_code == 401
        assert client.post(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json=payload).status_code == 403
        response = client.post(endpoint, headers=_admin_headers(), json=payload)
    assert response.status_code == 502
    assert response.json() == {'error': 'invalid_model_response'}


def test_video_assistant_rejects_shot_durations_that_differ_from_the_request(tmp_path):
    class WrongDurationGemini(_VideoAssistantGemini):
        def generate_json(self, profile, model, prompt, schema):
            result = super().generate_json(profile, model, prompt, schema)
            result['shots'][0]['duration'] = 3
            return result

    app = create_app(
        _Services(), video_settings_store=VideoSettingsStore(tmp_path / 'video.json'),
        ai_services_store=_ai_store(tmp_path), gemini_adapter=WrongDurationGemini(),
    )
    with TestClient(app) as client:
        response = client.post('/api/video/assistant/draft', headers=_admin_headers(), json={
            'idea': '一段短故事', 'style_prompt': '', 'aspect_ratio': '9:16',
            'shot_count': 1, 'shot_duration': 5,
        })
    assert response.status_code == 502
    assert response.json() == {'error': 'invalid_model_response'}


def test_run_responses_replace_provider_media_urls_with_same_origin_links(tmp_path):
    settings = VideoSettingsStore(tmp_path / 'settings.json')
    settings.update(base_url='https://provider.test/v1', api_key='secret')
    app = create_app(
        _Services(), video_settings_store=settings,
        video_run_service=_RunService(_completed_run()),
    )
    with TestClient(app) as client:
        response = client.get('/api/video/runs', headers=_admin_headers())
    assert response.status_code == 200
    assert 'provider.test' not in response.text
    assert response.json()['runs'][0]['assets']['video-node'] == (
        '/api/video/runs/run-1/assets/video-node/content'
    )


def test_run_status_read_does_not_poll_upstream_when_scheduler_is_enabled(tmp_path):
    run_service = _RunService(_completed_run())
    app = create_app(
        _Services(),
        video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'),
        video_run_service=run_service,
        video_scheduler_enabled=True,
        video_scheduler_interval=60,
    )
    with TestClient(app) as client:
        response = client.get('/api/video/runs/run-1', headers=_admin_headers())
    assert response.status_code == 200
    assert run_service.tick_calls == 0


def test_unknown_video_run_returns_not_found(tmp_path):
    run_service = _RunService(_completed_run())
    app = create_app(
        _Services(),
        video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'),
        video_run_service=run_service,
    )
    with TestClient(app) as client:
        response = client.get('/api/video/runs/missing', headers=_admin_headers())
    assert response.status_code == 404
    assert response.json() == {'error': 'not_found'}


def test_cancel_unknown_video_run_returns_not_found(tmp_path):
    run_service = _RunService(_completed_run())
    app = create_app(
        _Services(),
        video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'),
        video_run_service=run_service,
    )
    with TestClient(app) as client:
        response = client.post(
            '/api/video/runs/missing/cancel', headers=_admin_headers(), json={},
        )
    assert response.status_code == 404
    assert response.json() == {'error': 'not_found'}


def test_generated_media_proxy_requires_admin_and_streams_same_origin_content(tmp_path):
    settings = VideoSettingsStore(tmp_path / 'settings.json')
    settings.update(base_url='https://provider.test/v1', api_key='secret')
    media = _Media()
    seen = {}

    class Provider:
        def open_asset(self, url, provider_settings, *, range_header=None):
            seen['url'] = url
            seen['api_key'] = provider_settings.api_key
            seen['range'] = range_header
            return media

    app = create_app(
        _Services(), video_settings_store=settings,
        video_provider_factory=Provider,
        video_run_service=_RunService(_completed_run()),
    )
    path = '/api/video/runs/run-1/assets/video-node/content'
    with TestClient(app) as client:
        anonymous = client.get(path)
        response = client.get(path, headers={**_admin_headers(), 'Range': 'bytes=0-3'})
    assert anonymous.status_code == 401
    assert response.status_code == 206
    assert response.content == b'mp4!'
    assert response.headers['content-type'].startswith('video/mp4')
    assert response.headers['content-range'] == 'bytes 0-3/4'
    assert 'no-store' in response.headers['cache-control']
    assert seen == {
        'url': 'http://provider.test/v1/media/videos/asset_123',
        'api_key': 'secret', 'range': 'bytes=0-3',
    }
    assert media.closed is True


def test_generated_media_proxy_preserves_unsatisfied_range_response(tmp_path):
    settings = VideoSettingsStore(tmp_path / 'settings.json')
    settings.update(base_url='https://provider.test/v1', api_key='secret')

    class Provider:
        def open_asset(self, url, provider_settings, *, range_header=None):
            del url, provider_settings, range_header
            raise ProviderError(
                'range_not_satisfiable', status=416,
                content_range='bytes */4096',
            )

    app = create_app(
        _Services(), video_settings_store=settings,
        video_provider_factory=Provider,
        video_run_service=_RunService(_completed_run()),
    )
    path = '/api/video/runs/run-1/assets/video-node/content'
    with TestClient(app) as client:
        response = client.get(path, headers={**_admin_headers(), 'Range': 'bytes=9000-'})
    assert response.status_code == 416
    assert response.headers['content-range'] == 'bytes */4096'
    assert response.content == b''


def test_video_run_scheduler_resumes_pending_runs_without_browser_polling(tmp_path):
    ticked = threading.Event()

    class SchedulerRunService:
        def resume_pending(self):
            return [{'id': 'run-pending'}]

        def tick(self, run_id):
            assert run_id == 'run-pending'
            ticked.set()

    app = create_app(
        _Services(),
        video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'),
        video_run_service=SchedulerRunService(),
        video_scheduler_enabled=True,
        video_scheduler_interval=0.01,
    )
    with TestClient(app):
        assert ticked.wait(1), 'the application should advance persisted runs without a client request'
