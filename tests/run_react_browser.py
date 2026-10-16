"""Run the shop's React acceptance checks against a managed fictional preview."""

import os
import subprocess
import sys
from pathlib import Path

from react_preview_server import preview_server

BROWSER_TESTS = (
    'react_home_browser.cjs',
    'react_shop_browser.cjs',
    'react_shop_sources_browser.cjs',
    'react_shop_admin_layout_browser.cjs',
    'react_shop_inventory_browser.cjs',
    'react_shop_procurement_browser.cjs',
    'react_shop_promo_browser.cjs',
    'react_shop_offer_browser.cjs',
    'react_shop_referral_browser.cjs',
    'react_shop_navigation_browser.cjs',
    'react_shop_services_browser.cjs',
    'react_shop_gemini_browser.cjs',
    'react_shop_duolingo_browser.cjs',
    'react_shop_sms_browser.cjs',
)
# Suites whose fixtures need their own runner.
DEDICATED_RUNNERS = {
    'react_shop_duolingo_browser.cjs': 'tests/run_shop_duolingo_browser.py',
    'react_shop_gemini_browser.cjs': 'tests/run_shop_gemini_browser.py',
    'react_shop_sms_browser.cjs': 'tests/run_shop_sms_browser.py',
}


def main():
    root = Path(__file__).resolve().parents[1]
    selected = os.environ.get('REACT_BROWSER_TEST')
    if selected is not None and selected not in BROWSER_TESTS:
        print(f'unknown REACT_BROWSER_TEST: {selected}', file=sys.stderr)
        raise SystemExit(64)
    if selected is None:
        # Keep each browser suite in a fresh Python process so one suite's
        # fixtures and threads can never leak into the next.
        for browser_test in BROWSER_TESTS:
            env = dict(os.environ, REACT_BROWSER_TEST=browser_test)
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve())],
                cwd=root,
                env=env,
                check=True,
                timeout=180,
            )
        return
    runner = DEDICATED_RUNNERS.get(selected)
    if runner is not None:
        subprocess.run([sys.executable, str(root / runner)], cwd=root, check=True, timeout=180)
        return
    with preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
            REACT_PREVIEW_ADMIN_OTHER_COOKIE=server.preview_admin_other_cookie,
            REACT_PREVIEW_LOGIN_PASSWORD=server.preview_login_password,
            REACT_SCREENSHOT_DIR=os.environ.get(
                'REACT_SCREENSHOT_DIR', str(root / 'test-results' / 'react-screenshots')
            ),
        )
        subprocess.run(
            ['node', str(root / 'tests' / selected)],
            cwd=root,
            env=env,
            check=True,
            timeout=180,
        )


if __name__ == '__main__':
    main()
