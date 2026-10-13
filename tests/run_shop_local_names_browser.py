"""Local-name editing against the real shop backend, with only fictional supplier data."""

import json
import os
import subprocess
from pathlib import Path

import react_preview_server as preview
from web_api.shop_anli_source import normalize_products
from web_api.shop_store import ShopStore


async def supplier_feed():
    value = json.loads((Path(__file__).parent / 'fixtures/shop/anli-products.json').read_text())
    return normalize_products(value, {row['id']: {**row, 'config': []} for row in value['data']})


class LocalStore(ShopStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, local_products=True, secondary_fetcher=supplier_feed)


def main():
    preview.ShopStore = LocalStore
    if os.environ.get('REACT_PREVIEW_DIST'):
        preview.DIST = Path(os.environ['REACT_PREVIEW_DIST'])
    with preview.preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                   REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie)
        subprocess.run(['node', 'tests/react_shop_local_names_browser.cjs'],
                       env=env, check=True, timeout=180)


if __name__ == '__main__':
    main()
