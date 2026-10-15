"""Focused real-app check of the Claude product notices; run through the private harness."""

import json
import os
import subprocess
from pathlib import Path

import react_preview_server as preview
from web_api.shop_anli_source import normalize_products
from web_api.shop_store import ShopStore


class FixtureStore(ShopStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, secondary_fetcher=self.anli, clock=lambda: 1000)

    async def anli(self):
        value = json.loads((Path(__file__).parent / 'fixtures/shop/anli-products.json').read_text())
        return normalize_products(
            value, {row['id']: {**row, 'config': []} for row in value['data']}
        )


def main():
    preview.DIST = Path(os.environ['CLAUDE_PROJECT_TEST_DIST']).resolve()
    preview.ShopStore = FixtureStore
    with preview.preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
        )
        subprocess.run(
            ['node', 'tests/react_claude_notices_browser.cjs'], env=env, check=True, timeout=180
        )


if __name__ == '__main__':
    main()
