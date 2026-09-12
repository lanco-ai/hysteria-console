"""Run React logs acceptance checks against a managed fictional preview."""

import os
import subprocess
from pathlib import Path

from react_preview_server import preview_server


def main():
    root = Path(__file__).resolve().parents[1]
    default_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-logs-slice' / 'task-2-screenshots'
    )
    with preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
            REACT_PREVIEW_USER_COOKIE=server.preview_user_cookie,
            REACT_SCREENSHOT_DIR=os.environ.get('REACT_SCREENSHOT_DIR', str(default_screenshots)),
        )
        subprocess.run(
            ['node', str(root / 'tests' / 'react_logs_browser.cjs')],
            cwd=root,
            env=env,
            check=True,
            timeout=180,
        )


if __name__ == '__main__':
    main()
