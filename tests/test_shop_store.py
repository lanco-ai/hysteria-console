import asyncio
import copy
import json
from pathlib import Path

import pytest
import state_store
from web_api.shop_source import SourceError, normalize_products
from web_api.shop_store import Conflict, ShopStore


def products():
    return json.loads((Path(__file__).parent / 'fixtures/shop/products.json').read_text())['data']


def make_store(tmp_path, **kwargs):
    async def fetch():
        return normalize_products(products())

    return ShopStore(tmp_path / 'source.json', fetcher=kwargs.pop('fetcher', fetch), **kwargs)


def test_draft_then_publish_private_cost_and_refresh_preserves_retail(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        assert (await store.public())['products'] == []
        admin = await store.admin()
        assert admin['revision'] == 0 and len(admin['items']) == 7
        await store.update(
            {
                'revision': 0,
                'telegram': 'merchant_shop',
                'skus': {'2:7': {'price_cents': 110001, 'published': True}},
            }
        )
        public = await store.public()
        assert public['products'][0]['variants'][0]['price_cents'] == 110001
        assert public['telegram'] == 'merchant_shop'
        text = json.dumps(public)
        for private in [
            'cost',
            'source_url',
            'qiangyunai',
            '100000',
            'title_private',
            'cnadsiuvhga',
        ]:
            assert private not in text
        assert public['products'][0]['variants'][0]['quantity'] is None
        with pytest.raises(Conflict):
            await store.update({'revision': 0, 'telegram': '', 'skus': {}})
        store.clock = lambda: 100000000000
        await store.refresh()
        assert (await store.public())['products'][0]['variants'][0]['price_cents'] == 110001

    asyncio.run(run())


@pytest.mark.parametrize(
    'change',
    [
        {'telegram': 'https://t.me/evil'},
        {'telegram': '@wrong'},
        {'skus': {'2:7': {'price_cents': None, 'published': True}}},
        {'skus': {'2:7': {'price_cents': 0, 'published': True}}},
        {'skus': {'2:7': {'price_cents': True, 'published': True}}},
        {'skus': {'2:7': {'price_cents': 100.5, 'published': True}}},
        {'skus': {'bad': {'price_cents': 100, 'published': True}}},
        {'revision': True},
        {'url': 'https://evil.test'},
    ],
)
def test_bad_merchant_settings(tmp_path, change):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        with pytest.raises(ValueError):
            await store.update({'revision': 0, 'telegram': '', 'skus': {}, **change})
        assert (await store.admin())['revision'] == 0

    asyncio.run(run())


def test_refresh_exclusion_backoff_stale_and_removed_skus(tmp_path):
    async def run():
        now = [1000]
        count = [0]
        fail = [False]

        async def fetch():
            count[0] += 1
            await asyncio.sleep(0.02)
            if fail[0]:
                raise SourceError('private url / token must not leak')
            return normalize_products(products())

        store = make_store(tmp_path, fetcher=fetch, clock=lambda: now[0])
        other = make_store(tmp_path, fetcher=fetch, clock=lambda: now[0])
        await asyncio.gather(store.refresh(), other.refresh())
        assert count[0] == 1
        await store.update(
            {
                'revision': 0,
                'telegram': '',
                'skus': {'2:7': {'price_cents': 110000, 'published': True}},
            }
        )
        fail[0] = True
        now[0] += 901
        await store.refresh()
        await store.refresh(manual=True)
        assert count[0] == 2
        public = await store.public()
        assert public['status'] == 'stale' and len(public['products']) == 1
        assert public['products'][0]['variants'][0]['available'] is False
        assert 'private url' not in json.dumps(await store.admin())

        async def removed():
            return []

        store.fetcher = removed
        now[0] += 901
        await store.refresh()
        assert (await store.public())['products'] == []

    asyncio.run(run())


def test_corrupt_persisted_state_fails_closed(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        data = json.loads(store.path.read_text())
        data['items'][0]['public_title'] = 'https://supplier.test'
        store.path.write_text(json.dumps(data))
        with pytest.raises(state_store.StateStoreError):
            await store.public()

    asyncio.run(run())


def test_simultaneous_price_updates_have_one_winner(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        payload = {
            'revision': 0,
            'telegram': '',
            'skus': {'2:7': {'price_cents': 100, 'published': True}},
        }
        results = await asyncio.gather(
            store.update(payload), store.update(copy.deepcopy(payload)), return_exceptions=True
        )
        assert sum(isinstance(result, Conflict) for result in results) == 1
        assert (await store.admin())['revision'] == 1

    asyncio.run(run())


def test_cancelled_refresh_releases_lock_and_keeps_backoff(tmp_path):
    async def run():
        started = asyncio.Event()

        async def wait():
            started.set()
            await asyncio.Event().wait()

        store = make_store(tmp_path, fetcher=wait)
        task = asyncio.create_task(store.refresh())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await store.admin())['retry_after_seconds'] > 0
        lock = store._acquire()
        assert lock is not None
        lock.__exit__(None, None, None)

    asyncio.run(run())


def test_failed_manual_refresh_marks_even_recent_snapshot_stale(tmp_path):
    async def run():
        now = [1000]
        store = make_store(tmp_path, clock=lambda: now[0])
        await store.refresh()
        await store.update(
            {
                'revision': 0,
                'telegram': '',
                'skus': {'2:7': {'price_cents': 10000, 'published': True}},
            }
        )
        now[0] += 61

        async def failed():
            raise SourceError('unavailable')

        store.fetcher = failed
        await store.refresh(manual=True)
        public = await store.public()
        assert public['status'] == 'stale'
        assert public['products'][0]['variants'][0]['available'] is False

    asyncio.run(run())


def test_clock_rollback_does_not_make_inventory_indefinitely_fresh(tmp_path):
    async def run():
        now = [1000]
        store = make_store(tmp_path, clock=lambda: now[0])
        await store.refresh()
        now[0] = 900
        assert (await store.public())['status'] == 'stale'

    asyncio.run(run())
