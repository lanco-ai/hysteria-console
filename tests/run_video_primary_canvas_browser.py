"""Run the video canvas browser test against an isolated React build."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))

import react_preview_server


def main():
    dist = Path(os.environ['VIDEO_TEST_DIST']).resolve()
    react_preview_server.DIST = dist
    with react_preview_server.preview_server() as server:
        env = dict(os.environ)
        env['PREVIEW_BASE_URL'] = f'http://127.0.0.1:{server.server_port}'
        env['REACT_PREVIEW_ADMIN_COOKIE'] = server.preview_admin_cookie
        subprocess.run(
            ['node', str(ROOT / 'tests' / 'react_video_primary_canvas_browser.cjs')],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=180,
        )


if __name__ == '__main__':
    main()
