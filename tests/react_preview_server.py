"""Serve the built shop frontend against fictional shop state for browser tests.

The shop service delegates sessions to the network service, so this preview
plays the network service's small session surface itself: ``/api/v1/session``,
``/api/v1/login`` and ``/api/v1/logout`` backed by in-memory fictional sessions.
Runners may replace ``ShopStore`` and ``DIST`` before calling ``preview_server``.
"""

import asyncio
import html
import json
import mimetypes
import secrets
import socket
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'server', ROOT / 'tests'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import http_utils  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from preview_http_server import managed_preview_http_server  # noqa: E402
from preview_http_server import read_request_body as _read_request_body  # noqa: E402
from web_api import create_app  # noqa: E402
from web_api.services import LoginRequired, session_cookies  # noqa: E402
from web_api.shop_source import normalize_products  # noqa: E402
from web_api.shop_store import ShopStore  # noqa: E402

DIST = ROOT / 'frontend' / 'dist'
ASSET_PREFIX = '/static/shop/'
REACT_PAGES = {
    '/': ('购物 · LancoAI', 'page-portal page-workbench'),
    '/__react/': ('购物 · LancoAI', 'page-portal page-workbench'),
    '/admin/shop': ('商品管理', 'has-shell'),
    '/__react/admin/shop': ('商品管理', 'has-shell'),
}
LOGIN_PAGES = {'/', '/__react/'}
PUBLIC_HOST = 'preview.invalid'
PREVIEW_LOGIN_PASSWORD = 'preview-only-password'
PASSWORD_MAX_LENGTH = 256
RECEIPT_TIMEOUT = 10
MAX_JSON_BYTES = 128 * 1024


class PreviewSessions:
    """In-memory stand-in for the network service's admin sessions."""

    def __init__(self):
        self._lock = threading.Lock()
        self._admin = set()

    def create_admin(self):
        sid = secrets.token_urlsafe(24)
        with self._lock:
            self._admin.add(sid)
        return sid

    def revoke(self, sid):
        with self._lock:
            self._admin.discard(sid)

    def is_admin(self, sid):
        with self._lock:
            return bool(sid) and sid in self._admin

    # Same contract as RemoteSessionServices.read_session.
    def read_session(self, *, headers, path):
        if self.is_admin(session_cookies(headers).get('sid')):
            return {'role': 'admin'}
        raise LoginRequired


def _manifest_assets(dist):
    manifest = json.loads((dist / 'manifest.json').read_text(encoding='utf-8'))
    entry = manifest.get('index.html')
    if not isinstance(entry, dict) or not entry.get('isEntry'):
        raise RuntimeError('React build manifest has no index entry')
    selected = set()
    pending = ['index.html']
    while pending:
        key = pending.pop()
        item = manifest.get(key)
        if not isinstance(item, dict):
            raise RuntimeError(f'React build manifest references missing entry: {key}')
        file = item.get('file')
        if not isinstance(file, str):
            raise RuntimeError(f'React build manifest entry has no file: {key}')
        selected.add(ASSET_PREFIX + file)
        for css in item.get('css', []):
            if isinstance(css, str):
                selected.add(ASSET_PREFIX + css)
        for imported in item.get('imports', []):
            if isinstance(imported, str):
                pending.append(imported)
    # Fonts and other emitted files are not always listed in the manifest.
    assets_root = (dist / 'assets').resolve()
    if assets_root.is_dir():
        for file in assets_root.rglob('*'):
            if file.is_file() and file.is_relative_to(dist.resolve()):
                selected.add(ASSET_PREFIX + file.relative_to(dist).as_posix())
    return selected


def _handler(api_client, sessions, dist, allowed_assets):
    class ShopPreview(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *_args):
            pass

        def _write(self, status, payload, content_type, headers=()):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            for name, value in headers:
                if name.lower() not in {'content-length', 'content-type', 'connection'}:
                    self.send_header(name, value)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def _json(self, status, value, headers=()):
            payload = json.dumps(value, separators=(',', ':')).encode()
            base = [('Cache-Control', 'no-store'), *http_utils.SECURITY_HEADERS.items()]
            self._write(status, payload, 'application/json', [*base, *headers])

        def _body(self, limit):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                return None
            if length < 0 or length > limit:
                return None
            try:
                payload = _read_request_body(
                    self.rfile, self.connection, length, timeout=RECEIPT_TIMEOUT
                )
            except socket.timeout:
                return None
            return payload if len(payload) == length else None

        def _sid(self):
            return session_cookies(self.headers).get('sid')

        def _api(self, body=None):
            headers = list(self.headers.raw_items())
            if not any(name.lower() == 'cookie' for name, _ in headers):
                headers.append(('Cookie', ''))
            method = 'GET' if self.command == 'HEAD' else self.command
            response = api_client.request(method, self.path, headers=headers, content=body)
            self._write(
                response.status_code,
                response.content,
                response.headers.get('content-type', 'application/json'),
                response.headers.multi_items(),
            )

        # --- network service stand-in ---------------------------------------
        def _session(self):
            if sessions.is_admin(self._sid()):
                self._json(200, {'role': 'admin'})
            else:
                self._json(401, {'error': 'login_required'})

        def _login(self):
            body = self._body(http_utils.MAX_FORM_BYTES)
            if body is None:
                self._json(400, {'error': 'bad_request'})
                return
            form = parse_qs(body.decode('utf-8', 'replace'))
            if (form.get('password') or [''])[0] != PREVIEW_LOGIN_PASSWORD:
                self._json(200, {'ok': False, 'message': '密码错误'})
                return
            sid = sessions.create_admin()
            cookie = f'sid={sid}; Path=/; HttpOnly; SameSite=Lax'
            self._json(200, {'ok': True, 'redirect_to': '/admin/plans'}, [('Set-Cookie', cookie)])

        def _logout(self):
            self._body(http_utils.MAX_FORM_BYTES)
            sessions.revoke(self._sid())
            cookie = 'sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
            self._json(200, {'ok': True, 'redirect_to': '/login'}, [('Set-Cookie', cookie)])

        # --- documents and assets -------------------------------------------
        def _asset(self, path):
            if path not in allowed_assets:
                self.send_error(404)
                return
            file = (dist / path.removeprefix(ASSET_PREFIX)).resolve()
            if not file.is_relative_to(dist.resolve()) or not file.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if file.suffix == '.js':
                content_type = 'text/javascript'
            self._write(200, file.read_bytes(), content_type)

        def _page(self, path):
            title, body_class = REACT_PAGES[path]
            payload = (dist / 'index.html').read_text(encoding='utf-8')
            host = html.escape(PUBLIC_HOST, quote=True)
            replacements = (
                ('<title>清零日志</title>', f'<title>{html.escape(title)}</title>'),
                ('<body class="has-shell">', f'<body class="{body_class}">'),
                ('data-public-host=""', f'data-public-host="{host}"'),
            )
            for marker, replacement in replacements:
                if payload.count(marker) != 1:
                    raise RuntimeError(f'React document marker is missing or ambiguous: {marker}')
                payload = payload.replace(marker, replacement, 1)
            if path in LOGIN_PAGES:
                marker = f'data-public-host="{host}"'
                payload = payload.replace(
                    marker, marker + f' data-password-max-length="{PASSWORD_MAX_LENGTH}"', 1
                )
            self._write(200, payload.encode(), 'text/html; charset=utf-8')

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in REACT_PAGES:
                self._page(path)
            elif path == '/api/v1/session':
                self._session()
            elif path == '/api/plans/reminders':
                # The workbench + AI service owns reminders; none in the shop preview.
                self._json(200, {'items': []})
            elif path.startswith('/api/v1/shop/'):
                self._api()
            elif path.startswith(ASSET_PREFIX):
                self._asset(path)
            else:
                self.send_error(404)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == '/api/v1/login':
                self._login()
            elif path == '/api/v1/logout':
                self._logout()
            elif path.startswith('/api/v1/shop/'):
                body = self._body(MAX_JSON_BYTES)
                if body is None:
                    self._json(400, {'error': 'bad_request'})
                    return
                self._api(body)
            else:
                self.send_error(404)

        def _write_through(self):
            if not urlsplit(self.path).path.startswith('/api/v1/shop/'):
                self.send_error(404)
                return
            body = self._body(MAX_JSON_BYTES)
            if body is None:
                self._json(400, {'error': 'bad_request'})
                return
            self._api(body)

        do_PUT = _write_through
        do_PATCH = _write_through
        do_DELETE = _write_through

    return ShopPreview


@contextmanager
def preview_server(port=0, *, overview_fixture=False):
    """Serve the built shop frontend and fictional state on loopback."""
    del overview_fixture  # Accepted for runner compatibility; the shop has no overview.
    dist = Path(DIST)
    if not (dist / 'index.html').is_file() or not (dist / 'manifest.json').is_file():
        raise RuntimeError('Build the React frontend before starting its preview')
    allowed_assets = _manifest_assets(dist)
    sessions = PreviewSessions()
    with tempfile.TemporaryDirectory(prefix='shop-react-fixture-') as directory:

        async def shop_fetcher():
            fixture = Path(__file__).parent / 'fixtures' / 'shop' / 'products.json'
            return normalize_products(json.loads(fixture.read_text())['data'])

        shop_store = ShopStore(Path(directory) / 'shop-source.json', fetcher=shop_fetcher)
        asyncio.run(shop_store.refresh())
        app = create_app(sessions, max_requests=4, shop_store=shop_store)
        with TestClient(app, client=('127.0.0.1', 50000)) as api_client:
            handler = _handler(api_client, sessions, dist, allowed_assets)
            with managed_preview_http_server(('127.0.0.1', port), handler) as server:
                server.preview_admin_cookie = sessions.create_admin()
                server.preview_admin_other_cookie = sessions.create_admin()
                server.preview_login_password = PREVIEW_LOGIN_PASSWORD
                yield server


if __name__ == '__main__':
    with preview_server(18765):
        threading.Event().wait()
