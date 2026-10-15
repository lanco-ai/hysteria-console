"""Loopback-only built React preview backed by the real workbench + AI app.

The workbench + AI service delegates sessions to the network service, so this
preview plays that service's small session surface itself (``/api/v1/session``,
``/api/v1/login``, ``/api/v1/logout``) with in-memory fictional sessions.
"""

import builtins
import html
import io
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'server'), str(ROOT), str(ROOT / 'tests')]

import http_utils
from fastapi.testclient import TestClient
from preview_http_server import managed_preview_http_server
from preview_http_server import read_request_body as _read_request_body
from web_api import create_app
from web_api.ai.service_store import AIServiceStore
from web_api.chat_workspace_store import WorkspaceStore
from web_api.github_trending_source import SourceError
from web_api.github_trending_store import TrendingStore
from web_api.journal_service import JournalStore
from web_api.plans_service import PlanStore
from web_api.schedule_service import ScheduleStore
from web_api.service_center import ServiceCenterStore
from web_api.services import LoginRequired, session_cookies
from web_api.video_service import AssetStore, VideoSettingsStore, WorkflowStore


DIST = ROOT / 'frontend' / 'dist'
REACT_PAGES = {
    '/trending': ('GitHub 热榜 · LancoAI', 'page-portal page-workbench'),
    '/__react/trending': ('GitHub 热榜 · LancoAI', 'page-portal page-workbench'),
    '/admin/plans': ('今日计划', 'has-shell'),
    '/__react/admin/plans': ('今日计划', 'has-shell'),
    '/admin/chat': ('AI 对话', 'has-shell page-workbench'),
    '/__react/admin/chat': ('AI 对话', 'has-shell page-workbench'),
    '/admin/video': ('AI 视频', 'has-shell'),
    '/__react/admin/video': ('AI 视频', 'has-shell'),
    '/admin/services': ('服务中心', 'has-shell'),
    '/__react/admin/services': ('服务中心', 'has-shell'),
}
LOGIN_PAGES = {'/trending', '/__react/trending'}
ASSET_PREFIX = '/static/workbench/'
PASSWORD_MAX_LENGTH = 256
PUBLIC_HOST = 'preview.invalid'
PREVIEW_LOGIN_PASSWORD = 'preview-only-password'
RECEIPT_TIMEOUT = 10
PROTECTED_PATHS = (
    '/root/hysteria',
    '/usr/local/etc/xray',
    '/etc/hysteria',
    '/etc/lancoai',
    '/run/hy2-locks',
    '/var/lib/hysteria',
)


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


@contextmanager
def isolated_preview(patch):
    """Fail closed on outbound connections, service commands and production paths."""
    allowed_ports = set()
    connect = socket.socket.connect
    popen = subprocess.Popen

    def preview_connect(connection, address):
        if (
            not isinstance(address, tuple)
            or address[0] != '127.0.0.1'
            or address[1] not in allowed_ports
        ):
            raise PermissionError('Preview forbids external connection')
        return connect(connection, address)

    def preview_process(args, *rest, **kwargs):
        if (
            not isinstance(args, (list, tuple))
            or not args
            or Path(args[0]).name != 'node'
            or kwargs.get('shell')
        ):
            raise PermissionError('Preview forbids service command')
        return popen(args, *rest, **kwargs)

    def guard(opener):
        def checked(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                path = os.path.realpath(os.fsdecode(file))
                if any(path == prefix or path.startswith(prefix + '/') for prefix in PROTECTED_PATHS):
                    raise PermissionError('Preview cannot access production path')
            return opener(file, *args, **kwargs)

        return checked

    patch.setattr(socket.socket, 'connect', preview_connect)
    patch.setattr(subprocess, 'Popen', preview_process)
    patch.setattr(builtins, 'open', guard(builtins.open))
    patch.setattr(io, 'open', guard(io.open))
    patch.setattr(os, 'open', guard(os.open))
    yield allowed_ports


class PreviewWorkspaceStore(WorkspaceStore):
    def __init__(self, path):
        super().__init__(path)
        self.embedder = self

    def available(self):
        return True

    def embed(self, texts, **kwargs):
        return [[1.0] + [0.0] * 383 for _ in texts]


class PreviewWorkspaceProvider:
    """Fictional streaming answers; cannot access any external provider."""

    def complete(self, messages, **kwargs):
        content = (
            json.dumps({'calls': [{'tool': 'python', 'arguments': {'code': 'print(2.0)'}}]})
            if 'calls' in messages[0]['content']
            else '预览摘要：已经讨论写入放大，后续通过实验验证。'
        )
        return {
            'choices': [{'message': {'content': content}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 10},
        }

    def stream(self, messages, **_kwargs):
        from web_api.chat_workspace_routes import sse

        async def generate():
            yield sse(
                {
                    'type': 'delta',
                    'text': '### 理解与验证\n\nLSM Tree 将随机写入变成顺序写入。 [S1]\n\n| 项目 | 观察 |\n| --- | --- |\n| 写入放大 | 需要测量 |\n\n$E = mc^2$\n\n'
                    + (
                        messages[-1]['content']
                        if isinstance(messages[-1]['content'], str)
                        else messages[-1]['content'][0]['text']
                    ),
                }
            )
            yield sse({'type': 'usage', 'usage': {'prompt_tokens': 42, 'completion_tokens': 24}})
            yield sse({'type': 'done'})

        return generate()


PREVIEW_CHAT_MODELS = (
    {'id': 'preview-chat-listed-first', 'name': 'Preview Chat Listed First'},
    {'id': 'preview-chat-fast', 'name': 'Preview Chat Fast', 'context_window': 64000},
)


def _preview_assistant_result(schema):
    if 'suggestions' in schema.get('properties', {}):
        return {
            'summary': '结构化输出检查',
            'suggestions': [
                {
                    'title': '测试建议',
                    'notes': '',
                    'quadrant': 'important',
                    'start_time': '',
                    'estimate_minutes': 30,
                    'reminder_offset_minutes': 0,
                    'reason': '检查计划建议结构',
                }
            ],
        }
    return {
        'title': 'Preview Storyboard',
        'rewritten_text': 'A short structured test story.',
        'style_prompt': 'Soft light.',
        'aspect_ratio': '16:9',
        'shots': [
            {
                'title': 'Preview Shot',
                'script': 'A person looks up.',
                'shot_type': '近景',
                'character': 'A person',
                'scene': 'A bright room',
                'duration': 5,
                'image_prompt': 'A bright room in soft light.',
                'motion_prompt': 'Slow push in.',
                'dialogue': 'Hello.',
            }
        ],
    }


class PreviewOpenAIGateway(BaseHTTPRequestHandler):
    """Deterministic OpenAI-compatible gateway on loopback for the Chat API profile."""

    def log_message(self, *_args):
        pass

    def _send(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self):
        token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        if token:
            return True
        self._send(401, {'error': {'message': 'missing preview credential'}})
        return False

    def do_GET(self):
        if self.path != '/v1/models':
            self._send(404, {'error': {'message': 'not found'}})
        elif self._authorized():
            self._send(200, {'object': 'list', 'data': list(PREVIEW_CHAT_MODELS)})

    def do_POST(self):
        if self.path != '/v1/chat/completions':
            self._send(404, {'error': {'message': 'not found'}})
            return
        if not self._authorized():
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
            payload = json.loads(self.rfile.read(min(length, 256 * 1024)))
        except ValueError:
            self._send(400, {'error': {'message': 'invalid request'}})
            return
        if payload.get('model') != 'preview-chat-fast' or not payload.get('messages'):
            self._send(404, {'error': {'message': 'model not found'}})
            return
        response_format = payload.get('response_format') or {}
        if response_format.get('type') == 'json_schema':
            result = _preview_assistant_result(response_format['json_schema']['schema'])
            content = json.dumps(result, ensure_ascii=False)
        else:
            content = 'capability test passed'
        self._send(
            200,
            {
                'object': 'chat.completion',
                'model': payload['model'],
                'choices': [
                    {
                        'index': 0,
                        'message': {'role': 'assistant', 'content': content},
                        'finish_reason': 'stop',
                    }
                ],
            },
        )


async def _offline_trending_fetcher(_period):
    raise SourceError('upstream_unavailable')


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
    # CSS may reference local fonts or other emitted files that Vite does not
    # list in the entry's ``css``/``imports`` arrays.  Keep the preview's
    # allowlist in lock-step with the release directory while still rejecting
    # missing and out-of-tree paths in ``_react_asset``.
    assets_root = (dist / 'assets').resolve()
    if assets_root.is_dir():
        for file in assets_root.rglob('*'):
            if file.is_file() and file.is_relative_to(dist.resolve()):
                selected.add(ASSET_PREFIX + file.relative_to(dist).as_posix())
    return selected


def _handler(api_client, sessions, allowed_assets):
    class ReactPreview(BaseHTTPRequestHandler):
        def log_request(self, code='-', size='-'):
            if str(code).isdigit() and int(code) >= 400:
                super().log_request(code, size)

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

        # --- network service stand-in ---------------------------------------
        def _json(self, status, value, headers=()):
            payload = json.dumps(value, separators=(',', ':')).encode()
            base = [('Cache-Control', 'no-store'), *http_utils.SECURITY_HEADERS.items()]
            self._write(status, payload, 'application/json', [*base, *headers])

        def _sid(self):
            return session_cookies(self.headers).get('sid')

        def _session(self):
            if sessions.is_admin(self._sid()):
                self._json(200, {'role': 'admin'})
            else:
                self._json(401, {'error': 'login_required'})

        def _form_body(self):
            try:
                length = http_utils.form_content_length(self.headers)
                payload = _read_request_body(
                    self.rfile, self.connection, length, timeout=RECEIPT_TIMEOUT
                )
            except (http_utils.RequestTooLarge, http_utils.BadRequest, socket.timeout):
                return None
            return payload if len(payload) == length else None

        def _login(self):
            body = self._form_body()
            if body is None:
                self._json_error(400, 'bad_request')
                return
            form = parse_qs(body.decode('utf-8', 'replace'))
            if (form.get('password') or [''])[0] != PREVIEW_LOGIN_PASSWORD:
                self._json(200, {'ok': False, 'message': '密码错误'})
                return
            sid = sessions.create_admin()
            cookie = f'sid={sid}; Path=/; HttpOnly; SameSite=Lax'
            self._json(200, {'ok': True, 'redirect_to': '/admin/plans'}, [('Set-Cookie', cookie)])

        def _logout(self):
            self._form_body()
            sessions.revoke(self._sid())
            cookie = 'sid=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax'
            self._json(200, {'ok': True, 'redirect_to': '/login'}, [('Set-Cookie', cookie)])

        def _json_api(self):
            try:
                raw_length = self.headers.get('Content-Length', '')
                length = int(raw_length)
                if length < 0 or length > (
                    10 * 1024 * 1024
                    if urlsplit(self.path).path == '/api/chat/documents/upload'
                    else 128 * 1024
                ):
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
            relative = path.removeprefix(ASSET_PREFIX)
            file = (Path(DIST) / relative).resolve()
            if not file.is_relative_to(Path(DIST).resolve()) or not file.is_file():
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
            payload = (Path(DIST) / 'index.html').read_text(encoding='utf-8')
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
            if path in LOGIN_PAGES:
                marker = f'data-public-host="{escaped_public_host}"'
                replacement = marker + f' data-password-max-length="{PASSWORD_MAX_LENGTH}"'
                if payload.count(marker) != 1:
                    raise RuntimeError('React login bootstrap marker is missing or ambiguous')
                payload = payload.replace(marker, replacement, 1)
            self._write(200, payload.encode(), 'text/html; charset=utf-8')

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in REACT_PAGES:
                self._react_page(path)
            elif path == '/api/v1/session':
                self._session()
            elif path.startswith('/api/'):
                self._api()
            elif path.startswith(ASSET_PREFIX):
                self._react_asset(path)
            else:
                self.send_error(404)

        def do_HEAD(self):
            self.do_GET()

        def do_POST(self):
            path = urlsplit(self.path).path
            if path == '/api/v1/login':
                self._login()
                return
            if path in ('/api/v1/logout', '/api/v1/user/logout'):
                self._logout()
                return
            request_path = urlsplit(self.path).path
            if (
                request_path
                in {
                    '/api/chat/completions',
                    '/api/v1/admin/services/probe',
                    '/api/journal',
                }
                or request_path.startswith(('/api/chat/',))
                or (
                    request_path.startswith('/api/ai/services/')
                    and request_path.endswith(('/test', '/test/generation', '/test/structured'))
                )
            ):
                self._json_api()
                return
            self.send_error(405, 'Read-only preview')

        def do_PUT(self):
            request_path = urlsplit(self.path).path
            if request_path in {
                '/api/chat/settings',
                '/api/v1/admin/services',
                '/api/ai/service-bindings',
            } or request_path.startswith(
                ('/api/ai/services/', '/api/journal/', '/api/chat/', '/api/plans/schedule/')
            ):
                self._json_api()
                return
            self.send_error(501)

        def do_PATCH(self):
            if urlsplit(self.path).path.startswith('/api/chat/'):
                self._json_api()
                return
            self.send_error(404)

        def do_DELETE(self):
            if urlsplit(self.path).path.startswith(
                ('/api/journal/', '/api/chat/')
            ):
                self._json_api()
                return
            self.send_error(501)

    return ReactPreview


@contextmanager
def preview_server(port=0, *, overview_fixture=False):
    """Serve the built workbench + AI frontend and fictional state on loopback."""
    del overview_fixture  # Accepted for runner compatibility; there is no network overview.
    dist = Path(DIST)
    if not (dist / 'index.html').is_file() or not (dist / 'manifest.json').is_file():
        raise RuntimeError('Build the React frontend before starting its preview')
    allowed_assets = _manifest_assets(dist)
    sessions = PreviewSessions()
    with (
        tempfile.TemporaryDirectory(prefix='workbench-react-fixture-') as directory,
        pytest.MonkeyPatch.context() as patch,
        isolated_preview(patch) as allowed_ports,
    ):
        import base64

        import web_api.chat_tools as chat_tools
        from jsonschema import Draft202012Validator
        from referencing import Registry

        def preview_validate(schema, arguments):
            def refuse(uri):
                raise ValueError('Preview forbids schema networking')

            Draft202012Validator(schema, registry=Registry(retrieve=refuse)).validate(arguments)

        async def preview_python(code, files):
            # UI fixture only. Real container behavior is verified separately.
            return {
                'stdout': 'mean = 2.0',
                'error': None,
                'files': [{'name': 'mean.txt', 'data': base64.b64encode(b'mean=2.0').decode()}],
            }

        patch.setattr(chat_tools, 'validate_arguments', preview_validate)
        patch.setattr(chat_tools, 'run_python', preview_python)
        preview_ai_root = Path(directory) / 'ai'
        trending_store = TrendingStore(
            Path(directory) / 'github-trending.json',
            fetcher=_offline_trending_fetcher,
        )
        with managed_preview_http_server(('127.0.0.1', 0), PreviewOpenAIGateway) as gateway:
            allowed_ports.add(gateway.server_port)
            app = create_app(
                sessions,
                max_requests=4,
                service_center_store=ServiceCenterStore(Path(directory) / 'services.json'),
                ai_services_store=AIServiceStore(
                    preview_ai_root / 'services.json',
                    chat_legacy_path=preview_ai_root / 'missing-chat.json',
                    video_legacy_path=preview_ai_root / 'missing-video.json',
                    backup_dir=preview_ai_root / 'migration-backup',
                ),
                plans_store=PlanStore(Path(directory) / 'plans' / 'tasks.json'),
                schedule_store=ScheduleStore(Path(directory) / 'plans' / 'schedule.json'),
                journal_store=JournalStore(Path(directory) / 'journal' / 'entries.json'),
                chat_workspace_store=PreviewWorkspaceStore(
                    Path(directory) / 'chat' / 'workspace.sqlite3'
                ),
                chat_workspace_settings=PreviewWorkspaceProvider(),
                github_trending_store=trending_store,
                video_settings_store=VideoSettingsStore(preview_ai_root / 'video-settings.json'),
                video_workflow_store=WorkflowStore(preview_ai_root / 'video-workflows.json'),
                video_asset_store=AssetStore(preview_ai_root / 'video-assets'),
                internal_token_file=Path(directory) / 'internal-token',
            )
            with TestClient(app, client=('127.0.0.1', 50000)) as api_client:
                handler = _handler(api_client, sessions, allowed_assets)
                with managed_preview_http_server(('127.0.0.1', port), handler) as server:
                    server.preview_admin_cookie = sessions.create_admin()
                    server.preview_admin_other_cookie = sessions.create_admin()
                    server.preview_openai_base_url = f'http://127.0.0.1:{gateway.server_port}/v1'
                    server.preview_trending_store = trending_store
                    server.preview_login_password = PREVIEW_LOGIN_PASSWORD
                    allowed_ports.add(server.server_port)
                    yield server


if __name__ == '__main__':
    with preview_server(18765):
        threading.Event().wait()
