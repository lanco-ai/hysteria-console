"""Durable, private order deduplication independent from merchant edits."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import state_store


def test_sms_sales_deduplicate_across_concurrent_instances_and_restart(tmp_path):
    from web_api.shop_store import ShopSmsSales

    path = tmp_path / 'sms-sales.json'
    first, second = ShopSmsSales(path), ShopSmsSales(path)
    assert first.read() == 0
    work = [(first if i % 2 else second, order) for i, order in enumerate([101, 102, 103] * 8)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda task: task[0].record(task[1]), work))
    assert first.read() == 3 and ShopSmsSales(path).read() == 3
    ShopSmsSales(path).record(101)
    assert second.read() == 3
    ShopSmsSales(path).record(104)
    assert first.read() == 4
    value = json.loads(path.read_text())
    assert value['sold'] == len(value['orders']) == len(set(value['orders'])) == 4
    assert all(len(mark) == 64 for mark in value['orders'])
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('value', [
    'broken JSON',
    {'schema_version': 1, 'sold': 1, 'orders': []},
    {'schema_version': True, 'sold': 0, 'orders': []},
    {'schema_version': 1, 'sold': True, 'orders': []},
    {'schema_version': 1, 'sold': 2, 'orders': ['a' * 64, 'a' * 64]},
    {'schema_version': 1, 'sold': 1, 'orders': ['private raw order']},
    {'schema_version': 1, 'sold': 0, 'orders': [], 'secret': 'forbidden'},
])
def test_invalid_sales_state_degrades_reads_but_never_overwrites_evidence(tmp_path, value):
    from web_api.shop_store import ShopSmsSales

    path = tmp_path / 'sms-sales.json'
    before = value if isinstance(value, str) else json.dumps(value)
    path.write_text(before)
    ledger = ShopSmsSales(path)
    assert ledger.read() == 0
    with pytest.raises(state_store.InvalidJsonState):
        ledger.record(123)
    assert path.read_text() == before


@pytest.mark.parametrize('order', [True, None, 0, -1, '123', 2**53])
def test_invalid_order_identity_cannot_create_ledger(tmp_path, order):
    from web_api.shop_store import ShopSmsSales

    path = tmp_path / 'sms-sales.json'
    with pytest.raises(ValueError):
        ShopSmsSales(path).record(order)
    assert not path.exists()


def test_cancelled_sms_record_finishes_locked_write_before_returning(tmp_path, monkeypatch):
    from web_api.shop_store import ShopStore

    store = ShopStore(tmp_path / 'source.json')
    entered, release = threading.Event(), threading.Event()
    original = state_store.save_json_compact

    def paused(*args):
        entered.set()
        assert release.wait(3)
        original(*args)

    monkeypatch.setattr(state_store, 'save_json_compact', paused)

    async def run():
        task = asyncio.create_task(store.record_sms_received(123))
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert store.sms_sales.read() == 1
