"""Exact React document/asset boundary contracts."""

from pathlib import Path

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LoginRequired


class _Module:
    PASSWORD_MAX_LENGTH = 128

    @staticmethod
    def configured_public_host(_raw):
        return 'panel.example.test'


class StubDocumentServices:
    service_module = _Module()

    @staticmethod
    def _cookie(headers):
        return headers.get('cookie', '')

    def read_session(self, *, headers, path):
        del path
        cookie = self._cookie(headers)
        if cookie == 'sid=admin':
            return {'role': 'admin'}
        if cookie == 'usid=user':
            return {'role': 'user', 'username': 'alice'}
        raise LoginRequired

    def read_user_identity(self, *, headers, path):
        del path
        if self._cookie(headers) != 'usid=user':
            raise LoginRequired
        return {'role': 'user', 'username': 'alice'}

    def read_user_password(self, *, headers, path):
        del path
        if self._cookie(headers) != 'usid=user':
            raise LoginRequired
        return {
            'username': 'alice',
            'password_min_length': 8,
            'password_max_length': 128,
        }

    def read_admin_usage_csv(self, *, headers, path, window):
        del path
        if self._cookie(headers) != 'sid=admin':
            raise LoginRequired
        if window not in ('cycle', '30d'):
            raise ValueError('invalid usage export window')
        return {
            'body': f'user,bytes\nwindow,{window}\n',
            'filename': f'usage-{window}-20260915.csv',
        }


def _dist(tmp_path: Path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / 'index.js').write_text('export default 1;', encoding='utf-8')
    (tmp_path / 'index.html').write_text(
        '<!doctype html><title>清零日志</title>'
        '<body class="has-shell"><div id="root" data-public-host=""></div>'
        '<script type="module" src="/static/react/assets/index.js"></script>',
        encoding='utf-8',
    )
    (tmp_path / 'manifest.json').write_text('{}', encoding='utf-8')
    return tmp_path


def test_react_documents_are_exactly_served_and_guarded(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        anonymous = client.get('/admin', follow_redirects=False)
        assert anonymous.status_code == 303
        assert anonymous.headers['location'] == '/login'

        admin = client.get('/admin', headers={'Cookie': 'sid=admin'})
        assert admin.status_code == 200
        assert '<title>总览</title>' in admin.text
        assert '<body class="has-shell">' in admin.text
        assert 'data-public-host="panel.example.test"' in admin.text

        user = client.get('/user/panel', headers={'Cookie': 'usid=user'})
        assert user.status_code == 200
        assert '<title>用户面板 · Hysteria</title>' in user.text

        detail = client.get('/admin/user/alice', headers={'Cookie': 'sid=admin'})
        assert detail.status_code == 200
        assert '<body class="has-shell">' in detail.text

        wrong_realm = client.get('/admin', headers={'Cookie': 'usid=user'}, follow_redirects=False)
        assert wrong_realm.status_code == 303
        assert wrong_realm.headers['location'] == '/login'

        password = client.get('/user/change-password', headers={'Cookie': 'usid=user'})
        assert password.status_code == 200
        assert 'data-password-max-length' not in password.text


def test_react_documents_do_not_spa_fallback_and_assets_are_immutable(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        asset = client.get('/static/react/assets/index.js')
        assert asset.status_code == 200
        assert asset.text == 'export default 1;'
        assert client.get('/static/react/assets/missing.js').status_code == 404
        assert client.get('/static/react/manifest.json').status_code == 404
        assert client.get('/admin/unknown').status_code == 404

        head = client.head('/admin', headers={'Cookie': 'sid=admin'})
        assert head.status_code == 200
        assert head.content == b''
        assert int(head.headers['content-length']) == len(
            client.get('/admin', headers={'Cookie': 'sid=admin'}).content
        )


def test_legacy_daily_redirect_and_csv_export_are_authenticated(tmp_path):
    with TestClient(create_app(StubDocumentServices(), react_dist=_dist(tmp_path))) as client:
        redirect = client.get('/admin/daily', follow_redirects=False)
        assert redirect.status_code == 301
        assert redirect.headers['location'] == '/admin/usage'
        history_redirect = client.get('/admin/usage-history', follow_redirects=False)
        assert history_redirect.status_code == 301
        assert history_redirect.headers['location'] == '/admin/usage#usage-history'

        anonymous = client.get('/admin/usage.csv', follow_redirects=False)
        assert anonymous.status_code == 401
        assert anonymous.json() == {'error': 'login_required'}

        invalid = client.get('/admin/usage.csv?window=year', headers={'Cookie': 'sid=admin'})
        assert invalid.status_code == 400
        assert invalid.json() == {'error': 'invalid_window'}

        exported = client.get('/admin/usage.csv?window=30d', headers={'Cookie': 'sid=admin'})
        assert exported.status_code == 200
        assert exported.headers['content-type'].startswith('text/csv')
        assert exported.headers['content-disposition'] == (
            'attachment; filename="usage-30d-20260915.csv"'
        )
        assert exported.text == 'user,bytes\nwindow,30d\n'
