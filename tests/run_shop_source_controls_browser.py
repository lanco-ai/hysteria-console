"""Source switches against the real API and store, with fictional offline feeds only."""

import os
import subprocess
from pathlib import Path

import react_preview_server as preview
from tests.test_shop_fanai_source import commodity
from web_api.shop_fanai_source import normalize_products
from web_api.shop_store import ShopStore


async def fanai_feed():
    return normalize_products([commodity(16, '菲律宾ChatGpt pro20x卡密', 900, 2)], {2})


async def empty_feed():
    return []


class FixtureStore(ShopStore):
    def __init__(self, *args, **kwargs):
        self.fixture_now = 1000
        self.seeded = False
        super().__init__(*args, **kwargs, local_products=True, fanai_fetcher=fanai_feed,
                         sms_fetcher=empty_feed, clock=lambda: self.fixture_now)

    def _sources(self, settings=None):
        sources = super()._sources(settings)
        # This scenario compares two external sources and one merchant-owned product.
        sources['local']['items'] = [row for row in sources['local']['items']
                                     if row['product_id'] == '6000000000001']
        return sources

    async def refresh(self, *, manual=False):
        if manual:
            self.fixture_now += 61
        result = await super().refresh(manual=manual)
        if not self.seeded:
            self.seeded = True
            result = await self.update({
                'revision': 0, 'client': 3, 'telegram': 'preview_shop',
                'skus': {'2:7': {'price_cents': 120000, 'promo_price_cents': 119000, 'published': True},
                         '4000000000016:1': {'price_cents': 115000, 'published': True},
                         '6000000000001:1': {'price_cents': 13000, 'published': True}},
                'products': {'2': {'description': '预览商品说明', 'after_sales': '预览售后条款'}},
                'display': {'2': {'badge': '学生价', 'sold': 3}},
                'local_names': {'products': {'6000000000001': '本人账号开通指导'},
                                'variants': {'6000000000001:1': '微软邮箱指导'}},
            })
        return result


def main():
    preview.ShopStore = FixtureStore
    if os.environ.get('REACT_PREVIEW_DIST'):
        preview.DIST = Path(os.environ['REACT_PREVIEW_DIST'])
    with preview.preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                   REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie)
        subprocess.run(['node', 'tests/react_shop_source_controls_browser.cjs'],
                       env=env, check=True, timeout=180)


if __name__ == '__main__':
    main()
