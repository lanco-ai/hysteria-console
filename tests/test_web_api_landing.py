"""Structured administrator residential-egress API contracts."""

from fastapi.testclient import TestClient

from web_api import create_app


class StubLandingServices:
    def read_admin_landing(self, *, headers, path):
        del headers, path
        return {
            'ts': '2026-09-15T10:30:00+08:00',
            'revision': 'a' * 64,
            'nodes': [
                {
                    'id': 'home-a',
                    'name': '家庭出口 A',
                    'exit_ip': '203.0.113.10',
                    'isp': 'Example ISP',
                    'region': '上海',
                    'enabled': True,
                    'health': {'status': 'healthy'},
                    'socks_password': 'must-not-leak',
                },
            ],
            'users': [
                {'user': 'alice', 'revision': 'b' * 64, 'allowed_ids': ['home-a']},
            ],
            'private': 'must-not-leak',
        }


def test_landing_route_returns_public_nodes_and_strips_credentials():
    with TestClient(create_app(StubLandingServices())) as client:
        response = client.get('/api/v1/admin/landing-egresses')

    assert response.status_code == 200
    assert set(response.json()) == {'ts', 'revision', 'nodes', 'users'}
    assert response.json()['nodes'][0] == {
        'id': 'home-a',
        'name': '家庭出口 A',
        'exit_ip': '203.0.113.10',
        'isp': 'Example ISP',
        'region': '上海',
        'enabled': True,
        'health': {'status': 'healthy'},
    }
    assert 'must-not-leak' not in response.text

