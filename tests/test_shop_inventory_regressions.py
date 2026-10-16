"""Stock survives supplier churn; all fixtures are fictional and network-free."""
import asyncio
import json

import state_store
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_shop_inventory import MUSE_KEY, GEMINI_KEY, SECRET, store_with_prodseller, publish
from test_shop_routes import HEADERS, Sessions
from web_api.shop_inventory import ShopInventory
from web_api.shop_routes import register_shop_routes


def client_for(store):
    async def dispatch(fn, request):
        return fn(headers=request.headers, path=request.url.path)
    app = FastAPI()
    register_shop_routes(app, Sessions(), dispatch, store=store)
    return TestClient(app)


def delist(store, clock):
    async def empty():
        return []
    store.prodseller_fetcher = empty
    clock[0] += 901
    asyncio.run(store.refresh(manual=True))


def test_delisted_stock_can_be_sold_delivered_and_restocked_after_restart(tmp_path):
    store, clock = store_with_prodseller(tmp_path)
    publish(store, MUSE_KEY)
    with client_for(store) as client:
        root = '/api/v1/shop/admin/inventory'
        result = client.post(root + '/add', headers=HEADERS, json={'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]})
        assert result.status_code == 200
        delist(store, clock)
        # The actual supplier cache remains empty; archived metadata never invents fresh stock.
        assert store._source('prodseller')['items'] == []
        store.inventory = ShopInventory(tmp_path / 'inventory.json', clock=store.clock)
        public = asyncio.run(store.public())
        assert public['status'] == 'ready'
        product, = public['products']
        assert product['title'] == 'Muse AI'
        assert product['variants'][0]['quantity'] == 1 and product['variants'][0]['available']
        admin = asyncio.run(store.admin())
        assert admin['inventory_items'][0]['category'] == 'Muse AI'
        assert admin['items'][0]['available'] is False
        assert admin['items'][0]['archived'] is True
        assert SECRET not in json.dumps(admin) + json.dumps(public)
        result = client.post(root + '/deliver', headers=HEADERS, json={'sku': MUSE_KEY, 'request': 'a' * 32, 'note': ''})
        assert result.status_code == 200 and result.json()['delivered']['line'] == SECRET
        public = asyncio.run(store.public())
        assert public['products'][0]['variants'][0]['available'] is False
        item_id = result.json()['delivered']['id']
        restock = client.post(root + f'/items/{item_id}/restock', headers=HEADERS, json={'revision': result.json()['revision']})
        assert restock.status_code == 200
        assert asyncio.run(store.public())['products'][0]['variants'][0]['quantity'] == 1
    # Main state remains exactly compatible with the previous release's strict schema.
    state = json.loads((tmp_path / 'inventory.json').read_text())
    assert set(state) == {'schema', 'revision', 'items'} and state['schema'] == 1
    assert set(state['items'][0]) == {'id', 'sku', 'line', 'added_at', 'delivered_at', 'delivery', 'note'}
    assert (tmp_path / 'inventory-catalog.json').stat().st_mode & 0o777 == 0o600


def test_legacy_inventory_is_snapshotted_before_refresh_replaces_supplier_rows(tmp_path):
    store, clock = store_with_prodseller(tmp_path)
    publish(store, MUSE_KEY)
    store.inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]}, {MUSE_KEY})
    assert not (tmp_path / 'inventory-catalog.json').exists()
    delist(store, clock)
    assert asyncio.run(store.public())['products'][0]['variants'][0]['quantity'] == 1


def test_already_orphaned_legacy_stock_remains_manageable_without_metadata(tmp_path):
    store, clock = store_with_prodseller(tmp_path)
    delist(store, clock)
    store.inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]}, {MUSE_KEY})
    with client_for(store) as client:
        admin = client.get('/api/v1/shop/admin', headers=HEADERS).json()
        assert admin['inventory_items'][0]['key'] == MUSE_KEY
        assert admin['inventory_items'][0]['category'] == '库存'
        assert asyncio.run(store.public())['products'] == []
        result = client.post('/api/v1/shop/admin/inventory/deliver', headers=HEADERS,
                             json={'sku': MUSE_KEY, 'request': 'b' * 32, 'note': ''})
        assert result.status_code == 200 and result.json()['delivered']['line'] == SECRET


def test_stale_sources_do_not_block_own_stock_or_enable_unstocked_variants(tmp_path):
    store, clock = store_with_prodseller(tmp_path)
    publish(store, MUSE_KEY, GEMINI_KEY)
    # Source says both are in stock, but its quote is about to become stale.
    source = store._source('prodseller')
    for row in source['items']:
        row['available'] = True
    state_store.save_json(store.prodseller_path, source)
    store.inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]}, {MUSE_KEY})
    clock[0] += 901
    public = asyncio.run(store.public())
    assert public['status'] == 'ready'
    variants = {v['id']: v for p in public['products'] for v in p['variants']}
    assert variants[MUSE_KEY]['available'] and variants[MUSE_KEY]['quantity'] == 1
    assert not variants[GEMINI_KEY]['available']
    store.inventory.deliver({'sku': MUSE_KEY, 'request': 'c' * 32, 'note': ''}, {MUSE_KEY})
    assert asyncio.run(store.public())['status'] == 'stale'


def test_source_disable_and_delete_hide_public_goods_but_not_owned_units(tmp_path):
    store, clock = store_with_prodseller(tmp_path)
    publish(store, MUSE_KEY)
    with client_for(store) as client:
        root = '/api/v1/shop/admin'
        assert client.post(root + '/inventory/add', headers=HEADERS,
                           json={'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]}).status_code == 200
        state = store._settings()
        state['source_enabled'] = {'prodseller': False}
        state_store.save_json(store.settings_path, state)
        assert asyncio.run(store.public())['products'] == []
        assert len(asyncio.run(store.admin())['inventory_items']) == 1
        result = client.request('DELETE', root + '/sources/prodseller', headers=HEADERS,
                                   json={'revision': state['revision'], 'confirm': 'delete-source-and-products'})
        assert result.status_code == 200
        assert asyncio.run(store.public())['products'] == []
        assert len(asyncio.run(store.admin())['inventory_items']) == 1
        assert MUSE_KEY not in store._settings()['skus']
        assert client.post(root + '/inventory/deliver', headers=HEADERS,
                           json={'sku': MUSE_KEY, 'request': 'd' * 32, 'note': ''}).status_code == 200


def test_inventory_gets_do_not_create_metadata_or_mutate_stock(tmp_path):
    store, _ = store_with_prodseller(tmp_path)
    store.inventory.add({'revision': 0, 'sku': MUSE_KEY, 'lines': [SECRET]}, {MUSE_KEY})
    before = store.inventory.path.read_bytes()
    with client_for(store) as client:
        for url in ['/api/v1/shop/admin/inventory', '/api/v1/shop/admin', '/api/v1/shop/catalog']:
            assert client.get(url, headers=HEADERS).status_code == 200
    assert store.inventory.path.read_bytes() == before
    assert not (tmp_path / 'inventory-catalog.json').exists()


def test_supplier_identity_only_leaves_authenticated_overview(tmp_path):
    store, _ = store_with_prodseller(tmp_path)
    with client_for(store) as client:
        url = '/api/v1/shop/admin/procurement'
        assert client.get(url).status_code == 401
        assert client.get(url, headers={'Cookie': 'sid=user'}).status_code == 403
        response = client.get(url, headers=HEADERS)
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store'
        assert response.json()['supplier'] == {'name': 'ProdSeller', 'site': 'https://prodseller.com/'}
        public = client.get('/api/v1/shop/catalog')
        assert 'prodseller' not in public.text.lower()
