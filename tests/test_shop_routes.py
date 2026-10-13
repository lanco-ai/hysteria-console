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


def test_default_shop_routes_enable_private_local_claude_guidance(tmp_path, monkeypatch):
    from functools import partial

    from fastapi import FastAPI
    from web_api import shop_routes

    async def empty():
        return []

    async def dispatch(fn, request):
        return fn(headers=request.headers, path=request.url.path)

    monkeypatch.setattr(shop_routes, 'ShopStore', partial(ShopStore, tmp_path / 'source.json', fetcher=empty))
    monkeypatch.setattr(shop_routes, 'prodseller_configured', lambda: False)
    monkeypatch.setattr(shop_routes, 'lorry_configured', lambda: False)
    app = FastAPI()
    shop_routes.register_shop_routes(app, Sessions(), dispatch)
    with TestClient(app) as client:
        assert client.get('/api/v1/shop/admin').status_code == 401
        admin = client.get('/api/v1/shop/admin', headers=HEADERS)
        assert admin.status_code == 200
        assert [(row['key'], row['cost_cents']) for row in admin.json()['items']] == [
            ('6000000000001:1', 8500), ('6000000000001:2', 10500),
        ]
        public = client.get('/api/v1/shop/catalog')
        assert public.status_code == 200 and public.json()['products'] == []
        for private in ('6000000000001', 'cost_cents', 'sources', 'source_url'):
            assert private not in public.text
        assert not (tmp_path / 'source-merchant.json').exists()


def test_injected_shop_store_keeps_local_products_opt_in(tmp_path):
    from fastapi import FastAPI
    from web_api.shop_routes import register_shop_routes

    async def empty():
        return []

    async def dispatch(fn, request):
        return fn(headers=request.headers, path=request.url.path)

    app = FastAPI()
    register_shop_routes(app, Sessions(), dispatch,
                         store=ShopStore(tmp_path / 'source.json', fetcher=empty))
    with TestClient(app) as client:
        admin = client.get('/api/v1/shop/admin', headers=HEADERS).json()
        assert admin['items'] == [] and set(admin['sources']) == {'gpt'}
        assert client.get('/api/v1/shop/catalog').json()['products'] == []


def test_default_routes_still_work_after_store_only_rollback(monkeypatch):
    from fastapi import FastAPI
    from web_api import shop_routes

    class LegacyStore:
        # Rollback restores shop_store.py alone, leaving today's routes in place.
        def __init__(self, *, secondary_fetcher, prodseller_fetcher, lorry_fetcher,
                     fanai_fetcher, sms_fetcher):
            pass

        async def admin(self):
            return {'items': [], 'revision': 0}

        async def public(self):
            return {'products': [], 'status': 'unavailable'}

    async def dispatch(fn, request):
        return fn(headers=request.headers, path=request.url.path)

    monkeypatch.setattr(shop_routes, 'ShopStore', LegacyStore)
    monkeypatch.setattr(shop_routes, 'prodseller_configured', lambda: False)
    monkeypatch.setattr(shop_routes, 'lorry_configured', lambda: False)
    app = FastAPI()
    shop_routes.register_shop_routes(app, Sessions(), dispatch)
    with TestClient(app) as client:
        assert client.get('/api/v1/shop/catalog').json() == {
            'products': [], 'status': 'unavailable',
        }
        assert client.get('/api/v1/shop/admin', headers=HEADERS).json() == {
            'items': [], 'revision': 0,
        }


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


def test_promotion_prices_labels_sold_counts_and_wechat_reach_the_storefront(tmp_path):
    store, app = shop_app(tmp_path)
    # 售价 is the usual price; 学生价 (the label) names a lower 优惠价 for the buyers it applies to.
    payload = {
        'revision': 0, 'client': 3, 'telegram': '', 'wechat': 'upc-bh',
        'skus': {
            '2:7': {'price_cents': 12800, 'published': True, 'promo_price_cents': 11800},
            '2:2': {'price_cents': 14000, 'published': True},
        },
        'display': {'2': {'badge': '学生价', 'sold': 128}},
    }
    with TestClient(app) as client:
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()['wechat'] == 'upc-bh' and saved.json()['display'] == payload['display']
        assert saved.json()['skus']['2:7'] == {'price_cents': 12800, 'published': True, 'promo_price_cents': 11800}
        catalog = client.get('/api/v1/shop/catalog').json()
    assert catalog['wechat'] == 'upc-bh'
    product = catalog['products'][0]
    assert (product['badge'], product['sold'], product['views']) == ('学生价', 128, 0)
    assert {variant['id']: (variant['price_cents'], variant['promo_price_cents']) for variant in product['variants']} == {
        '2:7': (12800, 11800), '2:2': (14000, None),
    }
    assert catalog['stats'] == {'views': 0, 'sold': 128}
    assert 'list_price_cents' not in json.dumps(catalog)


@pytest.mark.parametrize('change', [
    {'wechat': 'a1'},
    {'wechat': '1abcdef'},
    {'wechat': 'upc bh!'},
    # The 优惠价 must be below the price, and needs one.
    {'skus': {'2:7': {'price_cents': 12800, 'published': True, 'promo_price_cents': 12800}}},
    {'skus': {'2:7': {'price_cents': 12800, 'published': True, 'promo_price_cents': 0}}},
    {'skus': {'2:7': {'price_cents': None, 'published': False, 'promo_price_cents': 11800}}},
    {'skus': {'2:7': {'price_cents': 12800, 'published': True, 'promo': 11800}}},
    # Today's page no longer sends a 原价.
    {'skus': {'2:7': {'price_cents': 12800, 'published': True, 'list_price_cents': 15300}}},
    {'client': 4},
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
    payload = {'revision': 0, 'client': 3, 'telegram': '', 'skus': {'2:7': {'price_cents': 12800, 'published': True}}, **change}
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


def test_older_admin_pages_keep_what_they_cannot_see(tmp_path):
    store, app = shop_app(tmp_path)
    current = {
        'revision': 0, 'client': 3, 'telegram': '', 'wechat': 'upc-bh',
        'skus': {'2:7': {'price_cents': 12800, 'published': True, 'promo_price_cents': 11800}},
        'display': {'2': {'badge': '学生价', 'sold': 36}},
    }
    with TestClient(app) as client:
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=current).status_code == 200
        # A page from before 628f723 sends only prices and 上架, and no mark.
        legacy = {'revision': 1, 'telegram': '', 'skus': {'2:7': {'price_cents': 13900, 'published': True}}, 'products': {}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=legacy).json()
        assert (state['wechat'], state['display'], state['skus']['2:7']) == (
            'upc-bh', {'2': {'badge': '学生价', 'sold': 36}}, {'price_cents': 13900, 'published': True, 'promo_price_cents': 11800},
        )
        assert 'client' not in state
        # A kept 优惠价 that is no longer below the price is dropped rather than refusing the save.
        legacy = {'revision': 2, 'telegram': '', 'skus': {'2:7': {'price_cents': 11000, 'published': True}}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=legacy).json()
        assert 'promo_price_cents' not in state['skus']['2:7'] and state['wechat'] == 'upc-bh'
        # A page of 628f723 (mark 2) priced a promotion as the selling price under a higher 原价:
        # that is the price and its 优惠价 now. Its WeChat ID and labels are its own to clear.
        marked = {'revision': 3, 'client': 2, 'telegram': '', 'wechat': 'upc-bh',
                  'skus': {'2:7': {'price_cents': 11800, 'published': True, 'list_price_cents': 12800}}, 'display': {}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=marked).json()
        assert state['skus']['2:7'] == {'price_cents': 12800, 'published': True, 'promo_price_cents': 11800}
        assert state['display'] == {}
        # Without a 原价 it keeps the 优惠价 it cannot show.
        marked = {**marked, 'revision': 4, 'skus': {'2:7': {'price_cents': 12900, 'published': True}}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=marked).json()
        assert state['skus']['2:7'] == {'price_cents': 12900, 'published': True, 'promo_price_cents': 11800}
        # Today's page clears a field by leaving it out.
        cleared = {'revision': 5, 'client': 3, 'telegram': '', 'wechat': '', 'skus': {'2:7': {'price_cents': 12800, 'published': True}}, 'display': {}}
        state = client.put('/api/v1/shop/admin', headers=HEADERS, json=cleared).json()
        assert (state['wechat'], state['display'], state['skus']['2:7']) == ('', {}, {'price_cents': 12800, 'published': True})
    stored = json.loads((tmp_path / 'source-merchant.json').read_text())
    assert 'client' not in stored and 'list_price_cents' not in json.dumps(stored)


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
