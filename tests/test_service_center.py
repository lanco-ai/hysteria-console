import pytest
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LoginRequired
from web_api.service_center import ServiceCenterStore


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def test_bookmarks_persist_with_revision_and_no_reseed_after_delete(tmp_path):
    store = ServiceCenterStore(tmp_path / 'bookmarks.json')
    original = store.read()
    assert len(original['items']) == 2
    saved = store.replace([], original['revision'])
    assert ServiceCenterStore(store.path).read() == saved
    assert saved['items'] == []
    with pytest.raises(ValueError, match='conflict'):
        store.replace(original['items'], original['revision'])
    assert store.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///root/.env', 'https://admin:password@example.com/', 'data:text/html,test'])
def test_reject_unsafe_links(tmp_path, url):
    store = ServiceCenterStore(tmp_path / 'bookmarks.json')
    data = store.read()
    data['items'][0]['url'] = url
    with pytest.raises(ValueError):
        store.replace(data['items'], data['revision'])


def test_admin_and_origin_guards_and_conflicts(tmp_path):
    store = ServiceCenterStore(tmp_path / 'bookmarks.json')
    with TestClient(create_app(Sessions(), service_center_store=store)) as client:
        endpoint = '/api/v1/admin/services'
        assert client.get(endpoint).status_code == 401
        assert client.get(endpoint, headers={'Cookie': 'sid=user'}).status_code == 403
        data = client.get(endpoint, headers=HEADERS).json()
        assert client.put(endpoint, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json=data).status_code == 403
        data['items'][0]['name'] = 'My API'
        assert client.put(endpoint, headers=HEADERS, json=data).status_code == 200
        assert client.put(endpoint, headers=HEADERS, json=data).status_code == 409
        assert client.get(endpoint, headers=HEADERS).json()['items'][0]['name'] == 'My API'
        assert client.put(endpoint, headers=HEADERS, content='x' * 70000).status_code == 413


def test_corrupt_storage_fails_without_overwriting(tmp_path):
    store = ServiceCenterStore(tmp_path / 'bookmarks.json')
    store.path.write_text('broken')
    with TestClient(create_app(Sessions(), service_center_store=store)) as client:
        assert client.get('/api/v1/admin/services', headers=HEADERS).status_code == 503
    assert store.path.read_text() == 'broken'


def test_service_document_requires_admin(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (tmp_path / 'index.html').write_text('<title>Panel</title><body><div id="root" data-public-host=""></div></body>')
    with TestClient(create_app(Sessions(), react_dist=tmp_path,
                               service_center_store=ServiceCenterStore(tmp_path / 'services.json'))) as client:
        for headers in ({}, {'Cookie': 'sid=user'}):
            response = client.get('/admin/services', headers=headers, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers['location'] == '/login?next=%2Fadmin%2Fservices'
        response = client.get('/admin/services', headers=HEADERS)
        assert response.status_code == 200
        assert '<title>服务中心</title>' in response.text
