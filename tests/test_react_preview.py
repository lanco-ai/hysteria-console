import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests import react_preview_server as preview


@pytest.fixture
def running_preview(tmp_path, monkeypatch):
    dist = tmp_path / 'dist'
    assets = dist / 'assets'
    assets.mkdir(parents=True)
    (assets / 'index-testhash.js').write_text('document.body.dataset.preview = "ready";')
    (dist / 'index.html').write_text(
        '<!doctype html><html><head><link rel="stylesheet" href="/static/style.css">'
        '<script type="module" src="/static/react/assets/index-testhash.js"></script>'
        '</head><body class="has-shell"><div id="root" data-public-host=""></div></body></html>'
    )
    (dist / 'manifest.json').write_text(
        json.dumps(
            {
                'index.html': {
                    'file': 'assets/index-testhash.js',
                    'isEntry': True,
                    'src': 'index.html',
                }
            }
        )
    )
    monkeypatch.setattr(preview, 'DIST', dist)
    with preview.preview_server() as server:
        yield server, f'http://127.0.0.1:{server.server_port}'


def _json(url, *, cookie=''):
    request = Request(url, headers={'Cookie': cookie} if cookie else {})
    with urlopen(request, timeout=5) as response:
        assert response.headers.get_content_type() == 'application/json'
        return response.status, json.load(response)


def _assert_head_matches_get(url, *, cookie=''):
    headers = {'Cookie': cookie} if cookie else {}
    with urlopen(Request(url, headers=headers), timeout=5) as response:
        payload = response.read()
        assert int(response.headers['Content-Length']) == len(payload)
    with urlopen(Request(url, headers=headers, method='HEAD'), timeout=5) as response:
        assert response.read() == b''
        assert int(response.headers['Content-Length']) == len(payload)


def test_react_preview_serves_built_entry_real_api_and_legacy_page(running_preview):
    server, base_url = running_preview
    with urlopen(base_url + '/__react/admin/logs', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert 'data-public-host="preview.invalid"' in page
        assert 'href="/static/style.css"' in page
        assert '/static/react/assets/' in page
        assert '/static/shell.js' not in page

    head = Request(base_url + '/__react/admin/logs', method='HEAD')
    with urlopen(head, timeout=5) as response:
        assert response.status == 200
        assert response.read() == b''
        assert int(response.headers['Content-Length']) == len(page.encode())

    status, session = _json(
        base_url + '/api/v1/session', cookie=f'sid={server.preview_admin_cookie}'
    )
    assert status == 200
    assert session == {'role': 'admin'}
    status, logs = _json(
        base_url + '/api/v1/admin/logs', cookie=f'sid={server.preview_admin_cookie}'
    )
    assert status == 200
    assert logs['limit'] == 300
    assert len(logs['rows']) == 1
    assert set(logs['rows'][0]) == {'time', 'actor', 'ip', 'action', 'target', 'month', 'detail'}

    with urlopen(base_url + '/admin/logs', timeout=5) as response:
        assert '最近清零记录' in response.read().decode()


def test_react_preview_preserves_authentication_and_asset_boundaries(running_preview):
    server, base_url = running_preview
    for cookie in ('', f'usid={server.preview_user_cookie}'):
        request = Request(
            base_url + '/api/v1/admin/logs',
            headers={'Cookie': cookie} if cookie else {},
        )
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 401

    with urlopen(base_url + '/__react/admin/logs', timeout=5) as response:
        page = response.read().decode()
    asset = page.split('src="/static/react/', 1)[1].split('"', 1)[0]
    with urlopen(base_url + '/static/react/' + asset, timeout=5) as response:
        assert response.status == 200
        assert response.headers.get_content_type() == 'text/javascript'
    _assert_head_matches_get(base_url + '/static/react/' + asset)
    _assert_head_matches_get(
        base_url + '/api/v1/session', cookie=f'sid={server.preview_admin_cookie}'
    )

    for route in (
        '/static/react/manifest.json',
        '/static/react/assets/missing.js',
        '/api/v1/missing',
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(base_url + route, timeout=5)
        assert error.value.code == 404


def test_react_preview_escapes_the_public_host_bootstrap(running_preview, monkeypatch):
    _, base_url = running_preview
    monkeypatch.setattr(preview, 'PUBLIC_HOST', '"><script>alert(1)</script>')
    with urlopen(base_url + '/__react/admin/logs', timeout=5) as response:
        page = response.read().decode()
    assert 'data-public-host="&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"' in page
    assert '<script>alert(1)</script>' not in page
