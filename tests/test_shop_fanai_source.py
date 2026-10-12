"""Fanai: an 异次元发卡 storefront read through its public category and commodity lists."""

import asyncio
import copy

import httpx
import pytest

CATEGORIES = [
    {'id': 1, 'name': 'GROK', 'status': 1, 'hide': 0},
    {'id': 2, 'name': 'CHATGPT', 'status': 1, 'hide': 0},
    {'id': 5, 'name': '推特蓝v', 'status': 1, 'hide': 0},
    {'id': 8, 'name': 'CLAUDE', 'status': 1, 'hide': 0},
    {'id': 3, 'name': '币安使用教程', 'status': 1, 'hide': 0},
]


def commodity(identity, name, price, category, stock=5, **changes):
    value = {'id': identity, 'name': name, 'price': price, 'user_price': price, 'stock': stock, 'category_id': category,
             'status': 1, 'hide': 0, 'delivery_way': 0, 'order_sold': 3, 'cover': '/x.png', 'tags': []}
    value.update(changes)
    return value


# Names as the storefront lists them (2026-10-12).
COMMODITIES = [
    commodity(20, 'Claude Max 5x (ios)', 750, 8),
    commodity(21, 'Claude Max 20x (ios)', 1450, 8, stock=7),
    commodity(22, 'Claude Pro （ios）', 128, 8, stock=11),
    commodity(24, 'Claude kyc 有量的私聊我', 10, 8, delivery_way=1),
    commodity(23, 'Gpt pro 5x 卡密ios', 599, 2, stock=3),
    commodity(25, 'kyc', 681, 2, stock=0),
    commodity(6, 'ChatGPT Plus 菲区卡密（全程质保）', 108, 2, stock=0),
    commodity(18, '20x成品号', 750, 2),
    commodity(5, 'GPT PRO 5X 智利卡密（全程质保）', 615, 2, stock=33),
    commodity(16, '菲律宾ChatGpt pro20x卡密（续费卡密售后官方同步）', 1010, 2, stock=17),
    commodity(3, 'Grok Super直充3个月（全程质保，自营到账时间几分钟到十几分钟内不等，介意勿拍）', 248, 1, stock=19, user_price=298),
    commodity(4, 'Grok Super直充1个月卡（全程质保）', 138, 1, stock=37),
    commodity(13, 'Grok Super Heavy一个月直充卡密（全程质保）', 780, 1, stock=13),
    commodity(1, '币安使用教程', 99999, 3),
]


def test_only_known_plans_from_the_chosen_categories_enter_under_shared_titles():
    from web_api.shop_fanai_source import categories_of, normalize_products

    categories = categories_of(copy.deepcopy(CATEGORIES))
    assert categories == {1, 2, 8}
    rows = normalize_products(copy.deepcopy(COMMODITIES), categories)
    assert [(row['key'], row['public_title'], row['cost_cents'], row['available']) for row in rows] == [
        ('4000000000006:1', 'ChatGPT Plus', 10800, False),
        ('4000000000023:1', 'ChatGPT Pro 100', 59900, True),
        ('4000000000005:1', 'ChatGPT Pro 100', 61500, True),
        ('4000000000016:1', 'ChatGPT Pro 200', 101000, True),
        ('4000000000021:1', 'Claude Max 20x', 145000, True),
        ('4000000000020:1', 'Claude Max 5x', 75000, True),
        ('4000000000022:1', 'Claude Pro', 12800, True),
        ('4000000000004:1', 'Grok SuperGrok', 13800, True),
        ('4000000000003:1', 'Grok SuperGrok 3 个月', 24800, True),
        ('4000000000013:1', 'Grok SuperGrok Heavy', 78000, True),
    ]
    by_key = {row['key']: row for row in rows}
    # The supplier's own name stays administrator-only; its item page is where the merchant buys.
    assert by_key['4000000000022:1']['title'] == 'Claude Pro （ios）'
    assert by_key['4000000000022:1']['source_url'] == 'https://fanai.us.ci/item/22'
    assert {row['label'] for row in rows} == {'标准规格'}


@pytest.mark.parametrize('change', [
    {'price': 0}, {'price': -5}, {'price': '12.345'}, {'price': None}, {'price': float('nan')},
    {'stock': -1}, {'stock': '5'}, {'id': 0}, {'id': 10**12}, {'name': ''},
])
def test_selected_commodities_with_invalid_fields_fail_closed(change):
    from web_api.shop_fanai_source import normalize_products
    from web_api.shop_source import SourceError

    with pytest.raises(SourceError):
        normalize_products([{**commodity(22, 'Claude Pro', 128, 8), **change}], {8})


def test_hidden_disabled_and_other_category_items_are_left_out():
    from web_api.shop_fanai_source import normalize_products

    items = [
        commodity(22, 'Claude Pro', 128, 8, hide=1),
        commodity(23, 'Claude Pro', 128, 8, status=0),
        commodity(24, 'Claude Pro', 128, 5),
    ]
    assert normalize_products(items, {8}) == []


@pytest.mark.parametrize('title, valid', [
    ('Claude Pro', True), ('Grok SuperGrok 3 个月', True), ('ChatGPT Pro 200', True),
    ('Grok SuperGrok 1 个月', False), ('Netflix', False), ('Claude Pro 3 个月 外链', False), (None, False),
])
def test_stored_public_titles_are_only_the_plans_this_feed_names(title, valid):
    from web_api.shop_fanai_source import valid_public_title

    assert valid_public_title(title) is valid


def test_fetch_reads_each_chosen_category_page_by_page_and_only_reads():
    from web_api.shop_fanai_source import fetch_catalog

    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        assert request.method == 'GET' and request.url.host == 'fanai.us.ci'
        if request.url.path == '/user/api/index/data':
            return httpx.Response(200, json={'code': 200, 'msg': 'success', 'data': CATEGORIES})
        assert request.url.path == '/user/api/index/commodity'
        category, page = int(request.url.params['categoryId']), int(request.url.params['page'])
        items = [item for item in COMMODITIES if item['category_id'] == category]
        size = int(request.url.params['limit'])
        return httpx.Response(200, json={'code': 200, 'msg': 'success', 'total': len(items), 'data': items[(page - 1) * size:page * size]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_catalog(client=client)

    rows = asyncio.run(run())
    assert len(rows) == 10
    assert [path for _, path, _ in seen] == ['/user/api/index/data'] + ['/user/api/index/commodity'] * 3
    assert sorted(params['categoryId'] for _, path, params in seen if params) == ['1', '2', '8']


@pytest.mark.parametrize('response', [
    httpx.Response(404, text='<html>missing</html>'),
    httpx.Response(200, text='not json'),
    httpx.Response(200, json={'code': 500, 'msg': 'error', 'data': []}),
    httpx.Response(200, json={'code': 200, 'data': {'items': []}}),
    httpx.Response(302, headers={'Location': 'http://127.0.0.1/'}),
])
def test_upstream_errors_become_source_errors(response):
    from web_api.shop_fanai_source import fetch_catalog
    from web_api.shop_source import SourceError

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
            return await fetch_catalog(client=client)

    with pytest.raises(SourceError):
        asyncio.run(run())


def test_store_keeps_fanai_rows_and_files_them_under_their_brands(tmp_path):
    from web_api.shop_fanai_source import categories_of, normalize_products
    from web_api.shop_store import ShopStore

    rows = normalize_products(copy.deepcopy(COMMODITIES), categories_of(copy.deepcopy(CATEGORIES)))

    async def no_gpt():
        return []

    async def fanai():
        return rows

    store = ShopStore(tmp_path / 'source.json', fetcher=no_gpt, fanai_fetcher=fanai)
    state = asyncio.run(store.refresh(manual=True))
    assert len(state['items']) == 10
    assert state['sources']['fanai']['site'] == 'https://fanai.us.ci/'
    assert {row['category'] for row in state['items']} == {'GPT', 'Claude', 'Grok'}
    assert {row['buy_url'] for row in state['items'] if row['key'] == '4000000000022:1'} == {'https://fanai.us.ci/item/22'}
    skus = {row['key']: {'price_cents': 99900, 'published': True} for row in rows}
    asyncio.run(store.update({'revision': 0, 'telegram': 'my_shop', 'skus': skus}))
    catalog = asyncio.run(store.public())
    assert {product['category'] for product in catalog['products']} == {'GPT', 'Claude', 'Grok'}
    assert 'fanai' not in str(catalog).lower()


def test_store_rejects_tampered_fanai_rows(tmp_path):
    from web_api.shop_store import validate_items

    row = {
        'key': '4000000000022:1', 'product_id': '4000000000022', 'title': 'Claude Pro', 'public_title': 'Claude Pro',
        'label': '标准规格', 'source_url': 'https://fanai.us.ci/item/22', 'cost_cents': 12800,
        'available': True, 'quantity': None, 'sales': None,
    }
    validate_items([row], 'fanai')
    for change in ({'source_url': 'https://evil.test/item/22'}, {'public_title': 'Netflix'}, {'key': '4000000000022:2'},
                   {'source_url': 'https://fanai.us.ci/item/23'}):
        with pytest.raises(ValueError):
            validate_items([{**row, **change}], 'fanai')
