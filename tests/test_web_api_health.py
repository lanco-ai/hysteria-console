"""Structured administrator health API contracts."""

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LoginRequired


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
            'line_radar': {
                'window_hours': 24,
                'total_bytes': 100,
                'recommendation': 'default',
                'reason': 'balanced',
                'rows': [
                    {
                        'key': 'hysteria',
                        'label': 'Hysteria UDP',
                        'status': 'active',
                        'ok': True,
                        'bytes': 100,
                        'share': 100.0,
                        'active_users': 1,
                        'online': 1,
                        'profile': 'game',
                        'note': 'test',
                    }
                ],
            },
            'calibration': {
                'window_hours': 72,
                'sample_count': 1,
                'included_sample_count': 1,
                'app_raw_bytes': 100,
                'net_total_bytes': 200,
                'net_tx_bytes': 100,
                'current_multiplier': 1.0,
                'suggested_multiplier': None,
                'egress_multiplier': None,
                'delta_percent': None,
                'confidence': 'low',
                'ifaces': ['eth0'],
                'last_ts': '',
                'method': 'trimmed_weighted_ratio',
                'egress_sample_count': 1,
                'windows': [
                    {
                        'window_hours': 72,
                        'suggested_multiplier': None,
                        'egress_multiplier': None,
                        'app_raw_bytes': 100,
                        'included_sample_count': 1,
                        'sample_count': 1,
                        'confidence': 'low',
                    }
                ],
                'policy': {
                    'enabled': False,
                    'mode': 'total',
                    'min_confidence': 'medium',
                    'max_delta_percent': 25.0,
                    'min_delta_percent': 3.0,
                    'cooldown_hours': 24.0,
                },
            },
            'update': {
                'status': 'idle',
                'reason': '',
                'ts': '',
                'pending': False,
                'version': '',
                'previous_version': '',
            },
            'private_probe': {'token': 'must be removed'},
        }

    def submit_health_operation(self, *, headers, path, form, client_address, action):
        del headers, path, form, client_address
        return {
            'ok': True,
            'status': action,
            'reason': '',
            'ts': '2026-09-15T10:30:00+08:00',
            'pending': action == 'update-apply',
        }


class StubHealthFailureServices:
    def submit_health_operation(self, *, headers, path, form, client_address, action):
        del headers, path, form, client_address, action
        return {
            'ok': False,
            'status': 'error',
            'reason': 'update_busy',
            'ts': '',
            'pending': True,
        }


class StubUnauthorizedHealthServices:
    def submit_health_operation(self, *, headers, path, form, client_address, action):
        del headers, path, form, client_address, action
        raise LoginRequired


def test_health_route_returns_structured_status_and_strips_private_fields():
    with TestClient(create_app(StubHealthServices())) as client:
        response = client.get('/api/v1/admin/health')

    assert response.status_code == 200
    assert set(response.json()) == {'ts', 'kpis', 'services', 'line_radar', 'calibration', 'update'}
    assert response.json()['kpis'][0] == {'title': '整体状态', 'ok': True, 'label': '5/5 正常'}
    assert 'token' not in response.text


def test_health_operation_routes_return_safe_structured_success():
    headers = {'Origin': 'http://testserver'}
    with TestClient(create_app(StubHealthServices())) as client:
        for action in (
            'update-check',
            'update-apply',
            'test-alert',
            'multiplier-apply',
            'multiplier-auto',
        ):
            response = client.post(
                f'/api/v1/admin/health/{action}',
                headers=headers,
                data={},
            )
            assert response.status_code == 200
            assert response.json()['status'] == action
            assert 'private' not in response.text


def test_health_operation_maps_busy_result_to_conflict():
    with TestClient(create_app(StubHealthFailureServices())) as client:
        response = client.post(
            '/api/v1/admin/health/update-apply',
            headers={'Origin': 'http://testserver'},
            data={},
        )
    assert response.status_code == 409
    assert response.json()['reason'] == 'update_busy'


def test_health_operation_requires_admin_session():
    with TestClient(create_app(StubUnauthorizedHealthServices())) as client:
        response = client.post(
            '/api/v1/admin/health/test-alert',
            headers={'Origin': 'http://testserver'},
            data={},
        )
    assert response.status_code == 401
    assert response.json() == {'error': 'login_required'}
