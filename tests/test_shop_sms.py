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


def gateway(handler):
    from web_api.shop_sms_gateway import SmsGateway, register_sms_routes

    service = SmsGateway(transport=httpx.MockTransport(handler))
    app = FastAPI()
    register_sms_routes(app, gateway=service)
    return TestClient(app)


HEADERS = {'Sec-Fetch-Site': 'same-origin'}


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
