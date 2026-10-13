"""Gemini product copy acceptance with an entirely fictional supplier feed."""

import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path

import react_preview_server as preview
from web_api.shop_prodseller_source import normalize_products
from web_api.shop_store import ShopStore


async def gemini_feed():
    fixture = Path(__file__).parent / 'fixtures/shop/prodseller-products.json'
    return normalize_products(json.loads(fixture.read_text(), parse_float=Decimal))


class GeminiStore(ShopStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, prodseller_fetcher=gemini_feed)


def main():
    preview.ShopStore = GeminiStore
    with preview.preview_server() as server:
        env = dict(
            os.environ,
            PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
            REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie,
        )
        subprocess.run(
            ['node', 'tests/react_shop_gemini_browser.cjs'], env=env, check=True, timeout=120
        )


if __name__ == '__main__':
    main()
