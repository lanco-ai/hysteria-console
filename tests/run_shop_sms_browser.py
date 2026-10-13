"""SMS UI with a fictional quote and a fully mocked provider; no real CDK calls."""

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

        def reply(request):
            payload = dict(request.url.params) if request.method == 'GET' else json.loads(request.content)
            code = payload['activation_code']
            if request.url.path == '/api/verify-code':
                return httpx.Response(200, json={'ok': True, 'remaining': 1, 'max_uses': 1})
            if request.url.path == '/api/get-phone':
                return httpx.Response(200, json={'ok': True, 'phone': '12025550123', 'order_id': 123, 'price': 0.13})
            if request.url.path == '/api/cancel':
                return httpx.Response(200, json={'ok': True, 'status': 'CANCEL_PENDING', 'cancel_pending': True})
            assert request.url.path == '/api/check-sms'
            polls[code] = polls.get(code, 0) + 1
            if code.startswith('CANCEL'):
                return httpx.Response(200, json={'ok': True, 'status': 'CANCELED', 'wait_seconds': 70})
            if code.startswith('WAIT'):
                return httpx.Response(200, json={'ok': True, 'status': 'PENDING', 'wait_seconds': 70})
            return httpx.Response(200, json={'ok': True, 'status': 'RECEIVED', 'sms_code': '246810', 'wait_seconds': 70})

        super().__init__(transport=httpx.MockTransport(reply))


def main():
    preview.ShopStore = SmsStore
    sms_gateway.SmsGateway = MockGateway
    with preview.preview_server() as server:
        env = dict(os.environ, PREVIEW_BASE_URL=f'http://127.0.0.1:{server.server_port}',
                   REACT_PREVIEW_ADMIN_COOKIE=server.preview_admin_cookie)
        subprocess.run(['node', 'tests/react_shop_sms_browser.cjs'], env=env, check=True, timeout=150)


if __name__ == '__main__':
    main()
