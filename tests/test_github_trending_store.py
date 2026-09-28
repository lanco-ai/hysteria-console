import asyncio
import json

import pytest
import state_store

from web_api.github_trending_source import SourceError
from web_api.github_trending_store import TrendingStore

ITEMS = [
    {
        'source_rank': 1,
        'full_name': 'owner/repo',
        'html_url': 'https://github.com/owner/repo',
        'description': None,
        'language': None,
        'stars_total': 0,
        'stars_period': 1,
        'forks_count': None,
    }
]


def test_cache_expiry_failure_and_cooldown(tmp_path):
    now = [100000.0]
    calls = []

    async def fetch(period):
        calls.append(period)
        if len(calls) > 1:
            raise SourceError('rate_limited')
        return ITEMS

    async def run():
        store = TrendingStore(tmp_path / 'cache.json', fetcher=fetch, clock=lambda: now[0])
        assert (await store.read('weekly'))['status'] == 'loading'
        await store.refresh('weekly', manual=True)
        first = await store.read('weekly')
        assert first['items'] == ITEMS and not first['is_stale']
        assert first['cooldown_seconds'] == 600
        await store.refresh('weekly', manual=True)
        assert len(calls) == 1
        now[0] += 21601
        assert (await store.read('weekly'))['is_stale']
        await store.refresh('weekly')
        failed = await store.read('weekly')
        assert failed['items'] == ITEMS
        assert failed['last_success_at'] == first['last_success_at']
        assert failed['fetched_at'] == first['fetched_at']
        assert failed['error'] == 'rate_limited'
        assert failed['retry_after_seconds'] > 0
        await store.refresh('weekly', manual=True)
        assert len(calls) == 2

    asyncio.run(run())


def test_cross_instance_single_flight_and_period_isolation(tmp_path):
    async def run():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def fetch(period):
            calls.append(period)
            started.set()
            await release.wait()
            return ITEMS

        a = TrendingStore(tmp_path / 'cache.json', fetcher=fetch)
        b = TrendingStore(a.path, fetcher=fetch)
        task = asyncio.create_task(a.refresh('weekly', manual=True))
        await started.wait()
        assert (await b.read('weekly'))['refreshing']
        await b.refresh('weekly', manual=True)
        release.set()
        await task
        assert calls == ['weekly']
        assert (await b.read('weekly'))['items'] == ITEMS
        assert (await b.read('daily'))['items'] == []
        assert a.path.stat().st_mode & 0o777 == 0o600

    asyncio.run(run())


def test_initial_failure_and_cancellation_cleanup(tmp_path):
    async def run():
        async def fail(_):
            raise SourceError('parse_error')

        store = TrendingStore(tmp_path / 'cache.json', fetcher=fail)
        await store.refresh('daily')
        result = await store.read('daily')
        assert result['status'] == 'unavailable' and result['items'] == []
        assert result['fetched_at'] is None

        async def block(_):
            await asyncio.sleep(100)

        store.fetcher = block
        task = asyncio.create_task(store.refresh('weekly'))
        for _ in range(100):
            if (await store.read('weekly'))['refreshing']:
                break
            await asyncio.sleep(0.001)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert not (await store.read('weekly'))['refreshing']

    asyncio.run(run())


def test_automatic_fresh_cache_hit_does_not_fetch(tmp_path):
    calls = []

    async def fetch(period):
        calls.append(period)
        return ITEMS

    async def run():
        store = TrendingStore(tmp_path / 'cache.json', fetcher=fetch)
        await store.refresh('weekly')
        await store.refresh('weekly')
        assert calls == ['weekly']

    asyncio.run(run())


def test_cache_rejects_unrepresentable_integer(tmp_path):
    store = TrendingStore(tmp_path / 'cache.json')
    item = {**ITEMS[0], 'stars_total': 9007199254740992}
    store.path.write_text(json.dumps({'weekly': {'items': [item], 'last_success': 100000}}))
    with pytest.raises(state_store.InvalidJsonState):
        asyncio.run(store.read('weekly'))
