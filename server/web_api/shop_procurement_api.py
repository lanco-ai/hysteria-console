"""Explicit administrator-authorized procurement; never used by catalog refresh."""

import json
import re
from decimal import ROUND_HALF_UP, Decimal

import httpx

from .shop_prodseller_source import KEY_PATH, ORIGIN, PLANS, SELECTED, _api_key
from .shop_source import SourceError


class SupplierError(Exception):
    """Only a bounded, non-sensitive reason may escape this boundary."""

    def __init__(self, reason='supplier_unavailable'):
        super().__init__(reason)
        self.reason = reason


def cents(value, *, zero=False):
    if type(value) not in (int, Decimal):
        raise SupplierError('invalid_response')
    amount = Decimal(value)
    if not amount.is_finite() or amount < 0 or amount > 10000000:
        raise SupplierError('invalid_response')
    rounded = amount.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
    # The API can serialize binary floating-point residue (e.g. 0.3699999999999994).
    # Accept only negligible noise around a cent, never a genuine sub-cent quote.
    if abs(amount - rounded) > Decimal('0.00000001') or rounded < (0 if zero else Decimal('.01')):
        raise SupplierError('invalid_response')
    return int(rounded * 100)


class ProcurementAPI:
    def __init__(self, *, key_path=KEY_PATH, transport=None):
        self.key_path, self.transport = key_path, transport

    def request(self, method, path, *, payload=None, idempotency=None):
        try:
            headers = {'X-API-Key': _api_key(self.key_path), 'Accept': 'application/json',
                       'User-Agent': 'LancoAI-Procurement/1'}
        except SourceError as exc:
            raise SupplierError('credentials_unavailable') from exc
        if idempotency:
            headers['Idempotency-Key'] = idempotency
        try:
            with httpx.Client(trust_env=False, follow_redirects=False, transport=self.transport,
                              timeout=httpx.Timeout(12, connect=4)) as client:
                with client.stream(method, ORIGIN + path, headers=headers, json=payload) as response:
                    if response.status_code not in (200, 201):
                        # Never expose upstream error text: it may contain credentials or keys.
                        raise SupplierError({401: 'credentials_unavailable', 402: 'balance_low',
                                             409: 'stock_changed', 429: 'rate_limited'}.get(
                                                 response.status_code, 'supplier_unavailable'))
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 1_000_000:
                            raise SupplierError('invalid_response')
            value = json.loads(body, parse_float=Decimal)
            if not isinstance(value, dict):
                raise SupplierError('invalid_response')
            return value
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise SupplierError('supplier_unavailable') from exc

    def balance(self):
        return cents(self.request('GET', '/v1/balance').get('balance'), zero=True)

    def product(self, product_id):
        if product_id not in SELECTED:
            raise ValueError('invalid product')
        data = self.request('GET', '/v1/products/' + product_id)
        number, _ = SELECTED[product_id]
        if (data.get('id') != product_id or not isinstance(data.get('name'), str)
                or not PLANS[number].match(data['name'])
                or type(data.get('stock')) is not int or not 0 <= data['stock'] <= 10_000_000
                or data.get('requiresEmailActivation') is not False
                or not isinstance(data.get('delivery'), dict)
                or data.get('delivery', {}).get('type') != 'instant'):
            raise SupplierError('product_changed')
        return {'price_cents': cents(data.get('price')), 'stock': data['stock']}

    def buy(self, job):
        return self.request('POST', '/v1/orders',
                            payload={'productId': job['product_id'], 'quantity': job['quantity']},
                            idempotency='lancoai-preorder-' + job['id'])

    def order(self, order_id):
        if not re.fullmatch(r'[0-9a-f]{24}', order_id):
            raise ValueError('invalid order id')
        return self.request('GET', '/v1/orders/' + order_id)


def receipt(value, job, *, polling=False):
    """Validate identity before accepting delivery content; never stringify raw replies."""
    order_id, status = value.get('orderId'), value.get('status')
    if (not isinstance(order_id, str) or not re.fullmatch(r'[0-9a-f]{24}', order_id)
            or status not in ('pending', 'paid', 'delivered', 'failed')
            or not isinstance(value.get('product'), dict)
            or value['product'].get('id') != job['product_id']
            or (polling and order_id != job['order_id'])
            or (not polling and (type(value.get('quantity')) is not int
                                 or value['quantity'] != job['quantity']))
            or ('quantity' in value and value['quantity'] != job['quantity'])):
        raise SupplierError('invalid_response')
    amount = cents(value.get('amount'), zero=True)
    keys = []
    if status == 'delivered':
        keys = value.get('deliveredKeys')
        if keys is None and job['quantity'] == 1:
            keys = [value.get('deliveredKey')]
        if (not isinstance(keys, list) or len(keys) != job['quantity']
                or any(not isinstance(key, str) or not 0 < len(key) <= 16384 for key in keys)):
            raise SupplierError('invalid_response')
    return {'order_id': order_id, 'status': status, 'amount_cents': amount, 'keys': keys}
