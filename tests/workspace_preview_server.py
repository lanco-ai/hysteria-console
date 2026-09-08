"""Loopback-only visual test server. Isolated data; all writes rejected."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'hysteria'), str(Path(__file__).parent)]
import pytest
import subscription_service as ss
from test_product_ux_regressions import _seed_state


class Preview(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/static/style.css':
            payload = (Path(ss.__file__).parent / 'admin.css').read_bytes()
            kind = 'text/css'
        elif path.startswith('/static/'):
            scripts = {'/static/admin-poll.js': 'admin_poll.js', '/static/usage.js': 'usage.js', '/static/codex-quota.js': 'codex_quota.js'}
            file = (Path(ss.__file__).parent / scripts[path] if path in scripts else
                    Path(ss.__file__).parent / 'static' / path.removeprefix('/static/'))
            if '..' in Path(path).parts or not file.is_file():
                self.send_error(404)
                return
            payload = file.read_bytes()
            kind = 'text/javascript' if file.suffix == '.js' else 'application/octet-stream'
        elif path == '/admin':
            payload = ss.render_admin('preview.invalid', 'http://preview.invalid').encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/user/panel':
            payload = ss.render_user_panel('preview.invalid', 'http://preview.invalid', 'demo_alex', 'demo-token', DEMO, session_auth=True).encode()
            kind = 'text/html; charset=utf-8'
        elif path == '/history':
            payload = ss.render_admin_shell('usage', '历史每日明细', ss._render_daily_table_collapsed('preview.invalid')).encode()
            kind = 'text/html; charset=utf-8'
        elif path in ('/admin/usage', '/admin/settings', '/admin/config', '/admin/rules'):
            renderer = {'/admin/usage': ss.render_usage_page, '/admin/settings': ss.render_settings,
                        '/admin/config': ss.render_config_editor, '/admin/rules': ss.render_rules}[path]
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


DEMO = {'sub_token': 'demo-token', 'monthly_quota_bytes': 100 * 1024**3, 'max_devices': 3, 'disabled': False}

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='hy2-workspace-fixture-') as directory, pytest.MonkeyPatch.context() as patch:
        _seed_state(Path(directory), patch, users={'demo_alex': DEMO})
        patch.setattr(ss, '_landing_registry_or_empty', lambda: {})
        ThreadingHTTPServer(('127.0.0.1', 18764), Preview).serve_forever()
