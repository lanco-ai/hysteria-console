"""Run service bookmark CRUD against an isolated, real backend preview."""
import os
import subprocess
from pathlib import Path

from react_preview_server import preview_server

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    with preview_server() as server:
        subprocess.run(
            ['node', 'tests/react_services_browser.cjs'], cwd=ROOT,
            env=dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                     REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie),
            check=True, timeout=75,
        )
