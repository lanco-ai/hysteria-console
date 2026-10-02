import asyncio
import json
from pathlib import Path

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
        assert 'qiangyunai' not in public.text and 'cost_cents' not in public.text
        assert client.get('/api/v1/shop/missing').status_code == 404


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
