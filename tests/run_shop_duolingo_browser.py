"""Offline acceptance for a supplier-backed, unpriced Duolingo product."""
import os
import subprocess

import react_preview_server as preview
from test_shop_prodseller_duolingo import products
from web_api.shop_prodseller_source import normalize_products
from web_api.shop_store import ShopStore


async def feed():
    return normalize_products(products())


class DuolingoStore(ShopStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, prodseller_fetcher=feed)


def main():
    preview.ShopStore = DuolingoStore
    with preview.preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                   REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie)
        subprocess.run(['node', 'tests/react_shop_duolingo_browser.cjs'], env=env, check=True, timeout=180)


if __name__ == '__main__':
    main()
