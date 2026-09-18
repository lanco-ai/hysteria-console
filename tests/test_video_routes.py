import json

from fastapi.testclient import TestClient

import web_api.video_routes as video_routes
from web_api import create_app
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
