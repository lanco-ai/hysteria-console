import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.services import LoginRequired
from web_api.shop_source import normalize_products
from web_api.shop_store import ShopStore
from web_api.video_service import AssetStore, VideoSettingsStore, WorkflowStore


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def test_routes_auth_csrf_revision_allowlist(tmp_path):
    async def fetch():
        return normalize_products(
            json.loads((Path(__file__).parent / 'fixtures/shop/products.json').read_text())['data']
        )

    store = ShopStore(tmp_path / 'source.json', fetcher=fetch)
    asyncio.run(store.refresh())
    app = create_app(
        Sessions(),
        shop_store=store,
        video_settings_store=VideoSettingsStore(tmp_path / 'video/settings.json'),
        video_workflow_store=WorkflowStore(tmp_path / 'video/workflows.json'),
        video_asset_store=AssetStore(tmp_path / 'video/assets'),
    )
    with TestClient(app) as client:
        assert client.get('/api/v1/shop/catalog').json()['products'] == []
        assert client.get('/api/v1/shop/admin').status_code == 401
        assert client.get('/api/v1/shop/admin', headers={'Cookie': 'sid=user'}).status_code == 403
        payload = {
            'revision': 0,
            'telegram': 'my_merchant',
            'skus': {'2:7': {'price_cents': 110050, 'published': True}},
            'products': {'2': {'description': '商家商品说明', 'after_sales': '联系本店确认售后'}},
        }
        assert client.put('/api/v1/shop/admin', json=payload).status_code == 401
        assert (
            client.put(
                '/api/v1/shop/admin',
                headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'},
                json=payload,
            ).status_code
            == 403
        )
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, json=payload)
        assert saved.status_code == 200 and saved.json()['revision'] == 1
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=payload).status_code == 409
        assert (
            client.put('/api/v1/shop/admin', headers=HEADERS, content='x' * 65537).status_code
            == 413
        )
        assert (
            client.post(
                '/api/v1/shop/refresh', headers=HEADERS, json={'url': 'https://evil.test'}
            ).status_code
            == 422
        )
        assert client.post('/api/v1/shop/refresh', headers=HEADERS, json={}).status_code == 200
        public = client.get('/api/v1/shop/catalog')
        assert public.headers['cache-control'] == 'no-store'
        assert public.json()['products'][0]['variants'][0]['price_cents'] == 110050
        assert public.json()['products'][0]['description'] == '商家商品说明'
        assert public.json()['products'][0]['after_sales'] == '联系本店确认售后'
        assert 'qiangyunai' not in public.text and 'cost_cents' not in public.text
        assert client.get('/api/v1/shop/missing').status_code == 404


def test_eight_product_copy_uses_compact_utf8_body_limit(tmp_path):
    async def fetch():
        return normalize_products(
            json.loads((Path(__file__).parent / 'fixtures/shop/products.json').read_text())['data']
        )

    store = ShopStore(tmp_path / 'source.json', fetcher=fetch)
    asyncio.run(store.refresh())
    source = json.loads(store.path.read_text())
    sample = source['items'][0]
    source['items'] = [
        {**sample, 'key': f'{product_id}:7', 'product_id': str(product_id)}
        for product_id in range(1, 9)
    ]
    store.path.write_text(json.dumps(source))
    payload = {
        'revision': 0,
        'telegram': 'my_shop',
        'skus': {
            f'{product_id}:7': {'price_cents': 12345, 'published': False}
            for product_id in range(1, 9)
        },
        'products': {
            str(product_id): {'description': 'a' * 4000, 'after_sales': 'b' * 3950}
            for product_id in range(1, 9)
        },
    }

    def wire():
        return json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')

    headroom = 65536 - len(wire())
    assert 0 < headroom < 8000
    chinese = headroom // 2
    payload['products']['1']['description'] = '中' * chinese + 'a' * (4000 - chinese)
    near_limit = wire()
    assert 65534 <= len(near_limit) <= 65536

    app = create_app(
        Sessions(),
        shop_store=store,
        video_settings_store=VideoSettingsStore(tmp_path / 'video/settings.json'),
        video_workflow_store=WorkflowStore(tmp_path / 'video/workflows.json'),
        video_asset_store=AssetStore(tmp_path / 'video/assets'),
    )
    with TestClient(app) as client:
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, content=near_limit)
        assert saved.status_code == 200, saved.text
        assert saved.json()['revision'] == 1
        assert (
            saved.json()['products']['1']['description'] == payload['products']['1']['description']
        )
        payload['revision'] = 1
        payload['products']['1']['description'] = '中' * (chinese + 1) + 'a' * (3999 - chinese)
        assert len(wire()) > 65536
        assert client.put('/api/v1/shop/admin', headers=HEADERS, content=wire()).status_code == 413
        assert client.get('/api/v1/shop/admin', headers=HEADERS).json()['revision'] == 1
        legacy = {'revision': 1, 'telegram': payload['telegram'], 'skus': payload['skus']}
        kept = client.put('/api/v1/shop/admin', headers=HEADERS, json=legacy)
        assert kept.status_code == 200
        assert kept.json()['revision'] == 2
        assert (
            kept.json()['products']['1']['description']
            == saved.json()['products']['1']['description']
        )
        settings = json.loads(store.settings_path.read_text())
        settings['revision'] = 9
        store.settings_path.write_text(json.dumps(settings))
        payload['revision'] = 9
        payload['products'] = saved.json()['products']
        if len(wire()) == 65535:
            payload['products']['1']['after_sales'] += 'z'
        assert len(wire()) == 65536
        assert client.put('/api/v1/shop/admin', headers=HEADERS, content=wire()).status_code == 422
        unchanged = client.get('/api/v1/shop/admin', headers=HEADERS).json()
        assert unchanged['revision'] == 9
        assert (
            unchanged['products']['1']['description']
            == saved.json()['products']['1']['description']
        )


def test_shop_document_is_exact_admin_route():
    from web_api.document_routes import REACT_DOCUMENTS

    assert REACT_DOCUMENTS['/admin/shop'] == ('商品管理', 'has-shell', 'admin')
    assert '/admin/shop/missing' not in REACT_DOCUMENTS


def test_scheduler_failure_keeps_startup_and_public_reads_available(tmp_path):
    import threading

    from fastapi import FastAPI
    from web_api.shop_routes import register_shop_routes
    from web_api.shop_source import SourceError

    called = threading.Event()
    calls = []

    async def fetch():
        calls.append(1)
        called.set()
        raise SourceError('unavailable')

    store = ShopStore(tmp_path / 'source.json', fetcher=fetch)
    app = FastAPI()
    register_shop_routes(app, None, None, store=store, scheduler_enabled=True)
    with TestClient(app) as client:
        assert called.wait(2)
        for _ in range(3):
            assert client.get('/api/v1/shop/catalog').status_code == 200
    assert len(calls) == 1
    assert asyncio.run(store.public())['status'] == 'unavailable'


def test_two_sources_keep_admin_private_and_public_variant_freshness(tmp_path):
    from web_api.shop_anli_source import normalize_products as normalize_anli

    async def gpt():
        return normalize_products(
            json.loads((Path(__file__).parent / 'fixtures/shop/products.json').read_text())['data']
        )

    async def anli():
        value = json.loads((Path(__file__).parent / 'fixtures/shop/anli-products.json').read_text())
        return normalize_anli(value, {row['id']: {**row, 'config': []} for row in value['data']})

    store = ShopStore(tmp_path / 'source.json', fetcher=gpt, secondary_fetcher=anli)
    asyncio.run(store.refresh())
    app = create_app(
        Sessions(),
        shop_store=store,
        video_settings_store=VideoSettingsStore(tmp_path / 'video/settings.json'),
        video_workflow_store=WorkflowStore(tmp_path / 'video/workflows.json'),
        video_asset_store=AssetStore(tmp_path / 'video/assets'),
    )
    with TestClient(app) as client:
        assert client.get('/api/v1/shop/catalog').json()['products'] == []
        assert client.get('/api/v1/shop/admin').status_code == 401
        state = client.get('/api/v1/shop/admin', headers=HEADERS).json()
        assert len(state['items']) == 12 and set(state['sources']) == {'gpt', 'anli'}
        payload = {
            'revision': 0,
            'telegram': '',
            'skus': {'1000000000011:1': {'price_cents': 23456, 'published': True}},
            'products': {'1000000000011': {'description': '本店说明', 'after_sales': '本店条款'}},
        }
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=payload).status_code == 200
        response = client.get('/api/v1/shop/catalog')
        product = response.json()['products'][0]
        assert product['category'] == 'Grok' and product['variants'][0]['id'] == '1000000000011:1'
        assert product['variants'][0]['price_cents'] == 23456
        assert product['description'] == '本店说明'
        for private in [
            'sources',
            'cost_cents',
            'source_url',
            'faka.anligpt',
            '19000',
            '19500',
            'supplier marketing',
        ]:
            assert private not in response.text


def shop_app(tmp_path, clock=None):
    async def fetch():
        return normalize_products(
            json.loads((Path(__file__).parent / 'fixtures/shop/products.json').read_text())['data']
        )

    store = ShopStore(tmp_path / 'source.json', fetcher=fetch, **({'clock': clock} if clock else {}))
    asyncio.run(store.refresh())
    app = create_app(
        Sessions(),
        shop_store=store,
        video_settings_store=VideoSettingsStore(tmp_path / 'video/settings.json'),
        video_workflow_store=WorkflowStore(tmp_path / 'video/workflows.json'),
        video_asset_store=AssetStore(tmp_path / 'video/assets'),
    )
    return store, app


def test_promotion_labels_original_prices_sold_counts_and_wechat_reach_the_storefront(tmp_path):
    store, app = shop_app(tmp_path)
    payload = {
        'revision': 0, 'telegram': '', 'wechat': 'upc-bh',
        'skus': {
            '2:7': {'price_cents': 13300, 'published': True, 'list_price_cents': 15300},
            '2:2': {'price_cents': 14000, 'published': True},
        },
        'display': {'2': {'badge': '学生价', 'sold': 128}},
    }
    with TestClient(app) as client:
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()['wechat'] == 'upc-bh' and saved.json()['display'] == payload['display']
        catalog = client.get('/api/v1/shop/catalog').json()
    assert catalog['wechat'] == 'upc-bh'
    product = catalog['products'][0]
    assert (product['badge'], product['sold'], product['views']) == ('学生价', 128, 0)
    assert {variant['id']: variant['list_price_cents'] for variant in product['variants']} == {'2:7': 15300, '2:2': None}
    assert catalog['stats'] == {'views': 0, 'sold': 128}



@pytest.mark.parametrize('change', [
    {'wechat': 'a1'},
    {'wechat': '1abcdef'},
    {'wechat': 'upc bh!'},
    # 原价 must be above the selling price, and needs one.
    {'skus': {'2:7': {'price_cents': 13300, 'published': True, 'list_price_cents': 13300}}},
    {'skus': {'2:7': {'price_cents': None, 'published': False, 'list_price_cents': 15300}}},
    {'skus': {'2:7': {'price_cents': 13300, 'published': True, 'list_price': 15300}}},
    # A label is a few plain words, never a link or a contact.
    {'display': {'2': {'badge': '学生价学生价学生价', 'sold': 1}}},
    {'display': {'2': {'badge': 't.me/shop', 'sold': 1}}},
    {'display': {'2': {'badge': '加微信 upc', 'sold': 1}}},
    {'display': {'2': {'badge': '学生价', 'sold': -1}}},
    {'display': {'2': {'badge': '学生价', 'sold': True}}},
    {'display': {'2': {'badge': '学生价'}}},
    {'display': {'999': {'badge': '学生价', 'sold': 1}}},
])
def test_invalid_merchandising_is_refused(tmp_path, change):
    store, app = shop_app(tmp_path)
    payload = {'revision': 0, 'telegram': '', 'skus': {'2:7': {'price_cents': 13300, 'published': True}}, **change}
    with TestClient(app) as client:
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=payload).status_code == 422


def test_visits_count_each_visitor_once_a_day_for_the_shop_and_each_product_on_sale(tmp_path):
    now = [1_760_000_000.0]
    store, app = shop_app(tmp_path, clock=lambda: now[0])
    with TestClient(app) as client:
        client.put('/api/v1/shop/admin', headers=HEADERS, json={
            'revision': 0, 'telegram': 'my_shop', 'skus': {'2:7': {'price_cents': 13300, 'published': True}},
        })
        visit = lambda body, headers=None: client.post('/api/v1/shop/visit', headers=headers or {'Sec-Fetch-Site': 'same-origin'}, json=body)
        assert visit({}).status_code == 204
        assert visit({'product': '2'}).status_code == 204
        assert visit({'product': '2'}).status_code == 204
        # Products not on sale are not counted, and only same-site pages may report.
        assert visit({'product': '3'}).status_code == 204
        assert visit({'product': '2'}, {'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert visit({'product': 2}).status_code == 422
        assert visit({'product': '2', 'extra': 1}).status_code == 422
        catalog = client.get('/api/v1/shop/catalog').json()
        assert catalog['stats']['views'] == 1 and catalog['products'][0]['views'] == 1
        # The next day the same visitor counts again; the marks of the day before are gone.
        now[0] += 86400
        assert visit({'product': '2'}).status_code == 204
        catalog = client.get('/api/v1/shop/catalog').json()
        assert catalog['stats']['views'] == 2 and catalog['products'][0]['views'] == 2
    stats = json.loads((tmp_path / 'stats.json').read_text())
    assert len(stats['seen']) == 2 and 'testclient' not in json.dumps(stats)
    assert set(stats['products']) == {'2'}


def test_unreadable_statistics_never_break_the_catalog(tmp_path):
    store, app = shop_app(tmp_path)
    (tmp_path / 'stats.json').write_text('{"broken":')
    with TestClient(app) as client:
        client.put('/api/v1/shop/admin', headers=HEADERS, json={
            'revision': 0, 'telegram': 'my_shop', 'skus': {'2:7': {'price_cents': 13300, 'published': True}},
        })
        assert client.post('/api/v1/shop/visit', headers={'Sec-Fetch-Site': 'same-origin'}, json={'product': '2'}).status_code == 204
        catalog = client.get('/api/v1/shop/catalog')
        assert catalog.status_code == 200 and catalog.json()['stats']['views'] == 0
    # The damaged file is left for inspection rather than overwritten.
    assert (tmp_path / 'stats.json').read_text() == '{"broken":'


def test_an_admin_page_from_before_the_upgrade_keeps_what_it_cannot_see(tmp_path):
    store, app = shop_app(tmp_path)
    current = {
        'revision': 0, 'telegram': '', 'wechat': 'upc-bh', 'client': 2,
        'skus': {'2:7': {'price_cents': 13300, 'published': True, 'list_price_cents': 15300}},
        'display': {'2': {'badge': '学生价', 'sold': 36}},
    }
    with TestClient(app) as client:
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=current).status_code == 200
        # The old page sends only prices and 上架, and no mark of its version.
        legacy = {'revision': 1, 'telegram': '', 'skus': {'2:7': {'price_cents': 13900, 'published': True}}, 'products': {}}
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, json=legacy)
        assert saved.status_code == 200, saved.text
        state = saved.json()
        assert (state['wechat'], state['display'], state['skus']['2:7']) == (
            'upc-bh', {'2': {'badge': '学生价', 'sold': 36}}, {'price_cents': 13900, 'published': True, 'list_price_cents': 15300},
        )
        assert 'client' not in state
        # A 原价 no longer above the new price is dropped rather than refusing the save.
        legacy = {'revision': 2, 'telegram': '', 'skus': {'2:7': {'price_cents': 16000, 'published': True}}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=legacy).json()
        assert 'list_price_cents' not in state['skus']['2:7'] and state['wechat'] == 'upc-bh'
        # A page from 628f723, which sends the WeChat ID and labels but no mark, clears a 原价 by leaving it out.
        restored = {'revision': 3, 'telegram': '', 'wechat': 'upc-bh', 'client': 2, 'skus': {'2:7': {'price_cents': 13300, 'published': True, 'list_price_cents': 15300}}}
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=restored).status_code == 200
        unmarked = {'revision': 4, 'telegram': '', 'wechat': 'upc-bh', 'skus': {'2:7': {'price_cents': 13300, 'published': True}}, 'display': {}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=unmarked).json()
        assert 'list_price_cents' not in state['skus']['2:7'] and state['display'] == {}
        # The current page clears a field by leaving it out.
        cleared = {'revision': 5, 'telegram': '', 'wechat': '', 'client': 2, 'skus': {'2:7': {'price_cents': 13300, 'published': True}}, 'display': {}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=cleared).json()
        assert (state['wechat'], state['display']) == ('', {})
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json={**cleared, 'revision': 6, 'client': 3}).status_code == 422
    assert 'client' not in json.loads((tmp_path / 'source-merchant.json').read_text())


def test_the_shops_sold_total_keeps_products_taken_off_sale(tmp_path):
    store, app = shop_app(tmp_path)
    with TestClient(app) as client:
        on_sale = {'revision': 0, 'telegram': 'my_shop',
                   'skus': {'2:7': {'price_cents': 13300, 'published': True}}, 'display': {'2': {'badge': '', 'sold': 36}}}
        client.put('/api/v1/shop/admin', headers=HEADERS, json=on_sale)
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 36
        off_sale = {**on_sale, 'revision': 1, 'skus': {'2:7': {'price_cents': 13300, 'published': False}}}
        client.put('/api/v1/shop/admin', headers=HEADERS, json=off_sale)
        catalog = client.get('/api/v1/shop/catalog').json()
    assert catalog['products'] == [] and catalog['stats']['sold'] == 36
