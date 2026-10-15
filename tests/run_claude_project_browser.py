"""Focused real-app checks; invoke only through the private namespace harness."""

import os
import subprocess
from pathlib import Path

import react_preview_server as preview


def main():
    preview.DIST = Path(os.environ['CLAUDE_PROJECT_TEST_DIST']).resolve()
    selected = os.environ.get('CLAUDE_PROJECT_BROWSER_TEST')
    # The Claude product-notice suite moved with the shop to service/shop.
    suites = ['react_project_selector_browser.cjs']
    if selected:
        if selected not in suites:
            raise ValueError('unknown focused browser test')
        suites = [selected]
    for suite in suites:
        with preview.preview_server() as server:
            env = dict(
                os.environ,
                PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
            )
            subprocess.run(['node', f'tests/{suite}'], env=env, check=True, timeout=180)


if __name__ == '__main__':
    main()
