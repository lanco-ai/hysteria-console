"""Structured administrator incident-console API contracts."""

from fastapi.testclient import TestClient
from web_api import create_app


def _incident_payload():
    return {
        'ts': '2026-09-15T10:30:00+08:00',
        'stats': {
            'current_hour_bytes': 1,
            'today_bytes': 2,
            'yesterday_bytes': 3,
            'last_7d_bytes': 4,
            'cycle_bytes': 5,
            'cycle_day': 6,
            'cycle_total_days': 30,
            'online': 1,
        },
        'peak_hour': {'hour': '2026-09-15T10', 'bytes': 9, 'users': []},
        'users': [],
        'line_radar': {
            'window_hours': 24,
            'total_bytes': 0,
            'recommendation': 'default',
            'reason': 'ok',
            'rows': [],
        },
        'cost_calibration': {'confidence': 'none'},
        'alerts': [],
        'private': 'must be removed',
    }


class StubIncidentServices:
    def build_incident_payload(self, *, now):
        del now
        return _incident_payload()

    def read_admin_incidents(self, *, headers, path):
        del headers, path
        return self.build_incident_payload(now=None)


def test_incidents_route_returns_structured_payload_and_strips_private_fields():
    with TestClient(create_app(StubIncidentServices())) as client:
        response = client.get('/api/v1/admin/incidents')

    assert response.status_code == 200
    assert set(response.json()) == {
        'ts',
        'stats',
        'peak_hour',
        'users',
        'line_radar',
        'cost_calibration',
        'alerts',
    }
    assert 'must be removed' not in response.text
