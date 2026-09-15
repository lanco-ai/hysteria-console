"""Structured user-panel bootstrap API contracts."""

from fastapi.testclient import TestClient
from web_api import create_app


class StubUserPanelServices:
    def read_user_panel(self, *, headers, path):
        del headers, path
        return {
            'ts': '2026-09-15T10:30:00+08:00',
            'username': 'alice',
            'revision': 'a' * 64,
            'used_bytes': 10,
            'total_bytes': 100,
            'remain_bytes': 90,
            'tx_bytes': 4,
            'rx_bytes': 6,
            'online': 1,
            'max_devices': 2,
            'percent': 10.0,
            'cycle_reset_date': '2026-10-01',
            'cycle_days_left': 16,
            'cycle_length_days': 30,
            'disabled': False,
            'expired': False,
            'expiry_label': '长期有效',
            'can_change_password': True,
            'can_select_egress': True,
            'subscription_profiles': [
                {
                    'key': 'default',
                    'label': '默认',
                    'description': '全部节点',
                    'url': 'https://example.invalid/sub/alice?token=redacted',
                    'qr_path': '/panel/alice/qr.svg?token=redacted',
                },
            ],
            'landing_nodes': [],
        }


def test_user_panel_route_returns_structured_data():
    with TestClient(create_app(StubUserPanelServices())) as client:
        response = client.get('/api/v1/user/panel')

    assert response.status_code == 200
    assert response.json()['username'] == 'alice'
    assert response.json()['subscription_profiles'][0]['key'] == 'default'
    assert set(response.json()) == {
        'ts',
        'username',
        'revision',
        'used_bytes',
        'total_bytes',
        'remain_bytes',
        'tx_bytes',
        'rx_bytes',
        'online',
        'max_devices',
        'percent',
        'cycle_reset_date',
        'cycle_days_left',
        'cycle_length_days',
        'disabled',
        'expired',
        'expiry_label',
        'can_change_password',
        'can_select_egress',
        'subscription_profiles',
        'landing_nodes',
    }
