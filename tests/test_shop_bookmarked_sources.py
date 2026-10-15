"""A saved source link is private metadata, never an active feed or cached catalog."""

import asyncio
import json
from pathlib import Path

import pytest
import state_store
from fastapi.testclient import TestClient

from tests.test_shop_routes import HEADERS, shop_app
from tests.test_shop_source_controls import payload
from tests.test_shop_source_lifecycle import removal
from tests.test_shop_store import make_store
from web_api.shop_store import Conflict


def bookmarked_store(tmp_path, **kwargs):
    store = make_store(tmp_path, **kwargs)
    # Routes opt into bookmarks after construction for store-only rollback compatibility.
    store.bookmarked_sources = True
    return store


def test_bookmark_is_private_non_stale_and_never_loads_a_cache_or_joins_refresh(tmp_path, monkeypatch):
    async def run():
        now = [1000]
        store = bookmarked_store(tmp_path, local_products=True, clock=lambda: now[0])
        cache = tmp_path / 'source-wzyp.json'
        cache.write_text('{not a readable catalog')
        original_load = state_store.load_json_strict

        def load(path, *args, **kwargs):
            assert Path(path) != cache, 'a bookmarked source must not load cache bytes'
            return original_load(path, *args, **kwargs)

        monkeypatch.setattr(state_store, 'load_json_strict', load)
        admin = await store.admin()
        source = admin['sources'].get('wzyp')
        assert source is not None, 'the requested source bookmark is missing'
        assert source == {
            'supplier': 'WZYP', 'site': 'https://wzyp.cn/shop/LHCdeAiPuzi',
            'covers': '待接入', 'enabled': False, 'can_read': False,
            'last_success': None, 'error': None, 'is_stale': False,
            'retry_after_seconds': 0, 'cooldown_seconds': 0,
        }
        assert 'wzyp' not in store._feed_ids()
        for manual in (False, True, False):
            now[0] += 901
            refreshed = await store.refresh(manual=manual)
            assert refreshed['sources']['wzyp'] == source
            assert all(item['provider'] != 'wzyp' for item in refreshed['items'])
            assert cache.read_text() == '{not a readable catalog'
            assert refreshed['error'] is None
        public = await store.public()
        assert public['status'] == 'ready'
        for private in ('wzyp', 'WZYP', '待接入', 'LHCdeAiPuzi', 'can_read', 'sources'):
            assert private not in json.dumps(public, ensure_ascii=False)
        assert not store.settings_path.exists()
    asyncio.run(run())


@pytest.mark.parametrize('client', [None, 2, 3])
def test_saves_cannot_enable_bookmark_and_old_pages_preserve_disabled_state(tmp_path, client):
    async def run():
        store = bookmarked_store(tmp_path)
        saved = await store.update(payload(source_enabled={'wzyp': False, 'gpt': False}))
        assert saved['sources']['wzyp']['enabled'] is False
        before = store.settings_path.read_bytes()
        with pytest.raises(ValueError):
            await store.update(payload(1, source_enabled={'wzyp': True}))
        assert store.settings_path.read_bytes() == before
        old = payload(1)
        if client is None:
            old.pop('client')
        else:
            old['client'] = client
        await store.update(old)
        assert json.loads(store.settings_path.read_text())['source_enabled'] == {'gpt': False, 'wzyp': False}
        # Even an older page explicitly clearing its known switches cannot enable a bookmark.
        reset = await store.update(payload(2, source_enabled={}))
        assert reset['sources']['wzyp']['enabled'] is False
        assert reset['sources']['gpt']['enabled'] is True
        restarted = bookmarked_store(tmp_path)
        assert (await restarted.admin())['sources']['wzyp']['enabled'] is False
    asyncio.run(run())


def test_bookmark_delete_and_readd_use_revision_generation_and_preserve_every_other_setting(tmp_path):
    async def run():
        store = bookmarked_store(tmp_path, local_products=True)
        await store.refresh()
        saved = payload(telegram='merchant_shop', wechat='merchant_shop',
                        skus={'2:7': {'price_cents': 11000, 'promo_price_cents': 10000, 'published': True}},
                        products={'2': {'description': '商户说明', 'after_sales': '商户售后'}},
                        display={'2': {'badge': '学生价', 'sold': 6}},
                        local_names={'products': {'6000000000001': '本人账号指导'}},
                        source_enabled={'wzyp': False, 'sms': False})
        await store.update(saved)
        previous = await store.admin()
        await store.visit('fictional-peer')
        await store.record_sms_received(23)
        protected = {path: path.read_bytes() for path in (store.path, store.stats.path, store.sms_sales.path)}
        bookmark_cache = tmp_path / 'source-wzyp.json'
        neighbor = tmp_path / 'source-wzyp-extra.json'
        bookmark_cache.write_text('old untrusted cache')
        neighbor.write_text('unrelated file')
        before = await store.public()
        with pytest.raises(ValueError):
            await store.delete_source('wzyp', {'revision': 1, 'confirm': 'yes'})
        with pytest.raises(Conflict):
            await store.delete_source('wzyp', removal(0))
        removed = await store.delete_source('wzyp', removal(1))
        assert 'wzyp' not in removed['sources']
        assert removed['removed_sources']['wzyp']['can_add'] is True
        assert removed['removed_sources']['wzyp']['can_read'] is False
        assert removed['source_lifecycle']['wzyp'] == {'generation': 1, 'deleted': True}
        assert not bookmark_cache.exists() and neighbor.read_text() == 'unrelated file'
        for field in ('telegram', 'wechat', 'skus', 'products', 'display', 'local_names'):
            assert removed[field] == previous[field]
        assert removed['source_enabled'] == {'sms': False}
        assert (await store.delete_source('wzyp', removal(2)))['revision'] == 2
        restarted = bookmarked_store(tmp_path, local_products=True)
        assert 'wzyp' not in (await restarted.admin())['sources']
        bookmark_cache.write_text('{residual invalid cache')
        with pytest.raises(Conflict):
            await restarted.add_source('wzyp', {'revision': 1})
        added = await restarted.add_source('wzyp', {'revision': 2})
        assert added['source_lifecycle']['wzyp'] == {'generation': 2, 'deleted': False}
        assert added['sources']['wzyp']['enabled'] is False
        assert added['sources']['wzyp']['can_read'] is False
        assert not bookmark_cache.exists()
        assert (await restarted.add_source('wzyp', {'revision': 3}))['revision'] == 3
        await restarted.refresh(manual=True)
        assert not bookmark_cache.exists()
        assert await restarted.public() == before
        assert all(path.read_bytes() == value for path, value in protected.items())
    asyncio.run(run())


def test_bookmark_routes_reject_enable_and_reuse_authenticated_lifecycle(tmp_path):
    store, app = shop_app(tmp_path)
    store.bookmarked_sources = True
    endpoint = '/api/v1/shop/admin/sources/wzyp'
    with TestClient(app) as client:
        assert client.get('/api/v1/shop/admin').status_code == 401
        assert client.put('/api/v1/shop/admin', headers=HEADERS,
                          json=payload(source_enabled={'wzyp': True})).status_code == 422
        disabled = client.put('/api/v1/shop/admin', headers=HEADERS,
                              json=payload(source_enabled={'wzyp': False}))
        assert disabled.status_code == 200
        for method, data in [('DELETE', removal(1)), ('POST', {'revision': 1})]:
            assert client.request(method, endpoint, json=data).status_code == 401
            assert client.request(method, endpoint, headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'},
                                  json=data).status_code == 403
        assert client.request('DELETE', endpoint, headers=HEADERS, json=removal(1)).status_code == 200
        assert client.post(endpoint, headers=HEADERS, json={'revision': 1}).status_code == 409
        added = client.post(endpoint, headers=HEADERS, json={'revision': 2})
        assert added.status_code == 200 and added.json()['sources']['wzyp']['enabled'] is False
        assert 'wzyp' not in client.get('/api/v1/shop/catalog').text
