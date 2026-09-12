import http.client
import json
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
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
        '<!doctype html><html><head><title>清零日志</title>'
        '<link rel="stylesheet" href="/static/style.css">'
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


def _post_form(url, fields, *, cookie=''):
    body = urlencode(fields).encode()
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    if cookie:
        headers['Cookie'] = cookie
    request = Request(url, data=body, headers=headers, method='POST')
    try:
        response = urlopen(request, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, json.load(response)


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
        assert '<title>清零日志</title>' in page
        assert '<body class="has-shell">' in page
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


def test_react_preview_serves_exact_public_entry_with_public_document_shell(running_preview):
    _, base_url = running_preview
    with urlopen(base_url + '/__react/', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>Hysteria · 连接网络，掌控全局</title>' in page
        assert '<body class="page-home page-site">' in page
        assert '/static/react/assets/' in page

    with urlopen(base_url + '/', timeout=5) as response:
        assert response.status == 200
        assert '界面示意 · 非实时数据' in response.read().decode()

    _assert_head_matches_get(base_url + '/__react/')


def test_react_preview_serves_exact_login_entry_with_password_limit(running_preview):
    _, base_url = running_preview
    with urlopen(base_url + '/__react/login', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>管理员登录 · Hysteria</title>' in page
        assert '<body class="page-auth page-admin-login">' in page
        assert f'data-password-max-length="{preview.legacy_preview.ss.PASSWORD_MAX_LENGTH}"' in page
    _assert_head_matches_get(base_url + '/__react/login')


def test_react_preview_rejects_unknown_react_api_asset_and_retired_routes(running_preview):
    _, base_url = running_preview
    for route in (
        '/__react/missing',
        '/__react/admin/missing',
        '/__react/admin/codex',
        '/admin/codex',
        '/admin/codex.json',
        '/static/react/manifest.json',
        '/static/react/assets/missing.js',
        '/api/v1/missing',
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(base_url + route, timeout=5)
        assert error.value.code == 404
        with pytest.raises(HTTPError) as head_error:
            urlopen(Request(base_url + route, method='HEAD'), timeout=5)
        assert head_error.value.code == 404
        assert head_error.value.read() == b''


def test_react_preview_allows_only_login_post_and_preserves_cookie_isolation(running_preview):
    server, base_url = running_preview
    status, headers, failure = _post_form(
        base_url + '/api/v1/login',
        {'admin_username': 'admin', 'admin_password': 'wrong-fixture-password'},
    )
    assert status == 200
    assert failure == {'ok': False, 'message': '用户名或密码错误'}
    assert headers.get('Set-Cookie') is None

    status, headers, success = _post_form(
        base_url + '/api/v1/login',
        {'admin_username': 'admin', 'admin_password': server.preview_login_password},
    )
    assert status == 200
    assert success == {'ok': True, 'redirect_to': '/admin?msg=login+success'}
    cookie = headers['Set-Cookie'].split(';', 1)[0]
    assert cookie.startswith('sid=')
    assert 'HttpOnly' in headers['Set-Cookie']

    with pytest.raises(HTTPError) as anonymous:
        _json(base_url + '/api/v1/session')
    assert anonymous.value.code == 401
    assert _json(base_url + '/api/v1/session', cookie=cookie) == (200, {'role': 'admin'})

    for route in (
        '/__react/',
        '/__react/login',
        '/__react/admin/logs',
        '/login',
        '/logout',
        '/admin/reset-usage',
        '/api/v1/missing',
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base_url + route, data=b'', method='POST'), timeout=5)
        assert error.value.code == 405


def test_react_preview_rejects_bad_login_framing_before_authentication(
    running_preview,
    monkeypatch,
):
    _, base_url = running_preview
    calls = []
    monkeypatch.setattr(
        preview.legacy_preview.ss,
        'verify_secret',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = b'admin_username=admin&admin_password=preview-only-password'
    connection.putrequest('POST', '/api/v1/login')
    connection.putheader('Content-Type', 'application/x-www-form-urlencoded')
    connection.putheader('Content-Length', str(len(body)))
    connection.putheader('Content-Length', str(len(body)))
    connection.endheaders(body)
    response = connection.getresponse()
    assert response.status == 400
    assert response.getheader('Content-Type').startswith('application/json')
    assert json.loads(response.read()) == {'error': 'bad_request'}
    connection.close()
    assert calls == []


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
