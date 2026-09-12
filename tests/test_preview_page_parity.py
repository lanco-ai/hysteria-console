"""Real HTTP document coverage for the isolated, fictional preview."""

import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import workspace_preview_server as preview

PAGES = [
    ('/', 'href="/login"'),
    ('/admin/health', 'CRON 心跳'),
    ('/admin/incidents', '暂停 1 小时'),
    ('/admin/landing-egresses', '家宽出口'),
    ('/admin/logs', '最近清零记录'),
    ('/admin/user/demo_alex', 'demo_alex'),
    ('/user/change-password', 'id="user-new-password"'),
    ('/logout', 'action="/logout"'),
    ('/user/logout', 'action="/user/logout"'),
]


@pytest.fixture
def base_url():
    with preview.preview_server() as server:
        yield f'http://127.0.0.1:{server.server_port}'


@pytest.mark.parametrize(('route', 'content'), PAGES)
def test_preview_document(base_url, route, content):
    for _ in range(2):
        with urlopen(base_url + route) as response:
            assert response.status == 200
            assert response.headers.get_content_type() == 'text/html'
            body = response.read().decode()
            assert '<html' in body
            assert content in body


@pytest.mark.parametrize('query', ['snapshot=1', 'snapshot=%31'])
def test_preview_health_snapshot(base_url, query):
    with urlopen(base_url + '/admin/health.fragment?' + query) as response:
        assert response.headers.get_content_type() == 'application/json'
        payload = json.load(response)
        assert set(payload) == {'rows', 'kpis', 'update'}
        assert '<tr ' in payload['rows'] and 'CRON 心跳' in payload['rows']
        assert 'health-kpi-card' in payload['kpis']
        assert 'preview-policy-disabled' in payload['update']


@pytest.mark.parametrize(
    'query', ['', '?snapshot=0', '?snapshot=1&snapshot=1', '?other=snapshot=1']
)
def test_preview_health_fragment_defaults_to_html(base_url, query):
    with urlopen(base_url + '/admin/health.fragment' + query) as response:
        assert response.headers.get_content_type() == 'text/html'
        body = response.read().decode()
        assert '<tr ' in body and 'CRON 心跳' in body
        assert '<html' not in body


def test_preview_user_detail_refresh(base_url):
    with urlopen(base_url + '/admin/user/demo_alex.json?summary=1') as response:
        assert response.headers.get_content_type() == 'application/json'
        payload = json.load(response)
        assert payload['uid'] == 'demo_alex'
        assert payload['max_devices'] == 3


@pytest.mark.parametrize('route', ['/missing', '/admin/user/missing', '/static/missing.js'])
def test_missing_resources_remain_missing(base_url, route):
    with pytest.raises(HTTPError) as error:
        urlopen(base_url + route)
    assert error.value.code == 404


@pytest.mark.parametrize(('route', 'content'), PAGES)
def test_preview_rejects_writes(base_url, route, content):
    with pytest.raises(HTTPError) as error:
        urlopen(Request(base_url + route, data=b'', method='POST'))
    assert error.value.code == 405
