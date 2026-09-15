"""Authenticated administrator user-detail API contracts."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LoginRequired

NOW = datetime(2026, 9, 15, 10, 30, tzinfo=ZoneInfo('Asia/Shanghai'))


class StubServices:
    def __init__(self, payload=None):
        self.payload = payload
        self.requested = None

    def read_admin_user_detail(self, *, headers, path, uid):
        del headers, path
        self.requested = uid
        if uid == 'missing':
            return None
        if uid == 'unauthorized':
            raise LoginRequired
        return self.payload or {
            'ts': NOW.isoformat(timespec='seconds'),
            'uid': uid,
            'metered': True,
            'disabled': False,
            'expired': False,
            'expires_at': None,
            'expiry_label': '长期有效',
            'note': 'safe note',
            'online': 1,
            'max_devices': 2,
            'cycle_used_bytes': 10,
            'cycle_quota_bytes': 100,
            'quota_extra_bytes': 0,
            'current_hour_bytes': 3,
            'today_bytes': 8,
            'recent_alerts': [],
            'hourly_bars': [{'hour': '2026-09-15T10', 'bytes': 3}],
            'heatmap': [{'date': '2026-09-15', 'hours': [0] * 24}],
        }


def test_user_detail_route_returns_safe_chart_payload_and_uid():
    services = StubServices()
    with TestClient(create_app(services)) as client:
        response = client.get('/api/v1/admin/user/alice')

    assert response.status_code == 200
    assert services.requested == 'alice'
    assert response.json()['uid'] == 'alice'
    assert response.json()['heatmap'][0]['hours'] == [0] * 24
    assert 'sub_token' not in response.text


def test_user_detail_route_uses_404_and_401_boundaries():
    with TestClient(create_app(StubServices())) as client:
        assert client.get('/api/v1/admin/user/missing').status_code == 404
        assert client.get('/api/v1/admin/user/unauthorized').status_code == 401
        assert client.post('/api/v1/admin/user/alice').status_code == 405


def test_user_detail_model_rejects_malformed_chart_rows():
    services = StubServices(
        {
            'ts': NOW.isoformat(timespec='seconds'),
            'uid': 'alice',
            'metered': True,
            'disabled': False,
            'expired': False,
            'expires_at': None,
            'expiry_label': '长期有效',
            'note': '',
            'online': 0,
            'max_devices': 0,
            'cycle_used_bytes': 0,
            'cycle_quota_bytes': 0,
            'quota_extra_bytes': 0,
            'current_hour_bytes': 0,
            'today_bytes': 0,
            'recent_alerts': [],
            'hourly_bars': [{'hour': '2026-09-15T10', 'bytes': -1}],
            'heatmap': [{'date': '2026-09-15', 'hours': [0] * 24}],
        }
    )
    with TestClient(create_app(services)) as client:
        response = client.get('/api/v1/admin/user/alice')

    assert response.status_code == 500
    assert response.json() == {'error': 'internal_error'}
