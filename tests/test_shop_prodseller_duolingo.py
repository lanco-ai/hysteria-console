"""Duolingo import is a supplier-backed, initially unpublished product."""
import asyncio
import json
from decimal import Decimal

import pytest

from web_api.shop_prodseller_source import normalize_products
from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore, validate_items

SUPPLIER_ID = '6aa5bafb77c587d75cbe2a8e'
PRODUCT_ID = '2000000000003'
SKU = PRODUCT_ID + ':1'


def products(**overrides):
    return {'products': [{'id': SUPPLIER_ID, 'name': 'Duolingo Super 12M ',
                         'price': Decimal('.29'), 'inStock': True,
                         'requiresEmailActivation': False, 'delivery': {'type': 'instant'},
                         **overrides}]}


def test_duolingo_identity_cost_and_incompatible_delivery():
    rows = normalize_products(products())
    assert validate_items(rows, 'prodseller') == rows
    assert rows[0]['product_id'] == PRODUCT_ID and rows[0]['key'] == SKU
    assert rows[0]['title'] == 'Duolingo Super 12M · $0.29'
    assert rows[0]['cost_cents'] == 209
    for changed in ({'inStock': False}, {'requiresEmailActivation': True}, {'delivery': {'type': 'manual'}}):
        assert normalize_products(products(**changed))[0]['available'] is False


@pytest.mark.parametrize('name', ['Duolingo Super 1M', 'Duolingo Max 12M', 'Duolingo Super 12M Family', '', None])
def test_repurposed_supplier_id_fails_closed(name):
    with pytest.raises(SourceError):
        normalize_products(products(name=name))


def test_import_is_unpriced_unpublished_and_category_copy_stay_private_until_published(tmp_path):
    async def empty():
        return []
    async def feed():
        return normalize_products(products())
    async def run():
        store = ShopStore(tmp_path / 'source.json', fetcher=empty, prodseller_fetcher=feed, clock=lambda: 1000)
        admin = await store.refresh()
        row, = [item for item in admin['items'] if item['product_id'] == PRODUCT_ID]
        assert row['category'] == '多邻国' and row['provider'] == 'prodseller'
        assert SKU not in admin['skus'] and admin['revision'] == 0
        assert not store.settings_path.exists()
        assert (await store.public())['products'] == []
        assert 'Claim Offer' in admin['products'][PRODUCT_ID]['description']
        assert admin['products'][PRODUCT_ID]['after_sales'] == ''
        # Publishing remains an explicit merchant action, exercised only in isolated fixtures.
        await store.update({'revision': 0, 'client': 3, 'telegram': 'demo_shop',
                            'skus': {SKU: {'price_cents': 1500, 'published': True}}})
        public = await store.public()
        product, = public['products']
        assert product['title'] == 'Duolingo Super 12 个月' and product['category'] == '多邻国'
        wire = json.dumps(public)
        for private in ('prodseller', SUPPLIER_ID, 'cost_cents', 'source_url', '$0.29'):
            assert private not in wire
        admin = await store.admin()
        await store.update({'revision': admin['revision'], 'client': 3, 'telegram': 'demo_shop',
                            'skus': {SKU: {'price_cents': 1500, 'published': True}},
                            'source_enabled': {'prodseller': False}})
        assert (await store.public())['products'] == []
    asyncio.run(run())
