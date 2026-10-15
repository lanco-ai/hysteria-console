"""Bounded anonymous Anli acquisition; embedded JSON is data, never executable code."""

import asyncio
import json
import re
from decimal import Decimal

import httpx

from .shop_source import MAX_BODY, MAX_ROWS, SourceError, cents

ORIGIN = 'https://faka.anligpt.com'
PRODUCT_BASE = 1_000_000_000_000
# Keep both feeds disjoint and all keys within the legacy numeric/JS-safe schema.
MAX_EXTERNAL_ID = PRODUCT_BASE - 1
SELECTED = {
    5: 'Claude Pro',
    7: 'Claude Max 5x',
    8: 'Claude Max 20x',
    11: 'Grok SuperGrok',
    10: 'Grok SuperGrok Plus',
}


def external_id(value):
    if type(value) is not int or not 0 < value <= MAX_EXTERNAL_ID:
        raise SourceError('invalid commodity identity')
    return value


def _catalog(value):
    if (
        not isinstance(value, dict)
        or type(value.get('code')) is not int
        or value['code'] != 200
        or type(value.get('total')) is not int
        or not 0 <= value['total'] <= MAX_ROWS
        or not isinstance(value.get('data'), list)
        or len(value['data']) != value['total']
    ):
        raise SourceError('invalid or incomplete catalog')
    seen, selected = set(), {}
    for item in value['data']:
        if not isinstance(item, dict):
            raise SourceError('invalid commodity')
        identity = external_id(item.get('id'))
        if identity in seen:
            raise SourceError('duplicate commodity')
        seen.add(identity)
        if identity in SELECTED:
            selected[identity] = item
    return selected


def _active(item):
    for field in ('status', 'hide'):
        if type(item.get(field)) is not int or item[field] not in (0, 1):
            raise SourceError('invalid commodity visibility')
    return item['status'] == 1 and item['hide'] == 0


def normalize_products(value, details):
    selected = _catalog(value)
    rows = []
    for identity, public_title in SELECTED.items():
        item = selected.get(identity)
        if item is None or not _active(item):
            continue
        title = item.get('name')
        # An ID changing plans must not silently change a merchant's existing SKU.
        if (
            not isinstance(title, str)
            or not 0 < len(title) <= 2000
            or not re.match(re.escape(public_title) + r'(?:\s|$)', title)
            or (
                public_title == 'Grok SuperGrok'
                and re.match(r'Grok SuperGrok\s+Plus(?:\s|$)', title)
            )
        ):
            raise SourceError('unrecognised commodity plan')
        detail = details.get(identity)
        if (
            not isinstance(detail, dict)
            or type(detail.get('id')) is not int
            or detail['id'] != identity
            or detail.get('config') != []
            or not _active({**detail, 'hide': item['hide']})
        ):
            raise SourceError('unsupported commodity variants')
        stock = item.get('stock_state')
        if type(stock) is not int or not 0 <= stock <= 4:
            raise SourceError('invalid stock state')
        amount = item.get('price')  # Anonymous quote; user_price is a member quote.
        if type(amount) not in (str, int, float, Decimal):
            raise SourceError('invalid amount')
        # SKU 1 is our synthetic default, never an upstream SKU or product token.
        product_id = str(PRODUCT_BASE + identity)
        rows.append(
            {
                'key': f'{product_id}:1',
                'product_id': product_id,
                'title': title,
                'public_title': public_title,
                'label': '标准规格',
                'source_url': f'{ORIGIN}/item/{identity}',
                'cost_cents': cents(str(amount)),
                'available': stock > 0,
                'quantity': None,
                'sales': None,
            }
        )
    return rows


def _embedded(body, name):
    matches = list(re.finditer(r'setVar\(\s*"' + re.escape(name) + r'"\s*,\s*', body))
    if len(matches) != 1:
        raise SourceError('missing or duplicate embedded data')
    try:
        value, end = json.JSONDecoder(parse_float=Decimal).raw_decode(body[matches[0].end() :])
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid embedded data') from exc
    if not re.match(r'\s*\)\s*;', body[matches[0].end() + end :]):
        raise SourceError('invalid embedded data')
    return value


async def _body(client, path, *, params=None):
    request = client.build_request(
        'GET',
        ORIGIN + path,
        params=params,
        timeout=httpx.Timeout(8, connect=4),
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json,text/html'},
    )
    # Supplier Set-Cookie headers must never turn later reads into session quotes.
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
        return body.decode('utf-8')
    except UnicodeError as exc:
        raise SourceError('invalid response') from exc


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    try:
        async with asyncio.timeout(25):
            currency = _embedded(await _body(client, '/'), 'CURRENCY')
            if (
                not isinstance(currency, dict)
                or currency.get('code') != 'CNY'
                or type(currency.get('decimals')) is not int
                or currency['decimals'] != 2
                or type(currency.get('rate')) not in (str, int, Decimal)
                or str(currency['rate']) != '1'
            ):
                raise SourceError('unsupported currency')
            try:
                value = json.loads(
                    await _body(client, '/user/api/index/commodity', params={'categoryId': 0}),
                    parse_float=Decimal,
                )
            except ValueError as exc:
                raise SourceError('invalid response') from exc
            selected = _catalog(value)
            details = {}
            for identity, item in selected.items():
                if _active(item):
                    details[identity] = _embedded(
                        await _body(client, f'/item/{identity}'), '_var_item'
                    )
            return normalize_products(value, details)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
