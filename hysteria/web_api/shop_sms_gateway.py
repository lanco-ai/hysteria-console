"""Fixed-origin CDK gateway. No payment, persistence, forwarding cookies or raw errors."""

import asyncio
import hashlib
import json
import logging
import re
import time
from types import SimpleNamespace

import http_utils
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from .shop_sms_source import ORIGIN

STATES = frozenset({'PENDING', 'RECEIVED', 'CANCEL_PENDING', 'CANCELED', 'TIMEOUT', 'BANNED', 'FINISHED'})
PATHS = {'verify': '/api/verify-code', 'acquire': '/api/get-phone', 'check': '/api/check-sms', 'cancel': '/api/cancel'}


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # httpx logs complete request URLs at INFO, including upstream's mandatory
        # query credential. Drop these records even when a host enables debug logs.
        return ORIGIN not in record.getMessage()


logging.getLogger('httpx').addFilter(_PrivateRequestFilter())


class SmsError(Exception):
    def __init__(self, status=502, error='service_unavailable', retry_after=10):
        self.status, self.error, self.retry_after = status, error, retry_after


def integer(value, maximum=2**53 - 1):
    return type(value) is int and 0 <= value <= maximum


def validate_payload(action, value):
    fields = {'activation_code', 'order_id'} if action in {'check', 'cancel'} else {'activation_code'}
    if (not isinstance(value, dict) or set(value) != fields
            or not isinstance(value.get('activation_code'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value['activation_code'])
            or ('order_id' in fields and (not integer(value['order_id']) or not value['order_id']))):
        raise SmsError(422, 'invalid_request')
    return value


def retry_seconds(value):
    try:
        return min(300, max(10, int(value)))
    except (ValueError, TypeError):
        return 10


def project(action, data):
    if not isinstance(data, dict) or data.get('ok') is not True:
        raise SmsError(400, 'request_rejected')
    if action == 'verify':
        remaining, maximum = data.get('remaining'), data.get('max_uses')
        if not integer(remaining) or not integer(maximum) or remaining > maximum:
            raise SmsError()
        return {'ok': True, 'remaining': remaining, 'max_uses': maximum,
                'has_pending_order': data.get('has_pending_order') is True}
    waited = data.get('wait_seconds', 0)
    if not integer(waited, 86400):
        raise SmsError()
    code = data.get('sms_code')
    if code is not None and (not isinstance(code, str) or len(code) > 128 or any(ord(c) < 32 for c in code)):
        raise SmsError()
    # An SMS arriving during cancellation must never be hidden behind a cancel
    # status or interpreted as confirmation that the activation was refunded.
    status = 'RECEIVED' if code else data.get('status', 'PENDING' if action == 'acquire' else None)
    if data.get('cancel_pending') is True and not code and status == 'PENDING':
        status = 'CANCEL_PENDING'
    if status not in STATES or (status == 'RECEIVED' and not code):
        raise SmsError()
    result = {'ok': True, 'status': status, 'sms_code': code or None, 'wait_seconds': waited}
    if action == 'acquire':
        phone, order = data.get('phone'), data.get('order_id')
        if not isinstance(phone, str) or not re.fullmatch(r'\+?[1-9]\d{6,14}', phone) or not integer(order) or not order:
            raise SmsError()
        result.update(phone='+' + phone.lstrip('+'), order_id=order)
    return result


class SmsGateway:
    def __init__(self, *, transport=None, clock=time.monotonic):
        self.transport, self.clock = transport, clock
        self.windows, self.inflight = {}, set()

    def reserve(self, address, action, code):
        now = self.clock()
        for key in [key for key, (started, _) in self.windows.items() if now - started >= 60]:
            del self.windows[key]
        bucket = (address, 'read' if action == 'check' else 'write')
        started, count = self.windows.get(bucket, (now, 0))
        limit = 60 if action == 'check' else 15
        if count >= limit or (bucket not in self.windows and len(self.windows) >= 2048):
            raise SmsError(429, 'rate_limited', max(10, int(60 - (now - started))))
        self.windows[bucket] = (started, count + 1)
        identity = hashlib.sha256(code.encode()).digest()
        if len(self.inflight) >= 12 or identity in self.inflight:
            raise SmsError(429, 'request_in_progress', 10)
        self.inflight.add(identity)
        return identity

    async def call(self, action, payload, address):
        identity = self.reserve(address, action, payload['activation_code'])
        try:
            async with asyncio.timeout(25):
                async with httpx.AsyncClient(transport=self.transport, trust_env=False,
                                              timeout=httpx.Timeout(20, connect=5)) as client:
                    arguments = {'params': payload} if action == 'check' else {'json': {**payload, **({'mode': 'phone'} if action == 'verify' else {})}}
                    async with client.stream('GET' if action == 'check' else 'POST', ORIGIN + PATHS[action],
                                             follow_redirects=False, headers={'User-Agent': 'Mozilla/5.0',
                                                                             'Accept': 'application/json'}, **arguments) as response:
                        if 400 <= response.status_code < 500 and response.status_code != 429:
                            raise SmsError(409 if response.status_code == 409 else 400, 'request_rejected')
                        if response.status_code not in (200, 429):
                            raise SmsError()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 65536:
                                raise SmsError()
                        if response.status_code == 429:
                            try:
                                value = json.loads(body)
                            except ValueError:
                                value = {}
                            delay = response.headers.get('Retry-After') or (value.get('retry_after') if isinstance(value, dict) else None)
                            raise SmsError(429, 'rate_limited', retry_seconds(delay))
                        return project(action, json.loads(body))
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            raise SmsError() from None
        finally:
            self.inflight.discard(identity)


def register_sms_routes(app, *, gateway=None):
    gateway = gateway or SmsGateway()

    def respond(value, status=200, retry=None):
        headers = {'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'}
        if retry is not None:
            headers['Retry-After'] = str(retry)
        return JSONResponse(value, status_code=status, headers=headers)

    def endpoint(action):
        async def handle(request: Request):
            try:
                if (not any(request.headers.get(name) for name in ('origin', 'referer', 'sec-fetch-site'))
                        or not http_utils.is_same_origin_post(SimpleNamespace(headers=request.headers))):
                    raise SmsError(403, 'cross_site_request')
                if request.url.query:
                    raise SmsError(422, 'invalid_request')
                body = bytearray()
                async with asyncio.timeout(5):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 1024:
                            raise SmsError(413, 'request_too_large')
                payload = validate_payload(action, json.loads(body))
                peer = request.client.host if request.client else ''
                address = http_utils.request_client_ip(SimpleNamespace(client_address=(peer, 0), headers=request.headers))
                return respond(await gateway.call(action, payload, address))
            except SmsError as exc:
                return respond({'ok': False, 'error': exc.error, 'retry_after': exc.retry_after}, exc.status,
                               exc.retry_after if exc.status == 429 else None)
            except (ValueError, UnicodeError, TimeoutError):
                return respond({'ok': False, 'error': 'invalid_request'}, 422)
        return handle

    for action in PATHS:
        app.add_api_route('/api/v1/shop/sms/' + action, endpoint(action), methods=['POST'])
