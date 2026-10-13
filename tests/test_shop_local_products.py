"""Store-owned guidance stays private until the merchant explicitly publishes it."""

import asyncio
import json

import httpx
import pytest
import state_store

from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore


PRODUCT_ID = '6000000000001'
MICROSOFT_KEY = '6000000000001:1'
GOOGLE_KEY = '6000000000001:2'


async def empty_supplier():
    return []


def local_store(tmp_path, **kwargs):
    return ShopStore(tmp_path / 'source.json', local_products=True,
                     fetcher=kwargs.pop('fetcher', empty_supplier), **kwargs)


def test_claude_guidance_draft_has_private_costs_and_own_copy_without_writes(tmp_path):
    async def run():
        store = local_store(tmp_path, clock=lambda: 1000)
        admin = await store.admin()
        assert [(row['key'], row['label'], row['cost_cents']) for row in admin['items']] == [
            (MICROSOFT_KEY, '微软邮箱', 8500),
            (GOOGLE_KEY, '谷歌邮箱', 10500),
        ]
        for row in admin['items']:
            assert row['product_id'] == PRODUCT_ID
            assert row['public_title'] == 'Claude 开通指导'
            assert row['category'] == 'Claude' and row['provider'] == 'local'
            assert row['buy_url'] == '' and row['source_url'] == ''
        assert admin['sources']['local']['supplier'] == '本店'
        assert admin['sources']['local']['covers'] == '开通指导'
        assert admin['sources']['local']['site'] == ''
        assert admin['revision'] == 0 and admin['skus'] == {}
        assert (await store.public())['products'] == []
        notice = admin['products'][PRODUCT_ID]
        for scope in ('本人账号', '本人', '官方验证', '密码', '验证码', '身份证件',
                      '不包含账号或会员权益', '他人实名身份', '官方规则'):
            assert scope in notice['description']
        assert '指导进度' in notice['after_sales'] and '问题' in notice['after_sales']
        assert '不退' not in notice['after_sales'] and '无法退款' not in notice['after_sales']
        assert not store.path.exists() and not store.settings_path.exists()

    asyncio.run(run())


def test_claude_guidance_price_publish_hide_persists_without_public_cost_or_source(tmp_path):
    async def run():
        store = local_store(tmp_path)
        skus = {
            MICROSOFT_KEY: {'price_cents': 12345, 'published': False},
            GOOGLE_KEY: {'price_cents': 15678, 'published': False},
        }
        await store.update({'revision': 0, 'telegram': 'my_shop', 'client': 3, 'skus': skus})
        reopened = local_store(tmp_path)
        assert (await reopened.admin())['skus'] == skus
        assert (await reopened.public())['products'] == []
        await reopened.update({
            'revision': 1, 'telegram': 'my_shop', 'client': 3,
            'skus': {key: {**item, 'published': True} for key, item in skus.items()},
        })
        public = await local_store(tmp_path).public()
        product, = public['products']
        assert product['id'] == PRODUCT_ID and product['category'] == 'Claude'
        assert product['title'] == 'Claude 开通指导'
        assert [(row['id'], row['label'], row['price_cents'], row['available'])
                for row in product['variants']] == [
                    (MICROSOFT_KEY, '微软邮箱', 12345, True),
                    (GOOGLE_KEY, '谷歌邮箱', 15678, True),
                ]
        assert '本人账号' in product['description'] and '指导进度' in product['after_sales']
        wire = json.dumps(public, ensure_ascii=False)
        for private in ('cost_cents', 'source_url', 'buy_url', 'provider', 'sources',
                        '8500', '10500', '本店来源'):
            assert private not in wire
        await reopened.update({'revision': 2, 'telegram': 'my_shop', 'client': 3, 'skus': skus})
        hidden = local_store(tmp_path)
        assert (await hidden.public())['products'] == []
        assert (await hidden.admin())['skus'] == skus
        saved = json.loads(hidden.settings_path.read_text())
        assert saved['revision'] == 3 and saved['products'] == {}

    asyncio.run(run())


def test_local_copy_respects_saved_override_clear_and_projection_isolation(tmp_path):
    async def run():
        store = local_store(tmp_path)
        admin = await store.admin()
        admin['items'][0]['cost_cents'] = 1
        admin['products'][PRODUCT_ID]['description'] = 'caller mutation'
        reread = await store.admin()
        assert reread['items'][0]['cost_cents'] == 8500
        assert 'caller mutation' not in reread['products'][PRODUCT_ID]['description']
        for revision, notice in enumerate((
            {'description': '本店自定义操作说明', 'after_sales': '联系核对问题'},
            {'description': '', 'after_sales': ''},
        )):
            await store.update({
                'revision': revision, 'telegram': '', 'client': 3,
                'skus': {MICROSOFT_KEY: {'price_cents': 12345, 'published': True}},
                'products': {PRODUCT_ID: notice},
            })
            reopened = local_store(tmp_path)
            assert (await reopened.admin())['products'][PRODUCT_ID] == notice
            product, = (await reopened.public())['products']
            assert {field: product[field] for field in notice} == notice

    asyncio.run(run())


def test_local_only_refresh_never_fetches_or_caches_and_time_never_stales_it(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('local guidance must not create a supplier HTTP client')

    monkeypatch.setattr(httpx, 'AsyncClient', no_network)

    async def run():
        now = [1000]
        store = local_store(tmp_path, fetcher=None, clock=lambda: now[0])
        await store.refresh()
        assert [path.name for path in tmp_path.iterdir()] == ['source.json.refresh.lock']
        await store.update({
            'revision': 0, 'telegram': '', 'client': 3,
            'skus': {MICROSOFT_KEY: {'price_cents': 12345, 'published': True}},
        })
        for current in (1000 + 10 * 86400, 900):
            now[0] = current
            for _ in range(2):
                admin = await store.refresh(manual=True)
                assert set(admin['sources']) == {'local'}
                assert admin['sources']['local']['last_success'] == current
                assert admin['sources']['local']['is_stale'] is False
                public = await store.public()
                assert public['status'] == 'ready'
                assert public['products'][0]['variants'][0]['available'] is True
        assert not store.path.exists() and not list(tmp_path.glob('*local*'))

    asyncio.run(run())


def test_failed_supplier_refresh_does_not_stale_local_guidance(tmp_path):
    async def unavailable():
        raise SourceError('private supplier failure')

    async def run():
        store = local_store(tmp_path, fetcher=unavailable, clock=lambda: 1000)
        admin = await store.refresh()
        assert admin['sources']['gpt']['is_stale'] is True
        assert admin['sources']['local']['is_stale'] is False
        await store.update({
            'revision': 0, 'telegram': '', 'client': 3,
            'skus': {GOOGLE_KEY: {'price_cents': 15678, 'published': True}},
        })
        public = await store.public()
        assert public['status'] == 'ready' and len(public['products']) == 1
        assert public['products'][0]['variants'][0]['available'] is True
        assert 'private supplier failure' not in json.dumps(admin)
        cached = json.loads(store.path.read_text())
        assert cached['items'] == [] and cached['error'] == 'source_unavailable'

    asyncio.run(run())


def test_same_read_clock_rollback_does_not_expire_local_service(tmp_path):
    async def run():
        store = local_store(tmp_path, fetcher=None, clock=lambda: 1000)
        await store.update({
            'revision': 0, 'telegram': '', 'client': 3,
            'skus': {MICROSOFT_KEY: {'price_cents': 12345, 'published': True}},
        })
        readings = iter((1000, 999.99))
        store.clock = lambda: next(readings, 999.99)
        public = await store.public()
        assert public['status'] == 'ready'
        assert public['products'][0]['variants'][0]['available'] is True

    asyncio.run(run())


@pytest.mark.parametrize('local_enabled', [False, True])
def test_corrupt_single_supplier_keeps_only_enabled_local_guidance(tmp_path, local_enabled):
    async def run():
        store = ShopStore(tmp_path / 'source.json', fetcher=empty_supplier)
        store.local_products = local_enabled
        store.path.write_text('{broken cache')
        if not local_enabled:
            with pytest.raises(state_store.InvalidJsonState):
                await store.admin()
            return
        admin = await store.admin()
        assert [row['key'] for row in admin['items']] == [MICROSOFT_KEY, GOOGLE_KEY]
        assert admin['sources']['gpt']['error'] == 'source_unavailable'
        assert admin['sources']['local']['is_stale'] is False
        assert (await store.public())['products'] == []
        repaired = await store.refresh()
        assert repaired['sources']['gpt']['error'] is None
        assert [row['key'] for row in repaired['items']] == [MICROSOFT_KEY, GOOGLE_KEY]

    asyncio.run(run())
