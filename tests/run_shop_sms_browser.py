"""SMS UI with a fictional quote and a fully mocked provider; no real CDK calls."""

import itertools
import json
import os
import subprocess

import httpx
import react_preview_server as preview
import web_api.shop_sms_gateway as sms_gateway
from web_api.shop_sms_source import normalize_config
from web_api.shop_store import ShopStore


async def sms_feed():
    return normalize_config({
        'ok': True, 'enabled': True, 'maintenance': {'sms': False},
        'providers': [{'value': 'zpay_alipay', 'enabled': True, 'currency': 'CNY'}],
        'products': [{'type': 'sms', 'enabled': True, 'maintenance': False,
                      'prices': {'zpay_alipay': {'amount': 1, 'currency': 'CNY'}}}],
    })


class SmsStore(ShopStore):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, sms_fetcher=sms_feed)


class MockGateway(sms_gateway.SmsGateway):
    def __init__(self):
        polls = {}
        acquisitions = {}

        def reply(request):
            payload = dict(request.url.params) if request.method == 'GET' else json.loads(request.content)
            code = payload['activation_code']
            if request.url.path == '/api/verify-code':
                maximum = 3 if code.startswith('REUSE') else 1
                return httpx.Response(200, json={'ok': True, 'remaining': 0 if code.startswith('PENDING') else maximum - acquisitions.get(code, 0),
                                               'max_uses': maximum, 'has_pending_order': code.startswith('PENDING')})
            if request.url.path == '/api/get-phone':
                if payload.get('specific_phone') == '2025550199':
                    return httpx.Response(400, json={'ok': False, 'specific_phone_unavailable': True})
                if code.startswith('REUSE'):
                    acquisitions[code] = acquisitions.get(code, 0) + 1
                    return httpx.Response(200, json={'ok': True, 'phone': '1' + payload.get('specific_phone', '2025550123'),
                                                   'country': 187, 'order_id': 123 + acquisitions[code], 'price': 0.13})
                phone, country = ('+447700900123', 'uk') if code.startswith('FOREIGN') else \
                                 ('+14165550123', 'canada') if code.startswith('CANADA') else \
                                 ('551123456789', 'brazil') if code.startswith('BRAZIL-LAND') else \
                                 ('+5511912345678', 73) if code.startswith('BRAZIL') else \
                                 ('2025550123', {'note': 'private-country-note'}) if code.startswith('UNKNOWN') else \
                                 ('+12025550123', None) if code.startswith('LEGACY') else ('2025550123', 187)
                return httpx.Response(200, json={'ok': True, 'phone': phone, 'country': country, 'order_id': 123,
                                               'wait_seconds': 70 if code.startswith('CANCEL') else 50 if code.startswith('PENDING') else 0,
                                               'price': 0.13, 'operator': 'private-operator'})
            if request.url.path == '/api/cancel':
                return httpx.Response(200, json={'ok': True, 'status': 'CANCEL_PENDING', 'cancel_pending': True})
            assert request.url.path == '/api/check-sms'
            polls[code] = polls.get(code, 0) + 1
            if code.startswith('CANCEL'):
                return httpx.Response(200, json={'ok': True, 'status': 'CANCELED', 'wait_seconds': 70})
            if code.startswith('WAIT'):
                return httpx.Response(200, json={'ok': True, 'status': 'PENDING', 'wait_seconds': 70})
            if code.startswith('REUSE'):
                value = {124: '112233', 125: '223344', 126: '334455'}[int(payload['order_id'])]
                return httpx.Response(200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': value})
            return httpx.Response(200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810', 'wait_seconds': 70})

        # Browser time is accelerated; let independent scenarios advance the
        # gateway clock too. Real rate-limit behavior has separate backend tests.
        super().__init__(transport=httpx.MockTransport(reply), clock=itertools.count(step=5).__next__)


def main():
    preview.ShopStore = SmsStore
    sms_gateway.SmsGateway = MockGateway
    with preview.preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                   REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie)
        subprocess.run(['node', 'tests/react_shop_sms_browser.cjs'], env=env, check=True, timeout=150)


if __name__ == '__main__':
    main()
