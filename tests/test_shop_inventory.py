"""The merchant's own Gemini, Muse AI and SMS code stock: private lines, public counts, one unit per delivery."""

import asyncio
import json
from decimal import Decimal

import pytest
import state_store
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_shop_routes import HEADERS, Sessions
from web_api.shop_inventory import ShopInventory
from web_api.shop_prodseller_source import SELECTED, normalize_products
from web_api.shop_routes import register_shop_routes
from web_api.shop_store import Conflict, ShopStore

GEMINI, MUSE = list(SELECTED)[:2]
GEMINI_KEY, MUSE_KEY = '2000000000001:1', '2000000000002:1'
SECRET = 'fictional-muse@example.invalid | fictional-password'


def store_with_prodseller(tmp_path, *, now=1800000000):
    rows = normalize_products({'products': [
        {'id': GEMINI, 'name': 'Gemini Pro 18Months (link)', 'price': Decimal('0.85'), 'inStock': False,
         'requiresEmailActivation': False, 'delivery': {'type': 'instant'}},
        {'id': MUSE, 'name': 'Muse AI', 'price': Decimal('1.00'), 'inStock': False,
         'requiresEmailActivation': False, 'delivery': {'type': 'instant'}},
    ]})

    async def empty():
        return []

    async def prodseller():
        return rows

    clock = [now]
    store = ShopStore(tmp_path / 'source.json', fetcher=empty, prodseller_fetcher=prodseller, clock=lambda: clock[0])
    asyncio.run(store.refresh(manual=True))
    store.inventory = ShopInventory(tmp_path / 'inventory.json', clock=lambda: clock[0])
    return store, clock


def publish(store, *keys):
    revision = asyncio.run(store.admin())['revision']
    asyncio.run(store.update({'revision': revision, 'telegram': 'my_shop', 'client': 3,
                              'skus': {key: {'price_cents': 1888, 'published': True} for key in keys}}))


def test_add_skips_blank_and_repeated_lines_and_only_counts_leave_the_view(tmp_path):
    inventory = ShopInventory(tmp_path / 'inventory.json', clock=lambda: 1000)
    result = inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET, '  ', SECRET, 'second | pw ']}, {MUSE_KEY})
    assert (result['added'], result['skipped']) == (2, 1)
    again = inventory.add({'revision': result['revision'], 'sku': MUSE_KEY, 'lines': ['second | pw', 'third | pw']}, {MUSE_KEY})
    assert (again['added'], again['skipped']) == (1, 1)
    assert [len(again['skus'][MUSE_KEY][part]) for part in ('stock', 'delivered')] == [3, 0]
    assert 'fictional' not in json.dumps(inventory.view()) and 'pw' not in json.dumps(inventory.view())
    assert inventory.counts() == {MUSE_KEY: {'stock': 3, 'delivered': 0}}
    saved = tmp_path / 'inventory.json'
    assert saved.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('payload', [
    {'revision': 0, 'sku': GEMINI_KEY, 'lines': ['x']},  # not a stocked SKU here
    {'revision': 0, 'sku': MUSE_KEY, 'lines': []},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': ['   ']},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': ['a b']},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': ['x' * 2001]},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': ['x'] * 501},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': [1]},
    {'revision': 0, 'sku': MUSE_KEY, 'lines': ['x'], 'extra': 1},
])
def test_invalid_additions_change_nothing(tmp_path, payload):
    inventory = ShopInventory(tmp_path / 'inventory.json')
    with pytest.raises(ValueError):
        inventory.add(payload, {MUSE_KEY})
    assert not (tmp_path / 'inventory.json').exists()


def test_stale_revision_conflicts(tmp_path):
    inventory = ShopInventory(tmp_path / 'inventory.json')
    inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': ['a']}, {MUSE_KEY})
    with pytest.raises(Conflict):
        inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': ['b']}, {MUSE_KEY})


def test_delivery_takes_the_oldest_unit_once_per_request(tmp_path):
    clock = [1000]
    inventory = ShopInventory(tmp_path / 'inventory.json', clock=lambda: clock[0])
    inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': ['first', 'second']}, {MUSE_KEY})
    clock[0] = 2000
    request = 'a' * 32
    first = inventory.deliver({'sku': MUSE_KEY, 'request': request, 'note': ' 微信 buyer '}, {MUSE_KEY})
    assert first['delivered']['line'] == 'first' and first['delivered']['note'] == '微信 buyer'
    # A repeated click returns the same unit and takes nothing more.
    repeat = inventory.deliver({'sku': MUSE_KEY, 'request': request, 'note': ''}, {MUSE_KEY})
    assert repeat['delivered'] == first['delivered']
    assert inventory.counts() == {MUSE_KEY: {'stock': 1, 'delivered': 1}}
    second = inventory.deliver({'sku': MUSE_KEY, 'request': 'b' * 32, 'note': ''}, {MUSE_KEY})
    assert second['delivered']['line'] == 'second'
    with pytest.raises(ValueError, match='out of stock'):
        inventory.deliver({'sku': MUSE_KEY, 'request': 'c' * 32, 'note': ''}, {MUSE_KEY})
    with pytest.raises(ValueError):
        inventory.deliver({'sku': MUSE_KEY, 'request': 'd' * 32, 'note': 'x' * 61}, {MUSE_KEY})
    assert [meta['note'] for meta in inventory.view()['skus'][MUSE_KEY]['delivered']] == ['', '微信 buyer']


def test_reveal_remove_and_restock(tmp_path):
    inventory = ShopInventory(tmp_path / 'inventory.json')
    view = inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': ['keep', 'drop']}, {MUSE_KEY})
    keep, drop = view['skus'][MUSE_KEY]['stock']
    assert inventory.action(drop['id'], 'reveal', {'revision': 0}) == {'line': 'drop'}
    view = inventory.action(drop['id'], 'remove', {'revision': view['revision']})
    assert [meta['id'] for meta in view['skus'][MUSE_KEY]['stock']] == [keep['id']]
    with pytest.raises(ValueError):
        inventory.action(keep['id'], 'restock', {'revision': view['revision']})
    view = inventory.deliver({'sku': MUSE_KEY, 'request': 'e' * 32, 'note': '已付款'}, {MUSE_KEY})
    with pytest.raises(ValueError):
        inventory.action(keep['id'], 'remove', {'revision': view['revision']})
    view = inventory.action(keep['id'], 'restock', {'revision': view['revision']})
    assert inventory.counts() == {MUSE_KEY: {'stock': 1, 'delivered': 0}}
    assert view['skus'][MUSE_KEY]['stock'][0]['note'] == ''


def test_corrupt_inventory_fails_closed_for_the_merchant_and_open_for_the_catalog(tmp_path):
    inventory = ShopInventory(tmp_path / 'inventory.json')
    (tmp_path / 'inventory.json').write_text('{"schema": 1, "revision": 0, "items": [{"id": "x"}]}')
    assert inventory.counts() == {}
    with pytest.raises(state_store.InvalidJsonState):
        inventory.view()
    with pytest.raises(state_store.InvalidJsonState):
        inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': ['a']}, {MUSE_KEY})


def test_own_stock_sells_when_the_supplier_is_out_and_deliveries_count_as_sold(tmp_path):
    store, _ = store_with_prodseller(tmp_path)
    publish(store, MUSE_KEY, GEMINI_KEY)
    products = {item['id']: item for item in asyncio.run(store.public())['products']}
    assert products['2000000000002']['variants'][0]['available'] is False
    assert products['2000000000002']['variants'][0]['quantity'] is None
    store.inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET, 'two', 'three']}, {MUSE_KEY})
    store.inventory.deliver({'sku': MUSE_KEY, 'request': 'f' * 32, 'note': ''}, {MUSE_KEY})
    public = asyncio.run(store.public())
    products = {item['id']: item for item in public['products']}
    muse, = products['2000000000002']['variants']
    assert (muse['available'], muse['quantity']) == (True, 2)
    assert products['2000000000002']['sold'] == 1
    assert products['2000000000001']['variants'][0]['available'] is False  # Gemini has no own stock.
    # The store's short labels for both products.
    assert products['2000000000001']['tags'] == ['支持最新 Gemini 模型']
    assert products['2000000000002']['tags'] == ['质保首次登录', '不换 IP 不封号']
    assert 'fictional' not in json.dumps(public) and 'two' not in json.dumps(public)
    admin = asyncio.run(store.admin())
    assert admin['inventory'] == {MUSE_KEY: 2}
    assert admin['automatic_sold']['2000000000002'] == 1
    assert 'fictional' not in json.dumps(admin)


def test_routes_guard_stock_and_never_list_lines(tmp_path):
    store, _ = store_with_prodseller(tmp_path)
    store.inventory = None
    async def dispatch(fn, request): return fn(headers=request.headers, path=request.url.path)
    app = FastAPI()
    register_shop_routes(app, Sessions(), dispatch, store=store)
    root = '/api/v1/shop/admin/inventory'
    with TestClient(app) as client:
        for path in [root, root + '/add', root + '/deliver', root + '/items/' + 'a' * 32 + '/reveal']:
            method = client.get if path == root else client.post
            assert method(path).status_code == 401
            assert method(path, headers={'Cookie': 'sid=user'}).status_code == 403
            if path != root:
                assert method(path, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
        added = client.post(root + '/add', headers=HEADERS, json={'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]})
        assert added.status_code == 200 and added.json()['added'] == 1
        assert added.headers['cache-control'] == 'no-store'
        # Only Gemini, Muse AI and SMS code SKUs take own stock.
        assert client.post(root + '/add', headers=HEADERS, json={'revision': 1, 'sku': '1:1', 'lines': ['x']}).status_code == 422
        listed = client.get(root, headers=HEADERS)
        assert listed.status_code == 200 and SECRET not in listed.text
        item_id = listed.json()['skus'][MUSE_KEY]['stock'][0]['id']
        assert client.post(root + '/items/' + item_id + '/explode', headers=HEADERS, json={'revision': 1}).status_code == 404
        assert client.post(root + '/items/' + item_id + '/reveal', headers=HEADERS, json={'revision': 1}).json() == {'line': SECRET}
        assert client.get(root + '/items/' + item_id + '/reveal', headers=HEADERS).status_code == 405
        delivered = client.post(root + '/deliver', headers=HEADERS, json={'sku': MUSE_KEY, 'request': 'a' * 32, 'note': 'buyer'})
        assert delivered.json()['delivered']['line'] == SECRET
        for path in ['/api/v1/shop/admin', '/api/v1/shop/catalog']:
            assert SECRET not in client.get(path, headers=HEADERS).text


def test_sms_activation_codes_take_own_stock_and_count_once_as_sold(tmp_path):
    from test_shop_sms import CONFIG
    from web_api.shop_sms_source import PRODUCT_ID as SMS_ID
    from web_api.shop_sms_source import normalize_config

    async def empty():
        return []

    async def sms():
        rows = normalize_config(CONFIG)
        rows[0]['available'] = False
        return rows

    store = ShopStore(tmp_path / 'source.json', fetcher=empty, sms_fetcher=sms, clock=lambda: 1800000000)
    asyncio.run(store.refresh())
    sms_key = f'{SMS_ID}:1'
    async def dispatch(fn, request): return fn(headers=request.headers, path=request.url.path)
    app = FastAPI()
    register_shop_routes(app, Sessions(), dispatch, store=store)
    root = '/api/v1/shop/admin/inventory'
    code = 'FICTIONAL-SMS-CDK-0001'
    with TestClient(app) as client:
        publish(store, sms_key)
        before = client.get('/api/v1/shop/catalog').json()['products'][0]['variants'][0]
        assert (before['available'], before['quantity']) == (False, None)
        added = client.post(root + '/add', headers=HEADERS, json={'revision': 0, 'sku': sms_key, 'lines': [code]})
        assert added.status_code == 200 and added.json()['added'] == 1
        assert {row['key'] for row in client.get('/api/v1/shop/admin', headers=HEADERS).json()['inventory_items']} == {sms_key}
        # The SMS snapshot remains valid after inventory is reopened.
        store.inventory = ShopInventory(tmp_path / 'inventory.json', clock=store.clock)
        assert store.inventory.catalog()[sms_key]['product_id'] == SMS_ID
        variant, = next(item for item in client.get('/api/v1/shop/catalog').json()['products'] if item['id'] == SMS_ID)['variants']
        assert (variant['available'], variant['quantity']) == (True, 1)
        payload = {'sku': sms_key, 'request': 'b' * 32, 'note': ''}
        delivered = client.post(root + '/deliver', headers=HEADERS, json=payload)
        assert delivered.json()['delivered']['line'] == code
        repeated = client.post(root + '/deliver', headers=HEADERS, json=payload)
        assert repeated.status_code == 200 and repeated.json() == delivered.json()
        assert store.inventory.counts() == {sms_key: {'stock': 0, 'delivered': 1}}
        exhausted = client.get('/api/v1/shop/catalog').json()['products'][0]['variants'][0]
        assert (exhausted['available'], exhausted['quantity']) == (False, None)
        # A delivered code is counted when its SMS arrives, not again when it is handed over.
        assert client.get('/api/v1/shop/admin', headers=HEADERS).json()['automatic_sold'][SMS_ID] == 0
        asyncio.run(store.record_sms_received(123))
        asyncio.run(store.record_sms_received(123))
        assert client.get('/api/v1/shop/admin', headers=HEADERS).json()['automatic_sold'][SMS_ID] == 1
        assert client.get('/api/v1/shop/catalog').json()['products'][0]['sold'] == 1
        for path in [root, '/api/v1/shop/admin', '/api/v1/shop/catalog']:
            assert code not in client.get(path, headers=HEADERS).text
