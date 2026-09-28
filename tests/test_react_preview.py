import http.client
import json
import os
import socket
import subprocess
import sys
import threading
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

import pytest

from tests import react_preview_server as preview


def running_preview_dist(tmp_path):
    dist = tmp_path / 'dist'
    assets = dist / 'assets'
    assets.mkdir(parents=True)
    (assets / 'index-testhash.js').write_text('document.body.dataset.preview = "ready";')
    (assets / 'index-testhash.css').write_text('body { color: black; }')
    (dist / 'index.html').write_text(
        '<!doctype html><html><head><title>清零日志</title>'
        '<link rel="stylesheet" href="/static/react/assets/index-testhash.css">'
        '<script type="module" src="/static/react/assets/index-testhash.js"></script>'
        '</head><body class="has-shell"><div id="root" data-public-host=""></div></body></html>'
    )
    (dist / 'manifest.json').write_text(
        json.dumps(
            {
                'index.html': {
                    'file': 'assets/index-testhash.js',
                    'css': ['assets/index-testhash.css'],
                    'isEntry': True,
                    'src': 'index.html',
                }
            }
        )
    )
    return dist


@pytest.fixture
def running_preview(tmp_path, monkeypatch):
    dist = running_preview_dist(tmp_path)
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
        assert 'href="/static/react/assets/index-testhash.css"' in page
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
        assert '<title>Hysteria 工作台</title>' in page
        assert '<body class="has-shell page-workbench">' in page
        assert '/static/react/assets/' in page

    with urlopen(base_url + '/', timeout=5) as response:
        assert response.status == 200
        assert '<title>Hysteria 工作台</title>' in response.read().decode()

    _assert_head_matches_get(base_url + '/__react/')


def test_react_preview_serves_exact_login_entry_with_password_limit(running_preview):
    _, base_url = running_preview
    with urlopen(base_url + '/__react/login', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>Hysteria 工作台</title>' in page
        assert '<body class="has-shell page-workbench">' in page
        assert f'data-password-max-length="{preview.legacy_preview.ss.PASSWORD_MAX_LENGTH}"' in page
    _assert_head_matches_get(base_url + '/__react/login')


def test_react_preview_serves_user_login_entry_with_password_limit(running_preview):
    _, base_url = running_preview
    with urlopen(base_url + '/__react/user/login', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>Hysteria 工作台</title>' in page
        assert '<body class="has-shell page-workbench">' in page
        assert f'data-password-max-length="{preview.legacy_preview.ss.PASSWORD_MAX_LENGTH}"' in page
    _assert_head_matches_get(base_url + '/__react/user/login')


def test_react_preview_serves_auth_alias_with_workbench_bootstrap(running_preview):
    _, base_url = running_preview
    with urlopen(base_url + '/__react/auth', timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>Hysteria 工作台</title>' in page
        assert '<body class="has-shell page-workbench">' in page
        assert f'data-password-max-length="{preview.legacy_preview.ss.PASSWORD_MAX_LENGTH}"' in page
    _assert_head_matches_get(base_url + '/__react/auth')
    with urlopen(base_url + '/auth', timeout=5) as response:
        assert '<title>Hysteria 工作台</title>' in response.read().decode()


@pytest.mark.parametrize(
    ('path', 'title', 'body_class'),
    [
        ('/__react/admin/settings', '设置', 'has-shell'),
        ('/__react/user/change-password', '修改面板密码', 'page-auth'),
        ('/__react/admin/config', '模板与路由', 'has-shell'),
        ('/__react/admin/rules', '模板与路由', 'has-shell'),
        ('/__react/admin/landing-egresses', '家宽出口', 'has-shell'),
        ('/__react/user/panel', '用户面板 · Hysteria', ''),
    ],
)
def test_react_preview_serves_exact_password_page_entries(
    running_preview,
    path,
    title,
    body_class,
):
    _, base_url = running_preview
    with urlopen(base_url + path, timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert f'<title>{title}</title>' in page
        assert f'<body class="{body_class}">' in page
        assert 'data-public-host="preview.invalid"' in page
        assert '/static/react/assets/' in page
    _assert_head_matches_get(base_url + path)


@pytest.mark.parametrize('path', ['/__react/logout', '/__react/user/logout'])
def test_react_preview_serves_exact_logout_entries_without_private_reads(
    running_preview,
    path,
):
    _, base_url = running_preview
    with urlopen(base_url + path, timeout=5) as response:
        page = response.read().decode()
        assert response.headers.get_content_type() == 'text/html'
        assert '<title>确认退出</title>' in page
        assert '<body class="">' in page
        assert 'data-public-host="preview.invalid"' in page
        assert '/static/react/assets/' in page
    _assert_head_matches_get(base_url + path)


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


def test_react_preview_allows_exact_form_posts_and_preserves_cookie_isolation(running_preview):
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

    both_current = f'sid={server.preview_admin_cookie}; usid={server.preview_user_cookie}'
    status, headers, logout = _post_form(base_url + '/api/v1/logout', {}, cookie=both_current)
    assert status == 200
    assert logout == {'ok': True, 'redirect_to': '/login'}
    assert headers['Set-Cookie'].startswith('sid=; ')
    assert _json(base_url + '/api/v1/session', cookie=f'usid={server.preview_user_cookie}') == (
        200,
        {'role': 'user', 'username': 'demo_alex'},
    )
    assert _json(
        base_url + '/api/v1/session', cookie=f'sid={server.preview_admin_other_cookie}'
    ) == (200, {'role': 'admin'})

    status, headers, logout = _post_form(
        base_url + '/api/v1/user/logout',
        {},
        cookie=(f'usid={server.preview_user_cookie}; sid={server.preview_admin_other_cookie}'),
    )
    assert status == 200
    assert logout == {'ok': True, 'redirect_to': '/login'}
    assert headers['Set-Cookie'].startswith('usid=; ')
    assert _json(
        base_url + '/api/v1/session', cookie=f'sid={server.preview_admin_other_cookie}'
    ) == (200, {'role': 'admin'})
    assert _json(
        base_url + '/api/v1/session', cookie=f'usid={server.preview_user_other_cookie}'
    ) == (200, {'role': 'user', 'username': 'demo_alex'})

    for path in ('/api/v1/logout', '/api/v1/user/logout'):
        for _ in range(2):
            status, headers, logout = _post_form(base_url + path, {})
            assert status == 200
            assert logout == {'ok': True, 'redirect_to': '/login'}
            assert headers['Set-Cookie'].startswith(
                'sid=; ' if path == '/api/v1/logout' else 'usid=; '
            )

    for route in (
        '/__react/',
        '/__react/auth',
        '/__react/login',
        '/__react/logout',
        '/__react/user/logout',
        '/__react/admin/logs',
        '/__react/admin/settings',
        '/__react/user/change-password',
        '/__react/admin/config',
        '/__react/admin/rules',
        '/__react/admin/landing-egresses',
        '/__react/user/panel',
        '/login',
        '/logout',
        '/user/logout',
        '/admin/change-password',
        '/user/change-password',
        '/admin/reset-usage',
        '/api/v1/missing',
    ):
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base_url + route, data=b'', method='POST'), timeout=5)
        assert error.value.code == 405


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/login',
        '/api/v1/logout',
        '/api/v1/user/logout',
        '/api/v1/admin/change-password',
        '/api/v1/user/change-password',
    ],
)
def test_react_preview_rejects_bad_form_framing_before_service_dispatch(
    running_preview,
    monkeypatch,
    path,
):
    _, base_url = running_preview
    calls = []
    monkeypatch.setattr(
        preview.LegacyPanelServices,
        'submit_login',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        preview.LegacyPanelServices,
        'submit_logout',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    monkeypatch.setattr(
        preview.LegacyPanelServices,
        'submit_password_change',
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    body = b'admin_username=admin&admin_password=preview-only-password'
    connection.putrequest('POST', path)
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


@pytest.mark.parametrize(
    'path',
    [
        '/api/v1/login',
        '/api/v1/logout',
        '/api/v1/user/logout',
        '/api/v1/admin/change-password',
        '/api/v1/user/change-password',
    ],
)
def test_react_preview_rejects_oversized_allowed_form_before_reading_body(
    running_preview,
    path,
):
    _, base_url = running_preview
    parsed = urlsplit(base_url)
    connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
    connection.putrequest('POST', path)
    connection.putheader('Content-Type', 'application/x-www-form-urlencoded')
    connection.putheader('Content-Length', str(preview.http_utils.MAX_FORM_BYTES + 1))
    connection.endheaders()
    response = connection.getresponse()
    assert response.status == 413
    assert response.getheader('Content-Type').startswith('application/json')
    assert json.loads(response.read()) == {'error': 'request_too_large'}
    connection.close()


def test_react_preview_forwards_both_password_posts_to_real_isolated_services(running_preview):
    server, base_url = running_preview
    admin_status, admin_headers, admin_result = _post_form(
        base_url + '/api/v1/admin/change-password',
        {'current': 'wrong', 'new': 'new-password', 'confirm': 'new-password'},
        cookie=f'sid={server.preview_admin_cookie}',
    )
    user_status, user_headers, user_result = _post_form(
        base_url + '/api/v1/user/change-password',
        {'current': 'wrong', 'new': 'short', 'confirm': 'different'},
        cookie=f'usid={server.preview_password_user_cookie}',
    )

    assert (admin_status, admin_result) == (200, {'ok': False, 'code': 'password_wrong'})
    assert (user_status, user_result) == (200, {'ok': False, 'code': 'new password short'})
    assert admin_headers.get('Set-Cookie') is None
    assert user_headers.get('Set-Cookie') is None


def test_each_preview_context_restores_credentials_and_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, 'DIST', running_preview_dist(tmp_path))
    with preview.preview_server() as first:
        first_base = f'http://127.0.0.1:{first.server_port}'
        status, _, result = _post_form(
            first_base + '/api/v1/admin/change-password',
            {
                'current': first.preview_login_password,
                'new': 'changed-only-in-first-context',
                'confirm': 'changed-only-in-first-context',
            },
            cookie=f'sid={first.preview_admin_cookie}',
        )
        assert (status, result) == (
            200,
            {'ok': True, 'redirect_to': '/admin/settings?msg=password+changed'},
        )

    with preview.preview_server() as second:
        second_base = f'http://127.0.0.1:{second.server_port}'
        status, _, result = _post_form(
            second_base + '/api/v1/login',
            {'admin_username': 'admin', 'admin_password': second.preview_login_password},
        )
        assert status == 200
        assert result == {'ok': True, 'redirect_to': '/admin?msg=login+success'}
        assert _json(
            second_base + '/api/v1/user/password',
            cookie=f'usid={second.preview_must_change_cookie}',
        ) == (
            200,
            {
                'username': 'must_change',
                'password_min_length': preview.legacy_preview.ss.PASSWORD_MIN_LENGTH,
                'password_max_length': preview.legacy_preview.ss.PASSWORD_MAX_LENGTH,
            },
        )


def test_react_preview_teardown_closes_partial_header_connections(tmp_path, monkeypatch):
    handler_started = threading.Event()
    original_handler = preview._handler

    def observed_handler(api_client, allowed_assets):
        base = original_handler(api_client, allowed_assets)

        class ObservedHandler(base):
            def setup(self):
                super().setup()
                handler_started.set()

        return ObservedHandler

    monkeypatch.setattr(preview, '_handler', observed_handler)
    monkeypatch.setattr(preview, 'DIST', running_preview_dist(tmp_path))
    manager = preview.preview_server()
    server = manager.__enter__()
    connection = socket.create_connection(server.server_address, timeout=2)
    try:
        connection.settimeout(0.2)
        connection.sendall(b'POST /api/v1/login HTTP/1.1\r\nHost: preview.invalid\r\nX-Stall:')
        assert handler_started.wait(2), 'preview did not accept the partial request'
        manager.__exit__(None, None, None)
        manager = None
        assert connection.recv(1) == b''
    finally:
        connection.close()
        if manager is not None:
            manager.__exit__(None, None, None)


def test_react_preview_teardown_closes_partial_body_before_fixture_restore(
    tmp_path,
    monkeypatch,
):
    body_read_started = threading.Event()
    original_read_request_body = preview._read_request_body

    def observed_read_request_body(*args, **kwargs):
        body_read_started.set()
        return original_read_request_body(*args, **kwargs)

    monkeypatch.setattr(preview, '_read_request_body', observed_read_request_body)
    monkeypatch.setattr(preview, 'DIST', running_preview_dist(tmp_path))
    manager = preview.preview_server()
    server = manager.__enter__()
    connection = socket.create_connection(server.server_address, timeout=2)
    try:
        connection.settimeout(0.2)
        connection.sendall(
            b'POST /api/v1/login HTTP/1.1\r\n'
            b'Host: preview.invalid\r\n'
            b'Content-Type: application/x-www-form-urlencoded\r\n'
            b'Content-Length: 100\r\n\r\n'
            b'admin_username=admin'
        )
        assert body_read_started.wait(2), 'preview did not begin the partial body read'
        manager.__exit__(None, None, None)
        manager = None
        assert connection.recv(1) == b''
    finally:
        connection.close()
        if manager is not None:
            manager.__exit__(None, None, None)


def test_react_preview_teardown_waits_for_active_auth_before_fixture_restore(
    tmp_path,
    monkeypatch,
):
    auth_started = threading.Event()
    release_auth = threading.Event()
    auth_finished = threading.Event()
    fixture_restored = threading.Event()
    teardown_finished = threading.Event()
    shutdown_finished = threading.Event()
    order = []
    original_isolated_preview = preview.legacy_preview.isolated_preview
    original_submit_login = preview.LegacyPanelServices.submit_login

    @contextmanager
    def observed_isolated_preview(directory):
        with original_isolated_preview(directory) as allowed_ports:
            yield allowed_ports
        order.append('fixture-restored')
        fixture_restored.set()

    def gated_submit_login(service, **kwargs):
        auth_started.set()
        assert release_auth.wait(2), 'test did not release active authentication'
        assert not fixture_restored.is_set(), 'fixture restored while authentication was active'
        result = original_submit_login(service, **kwargs)
        order.append('auth-finished')
        auth_finished.set()
        return result

    monkeypatch.setattr(preview.legacy_preview, 'isolated_preview', observed_isolated_preview)
    monkeypatch.setattr(preview.LegacyPanelServices, 'submit_login', gated_submit_login)
    monkeypatch.setattr(preview, 'DIST', running_preview_dist(tmp_path))
    manager = preview.preview_server()
    server = manager.__enter__()
    original_shutdown = server.shutdown

    def observed_shutdown():
        original_shutdown()
        shutdown_finished.set()

    server.shutdown = observed_shutdown
    request_errors = []

    def submit():
        try:
            _post_form(
                f'http://127.0.0.1:{server.server_port}/api/v1/login',
                {'admin_username': 'admin', 'admin_password': server.preview_login_password},
            )
        except Exception as error:  # The teardown may close the response socket.
            request_errors.append(error)

    request_thread = threading.Thread(target=submit)
    request_thread.start()
    teardown_thread = None
    try:
        assert auth_started.wait(2), 'authentication did not reach the gated service'

        def teardown():
            manager.__exit__(None, None, None)
            teardown_finished.set()

        teardown_thread = threading.Thread(target=teardown)
        teardown_thread.start()
        assert shutdown_finished.wait(2), 'preview serving loop did not stop'
        assert not teardown_finished.wait(0.1), 'teardown escaped while authentication was active'
        release_auth.set()
        assert auth_finished.wait(2), 'authentication did not finish after release'
        assert teardown_finished.wait(2), 'preview teardown did not finish after authentication'
    finally:
        release_auth.set()
        if teardown_thread is None:
            manager.__exit__(None, None, None)
        else:
            teardown_thread.join(timeout=2)
            assert not teardown_thread.is_alive(), 'controlled preview teardown did not stop'
        request_thread.join(timeout=2)
        assert not request_thread.is_alive(), 'controlled authentication request did not stop'
    assert order == ['auth-finished', 'fixture-restored']


def test_form_body_receipt_uses_one_absolute_deadline():
    now = [0.0]

    class TrickledBody:
        reads = 0

        def read1(self, _limit):
            self.reads += 1
            now[0] += 0.04
            return b'x'

    class Connection:
        def __init__(self):
            self.timeouts = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def gettimeout(self):
            return None

    body = TrickledBody()
    connection = Connection()

    with pytest.raises(socket.timeout):
        preview._read_request_body(
            body,
            connection,
            10,
            timeout=0.1,
            monotonic=lambda: now[0],
        )

    assert body.reads == 3
    assert connection.timeouts == pytest.approx([0.1, 0.06, 0.02, None])


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


def test_react_overview_entry_and_scoped_account_operations(running_preview):
    server, base_url = running_preview
    cookie = f'sid={server.preview_admin_cookie}'
    with urlopen(base_url + '/__react/admin', timeout=5) as response:
        page = response.read().decode()
    assert '<title>总览</title>' in page and 'admin-poll.js' not in page
    assert _json(base_url + '/api/v1/admin/reload-status', cookie=cookie) == (
        200,
        {'pending': False, 'xray': False, 'tuic': False},
    )
    status, _, result = _post_form(
        base_url + '/api/v1/admin/users/create',
        {'user': 'overview_new', 'quota_gb': '150', 'quota_extra_gb': '0', 'guest': 'on'},
        cookie=cookie,
    )
    assert (status, result) == (200, {'ok': True, 'outcome': 'created', 'user': 'overview_new'})
    _, data = _json(base_url + '/api/v1/admin/overview-page', cookie=cookie)
    row = next(row for row in data['users'] if row['user'] == 'overview_new')
    status, _, result = _post_form(
        base_url + '/api/v1/admin/operations/rotate-token',
        {'user': 'overview_new', 'user_revision': row['revision']},
        cookie=cookie,
    )
    assert (status, result) == (
        200,
        {
            'ok': True,
            'action': 'rotate-token',
            'user': 'overview_new',
            'code': 'rotated',
        },
    )
    service = preview.legacy_preview.ss
    queue_path = service._revocation_queue_path()
    queue = service.load_json(queue_path, {})
    assert len(queue) == 1
    assert next(iter(queue.values()))['user'] == 'overview_new'
    assert str(queue_path).startswith('/tmp/hy2-react-fixture-')
    for url in ('/api/v1/admin/users/other', '/api/v1/admin/operations/other', '/admin/delete'):
        request = Request(base_url + url, data=b'user=overview_new', method='POST')
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=5)
        assert error.value.code == 405


def test_react_overview_fixture_nonzero_accounting_and_guards(tmp_path, monkeypatch):
    monkeypatch.setattr(preview, 'DIST', running_preview_dist(tmp_path))
    with preview.preview_server(overview_fixture=True) as server:
        base_url = f'http://127.0.0.1:{server.server_port}'
        cookie = f'sid={server.preview_admin_cookie}'
        _, before = _json(base_url + '/api/v1/admin/overview-page', cookie=cookie)
        row = next(row for row in before['users'] if row['user'] == 'demo_alex')
        assert row['used'] > 0 and before['cycle']['total_used'] > row['used']
        status, _, result = _post_form(
            base_url + '/api/v1/admin/operations/refresh-usage',
            {'user': row['user'], 'user_revision': row['revision']},
            cookie=cookie,
        )
        assert status == 200 and result['action'] == 'refresh-usage'
        _, after = _json(base_url + '/api/v1/admin/overview-page', cookie=cookie)
        assert after['cycle']['total_used'] == before['cycle']['total_used']
        assert next(row for row in after['users'] if row['user'] == 'demo_alex')['used'] == 0
        with pytest.raises(PermissionError, match='production path'):
            open('/root/hysteria/state/users.json')
        with pytest.raises(PermissionError, match='external connection'):
            socket.create_connection(('192.0.2.1', 443), timeout=0.1)
        with pytest.raises(PermissionError, match='service command'):
            subprocess.run(['systemctl', 'reload', 'hysteria'], check=True)


def test_react_browser_selector_rejects_unknown_file():
    result = subprocess.run(
        [sys.executable, str(preview.ROOT / 'tests' / 'run_react_browser.py')],
        cwd=preview.ROOT,
        env={**os.environ, 'REACT_BROWSER_TEST': 'missing-browser-test.cjs'},
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0
    assert 'unknown REACT_BROWSER_TEST' in result.stderr
