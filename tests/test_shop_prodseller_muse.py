"""ProdSeller Muse integration uses supplier identity and merchant-owned retail state."""

import asyncio
import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from web_api.shop_prodseller_source import fetch_catalog, normalize_products
from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore, validate_items

FIXTURE = Path(__file__).parent / 'fixtures/shop/prodseller-muse-products.json'
MUSE_ID = '2000000000002'
MUSE_KEY = MUSE_ID + ':1'
SUPPLIER_ID = '6ac65a53d07e0053469f477d'


def products():
    return json.loads(FIXTURE.read_text(), parse_float=Decimal)


async def fixture_feed():
    return normalize_products(products())


async def empty_feed():
    return []


def make_store(tmp_path, **kwargs):
    return ShopStore(tmp_path / 'source.json', fetcher=empty_feed,
                     prodseller_fetcher=kwargs.pop('prodseller_fetcher', fixture_feed), **kwargs)


def test_selected_muse_uses_api_unit_cost_and_ignores_same_named_unselected_product():
    rows = normalize_products(products())
    assert [row['product_id'] for row in rows] == ['2000000000001', MUSE_ID]
    muse = rows[1]
    assert muse == {
        'key': MUSE_KEY, 'product_id': MUSE_ID,
        'title': 'Muse AI · $1.10', 'public_title': 'Muse AI', 'label': '标准规格',
        'source_url': 'https://prodseller.com/v1/products/6ac65a53d07e0053469f477d',
        'cost_cents': 792, 'available': True, 'quantity': None, 'sales': None,
    }
    assert rows[0]['cost_cents'] == 274 and rows[0]['public_title'] == 'Gemini Pro 18 个月'
    changed = products()
    changed['products'][1]['price'] = Decimal('1.23')
    assert normalize_products(changed)[1]['cost_cents'] == 886
    changed['products'] = [row for row in changed['products'] if row['id'] != SUPPLIER_ID]
    assert [row['product_id'] for row in normalize_products(changed)] == ['2000000000001']


def test_adding_muse_preserves_gemini_name_guard_and_private_supplier_title():
    data = products()
    data['products'][0]['name'] = 'Gemini Pro 18Months (link) '
    assert normalize_products(data)[0]['title'] == 'Gemini Pro 18Months (link)  · $0.38'
    data['products'][0]['name'] = ' Gemini Pro 18Months (link)'
    with pytest.raises(SourceError):
        normalize_products(data)


@pytest.mark.parametrize('name', ['Muse AI', 'Muse AI ', ' muse AI  '])
def test_supplier_name_whitespace_does_not_change_muse_identity(name):
    data = products()
    data['products'][1]['name'] = name
    muse = normalize_products(data)[1]
    assert muse['product_id'] == MUSE_ID and muse['public_title'] == 'Muse AI'


@pytest.mark.parametrize('name', ['Museum AI', 'Muse AI Premium', 'Gemini Pro 18Months',
                                  'Amuse AI', 'Muse AI\nOther plan', '', None])
def test_muse_id_repurposed_for_another_plan_fails_closed(name):
    data = products()
    data['products'][1]['name'] = name
    with pytest.raises(SourceError):
        normalize_products(data)


@pytest.mark.parametrize('change', [
    lambda data: data['products'].append(copy.deepcopy(data['products'][1])),
    lambda data: data['products'][1].update(id='invalid'),
    lambda data: data['products'][1].update(inStock='true'),
    lambda data: data['products'][1].update(requiresEmailActivation=None),
    lambda data: data['products'][1].update(delivery={}),
])
def test_muse_invalid_or_duplicate_supplier_data_fails_closed(change):
    data = products()
    change(data)
    with pytest.raises(SourceError):
        normalize_products(data)


@pytest.mark.parametrize('amount', [None, '1.10', 0, -1, True, 1.10, Decimal('NaN'), 10001])
def test_muse_missing_free_or_invalid_price_is_not_imported(amount):
    data = products()
    data['products'][1]['price'] = amount
    with pytest.raises(SourceError):
        normalize_products(data)


@pytest.mark.parametrize('change', [{'inStock': False}, {'requiresEmailActivation': True},
                                     {'delivery': {'type': 'manual'}}])
def test_muse_stock_or_incompatible_delivery_disables_availability(change):
    data = products()
    data['products'][1].update(change)
    assert normalize_products(data)[1]['available'] is False


def test_muse_fetch_remains_one_bounded_authenticated_get(tmp_path):
    key = 'psk_' + 'm' * 48
    key_path = tmp_path / 'key'
    key_path.write_text(key)
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == 'GET' and str(request.url) == 'https://prodseller.com/v1/products'
        assert request.headers['x-api-key'] == key
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            rows = await fetch_catalog(client=client, key_path=key_path)
        assert [(row['product_id'], row['cost_cents']) for row in rows] == [
            ('2000000000001', 274), (MUSE_ID, 792),
        ]

    asyncio.run(run())
    assert len(requests) == 1


def test_muse_starts_unpriced_hidden_with_own_copy_and_publishes_without_supplier_details(tmp_path):
    async def run():
        store = make_store(tmp_path, clock=lambda: 1000)
        admin = await store.refresh()
        muse, = [row for row in admin['items'] if row['product_id'] == MUSE_ID]
        assert muse['provider'] == 'prodseller' and muse['category'] == 'Muse AI'
        assert muse['buy_url'] == 'https://prodseller.com/' and muse['cost_cents'] == 792
        assert admin['sources']['prodseller']['covers'] == 'Gemini / Muse AI'
        assert admin['revision'] == 0 and admin['skus'] == {}
        assert not store.settings_path.exists()
        assert (await store.public())['products'] == []
        notice = admin['products'][MUSE_ID]
        assert notice['after_sales'] == ''
        for expected in ('1 Billion', 'AI 图像', 'AI 视频', '1 小时', '邮箱地址', '邮箱登录信息',
                         'https://dev-api.cc/muse.php', 'https://www.tmail.pk/login',
                         'https://www.youtube.com/watch?v=lpsRG9VSDuQ', 'https://muse.ai/', 'OTP'):
            assert expected in notice['description']
        assert admin['products']['2000000000001']['description'] != notice['description']
        await store.update({'revision': 0, 'client': 3, 'telegram': 'test_shop',
                            'skus': {MUSE_KEY: {'price_cents': 1990, 'published': True}}})
        public = await make_store(tmp_path, clock=lambda: 1000).public()
        product, = public['products']
        assert (product['id'], product['title'], product['category']) == (MUSE_ID, 'Muse AI', 'Muse AI')
        assert product['description'] == notice['description'] and product['after_sales'] == ''
        assert product['variants'] == [{'id': MUSE_KEY, 'label': '标准规格', 'price_cents': 1990,
                                        'promo_price_cents': None, 'available': True,
                                        'quantity': None, 'sales': None}]
        wire = json.dumps(public, ensure_ascii=False)
        for private in ('cost_cents', 'source_url', 'buy_url', 'prodseller', SUPPLIER_ID,
                        '$1.10', '$1.00', '$1.20', 'snapshot stock', 'supplier marketing'):
            assert private not in wire

    asyncio.run(run())


def test_muse_copy_override_and_explicit_clear_survive_refresh_and_reopen(tmp_path):
    async def run():
        store = make_store(tmp_path, clock=lambda: 1000)
        await store.refresh()
        for revision, notice in enumerate((
            {'description': '本店 Muse 登录说明', 'after_sales': '请联系本店'},
            {'description': '', 'after_sales': ''},
        )):
            await store.update({'revision': revision, 'client': 3, 'telegram': '',
                                'skus': {MUSE_KEY: {'price_cents': 1990, 'published': True}},
                                'products': {MUSE_ID: notice}})
            store = make_store(tmp_path, clock=lambda: 2000 + revision * 1000)
            admin = await store.refresh()
            assert admin['products'][MUSE_ID] == notice
            product, = (await store.public())['products']
            assert {key: product[key] for key in notice} == notice

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['unavailable', 'free_price'])
def test_failed_muse_refresh_retains_last_cost_but_disables_cached_public_offer(tmp_path, failure):
    async def run():
        now = [1000]
        store = make_store(tmp_path, clock=lambda: now[0])
        await store.refresh()
        await store.update({'revision': 0, 'telegram': '',
                            'skus': {MUSE_KEY: {'price_cents': 1990, 'published': True}}})

        async def failed_feed():
            if failure == 'unavailable':
                raise SourceError('private supplier error')
            data = products()
            data['products'][1]['price'] = 0
            return normalize_products(data)

        store.prodseller_fetcher = failed_feed
        now[0] += 901
        admin = await store.refresh()
        assert admin['sources']['prodseller']['is_stale'] is True
        muse, = [row for row in admin['items'] if row['product_id'] == MUSE_ID]
        assert muse['cost_cents'] == 792
        product, = (await store.public())['products']
        assert product['variants'][0]['available'] is False
        assert 'private supplier error' not in json.dumps(admin)

    asyncio.run(run())


def test_out_of_stock_muse_stays_visible_but_unavailable_then_delisting_removes_it(tmp_path):
    async def run():
        now = [1000]
        data = products()

        async def feed():
            return normalize_products(data)

        store = make_store(tmp_path, prodseller_fetcher=feed, clock=lambda: now[0])
        await store.refresh()
        await store.update({'revision': 0, 'telegram': '',
                            'skus': {MUSE_KEY: {'price_cents': 1990, 'published': True}}})
        data['products'][1]['inStock'] = False
        now[0] += 901
        await store.refresh()
        assert (await store.public())['products'][0]['variants'][0]['available'] is False
        data['products'] = [item for item in data['products'] if item['id'] != SUPPLIER_ID]
        now[0] += 901
        await store.refresh()
        assert (await store.public())['products'] == []

    asyncio.run(run())


def test_muse_cache_rejects_wrong_supplier_identity_without_poisoning_other_sources(tmp_path):
    row = normalize_products(products())[1]
    validate_items([row], 'prodseller')
    for change in (
        {'source_url': 'https://prodseller.com/v1/products/6a31035939dc014325da2c66'},
        {'public_title': 'Gemini Pro 18 个月'},
        {'key': '2000000000003:1', 'product_id': '2000000000003'},
        {'key': MUSE_ID + ':2'},
    ):
        with pytest.raises(ValueError):
            validate_items([{**row, **change}], 'prodseller')
    with pytest.raises(ValueError):
        validate_items([row], 'gpt')

    async def run():
        store = make_store(tmp_path, clock=lambda: 1000)
        await store.refresh()
        snapshot = json.loads(store.prodseller_path.read_text())
        snapshot['items'][1]['public_title'] = 'Gemini Pro 18 个月'
        store.prodseller_path.write_text(json.dumps(snapshot))
        admin = await store.admin()
        assert admin['sources']['prodseller']['is_stale'] is True
        assert admin['sources']['gpt']['is_stale'] is False
        assert all(item['product_id'] != MUSE_ID for item in admin['items'])

    asyncio.run(run())


def test_unconfigured_or_disabled_prodseller_does_not_import_or_publish_muse(tmp_path):
    async def run():
        unconfigured = make_store(tmp_path / 'unconfigured', prodseller_fetcher=None)
        assert 'prodseller' not in (await unconfigured.refresh())['sources']
        assert (await unconfigured.public())['products'] == []

        store = make_store(tmp_path / 'configured', clock=lambda: 1000)
        await store.refresh()
        await store.update({'revision': 0, 'client': 3, 'telegram': '',
                            'source_enabled': {'prodseller': False},
                            'skus': {MUSE_KEY: {'price_cents': 1990, 'published': True}}})

        async def must_not_fetch():
            raise AssertionError('disabled ProdSeller must not read its API')

        store.prodseller_fetcher = must_not_fetch
        store.clock = lambda: 2000
        await store.refresh(manual=True)
        admin = await store.admin()
        assert admin['sources']['prodseller']['enabled'] is False
        assert admin['skus'][MUSE_KEY]['published'] is True
        assert any(row['product_id'] == MUSE_ID for row in admin['items'])
        assert (await store.public())['products'] == []

    asyncio.run(run())
