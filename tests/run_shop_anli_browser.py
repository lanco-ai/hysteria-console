"""Bounded real-app Anli fixture acceptance, run only in the private harness."""

import json
import os
import subprocess
from pathlib import Path

import react_preview_server as preview
from web_api.shop_anli_source import normalize_products
from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore


class FixtureStore(ShopStore):
    def __init__(self, *args, **kwargs):
        self.fixture_now = 1000
        self.anli_calls = 0
        super().__init__(
            *args, **kwargs, secondary_fetcher=self.anli_fetcher, clock=lambda: self.fixture_now
        )

    async def anli_fetcher(self):
        self.anli_calls += 1
        if self.anli_calls > 1:
            raise SourceError('fixture secondary unavailable')
        value = json.loads((Path(__file__).parent / 'fixtures/shop/anli-products.json').read_text())
        return normalize_products(
            value, {row['id']: {**row, 'config': []} for row in value['data']}
        )

    async def refresh(self, *, manual=False):
        if manual:
            self.fixture_now += 61
        return await super().refresh(manual=manual)


def main():
    preview.DIST = Path(os.environ['ANLI_TEST_DIST']).resolve()
    preview.ShopStore = FixtureStore
    with preview.preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
        )
        subprocess.run(
            ['node', 'tests/react_shop_anli_browser.cjs'], env=env, check=True, timeout=120
        )
    # Reuse the unchanged original fixture for the existing shop acceptance.
    preview.ShopStore = ShopStore
    with preview.preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
        )
        env.pop('REACT_SHOP_SCREENSHOT_DIR', None)
        subprocess.run(['node', 'tests/react_shop_browser.cjs'], env=env, check=True, timeout=180)


if __name__ == '__main__':
    main()
