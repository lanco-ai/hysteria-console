"""Read switches keep private state, stop subsequent feed reads, and hide public goods."""

import asyncio
import json

import pytest
import state_store
from fastapi.testclient import TestClient

from tests.test_shop_routes import HEADERS, shop_app
from tests.test_shop_store import make_store, products
from web_api.shop_source import normalize_products
from web_api.shop_store import Conflict, ShopStore


def payload(revision=0, **changes):
    return {'revision': revision, 'client': 3, 'telegram': '', 'skus': {}, **changes}


@pytest.mark.parametrize('flags', [None, [], False, {'gpt': 0}, {'anli': 1},
                                  {'sms': 'false'}, {'local': False}, {'unknown': True}])
def test_switch_schema_rejects_invalid_flags_without_writing(tmp_path, flags):
    async def run():
        store = make_store(tmp_path)
        with pytest.raises(ValueError):
            await store.update(payload(source_enabled=flags))
        assert not store.settings_path.exists()
    asyncio.run(run())


@pytest.mark.parametrize('client', [None, 2, 3])
def test_omitting_switches_preserves_them_and_explicit_reset_omits_empty_storage(tmp_path, client):
    async def run():
        store = make_store(tmp_path)
        saved = await store.update(payload(source_enabled={'gpt': False, 'sms': True}))
        assert saved['source_enabled'] == {'gpt': False}
        old = payload(1)
        if client is None:
            old.pop('client')
        else:
            old['client'] = client
        await store.update(old)
        assert json.loads(store.settings_path.read_text())['source_enabled'] == {'gpt': False}
        assert (await store.admin())['sources']['gpt']['enabled'] is False
        await store.update(payload(2, source_enabled={}))
        assert 'source_enabled' not in json.loads(store.settings_path.read_text())
        assert (await store.admin())['sources']['gpt']['enabled'] is True
        await store.update(payload(3, source_enabled={'gpt': True}))
        assert 'source_enabled' not in json.loads(store.settings_path.read_text())
    asyncio.run(run())


def test_disable_hides_public_but_preserves_cache_and_all_merchant_edits(tmp_path):
    async def run():
        store = make_store(tmp_path, local_products=True)
        await store.refresh()
        saved = payload(skus={'2:7': {'price_cents': 11000, 'promo_price_cents': 10000, 'published': True},
                              '6000000000001:1': {'price_cents': 12000, 'published': True}},
                        products={'2': {'description': '商品说明', 'after_sales': '售后说明'}},
                        display={'2': {'badge': '学生价', 'sold': 3}},
                        local_names={'products': {'6000000000001': '本人账号指导'},
                                     'variants': {'6000000000001:1': '微软邮箱指导'}})
        await store.update(saved)
        before = await store.admin()
        cache = store.path.read_bytes()
        await store.update({**saved, 'revision': 1, 'source_enabled': {'gpt': False}})
        admin = await store.admin()
        assert store.path.read_bytes() == cache
        for field in ('skus', 'products', 'display', 'local_names', 'items'):
            assert admin[field] == before[field]
        assert admin['sources']['local']['enabled'] is True
        public = await store.public()
        assert [item['id'] for item in public['products']] == ['6000000000001']
        assert public['products'][0]['title'] == '本人账号指导'
        assert public['status'] == 'ready'
        assert 'source_enabled' not in public and 'sources' not in public
        other = make_store(tmp_path, local_products=True)
        await other.update({**saved, 'revision': 2, 'source_enabled': {}})
        assert [item['id'] for item in (await store.public())['products']] == ['2', '6000000000001']
    asyncio.run(run())


@pytest.mark.parametrize('manual', [False, True])
def test_disabled_sources_never_fetch_even_when_due_and_enable_keeps_capabilities(tmp_path, manual):
    async def run():
        calls = []
        async def fetch():
            calls.append(True)
            return normalize_products(products())
        store = make_store(tmp_path, fetcher=fetch)
        await store.update(payload(source_enabled={'gpt': False, 'sms': True}))
        for _ in range(2):
            await store.refresh(manual=manual)
        assert calls == [] and not store.path.exists()
        assert set((await store.admin())['sources']) == {'gpt'}
        # A second process/instance re-enables; the first sees the persisted setting.
        await make_store(tmp_path).update(payload(1, source_enabled={}))
        await store.refresh(manual=manual)
        assert calls == [True] and len((await store.admin())['items']) == 7
    asyncio.run(run())


def test_disabling_during_a_fetch_skips_later_feeds_and_completion_cannot_unhide(tmp_path):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def first():
            calls.append('gpt')
            started.set()
            await finish.wait()
            return normalize_products(products())
        async def second():
            calls.append('anli')
            return []
        store = make_store(tmp_path, fetcher=first, secondary_fetcher=second)
        task = asyncio.create_task(store.refresh(manual=True))
        await started.wait()
        # Saving must not wait on network I/O; the short merchant lock is free here.
        await asyncio.wait_for(store.update(payload(source_enabled={'gpt': False, 'anli': False})), 2)
        finish.set()
        await task
        assert calls == ['gpt']
        assert len((await store.admin())['items']) == 7
        assert (await store.public())['products'] == []
        await store.update(payload(1, source_enabled={'gpt': False, 'anli': False},
                                   skus={'2:7': {'price_cents': 12300, 'published': True}}))
        assert (await store.public())['products'] == []
        assert json.loads(store.settings_path.read_text())['source_enabled'] == {'gpt': False, 'anli': False}
    asyncio.run(run())


def test_reenable_obeys_existing_backoff_and_disabled_errors_do_not_taint_status(tmp_path):
    async def run():
        now, calls = [1000], []
        async def failed():
            calls.append(True)
            raise RuntimeError('fictional feed unavailable')
        store = make_store(tmp_path, fetcher=failed, local_products=True, clock=lambda: now[0])
        await store.refresh()
        await store.update(payload(source_enabled={'gpt': False}))
        assert (await store.admin())['error'] is None
        assert (await store.public())['status'] == 'ready'
        await store.update(payload(1, source_enabled={}))
        await store.refresh(manual=True)
        assert len(calls) == 1
        now[0] += 901
        await store.refresh()
        assert len(calls) == 2
    asyncio.run(run())


def test_reenable_preserves_fresh_snapshot_ttl_and_manual_cooldown(tmp_path):
    async def run():
        now, calls = [1000], []
        async def fetch():
            calls.append(now[0])
            return normalize_products(products())
        store = make_store(tmp_path, fetcher=fetch, clock=lambda: now[0])
        await store.refresh()
        cached = store.path.read_bytes()
        await store.update(payload(source_enabled={'gpt': False}))
        now[0] = 1030
        await store.refresh(manual=True)
        await store.update(payload(1, source_enabled={}))
        # Re-enabling neither ages a fresh cache nor removes the original cooldown.
        await store.refresh()
        await store.refresh(manual=True)
        assert calls == [1000] and store.path.read_bytes() == cached
        now[0] = 1061
        await store.refresh(manual=True)
        assert calls == [1000, 1061]
        now[0] = 1961
        await store.refresh()
        assert calls == [1000, 1061, 1961]
    asyncio.run(run())


def test_disabled_corrupt_cache_is_isolated_but_invalid_merchant_still_fails_closed(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.update(payload(source_enabled={'gpt': False}))
        store.path.write_text('{invalid')
        assert (await store.admin())['items'] == []
        assert (await store.public())['products'] == []
        assert (await store.public())['status'] == 'unavailable'
        store.settings_path.write_text(json.dumps(payload(source_enabled={'gpt': 'false'})))
        for operation in (store.admin, store.public, store.refresh):
            with pytest.raises(state_store.InvalidJsonState):
                await operation()
    asyncio.run(run())


def test_switches_do_not_relax_known_product_or_revision_validation(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.update(payload(source_enabled={'gpt': False}))
        with pytest.raises(Conflict):
            await store.update(payload(source_enabled={}))
        with pytest.raises(ValueError):
            await store.update(payload(1, source_enabled={'gpt': False}, skus={'999:1': {'price_cents': 100, 'published': True}}))
        assert (await store.admin())['revision'] == 1
    asyncio.run(run())


def test_source_controls_reuse_authenticated_settings_endpoint_and_conflicts(tmp_path):
    store, app = shop_app(tmp_path)
    with TestClient(app) as client:
        assert client.put('/api/v1/shop/admin', json=payload(source_enabled={'gpt': False})).status_code == 401
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=payload(source_enabled={'local': False})).status_code == 422
        response = client.put('/api/v1/shop/admin', headers=HEADERS, json=payload(source_enabled={'gpt': False}))
        assert response.status_code == 200
        assert response.json()['sources']['gpt']['enabled'] is False
        assert client.put('/api/v1/shop/admin', headers=HEADERS, json=payload(source_enabled={})).status_code == 409
        assert client.get('/api/v1/shop/catalog').json()['products'] == []
