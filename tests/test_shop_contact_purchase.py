"""Only the merchant's Plus renewal SKU allows contact purchase without source stock."""

import asyncio
import json

import pytest

from test_shop_store import make_store, products
from web_api.shop_source import normalize_products


def test_plus_renewal_contact_purchase_preserves_supplier_stock_and_other_sold_out_skus(tmp_path):
    async def run():
        data = products()
        other = next(product for product in data if product['id'] == 2)['skus'][0]
        other.update(auto_stock_available=0, stock_status='out_of_stock', is_sold_out=True)

        async def fetch():
            return normalize_products(data)

        store = make_store(tmp_path, fetcher=fetch, clock=lambda: 1000)
        await store.refresh()
        await store.update({'revision': 0, 'telegram': 'merchant_shop', 'skus': {
            '1:1': {'price_cents': 12800, 'promo_price_cents': 12300, 'published': True},
            '1:4': {'price_cents': 12900, 'published': True},
            '2:7': {'price_cents': 110000, 'published': True},
        }})
        source_before = store.path.read_bytes()
        settings_before = store.settings_path.read_bytes()
        public = await store.public()
        variants = {variant['id']: variant for product in public['products'] for variant in product['variants']}
        assert public['status'] == 'ready'
        assert variants['1:4'] == {
            'id': '1:4', 'label': 'plus续费卡密', 'price_cents': 12900, 'promo_price_cents': None,
            'available': True, 'contact_only': True, 'quantity': None, 'sales': None,
        }
        assert variants['1:1']['available'] is True and 'contact_only' not in variants['1:1']
        assert variants['1:1']['promo_price_cents'] == 12300
        assert variants['2:7']['available'] is False and 'contact_only' not in variants['2:7']
        admin = await store.admin()
        assert next(row for row in admin['items'] if row['key'] == '1:4')['available'] is False
        assert admin['inventory'] == {}
        assert store.path.read_bytes() == source_before
        assert store.settings_path.read_bytes() == settings_before
        assert next(row for row in json.loads(source_before)['items'] if row['key'] == '1:4')['available'] is False

    asyncio.run(run())


def test_plus_renewal_contact_purchase_requires_a_fresh_source(tmp_path):
    async def run():
        now = [1000]
        store = make_store(tmp_path, clock=lambda: now[0])
        await store.refresh()
        await store.update({'revision': 0, 'telegram': 'merchant_shop',
                            'skus': {'1:4': {'price_cents': 12900, 'published': True}}})
        assert (await store.public())['products'][0]['variants'][0]['available'] is True
        now[0] = 1900
        public = await store.public()
        assert public['status'] == 'stale'
        assert public['products'][0]['variants'][0]['available'] is False

    asyncio.run(run())


@pytest.mark.parametrize('published,enabled', [(False, True), (True, False)])
def test_plus_renewal_contact_purchase_respects_merchant_visibility(tmp_path, published, enabled):
    async def run():
        store = make_store(tmp_path, clock=lambda: 1000)
        await store.refresh()
        await store.update({'revision': 0, 'telegram': 'merchant_shop',
                            'source_enabled': {'gpt': enabled},
                            'skus': {'1:4': {'price_cents': 12900, 'published': published}}})
        assert (await store.public())['products'] == []

    asyncio.run(run())
