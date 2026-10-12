import asyncio
import copy

import httpx
import pytest


def product(identity, title, skus, **changes):
    value = {'id': identity, 'slug': f'p{identity}', 'title': {'zh-CN': title}, 'is_sold_out': False, 'skus': skus}
    value.update(changes)
    return value


def sku(identity, price, label='', **changes):
    value = {'id': identity, 'spec_values': {'zh-CN': label} if label else {}, 'price_amount': price, 'is_active': True, 'is_sold_out': False}
    value.update(changes)
    return value


CATALOG = [
    product(1, 'ChatGPT Plus 成品号 月卡', [sku(11, '88.00', '月卡'), sku(12, '30.00', '周卡')]),
    product(2, 'Claude Max 20x 官方订阅', [sku(21, '1200')]),
    product(3, 'Claude Pro 代充', [sku(31, '135.5')], is_sold_out=True),
    product(4, 'SuperGrok Heavy', [sku(41, '1500', is_sold_out=True)]),
    product(5, 'SuperGrok 一个月', [sku(51, '160', '1 个月'), sku(52, '1', '停售', is_active=False)]),
    product(6, 'Netflix 共享', [sku(61, '20')]),
]


def test_only_known_plans_enter_under_shared_public_titles():
    from web_api.shop_fanai_source import normalize_products

    rows = normalize_products(copy.deepcopy(CATALOG))
    assert [(row['key'], row['public_title'], row['label'], row['cost_cents'], row['available']) for row in rows] == [
        ('4000000000001:12', 'ChatGPT Plus', '周卡', 3000, True),
        ('4000000000001:11', 'ChatGPT Plus', '月卡', 8800, True),
        ('4000000000002:21', 'Claude Max 20x', '标准规格', 120000, True),
        ('4000000000003:31', 'Claude Pro', '标准规格', 13550, False),
        ('4000000000004:41', 'Grok SuperGrok Heavy', '标准规格', 150000, False),
        ('4000000000005:51', 'Grok SuperGrok', '1 个月', 16000, True),
    ]
    assert rows[0]['source_url'] == 'https://fanai.us.ci/products/p1'


@pytest.mark.parametrize('change', [{'skus': []}, {'slug': ''}, {'id': 10**12}])
def test_selected_products_with_invalid_fields_fail_closed(change):
    from web_api.shop_fanai_source import normalize_products
    from web_api.shop_source import SourceError

    with pytest.raises(SourceError):
        normalize_products([{**product(2, 'Claude Pro', [sku(21, '100')]), **change}])


def test_fetch_reads_the_anonymous_public_catalog_of_fanai():
    from web_api.shop_fanai_source import fetch_catalog

    def handler(request):
        assert request.url.host == 'fanai.us.ci' and request.method == 'GET'
        if request.url.path == '/api/v1/public/config':
            return httpx.Response(200, json={'status_code': 0, 'data': {'currency': 'CNY'}})
        assert request.url.path == '/api/v1/public/products'
        return httpx.Response(200, json={
            'status_code': 0, 'data': CATALOG,
            'pagination': {'page': 1, 'total': len(CATALOG), 'total_page': 1},
        })

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_catalog(client=client)

    assert len(asyncio.run(run())) == 6


def test_store_groups_fanai_rows_under_existing_brands(tmp_path):
    from web_api.shop_fanai_source import normalize_products
    from web_api.shop_store import ShopStore

    rows = normalize_products(copy.deepcopy(CATALOG))

    async def no_gpt():
        return []

    async def fanai():
        return rows

    store = ShopStore(tmp_path / 'source.json', fetcher=no_gpt, fanai_fetcher=fanai)
    state = asyncio.run(store.refresh(manual=True))
    assert set(state['sources']) == {'gpt', 'fanai'}
    skus = {row['key']: {'price_cents': 99900, 'published': True} for row in rows}
    asyncio.run(store.update({'revision': 0, 'telegram': 'my_shop', 'skus': skus}))
    categories = {product['title']: product['category'] for product in asyncio.run(store.public())['products']}
    assert categories == {
        'ChatGPT Plus': 'GPT', 'Claude Max 20x': 'Claude', 'Claude Pro': 'Claude',
        'Grok SuperGrok Heavy': 'Grok', 'Grok SuperGrok': 'Grok',
    }
