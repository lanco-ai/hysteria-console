"""Fixed-origin CDK gateway. No payment, credential persistence, forwarded cookies or raw errors."""

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime
from types import SimpleNamespace

import http_utils
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from .shop_sms_source import ORIGIN

STATES = frozenset({'PENDING', 'RECEIVED', 'CANCEL_PENDING', 'CANCELED', 'TIMEOUT', 'BANNED', 'FINISHED'})
PATHS = {'verify': '/api/verify-code', 'acquire': '/api/get-phone', 'check': '/api/check-sms', 'cancel': '/api/cancel'}
COUNTRIES = {'187': 'US', '12': 'US', 'usa': 'US', 'america': 'US', 'canada': 'CA',
             '73': 'BR', 'brazil': 'BR', 'uk': 'GB', 'england': 'GB'}
DIAL_CODES = {'US': '1', 'CA': '1', 'BR': '55', 'GB': '44'}


class _PrivateRequestFilter(logging.Filter):
    def filter(self, record):
        # httpx logs complete request URLs at INFO, including upstream's mandatory
        # query credential. Drop these records even when a host enables debug logs.
        return ORIGIN not in record.getMessage()


logging.getLogger('httpx').addFilter(_PrivateRequestFilter())


class SmsError(Exception):
    def __init__(self, status=502, error='service_unavailable', retry_after=10, *, cancel_timing=None):
        self.status, self.error, self.retry_after = status, error, retry_after
        self.cancel_timing = cancel_timing or {}


def integer(value, maximum=2**53 - 1):
    return type(value) is int and 0 <= value <= maximum


def validate_payload(action, value):
    fields = {'activation_code', 'order_id'} if action in {'check', 'cancel'} else {'activation_code'}
    optional = {'specific_phone'} if action == 'acquire' else set()
    if (not isinstance(value, dict) or not fields <= set(value) or not set(value) <= fields | optional
            or not isinstance(value.get('activation_code'), str)
            or not re.fullmatch(r'[A-Za-z0-9_-]{8,128}', value['activation_code'])
            or ('order_id' in fields and (not integer(value['order_id']) or not value['order_id']))):
        raise SmsError(422, 'invalid_request')
    if 'specific_phone' in value:
        if not isinstance(value['specific_phone'], str) or not re.fullmatch(r'[0-9]{10}', value['specific_phone']):
            raise SmsError(422, 'invalid_request')
        return {**value, 'use_specific_phone': True}
    return value


def retry_seconds(value):
    try:
        return min(300, max(10, int(value)))
    except (ValueError, TypeError):
        return 10


def rejection_reason(action, data):
    if action == 'cancel' and isinstance(data, dict) and data.get('cancel_too_early') is True:
        return 'cancel_too_early'
    if action == 'acquire' and isinstance(data, dict):
        for flag in ('specific_phone_invalid', 'specific_phone_unavailable', 'specific_phone_unsupported'):
            if data.get(flag) is True:
                return flag
    return 'request_rejected'


def cancel_timing(data, *, rejected=False):
    # Optional supplier metadata must never make an already allocated order
    # unusable. Only timezone-qualified timestamps have an unambiguous deadline.
    deadline = data.get('cancel_available_at')
    if (isinstance(deadline, str) and len(deadline) <= 64
            and re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', deadline)):
        try:
            seconds = max(0, math.ceil(datetime.fromisoformat(deadline.replace('Z', '+00:00')).timestamp() - time.time()))
            if seconds <= 86400 and (not rejected or seconds > 0):
                return {'cancel_retry_after': seconds, 'cancel_timing_source': 'upstream'}
        except (ValueError, OverflowError, OSError):
            pass
    waited = data.get('wait_seconds', 0)
    seconds = max(0, 60 - waited) if integer(waited, 86400) else 60
    return {'cancel_retry_after': seconds if seconds or not rejected else 10,
            'cancel_timing_source': 'fallback'}


def rejection(action, data, status=400):
    reason = rejection_reason(action, data)
    return SmsError(status, reason, cancel_timing=cancel_timing(data, rejected=True)
                    if reason == 'cancel_too_early' else None)


def acquired_phone(phone, metadata):
    country = COUNTRIES.get(str(metadata).strip().lower()) if type(metadata) in (str, int) else None
    if country in {'US', 'CA'}:
        if re.fullmatch(r'[1-9][0-9]{9}', phone):
            return '+1' + phone, country
        if re.fullmatch(r'\+?1[0-9]{10}', phone):
            return '+' + phone.lstrip('+'), country
        # Do not turn an explicitly international number into a US/Canada one
        # when upstream metadata disagrees with the allocated number.
        return phone, None
    if country:
        dial = DIAL_CODES[country]
        if phone.startswith('+'):
            return phone, country if phone.startswith('+' + dial) else None
        return ('+' + phone if phone.startswith(dial) else phone), country
    # An allocated order remains usable even without trustworthy country data.
    return phone, None


def project(action, data):
    if not isinstance(data, dict) or data.get('ok') is not True:
        raise rejection(action, data)
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
    if status == 'PENDING':
        result.update(cancel_timing(data))
    if action == 'acquire':
        phone, order = data.get('phone'), data.get('order_id')
        if not isinstance(phone, str) or not re.fullmatch(r'\+?[1-9]\d{6,14}', phone) or not integer(order) or not order:
            raise SmsError()
        phone, country = acquired_phone(phone, data.get('country'))
        result.update(phone=phone, order_id=order, country=country)
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
                        rejected = 400 <= response.status_code < 500 and response.status_code != 429
                        if response.status_code != 200 and not (rejected or response.status_code == 429):
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
                        if rejected:
                            try:
                                data = json.loads(body)
                            except ValueError:
                                data = None
                            raise rejection(action, data, 409 if response.status_code == 409 else 400)
                        return project(action, json.loads(body))
        except (httpx.HTTPError, TimeoutError, ValueError, TypeError):
            raise SmsError() from None
        finally:
            self.inflight.discard(identity)


def register_sms_routes(app, *, gateway=None, on_received=None):
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
                result = await gateway.call(action, payload, address)
                if on_received and result.get('status') == 'RECEIVED' and result.get('sms_code'):
                    order_id = result['order_id'] if action == 'acquire' else payload['order_id']
                    try:
                        await on_received(order_id)
                    except Exception:
                        # Accounting is best effort; a delivered SMS must remain
                        # visible. Never log an exception that may carry secrets.
                        logging.getLogger(__name__).warning('SMS sale could not be recorded')
                return respond(result)
            except SmsError as exc:
                return respond({'ok': False, 'error': exc.error, 'retry_after': exc.retry_after, **exc.cancel_timing}, exc.status,
                               exc.retry_after if exc.status == 429 else None)
            except (ValueError, UnicodeError, TimeoutError):
                return respond({'ok': False, 'error': 'invalid_request'}, 422)
        return handle

    for action in PATHS:
        app.add_api_route('/api/v1/shop/sms/' + action, endpoint(action), methods=['POST'])
