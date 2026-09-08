"""HTTP contracts for the extracted read-only endpoints, using isolated state."""

import importlib
import json
from datetime import datetime

import pytest
import subscription_service as ss

from tests.test_reliability_regressions import _configure_state, _request, _running_server

NOW = datetime.fromisoformat('2026-09-08T12:00:00+08:00')
CASES = [
    ('/admin/usage', 302, b'usage page'),
    ('/admin/analytics.json', 401, b'{"charts":true}'),
    ('/admin/analytics.json?summary=yes', 401, b'{"charts":false}'),
    ('/admin/usage-history', 401, b'history'),
    ('/admin/usage.json', 401, b'{"bytes":123}'),
    ('/admin/usage.csv', 302, b'window,cycle\n'),
    ('/admin/health', 302, b'health page'),
    ('/admin/health.fragment', 401, b'health rows'),
    ('/admin/health.fragment?snapshot=1', 401, None),
]


@pytest.fixture
def read_state(tmp_path, monkeypatch):
    paths = _configure_state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, 'local_now', lambda: NOW)
    calls = []

    def result(label, value):
        def build(*args, **kwargs):
            calls.append(label)
            return value

        return build

    monkeypatch.setattr(ss, 'render_usage_page', result('usage', 'usage page'))
    monkeypatch.setattr(ss, 'render_health', result('health', 'health page'))
    monkeypatch.setattr(ss, '_render_daily_table_collapsed', result('history', 'history'))
    monkeypatch.setattr(ss, '_build_usage_json_payload', result('usage-data', {'bytes': 123}))
    monkeypatch.setattr(ss, 'render_health_fragment', result('rows', 'health rows'))
    monkeypatch.setattr(ss, '_render_health_top_kpis', result('kpis', {'整体状态': {}}))
    monkeypatch.setattr(ss, '_health_top_kpi_card', result('card', 'health kpis'))
    monkeypatch.setattr(ss.hysteria_update, 'render_history', result('update', 'update history'))

    def analytics(*, now, include_charts):
        assert now == NOW
        calls.append('analytics')
        return {'charts': include_charts}

    def csv(*, now, window):
        assert now == NOW
        calls.append('csv')
        return f'window,{window}\n'

    monkeypatch.setattr(ss, '_build_analytics_json_payload', analytics)
    monkeypatch.setattr(ss, '_build_usage_csv', csv)
    cookie = {'Cookie': f'sid={ss.create_session()}'}
    before = {path: path.read_bytes() for path in paths.values() if path.is_file()}
    return cookie, calls, before


@pytest.mark.parametrize('path,denied,expected', CASES)
@pytest.mark.parametrize('method', ['GET', 'HEAD'])
@pytest.mark.parametrize('authenticated', [False, True])
def test_read_http_contract(read_state, path, denied, expected, method, authenticated):
    cookie, calls, before = read_state
    with _running_server() as server:
        response = _request(server, method, path, headers=cookie if authenticated else {})
    assert response.status == (200 if authenticated else denied)
    assert 'set-cookie' not in response.headers
    assert response.headers['x-content-type-options'] == 'nosniff'
    if not authenticated:
        assert calls == [], 'Do not read protected data before authorization'
        if denied == 302:
            assert response.headers['location'] == '/login'
        else:
            assert 'location' not in response.headers
            if method == 'GET':
                if path.startswith('/admin/health.fragment'):
                    assert (
                        response.body == '<div class="err" role="alert">登录已失效</div>'.encode()
                    )
                elif path == '/admin/usage-history':
                    assert (
                        response.body
                        == '<div class="err" role="alert">登录已失效，请重新登录</div>'.encode()
                    )
                else:
                    assert json.loads(response.body) == {'error': 'login_required'}
    else:
        assert calls
        assert 'no-store' in response.headers['cache-control']
    if method == 'HEAD':
        assert response.body == b''
    elif authenticated:
        if expected is not None:
            assert response.body == expected
        else:
            assert json.loads(response.body) == {
                'rows': 'health rows',
                'kpis': 'health kpis',
                'update': 'update history',
            }
    for file, contents in before.items():
        assert file.read_bytes() == contents, f'Read route mutated {file.name}'


@pytest.mark.parametrize('method', ['GET', 'HEAD'])
def test_csv_validation_and_download_header(read_state, method):
    cookie, calls, _ = read_state
    with _running_server() as server:
        invalid = _request(server, method, '/admin/usage.csv?window=invalid', headers=cookie)
        assert invalid.status == 400
        assert calls == []
        valid = _request(server, method, '/admin/usage.csv?window=30d', headers=cookie)
    assert valid.status == 200
    assert valid.headers['content-disposition'] == 'attachment; filename="usage-30d-20260908.csv"'
    assert valid.body == (b'' if method == 'HEAD' else b'window,30d\n')


def test_unrelated_routes_are_not_consumed_or_authenticated():
    routes = importlib.import_module('admin_read_routes')
    # No methods exist: an unknown route must not touch the handler/context.
    for path in ('/login', '/admin/delete', '/user/panel', '/admin/not-found'):
        assert (
            routes.handle_read(
                object(),
                object(),
                path=path,
                query={},
                host='test',
                send_payload=True,
            )
            is False
        )


def test_admin_bearer_exchange_remains_before_read_dispatch(read_state):
    _, calls, _ = read_state
    with _running_server() as server:
        response = _request(server, 'GET', '/admin/health?token=admin-token')
    assert response.status == 303
    assert response.headers['location'] == '/admin/health'
    assert response.headers['set-cookie'].startswith('sid=')
    assert calls == [], 'Exchange credentials before rendering a protected page'
