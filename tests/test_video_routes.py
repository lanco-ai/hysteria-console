import json

from fastapi.testclient import TestClient

import web_api.video_routes as video_routes
from web_api import create_app
from web_api.video_provider import ProviderError
from web_api.video_service import VideoSettingsStore
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

    def get(self, run_id):
        return self.run if run_id == self.run['id'] else None

    def list(self):
        return [self.run]

    def tick(self, run_id):
        return self.get(run_id)


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


def test_run_responses_replace_provider_media_urls_with_same_origin_links(tmp_path):
    settings = VideoSettingsStore(tmp_path / 'settings.json')
    settings.update(base_url='http://provider.test/v1', api_key='secret')
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


def test_generated_media_proxy_requires_admin_and_streams_same_origin_content(tmp_path):
    settings = VideoSettingsStore(tmp_path / 'settings.json')
    settings.update(base_url='http://provider.test/v1', api_key='secret')
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
    settings.update(base_url='http://provider.test/v1', api_key='secret')

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
