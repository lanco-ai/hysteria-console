"""Run browser checks against a managed fictional loopback preview."""

import os
import subprocess
from pathlib import Path

from workspace_preview_server import preview_server


def main():
    root = Path(__file__).resolve().parents[1]
    with preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_address[1]}')
        for name in ('workspace_visual.cjs', 'usage_refresh_browser.cjs', 'user_panel_browser.cjs'):
            subprocess.run(
                ['node', str(root / 'tests' / name)], cwd=root, env=env, check=True, timeout=180
            )


if __name__ == '__main__':
    main()
