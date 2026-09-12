"""Loopback-only built React preview backed by the real FastAPI adapter."""

import html
import json
import mimetypes
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'hysteria'), str(ROOT)]

from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LegacyPanelServices

from tests import workspace_preview_server as legacy_preview

DIST = ROOT / 'frontend' / 'dist'
REACT_ENTRY = '/__react/admin/logs'
PUBLIC_HOST = 'preview.invalid'


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
            headers = {name: value for name, value in self.headers.items()}
            method = 'GET' if self.command == 'HEAD' else self.command
            response = api_client.request(method, self.path, headers=headers)
            self._write(
                response.status_code,
                response.content,
                response.headers.get('content-type', 'application/json'),
                response.headers.items(),
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

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == REACT_ENTRY:
                payload = (DIST / 'index.html').read_text(encoding='utf-8')
                marker = 'data-public-host=""'
                if payload.count(marker) != 1:
                    raise RuntimeError('React root bootstrap marker is missing or ambiguous')
                payload = payload.replace(
                    marker,
                    f'data-public-host="{html.escape(PUBLIC_HOST, quote=True)}"',
                    1,
                )
                self._write(200, payload.encode(), 'text/html; charset=utf-8')
            elif path.startswith('/api/'):
                self._api()
            elif path.startswith('/static/react/'):
                self._react_asset(path)
            else:
                super().do_GET()

        def do_HEAD(self):
            self.do_GET()

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
        admin_cookie = service.create_session('admin', service._credential_generation('test-hash'))
        user_cookie = service.create_user_session(
            'demo_alex',
            service._credential_generation(legacy_preview.DEMO['sub_token']),
            service.USER_SESSION_SUBSCRIPTION_TOKEN,
        )
        app = create_app(LegacyPanelServices(service), max_requests=4)
        with TestClient(app) as api_client:
            handler = _handler(api_client, allowed_assets)
            with ThreadingHTTPServer(('127.0.0.1', port), handler) as server:
                server.preview_admin_cookie = admin_cookie
                server.preview_user_cookie = user_cookie
                allowed_ports.add(server.server_port)
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    yield server
                finally:
                    server.shutdown()
                    worker.join(timeout=5)


if __name__ == '__main__':
    with preview_server(18765):
        threading.Event().wait()
