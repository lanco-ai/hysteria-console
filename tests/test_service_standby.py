import pytest
from fastapi.testclient import TestClient
from standby import create_standby_app, standby_enabled


def test_standby_serves_only_health_and_assets(tmp_path):
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets/app.js').write_text('const ok = true;')
    with TestClient(create_standby_app(tmp_path, '/static/test')) as client:
        assert client.get('/healthz').json() == {'ok': True, 'standby': True}
        assert client.head('/healthz').content == b''
        assert client.get('/static/test/assets/app.js').status_code == 200
        for path in (
            '/',
            '/api/v1/github-trending',
            '/api/video/runs/existing',
            '/api/chat/workspaces',
            '/api/v1/shop/catalog',
            '/api/v1/shop/admin',
            '/internal/v1/agent-intent',
        ):
            for method in ('GET', 'POST', 'PUT', 'DELETE'):
                response = client.request(method, path, headers={'Cookie': 'sid=anything'})
                assert response.status_code == 503
                assert response.json() == {'error': 'service_standby'}
                assert response.headers['cache-control'] == 'no-store'
    assert sorted(p.name for p in tmp_path.iterdir()) == ['assets']


def test_standby_is_independent_of_scheduler_after_cutover():
    assert standby_enabled({}, 'WORKBENCH_SCHEDULERS')
    assert standby_enabled(
        {'WORKBENCH_SCHEDULERS': '1', 'LANCOAI_STANDBY': '1'}, 'WORKBENCH_SCHEDULERS'
    )
    assert not standby_enabled({'LANCOAI_STANDBY': '0'}, 'WORKBENCH_SCHEDULERS')
    with pytest.raises(ValueError):
        standby_enabled({'LANCOAI_STANDBY': 'typo'}, 'WORKBENCH_SCHEDULERS')
