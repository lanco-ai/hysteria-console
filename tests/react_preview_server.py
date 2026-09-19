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
from preview_http_server import managed_preview_http_server
from preview_http_server import read_request_body as _read_request_body
from web_api import create_app
from web_api.ai.service_store import AIServiceStore
from web_api.services import LegacyPanelServices
from web_api.service_center import ServiceCenterStore
from web_api.plans_service import PlanStore

from tests import workspace_preview_server as legacy_preview

DIST = ROOT / 'frontend' / 'dist'
REACT_PAGES = {
    '/__react/admin/services': ('服务中心', 'has-shell'),
    '/admin/services': ('服务中心', 'has-shell'),
    '/__react/admin/plans': ('今日计划', 'has-shell'),
    '/admin/plans': ('今日计划', 'has-shell'),
    '/__react/admin': ('总览', 'has-shell'),
    '/__react/': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/__react/auth': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/auth': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/__react/admin/logs': ('清零日志', 'has-shell'),
    '/__react/admin/settings': ('设置', 'has-shell'),
    '/__react/admin/usage': ('流量分析', 'has-shell'),
    '/__react/admin/health': ('健康状态', 'has-shell'),
    '/__react/admin/incidents': ('事故处理', 'has-shell'),
    '/__react/admin/config': ('模板配置', 'has-shell'),
    '/__react/admin/rules': ('路由规则', 'has-shell'),
    '/__react/admin/landing-egresses': ('家宽出口', 'has-shell'),
    '/__react/admin/user/demo_alex': ('demo_alex · 用量画像', 'has-shell'),
    '/__react/login': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/login': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/__react/user/login': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/user/login': ('Hysteria 工作台', 'has-shell page-workbench'),
    '/__react/logout': ('确认退出', ''),
    '/__react/user/logout': ('确认退出', ''),
    '/__react/user/change-password': ('修改面板密码', 'page-auth'),
    '/__react/user/panel': ('用户面板 · Hysteria', ''),
    '/__react/admin/chat': ('AI 对话', 'has-shell'),
    '/admin/chat': ('AI 对话', 'has-shell'),
    '/__react/admin/video': ('AI 视频', 'has-shell'),
    '/admin/video': ('AI 视频', 'has-shell'),
}
PUBLIC_HOST = 'preview.invalid'
PREVIEW_LOGIN_PASSWORD = 'preview-only-password'
PREVIEW_USER_PASSWORD = 'preview-user-password'
PREVIEW_MUST_CHANGE_PASSWORD = 'preview-change-required-password'
RECEIPT_TIMEOUT = 10


class PreviewGeminiAdapter:
    """Deterministic Gemini model-list double; never performs network requests."""

    def list_models(self, profile):
        if not profile.get('api_key'):
            raise ValueError('missing preview credential')
        return [{'id': 'gemini-preview-fast', 'name': 'Gemini Preview Fast', 'input_token_limit': 64000}]


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
    # CSS may reference local fonts or other emitted files that Vite does not
    # list in the entry's ``css``/``imports`` arrays.  Keep the preview's
    # allowlist in lock-step with the release directory while still rejecting
    # missing and out-of-tree paths in ``_react_asset``.
    assets_root = (dist / 'assets').resolve()
    if assets_root.is_dir():
        for file in assets_root.rglob('*'):
            if file.is_file() and file.is_relative_to(dist.resolve()):
                selected.add('/static/react/' + file.relative_to(dist).as_posix())
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

        def _form_api(self):
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

        def _json_api(self):
            try:
                raw_length = self.headers.get('Content-Length', '')
                length = int(raw_length)
                if length < 0 or length > 128 * 1024:
                    raise ValueError
            except (TypeError, ValueError):
                self._json_error(400, 'bad_request')
                return
            try:
                payload = _read_request_body(
                    self.rfile,
                    self.connection,
                    length,
                    timeout=RECEIPT_TIMEOUT,
                )
            except socket.timeout:
                self._json_error(408, 'request_timeout')
                return
            headers = list(self.headers.raw_items())
            if not any(name.lower() == 'cookie' for name, _ in headers):
                headers.append(('Cookie', ''))
            response = api_client.request(
                self.command,
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
            if path in REACT_PAGES:
                title, body_class = REACT_PAGES[path]
            elif path.startswith('/__react/admin/user/') and path.count('/') == 4:
                uid = path.rsplit('/', 1)[-1]
                title, body_class = f'{uid} · 用量画像', 'has-shell'
            else:
                self.send_error(404)
                return
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
            if path in ('/__react/', '/__react/auth', '/__react/login', '/__react/user/login', '/login', '/user/login'):
                marker = f'data-public-host="{escaped_public_host}"'
                replacement = (
                    marker + f' data-password-max-length="{legacy_preview.ss.PASSWORD_MAX_LENGTH}"'
                )
                if payload.count(marker) != 1:
                    raise RuntimeError('React login bootstrap marker is missing or ambiguous')
                payload = payload.replace(marker, replacement, 1)
            self._write(200, payload.encode(), 'text/html; charset=utf-8')

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in REACT_PAGES or (
                path.startswith('/__react/admin/user/') and path.count('/') == 4
            ):
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
            if urlsplit(self.path).path in {
                '/api/v1/admin/users/create',
                '/api/v1/admin/users/update',
                '/api/v1/admin/operations/cycle',
                '/api/v1/admin/operations/reset-usage',
                '/api/v1/admin/operations/refresh-usage',
                '/api/v1/admin/operations/reset-usage-all',
                '/api/v1/admin/operations/pause-user',
                '/api/v1/admin/operations/toggle-user',
                '/api/v1/admin/operations/rotate-token',
                '/api/v1/admin/operations/delete',
                '/api/v1/admin/config/save',
                '/api/v1/admin/rules/save',
                '/api/v1/admin/health/update-check',
                '/api/v1/admin/health/update-apply',
                '/api/v1/admin/health/test-alert',
                '/api/v1/admin/health/multiplier-apply',
                '/api/v1/admin/health/multiplier-auto',
                '/api/v1/login',
                '/api/v1/logout',
                '/api/v1/user/logout',
                '/api/v1/admin/change-password',
                '/api/v1/user/change-password',
            }:
                self._form_api()
                return
            request_path = urlsplit(self.path).path
            if request_path in {'/api/chat/completions', '/api/v1/admin/services/probe'} or (
                request_path.startswith('/api/ai/services/') and request_path.endswith('/test')
            ):
                self._json_api()
                return
            super().do_POST()

        def do_PUT(self):
            request_path = urlsplit(self.path).path
            if request_path in {'/api/chat/settings', '/api/v1/admin/services', '/api/ai/service-bindings'} or request_path.startswith('/api/ai/services/'):
                self._json_api()
                return
            super().do_PUT()

    return ReactPreview


@contextmanager
def preview_server(port=0, *, overview_fixture=False):
    """Serve built React, legacy pages, and fictional authenticated API state."""
    if not (DIST / 'index.html').is_file() or not (DIST / 'manifest.json').is_file():
        raise RuntimeError('Build the React frontend before starting its preview')
    allowed_assets = _manifest_assets(DIST)
    with (
        tempfile.TemporaryDirectory(prefix='hy2-react-fixture-') as directory,
        legacy_preview.isolated_preview(directory) as allowed_ports,
        legacy_preview.pytest.MonkeyPatch.context() as patch,
    ):
        service = legacy_preview.ss
        # Only the controlled React preview doubles external effects. Accounting,
        # account state, session invalidation and revocation WAL still use real
        # services in the temporary directory under the unchanged outer guards.
        for module in (service.xray_config, service.tuic_config):
            for name, value in vars(module).copy().items():
                if isinstance(value, Path) and name.isupper():
                    patch.setattr(module, name, Path(directory) / module.__name__ / value.name)
            patch.setattr(module, 'reload_async', lambda: True)
        patch.setattr(
            service, '_sync_static_access_from_users', lambda *args, **kwargs: (True, True)
        )
        patch.setattr(
            service,
            'hy_kick',
            lambda users: service.CredentialActionResult(
                action='kick',
                target=','.join(users),
                attempted=False,
                ok=True,
                code='preview-confirmed',
                retryable=False,
            ),
        )
        meta = service.load_meta()
        admin_hash = service.hash_secret(PREVIEW_LOGIN_PASSWORD)
        meta['admin_pass_hash'] = admin_hash
        service.save_json(service.META_FILE, meta)
        users = service.load_json(service.USERS_FILE, {})
        demo_hash = service.hash_secret(PREVIEW_USER_PASSWORD)
        must_change_hash = service.hash_secret(PREVIEW_MUST_CHANGE_PASSWORD)
        users['demo_alex']['panel_pass_hash'] = demo_hash
        users['must_change'] = {
            'sub_token': 'must-change-token',
            'panel_pass_hash': must_change_hash,
            'panel_password_must_change': True,
            'monthly_quota_bytes': 10 * 1024**3,
            'max_devices': 1,
            'disabled': False,
        }
        if overview_fixture:
            users['demo_alex'].update(note='预览套餐 · 主账户', metered=True, tuic_enabled=True)
            users['unlimited'] = {
                'sub_token': 'fictional-unlimited-token',
                'monthly_quota_bytes': 0,
                'max_devices': 0,
                'disabled': True,
                'expires_at': '2026-07-01',
                'note': '预览 · 已停用且过期',
            }
            daily = {
                '2026-07-17': {
                    'demo_alex': {'tx': 2 * 1024**3, 'rx': 3 * 1024**3, 'total': 5 * 1024**3},
                    'must_change': {'tx': 4 * 1024**3, 'rx': 5 * 1024**3, 'total': 9 * 1024**3},
                },
                '2026-07-18': {
                    'demo_alex': {'tx': 1024**3, 'rx': 1024**3, 'total': 2 * 1024**3},
                },
            }
            service.save_json(service.USAGE_DAILY_FILE, daily)
            service.save_json(service.USAGE_FILE, {'2026-07': daily['2026-07-17']})
            service.save_json(service.USAGE_HOURLY_FILE, {'2026-07-18T12': daily['2026-07-18']})
            service.save_json(service.ONLINE_FILE, {'demo_alex': 2})
        service.save_json(service.USERS_FILE, users)
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
            admin_other_cookie = service.create_session(
                'admin', service._credential_generation(admin_hash)
            )
            user_cookie = service.create_user_session(
                'demo_alex',
                service._credential_generation(legacy_preview.DEMO['sub_token']),
                service.USER_SESSION_SUBSCRIPTION_TOKEN,
            )
            user_other_cookie = service.create_user_session(
                'demo_alex',
                service._credential_generation(legacy_preview.DEMO['sub_token']),
                service.USER_SESSION_SUBSCRIPTION_TOKEN,
            )
            password_user_cookie = service.create_user_session(
                'demo_alex',
                service._credential_generation(demo_hash),
                service.USER_SESSION_PANEL_PASSWORD,
            )
            password_user_other_cookie = service.create_user_session(
                'demo_alex',
                service._credential_generation(demo_hash),
                service.USER_SESSION_PANEL_PASSWORD,
            )
            must_change_cookie = service.create_user_session(
                'must_change',
                service._credential_generation(must_change_hash),
                service.USER_SESSION_PANEL_PASSWORD,
            )
            preview_ai_root = Path(directory) / 'ai'
            app = create_app(
                LegacyPanelServices(service), max_requests=4,
                service_center_store=ServiceCenterStore(Path(directory) / 'services.json'),
                ai_services_store=AIServiceStore(
                    preview_ai_root / 'services.json',
                    chat_legacy_path=preview_ai_root / 'missing-chat.json',
                    video_legacy_path=preview_ai_root / 'missing-video.json',
                    backup_dir=preview_ai_root / 'migration-backup',
                ),
                plans_store=PlanStore(Path(directory) / 'plans' / 'tasks.json'),
                gemini_adapter=PreviewGeminiAdapter(),
            )
            with TestClient(app, client=('127.0.0.1', 50000)) as api_client:
                handler = _handler(api_client, allowed_assets)
                with managed_preview_http_server(('127.0.0.1', port), handler) as server:
                    server.preview_admin_cookie = admin_cookie
                    server.preview_admin_other_cookie = admin_other_cookie
                    server.preview_user_cookie = user_cookie
                    server.preview_user_other_cookie = user_other_cookie
                    server.preview_password_user_cookie = password_user_cookie
                    server.preview_password_user_other_cookie = password_user_other_cookie
                    server.preview_must_change_cookie = must_change_cookie
                    server.preview_login_password = PREVIEW_LOGIN_PASSWORD
                    server.preview_user_password = PREVIEW_USER_PASSWORD
                    server.preview_must_change_password = PREVIEW_MUST_CHANGE_PASSWORD
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
