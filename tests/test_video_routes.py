import json
import threading

import pytest
from fastapi.testclient import TestClient

import web_api.video_routes as video_routes
from web_api import create_app
from web_api.video_provider import ProviderError
from web_api.video_models import VideoSettings
from web_api.video_service import RunService, VideoSettingsError, VideoSettingsStore, WorkflowStore
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


def test_direct_video_assistant_route_is_removed_but_shared_schema_remains(tmp_path):
    from web_api.video_routes import VideoAssistantResult

    assert VideoAssistantResult.__name__ == 'VideoAssistantResult'
    app = create_app(_Services(), video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'))
    with TestClient(app) as client:
        response = client.post('/api/video/assistant/draft', headers=_admin_headers(), json={})
    assert response.status_code == 404


def test_run_presentation_converts_candidate_refs_to_admin_asset_urls(tmp_path):
    run = _completed_run()
    run['assets']['image-node'] = ['asset://first123', 'asset://last456']
    app = create_app(_Services(), video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'),
                     video_run_service=_RunService(run))
    with TestClient(app) as client:
        response = client.get('/api/video/runs/run-1', headers=_admin_headers())
    assert response.status_code == 200
    assert response.json()['assets']['image-node'] == [
        '/api/video/assets/first123/content', '/api/video/assets/last456/content',
    ]


def test_create_run_accepts_target_node_and_rejects_invalid_target_shape(tmp_path):
    workflows = WorkflowStore(tmp_path / 'workflows.json')
    saved = workflows.save({'title': 'target', 'nodes': [
        {'id': 'prompt', 'type': 'prompt', 'data': {'text': 'forest'}},
        {'id': 'image', 'type': 'text_to_image', 'data': {}},
        {'id': 'sibling', 'type': 'prompt', 'data': {'text': 'other'}},
    ], 'edges': [{'source': 'prompt', 'sourceHandle': 'text', 'target': 'image', 'targetHandle': 'prompt'}]})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), object(), tmp_path / 'runs.json')
    app = create_app(_Services(), video_workflow_store=workflows, video_run_service=service,
                     video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'))
    with TestClient(app) as client:
        response = client.post('/api/video/runs', headers=_admin_headers(), json={
            'workflow_id': saved['id'], 'target_node_id': 'image',
        })
        invalid = client.post('/api/video/runs', headers=_admin_headers(), json={
            'workflow_id': saved['id'], 'target_node_id': ['image'],
        })
    assert response.status_code == 202
    assert response.json()['order'] == ['prompt', 'image']
    assert invalid.status_code == 422


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


def test_create_run_returns_accepted_receipt_when_candidate_claim_is_unavailable(tmp_path, monkeypatch):
    workflows = WorkflowStore(tmp_path / 'workflows.json')
    saved = workflows.save({'nodes': [
        {'id': 'image', 'type': 'text_to_image', 'data': {'prompt': 'forest'}},
    ], 'edges': []})
    service = RunService(workflows, VideoSettings('https://provider.test/v1', 'secret'), object(), tmp_path / 'runs.json')
    def unavailable(*args, **kwargs):
        raise OSError('workflow storage temporarily unavailable')
    monkeypatch.setattr(workflows, 'reconcile_candidate_run', unavailable)
    app = create_app(_Services(), video_workflow_store=workflows, video_run_service=service,
                     video_settings_store=VideoSettingsStore(tmp_path / 'settings.json'))
    with TestClient(app) as client:
        response = client.post('/api/video/runs', headers=_admin_headers(), json={
            'workflow_id': saved['id'], 'target_node_id': 'image',
        })
    assert response.status_code == 202
    assert response.json()['state'] == 'queued'
    assert service.get(response.json()['id']) is not None
    assert len(service.list()) == 1
