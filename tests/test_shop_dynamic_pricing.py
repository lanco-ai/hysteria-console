"""Authoritative cost-plus pricing, quote freshness and backward-compatible settings."""
import asyncio
import copy
import json

import pytest

from test_shop_store import make_store, prodseller_rows
from test_shop_prodseller_duolingo import products as duolingo_products
from web_api.shop_inventory import ShopInventory
from web_api.shop_prodseller_source import normalize_products
from web_api.shop_source import MAX_CENTS
from web_api.shop_store import DYNAMIC_PRICING_MINIMUMS, cost_plus_price, validate_settings

SKU = '2000000000001:1'
DUO_SKU = '2000000000003:1'
RULE = {'mode': 'cost_plus', 'profit_cents': 500}
MANUAL = {'price_cents': 1000, 'promo_price_cents': 700, 'published': True}


@pytest.mark.parametrize('cost,expected', [(200, 700), (201, 788), (288, 788), (289, 799),
    (299, 799), (300, 800), (756, 1288), (788, 1288), (789, 1299), (799, 1299),
    (800, 1300), (MAX_CENTS - 500, MAX_CENTS), (MAX_CENTS - 499, None),
    (0, None), (-1, None), (True, None), (7.56, None), (None, None)])
def test_price_rounding(cost, expected):
    assert cost_plus_price(cost) == expected


def test_all_cent_boundaries_preserve_floor_and_choose_smallest_allowed_price():
    for cost in range(1, 10000):
        result = cost_plus_price(cost)
        assert result - cost >= 500
        assert result % 100 in {0, 88, 99}
        assert not any(n % 100 in {0, 88, 99} for n in range(cost + 500, result))


@pytest.mark.parametrize('rule', [False, [], {}, {'mode': 'fixed', 'profit_cents': 500},
    {'mode': 'cost_plus', 'profit_cents': 499}, {'mode': 'cost_plus', 'profit_cents': True},
    {'mode': 'cost_plus', 'profit_cents': 500.5}, {'mode': 'cost_plus', 'profit_cents': MAX_CENTS + 1},
    {**RULE, 'unknown': 1}])
def test_invalid_rules_rejected(rule):
    with pytest.raises(ValueError):
        validate_settings({'revision': 0, 'telegram': '', 'skus': {SKU: {**MANUAL, 'pricing': rule}}})


def test_rule_cannot_be_enabled_for_other_products():
    with pytest.raises(ValueError):
        validate_settings({'revision': 0, 'telegram': '', 'skus': {'1:4': {**MANUAL, 'pricing': RULE}}})


@pytest.mark.parametrize('profit', [500, 999])
def test_duolingo_requires_ten_yuan_minimum(profit):
    with pytest.raises(ValueError):
        validate_settings({'revision': 0, 'telegram': '', 'skus': {
            DUO_SKU: {**MANUAL, 'pricing': {**RULE, 'profit_cents': profit}}}})
    assert DYNAMIC_PRICING_MINIMUMS == {SKU: 500, DUO_SKU: 1000}


def test_duolingo_cost_plus_ten_changes_with_cost_without_overriding_supplier_stock(tmp_path):
    async def run():
        now, cost, available = [1000], [209], [False]

        async def feed():
            return [{**row, **({'cost_cents': cost[0], 'available': available[0]} if row['key'] == DUO_SKU else {})}
                    for row in prodseller_rows() + normalize_products(duolingo_products())]

        store = make_store(tmp_path, prodseller_fetcher=feed, clock=lambda: now[0])
        await store.refresh()
        gemini = {**MANUAL, 'pricing': RULE}
        await store.update({'revision': 0, 'telegram': '', 'skus': {
            SKU: gemini, DUO_SKU: {**MANUAL, 'pricing': {**RULE, 'profit_cents': 1000}}},
            'display': {'2000000000003': {'badge': '学生价', 'sold': 0}}})
        saved = store.settings_path.read_bytes()
        for next_cost, next_stock, expected in [(209, False, 1288), (1522, True, 2588), (1089, True, 2099)]:
            cost[0], available[0], now[0] = next_cost, next_stock, now[0] + 901
            await store.refresh()
            product = next(p for p in (await store.public())['products'] if p['id'] == '2000000000003')
            variant, = product['variants']
            assert (variant['price_cents'], variant['promo_price_cents'], variant['available']) == (expected, None, next_stock)
            assert variant['price_cents'] - next_cost >= 1000 and product['badge'] == ''
            assert store.settings_path.read_bytes() == saved
            assert (await store.admin())['skus'][SKU] == gemini
    asyncio.run(run())


def test_live_cost_projection_ignores_fixed_promo_preserves_manual_and_old_clients(tmp_path):
    async def run():
        now, cost = [1000], [756]

        async def feed():
            return [{**row, **({'cost_cents': cost[0], 'available': True} if row['key'] == SKU else {})}
                    for row in prodseller_rows()]

        store = make_store(tmp_path, prodseller_fetcher=feed, clock=lambda: now[0])
        await store.refresh()
        payload = {'revision': 0, 'client': 3, 'telegram': '', 'skus': {
            SKU: {**MANUAL, 'pricing': RULE}, '1:4': copy.deepcopy(MANUAL)},
            'display': {SKU.split(':')[0]: {'badge': '限时价', 'sold': 4}}}
        await store.update(payload)
        before = store.settings_path.read_bytes()
        for next_cost, expected in [(756, 1288), (1500, 2000), (200, 700)]:
            cost[0], now[0] = next_cost, now[0] + 901
            await store.refresh()
            public = await store.public()
            gemini = next(p for p in public['products'] if p['id'] == SKU.split(':')[0])
            variant = gemini['variants'][0]
            assert (variant['price_cents'], variant['promo_price_cents'], variant['available']) == (expected, None, True)
            assert gemini['badge'] == '' and gemini['sold'] == 4
            other = next(p for p in public['products'] if p['id'] == '1')['variants'][0]
            assert (other['price_cents'], other['promo_price_cents']) == (1000, 700)
            assert store.settings_path.read_bytes() == before
            for private in ['cost_cents', 'pricing', 'profit_cents', 'source_url']:
                assert private not in json.dumps(public)
        # A still-open older admin page cannot silently turn off this rule.
        payload['revision'] = 1
        del payload['skus'][SKU]['pricing']
        await store.update(payload)
        assert (await store.admin())['skus'][SKU]['pricing'] == RULE
        payload['revision'] = 2
        payload['skus'][SKU]['pricing'] = None
        await store.update(payload)
        public = next(p for p in (await store.public())['products'] if p['id'] == SKU.split(':')[0])
        assert public['badge'] == '限时价'
        assert public['variants'][0]['promo_price_cents'] == 700
        assert public['variants'][0]['price_cents'] == 1000
        assert 'pricing' not in (await store.admin())['skus'][SKU]
    asyncio.run(run())


def test_stale_archived_and_overflow_cost_cannot_sell_even_with_own_stock(tmp_path):
    async def run():
        now, rows = [1000], prodseller_rows()
        for row in rows:
            if row['key'] == SKU:
                row.update(cost_cents=756, available=False)

        async def feed():
            return copy.deepcopy(rows)

        store = make_store(tmp_path, prodseller_fetcher=feed, clock=lambda: now[0])
        store.inventory = ShopInventory(tmp_path / 'inventory.json', clock=store.clock)
        await store.refresh()
        metadata = {row['key']: row for row in rows}
        store.inventory.add({'revision': 0, 'sku': SKU, 'lines': ['fictional-gemini-stock']}, metadata)
        await store.update({'revision': 0, 'telegram': '', 'skus': {SKU: {**MANUAL, 'pricing': RULE}}})

        async def variant():
            return (await store.public())['products'][0]['variants'][0]

        assert (await variant())['available'] is True
        now[0] += 901
        assert (await variant())['available'] is False
        assert (await variant())['quantity'] is None
        await store.refresh()
        assert (await variant())['available'] is True
        for row in rows:
            if row['key'] == SKU:
                row['cost_cents'] = MAX_CENTS
        now[0] += 901
        await store.refresh()
        assert (await variant())['available'] is False
        rows.clear()
        now[0] += 901
        await store.refresh()
        assert (await variant())['available'] is False
        assert (await variant())['quantity'] is None
    asyncio.run(run())
