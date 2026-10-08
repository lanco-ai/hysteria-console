"""Bounded ProdSeller reseller-API acquisition for selected Gemini products.

Read-only by construction: only ``GET /v1/products`` is ever requested. Orders
spend the reseller balance and are never created here.
"""

import asyncio
import json
import re
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import httpx

from .shop_source import MAX_BODY, MAX_CENTS, MAX_ROWS, SourceError

ORIGIN = 'https://prodseller.com'
# Disjoint from the GPT (< 10^12) and Anli (10^12 .. 2*10^12 - 1) namespaces.
PRODUCT_BASE = 2_000_000_000_000
KEY_PATH = Path('/root/hysteria/state/shop/prodseller.key')
# Supplier quotes are USD; costs are shown in CNY at this reviewed fixed rate.
USD_CNY = Decimal('7.20')
MAX_USD = Decimal(10000)
# Supplier product ID -> (synthetic product number, public title). Only these
# products are imported; other ProdSeller products are deliberately ignored.
SELECTED = {
    '6a31035939dc014325da2c66': (1, 'Gemini Pro 18 个月'),
}
# An ID changing plans must not silently change a merchant's existing SKU.
PLAN = re.compile(r'Gemini\s*Pro\s*18\s*Months?\b', re.I)


def configured(key_path=KEY_PATH):
    return Path(key_path).is_file()


def _api_key(key_path):
    try:
        key = Path(key_path).read_text(encoding='ascii').strip()
    except (OSError, UnicodeError) as exc:
        raise SourceError('missing api key') from exc
    if not re.fullmatch(r'psk_[A-Za-z0-9]{16,128}', key):
        raise SourceError('invalid api key')
    return key


def _usd(value):
    if type(value) not in (int, Decimal):
        raise SourceError('invalid amount')
    amount = Decimal(value)
    if not amount.is_finite() or not 0 < amount <= MAX_USD:
        raise SourceError('invalid amount')
    return amount


def normalize_products(value):
    if (
        not isinstance(value, dict)
        or not isinstance(value.get('products'), list)
        or len(value['products']) > MAX_ROWS
    ):
        raise SourceError('invalid catalog')
    found = {}
    for item in value['products']:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get('id'), str)
            or not re.fullmatch(r'[0-9a-f]{24}', item['id'])
        ):
            raise SourceError('invalid product identity')
        if item['id'] in found:
            raise SourceError('duplicate product')
        found[item['id']] = item
    rows = []
    for supplier_id, (number, public_title) in SELECTED.items():
        item = found.get(supplier_id)
        if item is None:
            continue
        name = item.get('name')
        if not isinstance(name, str) or not 0 < len(name) <= 300 or not PLAN.match(name):
            raise SourceError('unrecognised product plan')
        price = _usd(item.get('price'))  # The unit price charged to this API key.
        delivery = item.get('delivery')
        if (
            type(item.get('inStock')) is not bool
            or type(item.get('requiresEmailActivation')) is not bool
            or not isinstance(delivery, dict)
            or not isinstance(delivery.get('type'), str)
        ):
            raise SourceError('invalid availability')
        cost = int((price * USD_CNY * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if not 0 < cost <= MAX_CENTS:
            raise SourceError('invalid amount')
        product_id = str(PRODUCT_BASE + number)
        rows.append(
            {
                'key': f'{product_id}:1',
                'product_id': product_id,
                # Administrator-only: keep the supplier's USD quote visible.
                'title': f'{name} · ${price:.2f}',
                'public_title': public_title,
                'label': '标准规格',
                'source_url': f'{ORIGIN}/v1/products/{supplier_id}',
                'cost_cents': cost,
                # Email-activated or non-instant products need a different checkout.
                'available': item['inStock']
                and not item['requiresEmailActivation']
                and delivery['type'] == 'instant',
                'quantity': None,
                'sales': None,
            }
        )
    return rows


async def _products(client, key):
    request = client.build_request(
        'GET',
        ORIGIN + '/v1/products',
        timeout=httpx.Timeout(8, connect=4),
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json', 'X-API-Key': key},
    )
    request.headers.pop('Cookie', None)
    request.headers.pop('Authorization', None)
    response = await client.send(request, stream=True, follow_redirects=False, auth=None)
    try:
        if response.status_code != 200:
            raise SourceError('upstream unavailable')
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise SourceError('response too large')
    finally:
        await response.aclose()
    try:
        return json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid response') from exc


async def fetch_catalog(*, client=None, key_path=KEY_PATH):
    key = _api_key(key_path)
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned, key_path=key_path)
    try:
        async with asyncio.timeout(20):
            return normalize_products(await _products(client, key))
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
