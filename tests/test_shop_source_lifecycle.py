"""Permanent source removal fences old readers and erases only its retail configuration."""
import asyncio
import copy
import json
from pathlib import Path

import pytest
import state_store
from fastapi.testclient import TestClient

from tests.test_shop_routes import HEADERS, shop_app
from tests.test_shop_source_controls import payload
from tests.test_shop_store import make_store, products
from web_api.shop_source import normalize_products
from web_api.shop_store import Conflict, validate_settings

CONFIRM = 'delete-source-and-products'


def removal(revision):
    return {'revision': revision, 'confirm': CONFIRM}


def test_delete_erases_all_historical_keys_and_keeps_other_sources_and_history(tmp_path):
    async def run():
        store = make_store(tmp_path, local_products=True)
        assert callable(getattr(store, 'delete_source', None)), 'source deletion API is missing'
        await store.refresh()
        saved = payload(skus={'2:7': {'price_cents': 11000, 'promo_price_cents': 10000, 'published': True},
                              '6000000000001:1': {'price_cents': 8500, 'published': True}},
                        telegram='shop_contact', wechat='shop_contact',
                        products={'2': {'description': 'private copy', 'after_sales': 'private promise'}},
                        display={'2': {'badge': '学生价', 'sold': 5}},
                        source_enabled={'gpt': False, 'sms': False},
                        local_names={'products': {'6000000000001': '自有商品'}})
        await store.update(saved)
        disk = json.loads(store.settings_path.read_text())
        # The old product disappeared from the current cache long ago.
        disk['skus']['999:3'] = {'price_cents': 123, 'published': False}
        disk['products']['999'] = {'description': 'old', 'after_sales': 'old'}
        disk['display']['999'] = {'badge': '', 'sold': 4}
        disk['skus']['2000000000002:1'] = {'price_cents': 1888, 'published': True}
        disk['products']['2000000000002'] = {'description': 'Muse', 'after_sales': 'new custom after sales'}
        state_store.save_json(store.settings_path, disk)
        await store.visit('fictional-peer')
        await store.record_sms_received(17)
        protected = {path: path.read_bytes() for path in (store.stats.path, store.sms_sales.path)}
        response = await store.delete_source('gpt', removal(1))
        assert response['revision'] == 2
        assert response['source_lifecycle'] == {'gpt': {'generation': 1, 'deleted': True}}
        assert 'gpt' not in response['sources']
        assert response['removed_sources']['gpt']['can_add'] is True
        assert response['source_enabled'] == {'sms': False}
        assert not store.path.exists()
        after = json.loads(store.settings_path.read_text())
        assert after['skus'] == {key: value for key, value in disk['skus'].items() if key.startswith(('6000', '2000'))}
        assert after['products'] == {'2000000000002': disk['products']['2000000000002']}
        assert after['display'] == {}
        for field in ('telegram', 'wechat', 'local_names'):
            assert after[field] == disk[field]
        assert all(path.read_bytes() == value for path, value in protected.items())
        public = await store.public()
        assert [row['id'] for row in public['products']] == ['6000000000001']
        for private in ('source_lifecycle', 'removed_sources', 'supplier', 'generation', 'private copy'):
            assert private not in json.dumps(public)
        # A residual invalid cache is ignored before opening/parsing it.
        store.path.write_text('{invalid')
        assert (await make_store(tmp_path, local_products=True).admin())['items'] == response['items']
        await store.add_source('gpt', {'revision': 2})
        assert not store.path.exists()
        assert (await store.admin())['source_lifecycle']['gpt'] == {'generation': 2, 'deleted': False}
        assert (await store.admin())['removed_sources'] == {}
        await store.refresh()
        current = await store.admin()
        assert len([row for row in current['items'] if row['provider'] == 'gpt']) == 7
        assert all(not current['skus'].get(row['key'], {}).get('published') for row in current['items'] if row['provider'] == 'gpt')
        assert '2' not in current['products'] and '999' not in current['display']
    asyncio.run(run())


@pytest.mark.parametrize('client', [None, 2, 3])
def test_old_put_cannot_restore_lifecycle_or_deleted_data(tmp_path, client):
    async def run():
        store = make_store(tmp_path)
        await store.delete_source('gpt', removal(0))
        request = payload(1, source_enabled={})
        if client is None:
            request.pop('client')
        else:
            request['client'] = client
        await store.update(request)
        assert (await store.admin())['source_lifecycle']['gpt']['deleted'] is True
        for bad in ({'source_lifecycle': {}}, {'source_lifecycle': {'gpt': {'generation': 2, 'deleted': False}}},
                    {'skus': {'2:7': {'price_cents': 100, 'published': True}}},
                    {'products': {'2': {'description': '', 'after_sales': ''}}},
                    {'display': {'2': {'badge': '', 'sold': 0}}}):
            with pytest.raises(ValueError):
                await store.update({**request, 'revision': 2, **bad})
        with pytest.raises(Conflict):
            await store.update({**request, 'revision': 0})
        await store.update({**request, 'revision': 2, 'source_enabled': {'gpt': True}})
        assert 'gpt' not in (await store.admin())['sources']
    asyncio.run(run())


@pytest.mark.parametrize('value', [None, [], {'local': {'generation': 1, 'deleted': True}},
    {'unknown': {'generation': 1, 'deleted': True}}, {'gpt': {}},
    {'gpt': {'generation': True, 'deleted': True}}, {'gpt': {'generation': 0, 'deleted': True}},
    {'gpt': {'generation': 1, 'deleted': 1}}, {'gpt': {'generation': 1, 'deleted': True, 'extra': 1}}])
def test_lifecycle_storage_schema_is_strict(value):
    with pytest.raises(ValueError):
        validate_settings(payload(source_lifecycle=value))


@pytest.mark.parametrize('readd', [False, True])
@pytest.mark.parametrize('failed', [False, True])
def test_in_flight_result_cannot_recreate_deleted_or_readded_cache(tmp_path, readd, failed):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def fetch():
            started.set()
            await finish.wait()
            if failed:
                raise RuntimeError('fictional upstream failure')
            return normalize_products(products())
        async def later():
            calls.append('anli')
            return []
        store = make_store(tmp_path, fetcher=fetch, secondary_fetcher=later)
        task = asyncio.create_task(store.refresh())
        await started.wait()
        await asyncio.wait_for(store.delete_source('gpt', removal(0)), 2)
        await store.delete_source('anli', removal(1))
        if readd:
            await store.add_source('gpt', {'revision': 2})
        finish.set()
        await task
        assert not store.path.exists() and not store.secondary_path.exists()
        assert calls == []
        assert (await store.admin())['items'] == []
    asyncio.run(run())


def test_price_save_does_not_discard_refresh_and_duplicate_transitions_are_noops(tmp_path):
    async def run():
        started, finish = asyncio.Event(), asyncio.Event()
        async def fetch():
            started.set()
            await finish.wait()
            return normalize_products(products())
        store = make_store(tmp_path, fetcher=fetch)
        task = asyncio.create_task(store.refresh())
        await started.wait()
        await store.update(payload(telegram='merchant_shop'))
        finish.set()
        await task
        assert len((await store.admin())['items']) == 7
        removed = await store.delete_source('gpt', removal(1))
        assert (await store.delete_source('gpt', removal(2))) == removed
        with pytest.raises(Conflict):
            await store.delete_source('gpt', removal(1))
        added = await store.add_source('gpt', {'revision': 2})
        assert (await store.add_source('gpt', {'revision': 3})) == added
        assert added['source_lifecycle']['gpt']['generation'] == 2
    asyncio.run(run())


@pytest.mark.parametrize('stage', ['unlink', 'fsync', 'save', 'after_replace'])
def test_storage_failure_never_claims_delete_success_or_rewrites_old_state(tmp_path, monkeypatch, stage):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        await store.update(payload(skus={'2:7': {'price_cents': 123, 'published': True}}))
        before = store.settings_path.read_bytes()
        original_save = state_store.save_json
        writes = []
        if stage == 'unlink':
            original = Path.unlink
            def unlink(path, *args, **kwargs):
                if path == store.path:
                    raise OSError('fictional unlink failure')
                return original(path, *args, **kwargs)
            monkeypatch.setattr(Path, 'unlink', unlink)
        elif stage == 'fsync':
            monkeypatch.setattr(state_store, '_fsync_dir', lambda path: (_ for _ in ()).throw(OSError('fictional fsync failure')))
        else:
            def save(path, value, *args, **kwargs):
                if path == store.settings_path:
                    writes.append(copy.deepcopy(value))
                    if stage == 'after_replace':
                        original_save(path, value, *args, **kwargs)
                        raise state_store.AtomicReplaceDurabilityUncertain('fictional durability failure')
                    raise OSError('fictional write failure')
                return original_save(path, value, *args, **kwargs)
            monkeypatch.setattr(state_store, 'save_json', save)
        with pytest.raises((OSError, state_store.StateStoreError)):
            await store.delete_source('gpt', removal(1))
        if stage == 'after_replace':
            assert json.loads(store.settings_path.read_text())['source_lifecycle']['gpt']['deleted'] is True
            assert len(writes) == 1
        else:
            assert store.settings_path.read_bytes() == before
    asyncio.run(run())


@pytest.mark.parametrize('method', ['DELETE', 'POST'])
def test_source_routes_guard_body_revision_and_provider(tmp_path, method):
    store, app = shop_app(tmp_path)
    endpoint = '/api/v1/shop/admin/sources/gpt'
    good = removal(0) if method == 'DELETE' else {'revision': 0}
    with TestClient(app) as client:
        def request(data=good, headers=HEADERS, url=endpoint):
            return client.request(method, url, headers=headers, json=data)
        assert request(headers={}).status_code == 401
        assert request(headers={'Cookie': 'sid=user', 'Sec-Fetch-Site': 'same-origin'}).status_code == 403
        assert request(headers={**HEADERS, 'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        for provider in ('local', 'unknown', 'GPT'):
            assert request(url=endpoint.rsplit('/', 1)[0] + '/' + provider).status_code == 422
        for bad in ({}, [], {**good, 'revision': True}, {**good, 'unexpected': 1}):
            assert request(data=bad).status_code == 422
        if method == 'DELETE':
            assert request(data={'revision': 0, 'confirm': 'yes'}).status_code == 422
        assert request(data={**good, 'revision': 10}).status_code == 409
        assert client.request(method, endpoint, headers=HEADERS, content='x' * 65537).status_code == 413
        assert request().status_code == 200
        assert request(data={**good, 'revision': 10}).status_code == 409


def test_deleted_unconfigured_source_can_be_seen_but_cannot_be_added(tmp_path):
    async def run():
        store = make_store(tmp_path)
        response = await store.delete_source('lorry', removal(0))
        assert response['removed_sources']['lorry']['can_add'] is False
        with pytest.raises(ValueError):
            await store.add_source('lorry', {'revision': 1})
    asyncio.run(run())
