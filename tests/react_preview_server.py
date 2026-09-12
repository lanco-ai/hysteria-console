"""Loopback-only built React preview backed by the real FastAPI adapter."""

import html
import json
import mimetypes
import socket
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hysteria'), str(ROOT), str(ROOT / 'tests')]

import http_utils
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices

from preview_http_server import managed_preview_http_server
from preview_http_server import read_request_body as _read_request_body
from tests import workspace_preview_server as legacy_preview

DIST = ROOT / 'frontend' / 'dist'
REACT_PAGES = {
    '/__react/': ('Hysteria · 连接网络，掌控全局', 'page-home page-site'),
    '/__react/admin/logs': ('清零日志', 'has-shell'),
    '/__react/login': ('管理员登录 · Hysteria', 'page-auth page-admin-login'),
}
PUBLIC_HOST = 'preview.invalid'
PREVIEW_LOGIN_PASSWORD = 'preview-only-password'
RECEIPT_TIMEOUT = 10


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
        selected.add('/static/react/' + file)
        for css in item.get('css', []):
            if isinstance(css, str):
                selected.add('/static/react/' + css)
        for imported in item.get('imports', []):
            if isinstance(imported, str):
                pending.append(imported)
    return selected


def _handler(api_client, allowed_assets):
    class ReactPreview(legacy_preview.Preview):
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

        def _api(self):
            headers = list(self.headers.raw_items())
            if not any(name.lower() == 'cookie' for name, _ in headers):
                headers.append(('Cookie', ''))
            method = 'GET' if self.command == 'HEAD' else self.command
            response = api_client.request(method, self.path, headers=headers)
            self._write(
                response.status_code,
                response.content,
                response.headers.get('content-type', 'application/json'),
                response.headers.multi_items(),
            )

        def _json_error(self, status, error):
            payload = json.dumps({'error': error}, separators=(',', ':')).encode()
            headers = [('Cache-Control', 'no-store'), *http_utils.SECURITY_HEADERS.items()]
            self._write(status, payload, 'application/json', headers)

        def _login_api(self):
            try:
                content_length = http_utils.form_content_length(self.headers)
            except http_utils.RequestTooLarge:
                self._json_error(413, 'request_too_large')
                return
            except http_utils.BadRequest:
                self._json_error(400, 'bad_request')
                return

            try:
                payload = _read_request_body(
                    self.rfile,
                    self.connection,
                    content_length,
                    timeout=RECEIPT_TIMEOUT,
                )
            except socket.timeout:
                self._json_error(408, 'request_timeout')
                return
            if len(payload) != content_length:
                self._json_error(400, 'bad_request')
                return

            headers = list(self.headers.raw_items())
            if not any(name.lower() == 'cookie' for name, _ in headers):
                headers.append(('Cookie', ''))
            response = api_client.request(
                'POST',
                self.path,
                headers=headers,
                content=payload,
            )
            self._write(
                response.status_code,
                response.content,
                response.headers.get('content-type', 'application/json'),
                response.headers.multi_items(),
            )

        def _react_asset(self, path):
            if path not in allowed_assets:
                self.send_error(404)
                return
            relative = path.removeprefix('/static/react/')
            file = (DIST / relative).resolve()
            if not file.is_relative_to(DIST.resolve()) or not file.is_file():
                self.send_error(404)
                return
            content_type = mimetypes.guess_type(file.name)[0] or 'application/octet-stream'
            if file.suffix == '.js':
                content_type = 'text/javascript'
            self._write(200, file.read_bytes(), content_type)

        def _react_page(self, path):
            title, body_class = REACT_PAGES[path]
            payload = (DIST / 'index.html').read_text(encoding='utf-8')
            escaped_public_host = html.escape(PUBLIC_HOST, quote=True)
            replacements = (
                ('<title>清零日志</title>', f'<title>{title}</title>'),
                ('<body class="has-shell">', f'<body class="{body_class}">'),
                (
                    'data-public-host=""',
                    f'data-public-host="{escaped_public_host}"',
                ),
            )
            for marker, replacement in replacements:
                if payload.count(marker) != 1:
                    raise RuntimeError(f'React document marker is missing or ambiguous: {marker}')
                payload = payload.replace(marker, replacement, 1)
            if path == '/__react/login':
                marker = f'data-public-host="{escaped_public_host}"'
                replacement = (
                    marker
                    + f' data-password-max-length="{legacy_preview.ss.PASSWORD_MAX_LENGTH}"'
                )
                if payload.count(marker) != 1:
                    raise RuntimeError('React login bootstrap marker is missing or ambiguous')
                payload = payload.replace(marker, replacement, 1)
            self._write(200, payload.encode(), 'text/html; charset=utf-8')

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in REACT_PAGES:
                self._react_page(path)
            elif path.startswith('/api/'):
                self._api()
            elif path.startswith('/static/react/'):
                self._react_asset(path)
            else:
                super().do_GET()

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            if urlsplit(self.path).path == '/api/v1/login':
                self._login_api()
                return
            super().do_POST()

    return ReactPreview


@contextmanager
def preview_server(port=0):
    """Serve built React, legacy pages, and fictional authenticated API state."""
    if not (DIST / 'index.html').is_file() or not (DIST / 'manifest.json').is_file():
        raise RuntimeError('Build the React frontend before starting its preview')
    allowed_assets = _manifest_assets(DIST)
    with (
        tempfile.TemporaryDirectory(prefix='hy2-react-fixture-') as directory,
        legacy_preview.isolated_preview(directory) as allowed_ports,
    ):
        service = legacy_preview.ss
        meta = service.load_meta()
        admin_hash = service.hash_secret(PREVIEW_LOGIN_PASSWORD)
        meta['admin_pass_hash'] = admin_hash
        service.save_json(service.META_FILE, meta)
        with service._login_failures_lock:
            login_trackers = (
                service._login_failures,
                service._user_login_failures,
                service._login_attempts_inflight,
            )
            service._login_failures = {}
            service._user_login_failures = {}
            service._login_attempts_inflight = {}
        try:
            admin_cookie = service.create_session(
                'admin', service._credential_generation(admin_hash)
            )
            user_cookie = service.create_user_session(
                'demo_alex',
                service._credential_generation(legacy_preview.DEMO['sub_token']),
                service.USER_SESSION_SUBSCRIPTION_TOKEN,
            )
            app = create_app(LegacyPanelServices(service), max_requests=4)
            with TestClient(app, client=('127.0.0.1', 50000)) as api_client:
                handler = _handler(api_client, allowed_assets)
                with managed_preview_http_server(
                    ('127.0.0.1', port), handler
                ) as server:
                    server.preview_admin_cookie = admin_cookie
                    server.preview_user_cookie = user_cookie
                    server.preview_login_password = PREVIEW_LOGIN_PASSWORD
                    allowed_ports.add(server.server_port)
                    yield server
        finally:
            with service._login_failures_lock:
                (
                    service._login_failures,
                    service._user_login_failures,
                    service._login_attempts_inflight,
                ) = login_trackers


if __name__ == '__main__':
    with preview_server(18765):
        threading.Event().wait()
