import asyncio
import copy
import hashlib
import hmac
import json

import httpx
import pytest

KEY = 'a' * 64
SECRET = 'b' * 128


def product(**changes):
    value = {
        'id': 17,
        'slug': 'x-premium-3m',
        'title': {'zh-CN': 'X Premium 3个月（官方礼物赠送·自动到账）'},
        'currency': 'CNY',
        'is_active': True,
        'price_amount': '99.00',
        'skus': [{'id': 18, 'spec_values': {}, 'price_amount': '99.00', 'stock_quantity': -1, 'is_active': True, 'stock_status': 'in_stock'}],
    }
    value.update(changes)
    return value


CATALOG = [
    product(),
    product(id=18, slug='x-premium-6m', title={'zh-CN': 'X Premium 6个月（官方礼物赠送·自动到账）'},
            skus=[{'id': 19, 'spec_values': {}, 'price_amount': '168.50', 'stock_quantity': 0, 'is_active': True}]),
    product(id=30, slug='muse', title={'zh-CN': 'Muse AI 会员'},
            skus=[{'id': 41, 'spec_values': {'zh-CN': '年卡'}, 'price_amount': '300', 'stock_quantity': 5, 'is_active': True},
                  {'id': 40, 'spec_values': {'zh-CN': '月卡'}, 'price_amount': '35.5', 'stock_quantity': 5, 'is_active': True},
                  {'id': 42, 'spec_values': {'zh-CN': '旧规格'}, 'price_amount': '1', 'stock_quantity': 5, 'is_active': False}]),
    product(id=50, slug='x-premium-12m', title={'zh-CN': 'X Premium 12个月'}),
    product(id=51, slug='netflix', title={'zh-CN': 'Netflix 会员'}, currency='USD'),
]


def credentials(tmp_path, value=None):
    path = tmp_path / 'lorry.json'
    path.write_text(json.dumps(value or {'api_key': KEY, 'api_secret': SECRET}))
    return path


def test_only_x_premium_and_muse_enter_with_private_upstream_identity():
    from web_api.shop_lorry_source import normalize_products

    rows = normalize_products(copy.deepcopy(CATALOG))
    assert [(row['key'], row['public_title'], row['label'], row['cost_cents'], row['available']) for row in rows] == [
        ('3000000000017:18', 'X Premium 3 个月', '标准规格', 9900, True),
        ('3000000000018:19', 'X Premium 6 个月', '标准规格', 16850, False),
        ('3000000000030:40', 'Muse AI', '月卡', 3550, True),
        ('3000000000030:41', 'Muse AI', '年卡', 30000, True),
    ]
    assert rows[0]['title'] == 'X Premium 3个月（官方礼物赠送·自动到账） · 商品 17'
    assert rows[0]['source_url'] == 'https://shop.lorry.site/products/x-premium-3m'


@pytest.mark.parametrize('change', [
    {'currency': 'USD'},
    {'is_active': 'yes'},
    {'skus': []},
    {'skus': [{'id': 18, 'price_amount': '0', 'stock_quantity': 1, 'is_active': True}]},
    {'skus': [{'id': 18, 'price_amount': '9.9', 'stock_quantity': -2, 'is_active': True}]},
    {'id': 0},
])
def test_selected_products_with_invalid_fields_fail_closed(change):
    from web_api.shop_lorry_source import normalize_products
    from web_api.shop_source import SourceError

    with pytest.raises(SourceError):
        normalize_products([product(**change)])


def test_signature_matches_the_documented_algorithm():
    from web_api.shop_lorry_source import sign

    expected = hmac.new(
        SECRET.encode(),
        b'GET\n/api/v1/upstream/products\n1700000000\nd41d8cd98f00b204e9800998ecf8427e',
        hashlib.sha256,
    ).hexdigest()
    assert sign(SECRET, 'GET', '/api/v1/upstream/products', '1700000000') == expected


def test_fetch_signs_every_page_without_query_and_never_writes(tmp_path):
    from web_api.shop_lorry_source import fetch_catalog, sign

    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == 'GET'
        assert request.url.path == '/api/v1/upstream/products'
        timestamp = request.headers['Dujiao-Next-Timestamp']
        assert request.headers['Dujiao-Next-Api-Key'] == KEY
        assert request.headers['Dujiao-Next-Signature'] == sign(SECRET, 'GET', '/api/v1/upstream/products', timestamp)
        page = int(request.url.params['page'])
        items = CATALOG[:3] if page == 1 else CATALOG[3:]
        return httpx.Response(200, json={'ok': True, 'total': len(CATALOG), 'items': items})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_catalog(client=client, credentials_path=credentials(tmp_path))

    rows = asyncio.run(run())
    assert len(rows) == 4 and len(seen) == 2


@pytest.mark.parametrize('response', [
    httpx.Response(401, json={'error_code': 'invalid_signature'}),
    httpx.Response(200, json={'ok': False, 'total': 0, 'items': []}),
    httpx.Response(200, json={'total': 3, 'items': CATALOG[:1]}),
    httpx.Response(200, text='not json'),
])
def test_upstream_errors_become_source_errors(tmp_path, response):
    from web_api.shop_lorry_source import fetch_catalog
    from web_api.shop_source import SourceError

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
            return await fetch_catalog(client=client, credentials_path=credentials(tmp_path))

    with pytest.raises(SourceError):
        asyncio.run(run())


@pytest.mark.parametrize('value', [{'api_key': KEY}, {'api_key': 'short', 'api_secret': SECRET}, {'api_key': KEY, 'api_secret': 'x/y' * 10}])
def test_invalid_credentials_are_rejected_before_any_request(tmp_path, value):
    from web_api.shop_lorry_source import fetch_catalog
    from web_api.shop_source import SourceError

    with pytest.raises(SourceError):
        asyncio.run(fetch_catalog(credentials_path=credentials(tmp_path, value)))


def test_store_accepts_lorry_rows_and_projects_x_and_muse_categories(tmp_path):
    from web_api.shop_lorry_source import normalize_products
    from web_api.shop_store import ShopStore

    rows = normalize_products(copy.deepcopy(CATALOG))

    async def no_gpt():
        return []

    async def lorry():
        return rows

    store = ShopStore(tmp_path / 'source.json', fetcher=no_gpt, lorry_fetcher=lorry)
    state = asyncio.run(store.refresh(manual=True))
    assert len(state['items']) == 4
    assert 'X 用户名' in state['products']['3000000000017']['description']
    skus = {row['key']: {'price_cents': 12900, 'published': True} for row in rows}
    asyncio.run(store.update({'revision': 0, 'telegram': 'my_shop', 'skus': skus}))
    catalog = asyncio.run(store.public())
    assert {product['category'] for product in catalog['products']} == {'X', 'Muse AI'}


def test_store_rejects_lorry_rows_outside_the_selected_titles(tmp_path):
    from web_api.shop_store import validate_items

    row = {
        'key': '3000000000051:1', 'product_id': '3000000000051', 'title': 'Netflix', 'public_title': 'Netflix',
        'label': '标准规格', 'source_url': 'https://shop.lorry.site/products/netflix', 'cost_cents': 100,
        'available': True, 'quantity': None, 'sales': None,
    }
    with pytest.raises(ValueError):
        validate_items([row], 'lorry')
