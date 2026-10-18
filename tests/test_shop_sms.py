"""Offline supplier contracts and secret-safe public redemption gateway."""

import asyncio
import copy
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


CODE = 'TEST-ONLY-FAKE-CDK-1234'
CONFIG = {
    'ok': True, 'enabled': True, 'maintenance': {'sms': False},
    'providers': [{'value': 'zpay_alipay', 'enabled': True, 'currency': 'CNY'}],
    'products': [{'type': 'sms', 'enabled': True, 'maintenance': False,
                  'prices': {'zpay_alipay': {'currency': 'CNY', 'amount': 1}}}],
}


def test_catalog_uses_cny_not_credits_and_never_creates_payment():
    from web_api.shop_sms_source import fetch_catalog

    def handle(request):
        assert request.method == 'GET'
        assert str(request.url) == 'https://phoneali.zephyr.baby/api/payments/config'
        return httpx.Response(200, json=CONFIG)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            return await fetch_catalog(client=client)

    row, = asyncio.run(run())
    assert row['cost_cents'] == 100 and row['available'] is True
    assert row['key'] == '5000000000001:1'


@pytest.mark.parametrize('field', ['enabled', 'maintenance'])
def test_maintenance_is_unavailable_without_forgetting_the_product(field):
    from web_api.shop_sms_source import normalize_config

    config = copy.deepcopy(CONFIG)
    config['products'][0][field] = field == 'maintenance'
    assert normalize_config(config)[0]['available'] is False


@pytest.mark.parametrize('price', [{'currency': 'CREDIT', 'amount': 88}, {'currency': 'CNY', 'amount': True},
                                 {'currency': 'CNY', 'amount': '1.001'}, {'currency': 'CNY', 'amount': 0}])
def test_ambiguous_quotes_fail_closed(price):
    from web_api.shop_sms_source import normalize_config
    from web_api.shop_source import SourceError

    config = copy.deepcopy(CONFIG)
    config['products'][0]['prices']['zpay_alipay'] = price
    with pytest.raises(SourceError):
        normalize_config(config)


def test_sms_is_draft_by_default_and_public_projection_hides_source(tmp_path):
    from web_api.shop_sms_source import normalize_config
    from web_api.shop_store import ShopStore

    async def empty():
        return []

    async def sms():
        return normalize_config(CONFIG)

    async def run():
        store = ShopStore(tmp_path / 'source.json', fetcher=empty, sms_fetcher=sms)
        admin = await store.refresh()
        assert admin['items'][0]['category'] == '短信接码'
        assert admin['items'][0]['provider'] == 'sms'
        assert admin['skus'] == {}
        assert (await store.public())['products'] == []
        await store.update({'revision': 0, 'telegram': 'test_merchant', 'client': 3,
                            'skus': {'5000000000001:1': {'price_cents': 250, 'published': True}}})
        public = await store.public()
        product, = public['products']
        assert product['variants'][0]['price_cents'] == 250
        assert product['description'] and product['category'] == '短信接码'
        wire = json.dumps(public)
        assert 'zephyr' not in wire and 'cost_cents' not in wire and 'source_url' not in wire

    asyncio.run(run())


def test_sms_copy_follows_the_default_until_edited_and_carries_its_tag(tmp_path):
    from web_api.shop_sms_source import NOTICE, PREVIOUS_DESCRIPTIONS, normalize_config
    from web_api.shop_store import ShopStore

    async def empty():
        return []

    async def sms():
        return normalize_config(CONFIG)

    async def published(store, revision, description):
        await store.update({'revision': revision, 'telegram': 'test_merchant', 'client': 3,
                            'skus': {'5000000000001:1': {'price_cents': 250, 'published': True}},
                            'products': {'5000000000001': {'description': description, 'after_sales': '本店售后'}}})
        saved = store.settings_path.read_bytes()
        product, = (await store.public())['products']
        assert store.settings_path.read_bytes() == saved, 'Reading upgraded copy must not rewrite merchant settings'
        return product

    async def run():
        store = ShopStore(tmp_path / 'source.json', fetcher=empty, sms_fetcher=sms)
        await store.refresh()
        assert '二验、三验' in NOTICE['description'] and 'ChatGPT Codex' in NOTICE['description']
        # A saved earlier default, even with different spacing, shows today's default; after-sales copy stays.
        for revision, old in enumerate([*PREVIOUS_DESCRIPTIONS, PREVIOUS_DESCRIPTIONS[-1].replace('\n\n', '\n')]):
            product = await published(store, revision, old)
            assert (product['description'], product['after_sales']) == (NOTICE['description'], '本店售后')
            assert product['tags'] == [{'text': '支持二验三验'}, {'text': '支持Codex', 'hot': True}]
            assert (await store.admin())['products']['5000000000001']['description'] == NOTICE['description']
        # The merchant's own words always win.
        product = await published(store, len(PREVIOUS_DESCRIPTIONS) + 1, '本店自己的接码说明')
        assert product['description'] == '本店自己的接码说明'

    asyncio.run(run())


def test_sms_formatted_historical_copy_upgrades_without_matching_custom_edits():
    from web_api.shop_sms_source import NOTICE, current_copy

    # The store's previously approved formatting of the old default is also historical copy.
    historical = (
        '**一次性短信接码激活码。** 交付后，在**本店“短信接码”页面**输入激活码，验证后获取号码并查看验证码。\n\n'
        '**当前默认服务为 ChatGPT**；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。\n\n'
        '> **号码有时效，取号后请及时使用，不是长期持有的手机号。** 请妥善保管激活码，勿转发他人。'
    )
    saved = {'description': historical, 'after_sales': '**本店自己的售后条款**'}
    upgraded = current_copy(saved)
    assert upgraded == {**saved, 'description': NOTICE['description']}
    assert '**支持二验、三验' in upgraded['description']
    # 二验三验 is the highlighted note now; the reminder about a number's time limit is gone.
    assert '> **支持二验、三验' in upgraded['description']
    assert '号码有时效' not in upgraded['description']
    assert saved['description'] == historical
    assert current_copy(upgraded) == upgraded
    for description in ['', historical + '\n本店另有约定。', historical.replace('ChatGPT', '其他平台')]:
        custom = {**saved, 'description': description}
        assert current_copy(custom) == custom


def gateway(handler):
    from web_api.shop_sms_gateway import SmsGateway, register_sms_routes

    service = SmsGateway(transport=httpx.MockTransport(handler))
    app = FastAPI()
    register_sms_routes(app, gateway=service)
    return TestClient(app)


HEADERS = {'Sec-Fetch-Site': 'same-origin'}


def sales_shop(tmp_path, monkeypatch, handler, *, default_store=False):
    """Keep the route factory and store real, replacing only supplier HTTP."""
    from functools import partial

    from web_api import shop_routes, shop_sms_gateway
    from web_api.shop_sms_source import normalize_config
    from web_api.shop_store import ShopStore

    async def empty():
        return []

    async def sms():
        return normalize_config(CONFIG)

    store = ShopStore(tmp_path / 'source.json', fetcher=empty, sms_fetcher=sms)
    asyncio.run(store.refresh())
    asyncio.run(store.update({'revision': 0, 'client': 3, 'telegram': 'test_shop',
                             'skus': {'5000000000001:1': {'price_cents': 250, 'published': True}},
                             'display': {'5000000000001': {'badge': '', 'sold': 7}}}))
    service = shop_sms_gateway.SmsGateway(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(shop_sms_gateway, 'SmsGateway', lambda: service)
    if default_store:
        monkeypatch.setattr(shop_routes, 'ShopStore', lambda **kwargs: store)
        monkeypatch.setattr(shop_routes, 'prodseller_configured', lambda: False)
        monkeypatch.setattr(shop_routes, 'lorry_configured', lambda: False)

    class Sessions:
        def read_session(self, **kwargs):
            return {'role': 'admin'}

    async def dispatch(fn, request):
        return fn(headers=request.headers, path=request.url.path)

    app = FastAPI()
    shop_routes.register_shop_routes(app, Sessions(), dispatch,
                                     **({} if default_store else {'store': store}))
    return store, TestClient(app)


def test_received_order_increases_public_sold_once_across_repeated_polls(tmp_path, monkeypatch):
    store, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(
        200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810'}))
    with client:
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7
        for _ in range(3):
            result = client.post('/api/v1/shop/sms/check', headers=HEADERS,
                                 json={'activation_code': CODE, 'order_id': 123})
            assert result.status_code == 200 and result.json()['sms_code'] == '246810'
            public = client.get('/api/v1/shop/catalog').json()
            assert public['products'][0]['sold'] == 8
            assert public['stats']['sold'] == 8
        admin = client.get('/api/v1/shop/admin').json()
        assert admin['display']['5000000000001']['sold'] == 7
        assert admin['automatic_sold'] == {'5000000000001': 1}


@pytest.mark.parametrize('default_store', [False, True])
def test_received_acquire_check_cancel_count_distinct_orders_and_preserve_old_manual_draft(tmp_path, monkeypatch, default_store):
    def handle(request):
        data = {'ok': True, 'status': 'RECEIVED', 'sms_code': '246810',
                'full_sms': 'private full SMS', 'activation_code': CODE}
        if request.url.path == '/api/get-phone':
            data.update(phone='2025550123', country=187, order_id=123)
        if request.url.path == '/api/cancel':
            data['status'] = 'CANCELED'
        return httpx.Response(200, json=data)

    store, client = sales_shop(tmp_path, monkeypatch, handle, default_store=default_store)
    with client:
        old = client.get('/api/v1/shop/admin').json()
        draft = {field: copy.deepcopy(old[field]) for field in ('revision', 'telegram', 'skus', 'display')}
        draft['client'] = 3
        requests = [('acquire', {'activation_code': CODE}),
                    ('check', {'activation_code': CODE, 'order_id': 124}),
                    ('cancel', {'activation_code': CODE, 'order_id': 125}),
                    ('check', {'activation_code': CODE + '-OTHER', 'order_id': 123})]
        for action, payload in requests:
            response = client.post('/api/v1/shop/sms/' + action, headers=HEADERS, json=payload)
            assert response.status_code == 200 and response.json()['status'] == 'RECEIVED'
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 10
        saved = client.put('/api/v1/shop/admin', headers=HEADERS, json=draft)
        assert saved.status_code == 200 and saved.json()['automatic_sold'] == {'5000000000001': 3}
        assert saved.json()['display']['5000000000001']['sold'] == 7
        assert client.get('/api/v1/shop/catalog').json()['products'][0]['sold'] == 10
        stored = json.loads(store.settings_path.read_text())
        assert 'automatic_sold' not in stored
        assert stored['revision'] == 2
        # Legacy pages omit display/client and must keep the manual history too.
        legacy = client.put('/api/v1/shop/admin', headers=HEADERS, json={
            'revision': 2, 'telegram': 'test_shop', 'skus': draft['skus'],
        })
        assert legacy.status_code == 200
        assert legacy.json()['display']['5000000000001']['sold'] == 7
        assert legacy.json()['automatic_sold'] == {'5000000000001': 3}
        spoofed = client.put('/api/v1/shop/admin', headers=HEADERS, json={
            **draft, 'revision': 3, 'automatic_sold': {'5000000000001': 0},
        })
        assert spoofed.status_code == 422
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 10
        ledger = (tmp_path / 'sms-sales.json').read_text()
        for private in (CODE, '246810', '2025550123', 'private full SMS', 'phoneali', 'order_id'):
            assert private not in ledger
        assert (tmp_path / 'sms-sales.json').stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('upstream,status', [
    ({'ok': True, 'status': 'PENDING', 'sms_code': None}, 200),
    ({'ok': True, 'status': 'CANCELED'}, 200),
    ({'ok': True, 'status': 'TIMEOUT'}, 200),
    ({'ok': True, 'status': 'BANNED'}, 200),
    ({'ok': True, 'status': 'RECEIVED', 'sms_code': ''}, 502),
    ({'ok': False, 'status': 'RECEIVED', 'sms_code': '123456'}, 400),
    ({'ok': True, 'status': 'RECEIVED', 'sms_code': ['123456']}, 502),
    ({'ok': True, 'status': 'RECEIVED', 'sms_code': '123456', 'wait_seconds': -1}, 502),
])
def test_no_valid_received_sms_never_counts_a_sale(tmp_path, monkeypatch, upstream, status):
    _, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=upstream))
    with client:
        result = client.post('/api/v1/shop/sms/check', headers=HEADERS,
                             json={'activation_code': CODE, 'order_id': 123})
        assert result.status_code == status
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7
        assert not (tmp_path / 'sms-sales.json').exists()


def test_client_cannot_spoof_received_status_or_sales_fields(tmp_path, monkeypatch):
    _, client = sales_shop(tmp_path, monkeypatch, lambda _: pytest.fail('spoof reached provider'))
    with client:
        for extra in ({'status': 'RECEIVED', 'sms_code': '123456'}, {'automatic_sold': 1}):
            result = client.post('/api/v1/shop/sms/check', headers=HEADERS,
                                 json={'activation_code': CODE, 'order_id': 123, **extra})
            assert result.status_code == 422
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7


def test_verify_metadata_is_not_a_received_order(tmp_path, monkeypatch):
    _, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(200, json={
        'ok': True, 'remaining': 1, 'max_uses': 1, 'status': 'RECEIVED', 'sms_code': '123456', 'order_id': 123,
    }))
    with client:
        assert client.post('/api/v1/shop/sms/verify', headers=HEADERS,
                           json={'activation_code': CODE}).status_code == 200
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7
        assert not (tmp_path / 'sms-sales.json').exists()


@pytest.mark.parametrize('invalid', [{'order_id': 0}, {'order_id': True}, {'phone': 'bad phone'}])
def test_unvalidated_acquire_sms_cannot_count_sales(tmp_path, monkeypatch, invalid):
    upstream = {'ok': True, 'phone': '2025550123', 'country': 187, 'order_id': 123,
                'status': 'RECEIVED', 'sms_code': '246810', **invalid}
    _, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(200, json=upstream))
    with client:
        assert client.post('/api/v1/shop/sms/acquire', headers=HEADERS,
                           json={'activation_code': CODE}).status_code == 502
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7
        assert not (tmp_path / 'sms-sales.json').exists()


def test_failed_ledger_write_delivers_sms_and_logs_no_private_error(tmp_path, monkeypatch, caplog):
    import state_store

    store, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(
        200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810'}))

    def fail(*args):
        raise OSError(CODE + ' private full SMS 246810')

    monkeypatch.setattr(state_store, 'save_json_compact', fail)
    with client:
        result = client.post('/api/v1/shop/sms/check', headers=HEADERS,
                             json={'activation_code': CODE, 'order_id': 123})
        assert result.status_code == 200 and result.json()['sms_code'] == '246810'
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 7
    assert CODE not in caplog.text and '246810' not in caplog.text
    assert 'could not be recorded' in caplog.text


def test_replaced_sales_ledger_deduplicates_retry_after_directory_fsync_failure(tmp_path, monkeypatch):
    import state_store
    from web_api.shop_store import ShopSmsSales

    store, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(
        200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810'}))
    original = state_store._fsync_dir

    def fail_directory_fsync(path):
        raise OSError('simulated directory fsync failure')

    monkeypatch.setattr(state_store, '_fsync_dir', fail_directory_fsync)
    with client:
        payload = {'activation_code': CODE, 'order_id': 123}
        first = client.post('/api/v1/shop/sms/check', headers=HEADERS, json=payload)
        assert first.status_code == 200 and first.json()['sms_code'] == '246810'
        # Rename made both the count and deduplication mark visible together.
        assert ShopSmsSales(tmp_path / 'sms-sales.json').read() == 1
        monkeypatch.setattr(state_store, '_fsync_dir', original)
        retry = client.post('/api/v1/shop/sms/check', headers=HEADERS, json=payload)
        assert retry.status_code == 200
        assert client.get('/api/v1/shop/catalog').json()['stats']['sold'] == 8
        assert store.sms_sales.read() == 1


def test_automatic_sold_survives_hidden_source_and_unpublishing_without_changing_manual_limit(tmp_path, monkeypatch):
    store, client = sales_shop(tmp_path, monkeypatch, lambda _: httpx.Response(
        200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810'}))
    with client:
        client.post('/api/v1/shop/sms/check', headers=HEADERS,
                    json={'activation_code': CODE, 'order_id': 123})
        for revision, source_enabled, published in [(1, {'sms': False}, True), (2, {}, False)]:
            result = client.put('/api/v1/shop/admin', headers=HEADERS, json={
                'client': 3, 'revision': revision, 'telegram': 'test_shop', 'source_enabled': source_enabled,
                'skus': {'5000000000001:1': {'price_cents': 250, 'published': published}},
                'display': {'5000000000001': {'badge': '', 'sold': 10_000_000}},
            })
            assert result.status_code == 200
            assert result.json()['automatic_sold'] == {'5000000000001': 1}
            public = client.get('/api/v1/shop/catalog').json()
            assert public['products'] == [] and public['stats']['sold'] == 10_000_001
        assert store.settings_path.exists()


@pytest.mark.parametrize('phone,country,expected_phone,expected_country', [
    ('9382805510', 187, '+19382805510', 'US'),
    ('2025550123', '187', '+12025550123', 'US'),
    ('2025550123', 12, '+12025550123', 'US'),
    ('2025550123', 'UsA', '+12025550123', 'US'),
    ('12025550123', 'america', '+12025550123', 'US'),
    ('+12025550123', 'usa', '+12025550123', 'US'),
    ('+14165550123', 'canada', '+14165550123', 'CA'),
    ('+5511987654321', 73, '+5511987654321', 'BR'),
    ('+447700900123', 'england', '+447700900123', 'GB'),
    ('+447700900123', 187, '+447700900123', None),
    ('2025550123', None, '2025550123', None),
    ('+12025550123', None, '+12025550123', None),
    ('12025550123', None, '12025550123', None),
    ('2025550123', 'private-country-note', '2025550123', None),
    ('2025550123', {'name': 'usa', 'note': 'private-country-note'}, '2025550123', None),
    ('2025550123', True, '2025550123', None),
    ('2025550123', ['usa'], '2025550123', None),
])
def test_acquired_phone_uses_confirmed_country_without_leaking_supplier_metadata(phone, country, expected_phone, expected_country):
    upstream = {'ok': True, 'phone': phone, 'country': country, 'order_id': 123,
                'price': 0.13, 'operator': 'private-operator', 'note': 'private-country-note'}
    with gateway(lambda _: httpx.Response(200, json=upstream)) as client:
        result = client.post('/api/v1/shop/sms/acquire', headers=HEADERS, json={'activation_code': CODE})
        assert result.status_code == 200
        assert result.json() == {'ok': True, 'status': 'PENDING', 'sms_code': None, 'wait_seconds': 0,
                                 'phone': expected_phone, 'order_id': 123, 'country': expected_country}
        assert 'private-' not in result.text and 'price' not in result.text and CODE not in result.text


def test_specific_phone_is_optional_and_only_forwarded_on_acquire():
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={'ok': True, 'phone': '12025550123', 'country': 187, 'order_id': 124})

    with gateway(handle) as client:
        url = '/api/v1/shop/sms/acquire'
        assert client.post(url, headers=HEADERS, json={'activation_code': CODE}).status_code == 200
        result = client.post(url, headers=HEADERS, json={'activation_code': CODE, 'specific_phone': '2025550123'})
        assert result.status_code == 200
        assert seen == [{'activation_code': CODE},
                        {'activation_code': CODE, 'specific_phone': '2025550123', 'use_specific_phone': True}]
        assert result.json()['order_id'] == 124 and 'specific_phone' not in result.json()


@pytest.mark.parametrize('number', ['', None, True, 2025550123, '202555012', '20255501234',
                                    '+12025550123', '12025550123', '202 5550123', '２０２５５５０１２３', '202555012x'])
def test_invalid_specific_phone_never_reaches_provider(number):
    with gateway(lambda _: pytest.fail('invalid phone reached provider')) as client:
        result = client.post('/api/v1/shop/sms/acquire', headers=HEADERS,
                             json={'activation_code': CODE, 'specific_phone': number})
        assert result.status_code == 422


@pytest.mark.parametrize('action', ['verify', 'check', 'cancel'])
def test_specific_phone_cannot_be_used_on_other_actions(action):
    with gateway(lambda _: pytest.fail('unexpected provider call')) as client:
        payload = {'activation_code': CODE, 'specific_phone': '2025550123'}
        if action != 'verify':
            payload['order_id'] = 123
        assert client.post('/api/v1/shop/sms/' + action, headers=HEADERS, json=payload).status_code == 422


@pytest.mark.parametrize('flag', ['specific_phone_invalid', 'specific_phone_unavailable', 'specific_phone_unsupported'])
@pytest.mark.parametrize('status', [200, 400])
def test_specific_phone_rejection_has_safe_reason_and_never_falls_back(flag, status):
    seen = []

    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(status, json={'ok': False, flag: True, 'error': CODE, 'price': 1.2})

    with gateway(handle) as client:
        result = client.post('/api/v1/shop/sms/acquire', headers=HEADERS,
                             json={'activation_code': CODE, 'specific_phone': '2025550123'})
        assert result.status_code == 400 and result.json()['error'] == flag
        assert CODE not in result.text and 'price' not in result.text
        assert len(seen) == 1 and seen[0]['specific_phone'] == '2025550123'


def test_client_cannot_choose_provider_flags_or_another_destination():
    with gateway(lambda _: pytest.fail('invalid fields reached provider')) as client:
        for field, value in [('use_specific_phone', False), ('product', 'other'), ('url', 'http://localhost')]:
            result = client.post('/api/v1/shop/sms/acquire', headers=HEADERS,
                                 json={'activation_code': CODE, 'specific_phone': '2025550123', field: value})
            assert result.status_code == 422


@pytest.mark.parametrize('status', [400, 403, 409])
def test_non_json_rejection_preserves_status_without_echoing_body(status):
    with gateway(lambda _: httpx.Response(status, text=CODE)) as client:
        result = client.post('/api/v1/shop/sms/verify', headers=HEADERS, json={'activation_code': CODE})
        assert result.status_code == (409 if status == 409 else 400)
        assert result.json()['error'] == 'request_rejected'
        assert CODE not in result.text


def test_gateway_requires_same_origin_and_keeps_secrets_out_of_local_urls():
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.host == 'phoneali.zephyr.baby'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(200, json={'ok': True, 'remaining': 1, 'max_uses': 1,
                                       'code': CODE, 'note': 'private supplier note'})

    with gateway(handle) as client:
        url = '/api/v1/shop/sms/verify'
        assert client.get(url, params={'activation_code': CODE}).status_code == 405
        assert client.post(url, json={'activation_code': CODE}).status_code == 403
        assert client.post(url, headers={'Origin': 'https://evil.test'}, json={'activation_code': CODE}).status_code == 403
        assert client.post(url, headers=HEADERS, json={'activation_code': CODE, 'url': 'http://localhost'}).status_code == 422
        result = client.post(url, headers=HEADERS, json={'activation_code': CODE})
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'no-store'
        assert CODE not in result.text and 'private supplier note' not in result.text
        assert result.json()['remaining'] == 1
        assert len(seen) == 1


def test_order_identity_mandatory_and_received_race_wins_over_cancel():
    def handle(request):
        assert request.method == 'POST' and request.url.path == '/api/cancel'
        assert json.loads(request.content) == {'activation_code': CODE, 'order_id': 123}
        return httpx.Response(200, json={'ok': True, 'status': 'CANCELED', 'sms_code': '246810',
                                       'full_sms': 'secret long message', 'price': 0.13})

    with gateway(handle) as client:
        url = '/api/v1/shop/sms/cancel'
        for value in (None, 0, True, -1, '123', 2**53):
            assert client.post(url, headers=HEADERS, json={'activation_code': CODE, 'order_id': value}).status_code == 422
        result = client.post(url, headers=HEADERS, json={'activation_code': CODE, 'order_id': 123})
        assert result.json() == {'ok': True, 'status': 'RECEIVED', 'sms_code': '246810', 'wait_seconds': 0}
        assert 'price' not in result.text and 'full_sms' not in result.text


@pytest.mark.parametrize('status', ['PENDING', 'RECEIVED', 'CANCELED', 'TIMEOUT', 'BANNED', 'FINISHED', 'CANCEL_PENDING'])
def test_polling_preserves_server_lifecycle_without_acquiring(status):
    def handle(request):
        assert request.method == 'GET' and request.url.path == '/api/check-sms'
        assert request.url.params['activation_code'] == CODE
        assert request.url.params['order_id'] == '123'
        return httpx.Response(200, json={'ok': True, 'status': status, 'wait_seconds': 70,
                                       'sms_code': '123456' if status == 'RECEIVED' else None})

    with gateway(handle) as client:
        result = client.post('/api/v1/shop/sms/check', headers=HEADERS, json={'activation_code': CODE, 'order_id': 123})
        assert result.status_code == 200 and result.json()['status'] == status


@pytest.mark.parametrize('response', [httpx.Response(302, headers={'Location': 'http://127.0.0.1/'}),
                                    httpx.Response(200, json={'ok': True, 'status': 'SURPRISE'}),
                                    httpx.Response(500, text=CODE), httpx.Response(200, text='x' * 70000)])
def test_upstream_failure_does_not_echo_credentials_or_follow_redirects(response):
    calls = []

    def handle(request):
        calls.append(request)
        return response

    with gateway(handle) as client:
        result = client.post('/api/v1/shop/sms/check', headers=HEADERS, json={'activation_code': CODE, 'order_id': 123})
        assert result.status_code == 502
        assert CODE not in result.text and '127.0.0.1' not in result.text
        assert len(calls) == 1


def test_rate_limit_has_bounded_retry_and_acquire_does_not_echo_cost():
    def handle(request):
        if request.url.path == '/api/get-phone':
            return httpx.Response(200, json={'ok': True, 'phone': '12025550123', 'country': 187, 'order_id': 123,
                                           'product': 'dr', 'price': 0.13, 'wait_seconds': 10})
        return httpx.Response(429, headers={'Retry-After': '35'}, json={'ok': False, 'error': CODE})

    with gateway(handle) as client:
        acquired = client.post('/api/v1/shop/sms/acquire', headers=HEADERS, json={'activation_code': CODE})
        assert acquired.status_code == 200
        assert acquired.json()['phone'] == '+12025550123'
        assert 'price' not in acquired.text and CODE not in acquired.text
        limited = client.post('/api/v1/shop/sms/check', headers=HEADERS, json={'activation_code': CODE, 'order_id': 123})
        assert limited.status_code == 429 and limited.headers['retry-after'] == '35'
        assert CODE not in limited.text


def test_rate_limit_json_retry_after_and_terminal_over_stale_cancel_flag():
    from web_api.shop_sms_gateway import project

    for status in ('CANCELED', 'TIMEOUT', 'BANNED', 'FINISHED'):
        assert project('check', {'ok': True, 'status': status, 'cancel_pending': True})['status'] == status
    with gateway(lambda request: httpx.Response(429, json={'ok': False, 'retry_after': 120})) as client:
        result = client.post('/api/v1/shop/sms/verify', headers=HEADERS, json={'activation_code': CODE})
        assert result.status_code == 429 and result.headers['retry-after'] == '120'


def test_client_logging_never_records_upstream_query_credentials(caplog):
    import logging

    def handle(request):
        return httpx.Response(200, json={'ok': True, 'status': 'PENDING'})

    with gateway(handle) as client, caplog.at_level(logging.DEBUG):
        result = client.post('/api/v1/shop/sms/check', headers=HEADERS, json={'activation_code': CODE, 'order_id': 123})
        assert result.status_code == 200
    assert CODE not in caplog.text and 'phoneali.zephyr.baby/api/check-sms' not in caplog.text


def test_bounded_per_client_rate_limit_and_no_credentials_in_limiter():
    from web_api.shop_sms_gateway import SmsError, SmsGateway

    clock = [0.0]
    service = SmsGateway(clock=lambda: clock[0])
    identity = service.reserve('example-peer', 'acquire', CODE)
    with pytest.raises(SmsError) as conflict:
        service.reserve('another-peer', 'acquire', CODE)
    assert conflict.value.status == 429
    assert CODE not in repr(service.__dict__)
    service.inflight.discard(identity)
    for _ in range(14):
        identity = service.reserve('example-peer', 'verify', CODE)
        service.inflight.discard(identity)
    with pytest.raises(SmsError):
        service.reserve('example-peer', 'verify', CODE)
    clock[0] = 61
    assert service.reserve('example-peer', 'verify', CODE)


def test_timeout_releases_lock_without_exposing_sensitive_exception():
    from web_api.shop_sms_gateway import SmsError, SmsGateway

    def handle(request):
        raise httpx.ReadTimeout(CODE, request=request)

    service = SmsGateway(transport=httpx.MockTransport(handle))

    async def run():
        for _ in range(2):
            with pytest.raises(SmsError) as failure:
                await service.call('acquire', {'activation_code': CODE}, 'test')
            assert failure.value.status == 502 and CODE not in str(failure.value)
            assert not service.inflight

    asyncio.run(run())
