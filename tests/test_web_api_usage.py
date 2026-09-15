"""Authenticated structured usage analytics API contracts."""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from web_api import create_app

NOW = datetime(2026, 9, 15, 10, 30, tzinfo=ZoneInfo('Asia/Shanghai'))


def _payload(*, charts=True):
    payload = {
        'ts': NOW.isoformat(timespec='seconds'),
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
        'private': 'must be removed',
    }
    if charts:
        payload.update(
            {
                'hourly_totals': [{'hour': '2026-09-15T10', 'bytes': 9}],
                'heatmap': [{'date': '2026-09-15', 'hours': [0] * 24}],
                'top_n': [{'uid': 'alice', 'last_24h_bytes': 9, 'spark': [0, 9]}],
            }
        )
    return payload


class StubServices:
    def __init__(self):
        self.include_charts = None

    def read_admin_usage(self, *, headers, path, include_charts):
        del headers, path
        self.include_charts = include_charts
        return _payload(charts=include_charts)

    def read_admin_usage_history(self, *, headers, path):
        del headers, path
        return {
            'ts': NOW.isoformat(timespec='seconds'),
            'retention_days': 2,
            'dates': ['2026-09-14', '2026-09-15'],
            'users': [{'uid': 'alice', 'values': [1, 2]}],
            'totals': [1, 2],
        }


def test_usage_route_returns_explicit_full_schema_and_strips_private_fields():
    services = StubServices()
    with TestClient(create_app(services)) as client:
        response = client.get('/api/v1/admin/usage')

    assert response.status_code == 200
    assert services.include_charts is True
    assert set(response.json()) == {'ts', 'stats', 'hourly_totals', 'heatmap', 'top_n'}
    assert 'private' not in response.text
    assert response.json()['heatmap'][0]['hours'] == [0] * 24


def test_usage_summary_query_omits_chart_arrays():
    services = StubServices()
    with TestClient(create_app(services)) as client:
        response = client.get('/api/v1/admin/usage?summary=yes')

    assert response.status_code == 200
    assert services.include_charts is False
    assert set(response.json()) == {'ts', 'stats'}


def test_usage_history_route_returns_structured_daily_rows():
    with TestClient(create_app(StubServices())) as client:
        response = client.get('/api/v1/admin/usage-history')

    assert response.status_code == 200
    assert set(response.json()) == {'ts', 'retention_days', 'dates', 'users', 'totals'}
    assert response.json()['users'][0] == {'uid': 'alice', 'values': [1, 2]}
