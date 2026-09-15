"""Run React acceptance checks against a managed fictional preview."""

import os
import subprocess
import sys
from pathlib import Path

from react_preview_server import preview_server


def main():
    root = Path(__file__).resolve().parents[1]
    default_logs_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-logs-slice' / 'task-2-screenshots'
    )
    default_home_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-public-home' / 'task-1-screenshots'
    )
    default_login_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-login' / 'task-1-screenshots'
    )
    default_logout_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-logout' / 'task-1-screenshots'
    )
    default_password_screenshots = (
        root / '.superpowers' / 'sdd' / '2026-09-12-react-password-pages' / 'task-1-screenshots'
    )
    browser_tests = (
        'react_logs_browser.cjs',
        'react_home_browser.cjs',
        'react_login_browser.cjs',
        'react_logout_browser.cjs',
        'react_password_pages_browser.cjs',
        'react_overview_browser.cjs',
        'react_usage_browser.cjs',
        'react_health_browser.cjs',
        'react_incidents_browser.cjs',
        'react_config_browser.cjs',
        'react_rules_browser.cjs',
    )
    selected = os.environ.get('REACT_BROWSER_TEST')
    if selected is not None and selected not in browser_tests:
        print(f'unknown REACT_BROWSER_TEST: {selected}', file=sys.stderr)
        raise SystemExit(64)
    for browser_test in browser_tests:
        if selected not in (None, browser_test):
            continue
        with preview_server(
            overview_fixture=browser_test in {
                'react_overview_browser.cjs',
                'react_usage_browser.cjs',
                'react_health_browser.cjs',
                'react_incidents_browser.cjs',
            }
        ) as server:
            env = dict(
                os.environ,
                PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
                REACT_PREVIEW_ADMIN_OTHER_COOKIE=server.preview_admin_other_cookie,
                REACT_PREVIEW_USER_COOKIE=server.preview_user_cookie,
                REACT_PREVIEW_USER_OTHER_COOKIE=server.preview_user_other_cookie,
                REACT_PREVIEW_PASSWORD_USER_COOKIE=server.preview_password_user_cookie,
                REACT_PREVIEW_PASSWORD_USER_OTHER_COOKIE=server.preview_password_user_other_cookie,
                REACT_PREVIEW_MUST_CHANGE_COOKIE=server.preview_must_change_cookie,
                REACT_PREVIEW_LOGIN_PASSWORD=server.preview_login_password,
                REACT_PREVIEW_USER_PASSWORD=server.preview_user_password,
                REACT_PREVIEW_MUST_CHANGE_PASSWORD=server.preview_must_change_password,
                REACT_SCREENSHOT_DIR=os.environ.get(
                    'REACT_SCREENSHOT_DIR', str(default_logs_screenshots)
                ),
                REACT_HOME_SCREENSHOT_DIR=os.environ.get(
                    'REACT_HOME_SCREENSHOT_DIR', str(default_home_screenshots)
                ),
                REACT_LOGIN_SCREENSHOT_DIR=os.environ.get(
                    'REACT_LOGIN_SCREENSHOT_DIR', str(default_login_screenshots)
                ),
                REACT_LOGOUT_SCREENSHOT_DIR=os.environ.get(
                    'REACT_LOGOUT_SCREENSHOT_DIR', str(default_logout_screenshots)
                ),
                REACT_PASSWORD_SCREENSHOT_DIR=os.environ.get(
                    'REACT_PASSWORD_SCREENSHOT_DIR', str(default_password_screenshots)
                ),
            )
            subprocess.run(
                ['node', str(root / 'tests' / browser_test)],
                cwd=root,
                env=env,
                check=True,
                timeout=180,
            )


if __name__ == '__main__':
    main()
