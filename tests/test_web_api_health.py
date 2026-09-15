"""Structured administrator health API contracts."""

from fastapi.testclient import TestClient

from web_api import create_app


class StubHealthServices:
    def read_admin_health(self, *, headers, path):
        del headers, path
        return {
            'ts': '2026-09-15T10:30:00+08:00',
            'kpis': [
                {'title': '整体状态', 'ok': True, 'label': '5/5 正常'},
            ],
            'services': [
                {'title': 'Hysteria', 'ok': True, 'label': 'active'},
            ],
            'private_probe': {'token': 'must be removed'},
        }


def test_health_route_returns_structured_status_and_strips_private_fields():
    with TestClient(create_app(StubHealthServices())) as client:
        response = client.get('/api/v1/admin/health')

    assert response.status_code == 200
    assert set(response.json()) == {'ts', 'kpis', 'services'}
    assert response.json()['kpis'][0] == {
        'title': '整体状态', 'ok': True, 'label': '5/5 正常'
    }
    assert 'token' not in response.text

