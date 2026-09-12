"""Loopback-only visual test server. Isolated data; all writes rejected."""

import builtins
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'hysteria'), str(Path(__file__).parent)]
import pytest
import subscription_service as ss
from preview_http_server import managed_preview_http_server
from test_product_ux_regressions import _seed_state


class Preview(BaseHTTPRequestHandler):
    def log_request(self, code='-', size='-'):
        if str(code).isdigit() and int(code) >= 400:
            super().log_request(code, size)

    def do_GET(self):
        request = urlsplit(self.path)
        path = request.path
        query = parse_qs(request.query)
        if path == '/admin/health.fragment' and query.get('snapshot') != ['1']:
            payload = ss.render_health_fragment().encode()
            kind = 'text/html; charset=utf-8'
        elif path in (
            '/admin/overview.json',
            '/admin/analytics.json',
            '/user/panel.json',
            '/admin/health.fragment',
            '/admin/user/demo_alex.json',
        ):
            if path == '/admin/user/demo_alex.json':
                data = ss._build_user_json_payload(
                    'demo_alex', now=ss.local_now(), include_charts='summary=1' not in self.path
                )
            elif path == '/admin/health.fragment':
                data = ss._build_health_read_snapshot()
            elif path == '/admin/overview.json':
                data = ss._build_overview_json_payload(now=ss.local_now())
            elif path == '/admin/analytics.json':
                data = ss._build_analytics_json_payload(
                    now=ss.local_now(), include_charts='summary=1' not in self.path
                )
            else:
                data = ss._build_panel_json_payload('demo_alex', DEMO, now=ss.local_now())
            payload = json.dumps(data).encode()
            kind = 'application/json'
        elif path == '/static/style.css':
            payload = (Path(ss.__file__).parent / 'admin.css').read_bytes()
            kind = 'text/css'
        elif path.startswith('/static/'):
            scripts = {
                '/static/admin-poll.js': 'admin_poll.js',
                '/static/usage.js': 'usage.js',
            }
            file = (
                Path(ss.__file__).parent / scripts[path]
                if path in scripts
                else Path(ss.__file__).parent / 'static' / path.removeprefix('/static/')
            )
            if '..' in Path(path).parts or not file.is_file():
                self.send_error(404)
                return
            payload = file.read_bytes()
            kind = 'text/javascript' if file.suffix == '.js' else 'application/octet-stream'
        elif path == '/':
            payload = ss.render_home('preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        elif path in ('/logout', '/user/logout'):
            payload = ss.render_logout_confirmation(
                'preview.invalid', user_panel=path == '/user/logout'
            ).encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/user/change-password':
            payload = ss.render_user_change_password('preview.invalid', 'demo_alex').encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/admin/user/demo_alex':
            payload = ss.render_user_detail_page('demo_alex', 'preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/login':
            payload = ss.render_login('preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/admin':
            payload = ss.render_admin('preview.invalid', 'http://preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/user/panel':
            config = dict(DEMO, disabled=True) if 'state=disabled' in self.path else DEMO
            payload = ss.render_user_panel(
                'preview.invalid',
                'http://preview.invalid',
                'demo_alex',
                'demo-token',
                config,
                session_auth=True,
            ).encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/history':
            payload = ss.render_admin_shell(
                'usage', '历史每日明细', ss._render_daily_table_collapsed('preview.invalid')
            ).encode()
            kind = 'text/html; charset=utf-8'
        elif path in (
            '/admin/usage',
            '/admin/settings',
            '/admin/config',
            '/admin/rules',
            '/admin/health',
            '/admin/incidents',
            '/admin/landing-egresses',
            '/admin/logs',
        ):
            renderer = {
                '/admin/usage': ss.render_usage_page,
                '/admin/settings': ss.render_settings,
                '/admin/config': ss.render_config_editor,
                '/admin/rules': ss.render_rules,
                '/admin/health': ss.render_health,
                '/admin/incidents': ss.render_incidents,
                '/admin/landing-egresses': ss.render_landing_egresses,
                '/admin/logs': ss.render_reset_logs,
            }[path]
            payload = renderer('preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):
        self.send_error(405, 'Read-only preview')


DEMO = {
    'sub_token': 'demo-token',
    'monthly_quota_bytes': 100 * 1024**3,
    'max_devices': 3,
    'disabled': False,
}


@contextmanager
def isolated_preview(directory):
    """Redirect renderer state before populating fictional preview data."""
    root = Path(directory).resolve()
    with pytest.MonkeyPatch.context() as patch:
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

        patch.setattr(socket.socket, 'connect', preview_connect)
        patch.setattr(subprocess, 'Popen', preview_process)
        protected = (
            '/root/hysteria',
            '/usr/local/etc/xray',
            '/etc/hysteria',
            '/run/hy2-locks',
            '/var/lib/hysteria',
        )

        def guard(opener):
            def checked(file, *args, **kwargs):
                if isinstance(file, (str, bytes, os.PathLike)):
                    path = os.path.realpath(os.fsdecode(file))
                    if any(path == prefix or path.startswith(prefix + '/') for prefix in protected):
                        raise PermissionError('Preview cannot access production path')
                return opener(file, *args, **kwargs)

            return checked

        # Redirected fixtures are the primary boundary; these guards fail closed
        # if a renderer or one of its dependencies retains a hard-coded path.
        patch.setattr(builtins, 'open', guard(builtins.open))
        patch.setattr(io, 'open', guard(io.open))
        patch.setattr(os, 'open', guard(os.open))
        for name, value in vars(ss).copy().items():
            if isinstance(value, Path) and name.isupper() and name != '_STATIC_DIR':
                patch.setattr(ss, name, root / name.lower() / value.name)
        patch.setattr(ss, 'HY_API_SECRET_FILE', str(root / 'api_secret'))
        _seed_state(root, patch, users={'demo_alex': DEMO})
        patch.setattr(ss.alerts, 'STATE_FILE', root / 'alerts.json')
        ss.alerts.STATE_FILE.write_text(json.dumps({'quota_80': {'demo_alex': '2026-07-14'}}))
        ss.RESET_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        ss.RESET_LOG_FILE.write_text(
            json.dumps(
                {
                    'time': ss.local_now().isoformat(),
                    'actor': 'preview-admin',
                    'ip': '192.0.2.10',
                    'action': 'reset_user',
                    'target': 'demo_alex',
                    'month': '2026-07',
                    'before': {'total': 1024**3},
                    'after': {'total': 0},
                }
            )
            + '\n'
        )
        update_path = root / 'update.json'
        update_path.write_text(
            json.dumps(
                {
                    'status': 'skipped',
                    'reason': 'preview-policy-disabled',
                    'ts': ss.local_now().isoformat(),
                }
            )
        )
        load_update_state = ss.hysteria_update.load_state
        patch.setattr(ss.hysteria_update, 'load_state', lambda: load_update_state(update_path))
        # Real health presentation consumes fictional results at the external probe seam.
        for name in (
            'probe_systemd',
            'probe_cert',
            'probe_panel_tls',
            'probe_certbot_renewal',
            'probe_disk',
            'probe_cron_heartbeat',
            'probe_auth_readiness',
            'probe_xray_config_permissions',
            'probe_hysteria_update',
            'probe_recent_backup',
        ):
            patch.setattr(ss, name, lambda *args, **kwargs: {'ok': True, 'label': '预览：正常'})
        patch.setattr(ss, '_landing_registry_or_empty', lambda: {})
        yield allowed_ports


@contextmanager
def preview_server(port=0):
    """Own fixture state, loopback socket and serving thread as one lifetime."""
    with (
        tempfile.TemporaryDirectory(prefix='hy2-workspace-fixture-') as directory,
        isolated_preview(directory) as allowed_ports,
    ):
        with managed_preview_http_server(('127.0.0.1', port), Preview) as server:
            allowed_ports.add(server.server_port)
            yield server


if __name__ == '__main__':
    with preview_server(18764):
        threading.Event().wait()
