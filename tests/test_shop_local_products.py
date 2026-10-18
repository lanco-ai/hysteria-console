"""Store-owned guidance stays private until the merchant explicitly publishes it."""

import asyncio
import json

import httpx
import pytest
import state_store

from web_api.shop_local_products import STUDENT_ENDS_AT
from web_api.shop_source import SourceError
from web_api.shop_store import Conflict, ShopStore, validate_settings


PRODUCT_ID = '6000000000001'
MICROSOFT_KEY = '6000000000001:1'
GOOGLE_KEY = '6000000000001:2'
VPN_ID, VPN_KEY = '6000000000002', '6000000000002:1'
STUDENT_ID, STUDENT_KEY = '6000000000003', '6000000000003:1'
GROK_ID = '6000000000004'
GROK_MONTH_KEY, GROK_QUARTER_KEY = f'{GROK_ID}:1', f'{GROK_ID}:2'


async def empty_supplier():
    return []


def local_keys(now=None):
    """Store-owned rows on sale now; the student service leaves after its last day."""
    import time
    keys = [MICROSOFT_KEY, GOOGLE_KEY, VPN_KEY]
    return keys + ([STUDENT_KEY] if (time.time() if now is None else now) <= STUDENT_ENDS_AT else []) + [GROK_MONTH_KEY, GROK_QUARTER_KEY]


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
            (VPN_KEY, '远程搭建指导', 0),
            (STUDENT_KEY, '标准规格', 0),
            (GROK_MONTH_KEY, '1 个月（月卡）', 13500),
            (GROK_QUARTER_KEY, '3 个月（季卡）', 25800),
        ]
        for row in admin['items'][:2]:
            assert row['product_id'] == PRODUCT_ID
            assert row['public_title'] == 'Claude 开通指导'
            assert row['category'] == 'Claude' and row['provider'] == 'local'
            assert row['buy_url'] == '' and row['source_url'] == ''
        assert admin['sources']['local']['supplier'] == '本店'
        assert admin['sources']['local']['covers'] == '本店商品'
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
        assert [row['key'] for row in admin['items']] == local_keys()
        assert admin['sources']['gpt']['error'] == 'source_unavailable'
        assert admin['sources']['local']['is_stale'] is False
        assert (await store.public())['products'] == []
        repaired = await store.refresh()
        assert repaired['sources']['gpt']['error'] is None
        assert [row['key'] for row in repaired['items']] == local_keys()

    asyncio.run(run())


def test_local_names_persist_in_admin_and_public_without_changing_other_fields(tmp_path):
    async def run():
        store = local_store(tmp_path)
        initial = await store.admin()
        names = {'products': {PRODUCT_ID: 'Claude 本人账号操作指导'},
                 'variants': {MICROSOFT_KEY: '微软邮箱指导', GOOGLE_KEY: '谷歌邮箱指导'}}
        skus = {MICROSOFT_KEY: {'price_cents': 12345, 'promo_price_cents': 11000, 'published': True},
                GOOGLE_KEY: {'price_cents': 15678, 'published': True}}
        await store.update({'revision': 0, 'telegram': '', 'client': 3, 'skus': skus,
                            'display': {PRODUCT_ID: {'badge': '限时价', 'sold': 12}},
                            'local_names': {'products': {PRODUCT_ID: '  Claude 本人账号操作指导  '},
                                            'variants': names['variants']}})
        reopened = local_store(tmp_path)
        admin = await reopened.refresh(manual=True)
        assert admin['local_names'] == names
        assert admin['skus'] == skus and admin['products'] == initial['products']
        assert admin['sources']['local']['supplier'] == '本店'
        for original, row in zip(initial['items'][:2], admin['items'][:2]):
            assert row['title'] == row['public_title'] == 'Claude 本人账号操作指导'
            assert row['label'] == names['variants'][row['key']]
            assert {key: value for key, value in row.items() if key not in ('title', 'public_title', 'label')} == {
                key: value for key, value in original.items() if key not in ('title', 'public_title', 'label')}
        product, = (await reopened.public())['products']
        assert product['id'] == PRODUCT_ID and product['category'] == 'Claude'
        assert product['title'] == 'Claude 本人账号操作指导'
        assert [(row['id'], row['label'], row['price_cents']) for row in product['variants']] == [
            (MICROSOFT_KEY, '微软邮箱指导', 12345), (GOOGLE_KEY, '谷歌邮箱指导', 15678)]
        assert product['variants'][0]['promo_price_cents'] == 11000
        assert product['badge'] == '限时价' and product['sold'] == 12
        assert '本人账号' in product['description']
        assert not {'cost_cents', 'provider', 'local_names'} & set(product)
        assert 'cost_cents' not in json.dumps(product)
        admin['local_names']['products'][PRODUCT_ID] = 'caller mutation'
        assert (await reopened.admin())['items'][0]['title'] == 'Claude 本人账号操作指导'

    asyncio.run(run())


@pytest.mark.parametrize('client', [None, 2, 3])
def test_pages_omitting_local_names_preserve_them_and_explicit_empty_resets(tmp_path, client):
    async def run():
        store = local_store(tmp_path)
        names = {'products': {PRODUCT_ID: '自定义指导名称'}, 'variants': {MICROSOFT_KEY: '自定义规格'}}
        await store.update({'revision': 0, 'telegram': '', 'client': 3, 'skus': {}, 'local_names': names})
        payload = {'revision': 1, 'telegram': 'my_shop', 'skus': {}}
        if client is not None:
            payload['client'] = client
        await local_store(tmp_path).update(payload)
        assert json.loads(store.settings_path.read_text())['local_names'] == names
        assert (await store.admin())['items'][0]['label'] == '自定义规格'
        await store.update({**payload, 'revision': 2, 'local_names': {}})
        admin = await local_store(tmp_path).admin()
        assert admin['items'][0]['title'] == 'Claude 开通指导'
        assert admin['items'][0]['label'] == '微软邮箱'
        assert 'local_names' not in json.loads(store.settings_path.read_text())

    asyncio.run(run())


def test_default_local_names_do_not_make_ordinary_saves_incompatible_with_old_schema(tmp_path):
    async def run():
        store = local_store(tmp_path)
        await store.update({'revision': 0, 'telegram': '', 'client': 3, 'skus': {},
                            'local_names': {'products': {PRODUCT_ID: 'Claude 开通指导'},
                                            'variants': {MICROSOFT_KEY: '微软邮箱', GOOGLE_KEY: '谷歌邮箱'}}})
        assert 'local_names' not in json.loads(store.settings_path.read_text())

    asyncio.run(run())


@pytest.mark.parametrize('names', [
    None, [], {'unexpected': {}}, {'products': []}, {'variants': []},
    {'products': {'1': '供应商改名'}}, {'variants': {'1:1': '供应商改名'}},
    {'products': {'6000000000009': '未知本店商品'}}, {'variants': {'6000000000001:3': '未知规格'}},
    {'products': {PRODUCT_ID: ''}}, {'variants': {MICROSOFT_KEY: '  '}},
    {'products': {PRODUCT_ID: '商品\n名称'}}, {'variants': {MICROSOFT_KEY: '规格\x00名称'}},
    {'products': {PRODUCT_ID: '商品\u2028名称'}}, {'variants': {MICROSOFT_KEY: '规格\u2029名称'}},
    {'products': {PRODUCT_ID: '\u202e隐藏方向'}}, {'variants': {MICROSOFT_KEY: '<b>规格</b>'}},
    {'products': {PRODUCT_ID: '名' * 121}}, {'variants': {MICROSOFT_KEY: '名' * 81}},
    {'products': {PRODUCT_ID: 123}}, {'variants': {MICROSOFT_KEY: False}},
])
def test_invalid_local_names_never_partially_change_merchant_settings(tmp_path, names):
    async def run():
        store = local_store(tmp_path)
        await store.update({'revision': 0, 'telegram': '', 'client': 3, 'skus': {}})
        before = store.settings_path.read_bytes()
        with pytest.raises(ValueError):
            await store.update({'revision': 1, 'telegram': 'my_shop', 'client': 3, 'skus': {},
                                'local_names': names})
        assert store.settings_path.read_bytes() == before

    asyncio.run(run())


def test_conflicting_name_save_changes_neither_names_nor_prices(tmp_path):
    async def run():
        store = local_store(tmp_path)
        payload = {'revision': 0, 'telegram': '', 'client': 3,
                   'skus': {MICROSOFT_KEY: {'price_cents': 12000, 'published': True}},
                   'local_names': {'products': {PRODUCT_ID: '已保存指导'}}}
        await store.update(payload)
        before = store.settings_path.read_bytes()
        with pytest.raises(Conflict):
            await local_store(tmp_path).update({**payload,
                'skus': {MICROSOFT_KEY: {'price_cents': 13000, 'published': False}},
                'local_names': {'products': {PRODUCT_ID: '过期页面指导'}}})
        assert store.settings_path.read_bytes() == before

    asyncio.run(run())


def test_valid_local_name_boundaries_and_partial_maps():
    for names in ({}, {'products': {PRODUCT_ID: '名' * 120}},
                  {'variants': {MICROSOFT_KEY: '名' * 80}},
                  {'products': {}, 'variants': {}}):
        assert validate_settings({'revision': 0, 'telegram': '', 'skus': {}, 'local_names': names})


def test_new_services_have_own_categories_copy_and_the_student_service_ends(tmp_path):
    async def run():
        now = [STUDENT_ENDS_AT - 60]
        store = local_store(tmp_path, clock=lambda: now[0])
        skus = {key: {'price_cents': 9900, 'published': True} for key in (VPN_KEY, STUDENT_KEY)}
        await store.update({'revision': 0, 'telegram': 'my_shop', 'client': 3, 'skus': skus,
                            'local_names': {'products': {STUDENT_ID: 'GPT 学生认证'}}})
        admin = await store.admin()
        categories = {row['product_id']: row['category'] for row in admin['items']}
        assert categories == {PRODUCT_ID: 'Claude', VPN_ID: 'VPN', STUDENT_ID: 'GPT', GROK_ID: 'Grok'}
        assert '> **服务器与域名：本店指导选购。**' in admin['products'][VPN_ID]['description']
        assert '不提供 VPN' not in admin['products'][VPN_ID]['description']
        assert '流量额度' in admin['products'][VPN_ID]['description']
        assert '10 月 31 日' in admin['products'][STUDENT_ID]['description']
        assert '他人学籍' in admin['products'][STUDENT_ID]['description']
        products = {item['id']: item for item in (await store.public())['products']}
        # Renamed, it keeps its category; only the limited-time service says when it ends.
        assert products[STUDENT_ID]['title'] == 'GPT 学生认证' and products[STUDENT_ID]['category'] == 'GPT'
        assert products[STUDENT_ID]['ends_at'] == STUDENT_ENDS_AT
        assert products[VPN_ID]['category'] == 'VPN' and 'ends_at' not in products[VPN_ID]
        assert [tag['text'] for tag in products[VPN_ID]['tags']] == ['固定IP', '支持各大 AI 软件', 'Claude 不封号']
        now[0] = STUDENT_ENDS_AT + 1
        assert [item['id'] for item in (await store.public())['products']] == [VPN_ID]
        assert STUDENT_KEY not in [row['key'] for row in (await store.admin())['items']]
        # Its saved name and price stay valid for later saves.
        later = await store.admin()
        await store.update({'revision': later['revision'], 'telegram': 'my_shop', 'client': 3, 'skus': skus,
                            'local_names': later['local_names']})
        assert (await store.admin())['local_names'] == {'products': {STUDENT_ID: 'GPT 学生认证'}}

    asyncio.run(run())


def test_local_grok_two_plans_publish_at_merchant_prices_without_supplier_or_credentials(tmp_path):
    async def run():
        store = local_store(tmp_path, fetcher=None)
        assert (await store.public())['products'] == []
        admin = await store.admin()
        rows = [row for row in admin['items'] if row['product_id'] == GROK_ID]
        assert [(row['key'], row['label'], row['category'], row['provider']) for row in rows] == [
            (GROK_MONTH_KEY, '1 个月（月卡）', 'Grok', 'local'),
            (GROK_QUARTER_KEY, '3 个月（季卡）', 'Grok', 'local'),
        ]
        assert all(row['source_url'] == '' and row['buy_url'] == '' for row in rows)
        assert [row['cost_cents'] for row in rows] == [13500, 25800]
        await store.update({
            'revision': 0, 'client': 3, 'telegram': 'my_shop',
            'skus': {
                GROK_MONTH_KEY: {'price_cents': 14500, 'published': True},
                GROK_QUARTER_KEY: {'price_cents': 26800, 'published': True},
            },
        })
        reopened = local_store(tmp_path, fetcher=None)
        catalog = await reopened.public()
        product, = catalog['products']
        assert catalog['status'] == 'ready'
        assert product['id'] == GROK_ID and product['category'] == 'Grok'
        assert [(v['id'], v['price_cents'], v['available']) for v in product['variants']] == [
            (GROK_MONTH_KEY, 14500, True), (GROK_QUARTER_KEY, 26800, True),
        ]
        for clause in ('不可囤货', '尽快使用', '不承诺', '不支持覆盖', '人工发货',
                       'https://6661231.xyz/', 'https://grok.com/api/auth/session',
                       '只提供 User ID', '不要发送整段页面内容'):
            assert clause in product['description']
        assert '90 天' in product['after_sales'] and '账号封禁不在质保范围' in product['after_sales']
        wire = json.dumps(product)
        assert all(private not in wire for private in ('cost_cents', 'source_url', 'provider', '13500', '25800'))
        assert not store.path.exists() and not list(tmp_path.glob('*local*'))

    asyncio.run(run())


def test_saved_vpn_copy_swaps_only_the_retired_line_for_the_guide_line():
    from web_api.shop_local_products import VPN_GUIDE_LINE, current_vpn_copy

    old = '**本店不提供 VPN、节点或账号，服务器与域名由买家自备、费用自付。** 服务方式为远程分步指导。'
    for line in (old, '> ' + old, '>' + old + '  '):
        saved = {'description': f'## 本店自己的标题\n\n{line}\n\n本店自己的说明。', 'after_sales': '本店售后'}
        assert current_vpn_copy(saved) == {**saved, 'description': f'## 本店自己的标题\n\n{VPN_GUIDE_LINE}\n\n本店自己的说明。'}
    # Anything else the merchant wrote, including a reworded line, is left alone.
    for description in ('', '本店自己的说明。', old.replace('费用自付', '费用另计')):
        saved = {'description': description, 'after_sales': ''}
        assert current_vpn_copy(saved) is saved
