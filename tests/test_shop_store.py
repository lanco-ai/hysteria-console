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


def test_merchant_product_copy_migration_projection_and_legacy_writer(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        legacy = json.dumps({'revision': 0, 'telegram': '', 'skus': {}})
        store.settings_path.write_text(legacy)
        assert (await store.admin())['products'] == {}
        assert store.settings_path.read_text() == legacy  # Reading never rewrites old files.
        copy_text = {
            'description': '<script>alert(1)</script>\n商家说明',
            'after_sales': '联系本店确认售后',
        }
        payload = {
            'revision': 0,
            'telegram': 'my_shop',
            'skus': {'2:7': {'price_cents': 12345, 'published': True}},
            'products': {'2': copy_text, '1': {'description': '草稿不可见', 'after_sales': ''}},
        }
        await store.update(payload)
        assert (await store.public())['products'][0]['description'] == copy_text['description']
        assert '草稿不可见' not in json.dumps(await store.public(), ensure_ascii=False)
        # Old clients omit products entirely and cannot erase existing copy.
        await store.update({'revision': 1, 'telegram': 'my_shop', 'skus': payload['skus']})
        assert (await store.admin())['products']['2'] == copy_text
        store.clock = lambda: 100000000000
        await store.refresh()
        assert (await store.public())['products'][0]['after_sales'] == copy_text['after_sales']
        assert (await store.admin())['products']['2'] == copy_text

    asyncio.run(run())


@pytest.mark.parametrize(
    'metadata',
    [
        None,
        [],
        {'99': {'description': '', 'after_sales': ''}},
        {'2': {'description': 1, 'after_sales': ''}},
        {'2': {'description': 'x' * 4001, 'after_sales': ''}},
        {'2': {'description': '', 'after_sales': '', 'html': ''}},
        {'02': {'description': '', 'after_sales': ''}},
    ],
)
def test_product_copy_rejects_invalid_or_unknown_metadata(tmp_path, metadata):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        with pytest.raises(ValueError):
            await store.update({'revision': 0, 'telegram': '', 'skus': {}, 'products': metadata})
        assert (await store.admin())['revision'] == 0

    asyncio.run(run())


def test_product_copy_total_utf8_budget(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        text = {'description': '中' * 4000, 'after_sales': '文' * 4000}
        with pytest.raises(ValueError, match='too large'):
            await store.update(
                {
                    'revision': 0,
                    'telegram': '',
                    'skus': {},
                    'products': {key: text for key in ['1', '2', '5']},
                }
            )
        assert (await store.admin())['revision'] == 0
        await store.update({'revision': 0, 'telegram': '', 'skus': {}, 'products': {'2': text}})
        assert (await store.admin())['products']['2'] == text

    asyncio.run(run())


def anli_rows():
    from web_api.shop_anli_source import normalize_products as normalize_anli

    data = json.loads((Path(__file__).parent / 'fixtures/shop/anli-products.json').read_text())
    return normalize_anli(data, {row['id']: {**row, 'config': []} for row in data['data']})


def test_separate_feeds_keep_numeric_identity_private_cost_and_legacy_snapshot(tmp_path):
    async def run():
        legacy = make_store(tmp_path, clock=lambda: 1000)
        await legacy.refresh()
        original = legacy.path.read_bytes()
        before = await legacy.admin()
        original_key = next(row['key'] for row in before['items'] if row['product_id'] == '5')
        old_copy = {'description': '原 GPT 说明', 'after_sales': '原 GPT 售后'}
        await legacy.update(
            {
                'revision': 0,
                'telegram': 'my_shop',
                'skus': {
                    original_key: {'price_cents': 77777, 'published': True},
                },
                'products': {'5': old_copy},
            }
        )
        merchant = legacy.settings_path.read_bytes()

        async def anli():
            return anli_rows()

        store = make_store(tmp_path, clock=lambda: 1000, secondary_fetcher=anli)
        admin = await store.refresh()
        assert len(admin['items']) == 12
        assert legacy.path.read_bytes() == original
        assert legacy.settings_path.read_bytes() == merchant
        assert all(row['key'] not in admin['skus'] for row in anli_rows())
        settings = {
            'revision': 1,
            'telegram': 'my_shop',
            'skus': {
                original_key: {'price_cents': 77777, 'published': True},
                '1000000000005:1': {'price_cents': 15432, 'published': True},
            },
            'products': {
                '5': old_copy,
                '1000000000005': {'description': '本店 Claude', 'after_sales': '本店售后'},
            },
        }
        await store.update(settings)
        public = await store.public()
        assert [(row['id'], row['category']) for row in public['products']] == [
            ('5', 'GPT'),
            ('1000000000005', 'Claude'),
        ]
        assert [row['variants'][0]['price_cents'] for row in public['products']] == [77777, 15432]
        assert public['products'][0]['description'] == old_copy['description']
        assert public['products'][1]['description'] == '本店 Claude'
        for private in [
            'cost_cents',
            'source_url',
            'faka.anligpt',
            'supplier marketing',
            '13300',
            'sources',
        ]:
            assert private not in json.dumps(public)
        # The old reader ignores the new sibling and accepts shared numeric settings.
        old_public = await legacy.public()
        assert len(old_public['products']) == 1 and old_public['products'][0]['id'] == '5'

    asyncio.run(run())


@pytest.mark.parametrize('failed_provider', ['gpt', 'anli'])
def test_failed_feed_does_not_gate_fresh_other_feed_and_has_own_backoff(tmp_path, failed_provider):
    async def run():
        now = [1000]
        calls = {'gpt': 0, 'anli': 0}
        fail = set()

        async def gpt():
            calls['gpt'] += 1
            if 'gpt' in fail:
                raise SourceError('private failure')
            return normalize_products(products())

        async def anli():
            calls['anli'] += 1
            if 'anli' in fail:
                raise SourceError('private failure')
            return anli_rows()

        store = make_store(tmp_path, fetcher=gpt, secondary_fetcher=anli, clock=lambda: now[0])
        await store.refresh()
        await store.update(
            {
                'revision': 0,
                'telegram': '',
                'skus': {
                    '2:7': {'price_cents': 12345, 'published': True},
                    '1000000000005:1': {'price_cents': 23456, 'published': True},
                    '1000000000008:1': {'price_cents': 34567, 'published': True},
                },
            }
        )
        fail.add(failed_provider)
        now[0] += 61
        admin = await store.refresh(manual=True)
        assert admin['sources'][failed_provider]['is_stale'] is True
        fresh_provider = 'anli' if failed_provider == 'gpt' else 'gpt'
        assert admin['sources'][fresh_provider]['is_stale'] is False
        public = await store.public()
        assert public['status'] == 'ready'
        variants = {
            item['id']: item for product in public['products'] for item in product['variants']
        }
        assert variants['2:7']['available'] is (failed_provider != 'gpt')
        assert variants['1000000000005:1']['available'] is (failed_provider != 'anli')
        assert variants['1000000000008:1']['available'] is False
        now[0] += 61
        await store.refresh(manual=True)
        assert calls[failed_provider] == 2 and calls[fresh_provider] == 3
        assert json.loads(store.path.read_text()).keys() == {
            'items',
            'last_success',
            'error',
            'retry_after',
            'manual_after',
        }

    asyncio.run(run())


def test_reserved_namespace_and_secondary_snapshot_validation(tmp_path):
    async def run():
        async def anli():
            return anli_rows()

        store = make_store(tmp_path, secondary_fetcher=anli)
        await store.refresh()
        value = json.loads(store.secondary_path.read_text())
        value['items'][0]['source_url'] = 'https://evil.test/item/5'
        store.secondary_path.write_text(json.dumps(value))
        # A corrupt second cache is ignored independently, never projected.
        public = await store.public()
        assert public['status'] == 'ready'
        assert (await store.admin())['sources']['anli']['is_stale'] is True
        from web_api.shop_store import validate_items

        row = {
            **normalize_products(products())[0],
            'key': '1000000000005:7',
            'product_id': '1000000000005',
        }
        with pytest.raises(ValueError):
            validate_items([row])

    asyncio.run(run())


def test_claude_defaults_only_fill_source_present_absent_copy_without_persisting(tmp_path):
    async def run():
        async def anli():
            return anli_rows()

        store = make_store(tmp_path, secondary_fetcher=anli, clock=lambda: 1000)
        await store.refresh()
        original = {
            'revision': 3,
            'telegram': 'my_shop',
            'skus': {row['key']: {'price_cents': 77777, 'published': True}
                     for row in (await store.admin())['items']},
            'products': {'5': {'description': '原 GPT 说明', 'after_sales': '原 GPT 售后'},
                         '1000000000011': {'description': '原 Grok 说明', 'after_sales': ''}},
        }
        store.settings_path.write_text(json.dumps(original, ensure_ascii=False))
        before = store.settings_path.read_bytes()
        admin = await store.admin()
        ids = {'1000000000005', '1000000000007', '1000000000008'}
        assert set(admin['products']) == ids | set(original['products'])
        from web_api.shop_notices import CLAUDE_NOTICE
        assert all(admin['products'][key] == CLAUDE_NOTICE for key in ids)
        public = await store.public()
        for product in public['products']:
            if product['id'] in ids:
                assert {field: product[field] for field in CLAUDE_NOTICE} == CLAUDE_NOTICE
            else:
                assert product['description'] == original['products'].get(
                    product['id'], {}
                ).get('description', '')
            assert all(variant['price_cents'] == 77777 for variant in product['variants'])
        assert admin['revision'] == 3 and admin['telegram'] == 'my_shop'
        assert store.settings_path.read_bytes() == before
        # Modifying a caller's projection must not change another default or future reads.
        admin['products']['1000000000005']['description'] = 'caller mutation'
        assert admin['products']['1000000000007'] == CLAUDE_NOTICE
        assert (await store.admin())['products']['1000000000005'] == CLAUDE_NOTICE
        # Persisted metadata survives removal, but absent products acquire no default.
        source = json.loads(store.secondary_path.read_text())
        source['items'] = [row for row in source['items'] if row['product_id'] != '1000000000007']
        store.secondary_path.write_text(json.dumps(source))
        assert '1000000000007' not in (await store.admin())['products']
        assert store.settings_path.read_bytes() == before

    asyncio.run(run())


def test_claude_defaults_can_save_override_clear_and_survive_legacy_updates(tmp_path):
    async def run():
        async def anli():
            return anli_rows()

        store = make_store(tmp_path, secondary_fetcher=anli)
        await store.refresh()
        admin = await store.admin()
        expected = copy.deepcopy(admin['products'])
        assert len(expected) == 4
        payload = {key: admin[key] for key in ('revision', 'telegram', 'skus', 'products')}
        await store.update(payload)
        assert json.loads(store.settings_path.read_text())['products'] == expected
        expected['1000000000005'] = {'description': '本店自定义', 'after_sales': '自定义售后'}
        expected['1000000000007'] = {'description': '', 'after_sales': ''}
        await store.update({**payload, 'revision': 1, 'products': expected})
        await store.update({'revision': 2, 'telegram': '', 'skus': {}})
        current = await store.admin()
        assert current['revision'] == 3 and current['products'] == expected
        assert json.loads(store.settings_path.read_text())['products'] == expected

    asyncio.run(run())


def test_claude_copy_exact_multilingual_content_and_unchanged_field_limit():
    import hashlib
    from web_api.shop_notices import CLAUDE_NOTICE
    from web_api.shop_store import validate_settings

    expected = {
        'description': (1351, '6dfa19a21f4ae4ce39a644c3ad7e29a75c7b8c288c8e587e21077f7017098bd7'),
        'after_sales': (210, '453b2bad66e56451bf3816597e84fc75862b6f20ebcb9fc69cd6c3e93148e097'),
    }
    for field, (length, digest) in expected.items():
        assert len(CLAUDE_NOTICE[field]) == length
        assert hashlib.sha256(CLAUDE_NOTICE[field].encode()).hexdigest() == digest
    for field in expected:
        value = {'revision': 0, 'telegram': '', 'skus': {},
                 'products': {'1000000000005': {**CLAUDE_NOTICE, field: 'x' * 4000}}}
        validate_settings(value)
        value['products']['1000000000005'][field] += 'x'
        with pytest.raises(ValueError, match='invalid product copy'):
            validate_settings(value)


def test_copy_size_limit_includes_incremented_revision_without_writing(tmp_path):
    async def run():
        store = make_store(tmp_path)
        await store.refresh()
        metadata = {str(i): {'description': 'x' * 4000, 'after_sales': 'x' * 4000}
                    for i in range(1, 10)}
        payload = {'revision': 9, 'telegram': '', 'skus': {}, 'products': metadata}
        # Tune the legitimate persisted legacy metadata to exactly the request limit.
        while len(json.dumps(payload, separators=(',', ':')).encode()) > 65536:
            last = next(reversed(metadata))
            if metadata[last]['after_sales']:
                metadata[last]['after_sales'] = metadata[last]['after_sales'][:-1]
            elif metadata[last]['description']:
                metadata[last]['description'] = metadata[last]['description'][:-1]
            else:
                del metadata[last]
        assert len(json.dumps(payload, separators=(',', ':')).encode()) == 65536
        store.settings_path.write_text(json.dumps(payload, separators=(',', ':')))
        before = store.settings_path.read_bytes()
        with pytest.raises(ValueError, match='too large'):
            await store.update(payload)
        assert store.settings_path.read_bytes() == before
        assert (await store.admin())['revision'] == 9

    asyncio.run(run())


def test_supplier_contacts_never_reach_product_copy():
    from web_api.shop_notices import GROK_NOTICE, sanitize_product_copy, strip_supplier_lines

    for text in GROK_NOTICE.values():
        lowered = text.lower()
        assert 'sub2buy' not in lowered and 't.me/' not in lowered
    legacy = {
        '1000000000011': {
            'description': '本商品卡密可囤1个月\n\n充值地址：[https://sub2buy.com/#/grok](https://sub2buy.com/#/grok)\n\n'
            '频道通知：https://t.me/buy_gptplus\n\nChannel Updates: https://t.me/buygpt_plus\n\n'
            '售后客服：https://T.me/bkbk58\n\n全平台通用',
            'after_sales': '',
        }
    }
    cleaned = sanitize_product_copy(legacy)['1000000000011']
    assert cleaned == {'description': '本商品卡密可囤1个月\n\n全平台通用', 'after_sales': ''}
    assert strip_supplier_lines('联系本店') == '联系本店'
