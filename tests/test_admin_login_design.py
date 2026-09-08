import http.client
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import subscription_service as ss


def test_login_has_only_admin_form_and_escapes_errors():
    page = ss.render_login('panel.test', msg='<bad>', username='a" autofocus="x')
    assert page.count('<form ') == 1
    assert 'name="admin_username"' in page
    assert 'name="admin_password"' in page
    assert 'form-user' not in page
    assert 'auth-tabs' not in page
    assert '管理员与用户共用' not in page
    assert '&lt;bad&gt;' in page
    assert 'a&quot; autofocus=&quot;x' in page
    assert 'autocomplete="current-password"' in page
    assert 'aria-controls="admin-password"' in page


def test_admin_login_post_still_authenticates_and_rejects_wrong_password(monkeypatch):
    monkeypatch.setattr(ss, 'load_meta', lambda: {'admin_user': 'admin', 'admin_pass_hash': 'fixture-hash'})
    monkeypatch.setattr(ss, 'verify_secret', lambda password, _hash: password == 'correct')
    monkeypatch.setattr(ss, '_begin_login_attempt', lambda *a: True)
    monkeypatch.setattr(ss, '_finish_login_attempt', lambda *a: None)
    monkeypatch.setattr(ss, 'create_session', lambda *a: 'test-session')
    server = ThreadingHTTPServer(('127.0.0.1', 0), ss.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        for password, expected in [('wrong', 200), ('correct', 302)]:
            conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            conn.request('POST', '/login', urlencode({'admin_username': 'admin', 'admin_password': password}), {'Content-Type': 'application/x-www-form-urlencoded', 'Host': 'panel.test'})
            response = conn.getresponse()
            body = response.read().decode()
            assert response.status == expected
            if expected == 302:
                assert response.getheader('Location').startswith('/admin')
                assert 'test-session' in response.getheader('Set-Cookie')
            else:
                assert '用户名或密码错误' in body
                assert 'value="wrong"' not in body
                assert 'form-user' not in body
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
