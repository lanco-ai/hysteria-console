"""Bounded, anonymous Fanai acquisition for ChatGPT, Claude and Grok plans.

Fanai runs an 异次元发卡 storefront. Read-only by construction: only its public
category list (``GET /user/api/index/data``) and commodity list
(``GET /user/api/index/commodity``) are requested, the same data its shop page
shows to any visitor. Plans are renamed to the public titles the other feeds use,
so the merchant can compare one plan's cost across suppliers and publish the
cheapest.
"""

import asyncio
import json
import re
from decimal import Decimal, InvalidOperation

import httpx

from .shop_source import MAX_BODY, MAX_CENTS, MAX_ROWS, SourceError

ORIGIN = 'https://fanai.us.ci'
# Disjoint from GPT, Anli (10^12 ..), ProdSeller (2*10^12 ..) and Lorry (3*10^12 ..).
PRODUCT_BASE = 4_000_000_000_000
MAX_EXTERNAL_ID = 10**12 - 1
# Only these storefront categories are read; names are compared case-insensitively.
CATEGORIES = frozenset({'chatgpt', 'claude', 'grok'})
PAGE_SIZE = 100
MAX_PAGES = 5
# The most specific plan first. Fanai names ChatGPT's $100 and $200 Pro plans by
# their usage ("Pro 5x", "Pro 20x"); the other feeds name them by price.
PLANS = (
    (re.compile(r'claude\s*max\s*20\s*x', re.I), 'Claude Max 20x'),
    (re.compile(r'claude\s*max\s*5\s*x', re.I), 'Claude Max 5x'),
    (re.compile(r'claude\s*pro', re.I), 'Claude Pro'),
    (re.compile(r'(?:grok\s*super|super\s*grok)\s*heavy', re.I), 'Grok SuperGrok Heavy'),
    (re.compile(r'grok\s*super|super\s*grok', re.I), 'Grok SuperGrok'),
    (re.compile(r'(?:chat\s*)?gpt\s*pro\s*20\s*x', re.I), 'ChatGPT Pro 200'),
    (re.compile(r'(?:chat\s*)?gpt\s*pro\s*5\s*x', re.I), 'ChatGPT Pro 100'),
    (re.compile(r'(?:chat\s*)?gpt\s*plus', re.I), 'ChatGPT Plus'),
    (re.compile(r'(?:chat\s*)?gpt\s*go(?![a-z])', re.I), 'ChatGPT Go'),
)
BASE_TITLES = frozenset(title for _, title in PLANS)
MONTHS = re.compile(r'(\d{1,2})\s*个月')
TITLE = re.compile(r'(?P<base>.+?)(?: (?P<months>\d{1,2}) 个月)?')


def public_title(name):
    """The plan a supplier name sells, with its length when longer than a month; else None."""
    plan = next((title for pattern, title in PLANS if pattern.search(name)), None)
    if plan is None:
        return None
    months = MONTHS.search(name)
    return f'{plan} {int(months[1])} 个月' if months and int(months[1]) > 1 else plan


def valid_public_title(title):
    match = TITLE.fullmatch(title) if isinstance(title, str) else None
    return bool(match) and match['base'] in BASE_TITLES and (match['months'] is None or int(match['months']) > 1)


def _identity(value):
    if type(value) is not int or not 0 < value <= MAX_EXTERNAL_ID:
        raise SourceError('invalid identity')
    return value


def _cents(value):
    if type(value) not in (int, float, str):
        raise SourceError('invalid amount')
    try:
        amount = Decimal(str(value))
    except InvalidOperation as exc:
        raise SourceError('invalid amount') from exc
    if not amount.is_finite() or amount != amount.quantize(Decimal('0.01')):
        raise SourceError('invalid amount')
    result = int(amount * 100)
    if not 0 < result <= MAX_CENTS:
        raise SourceError('invalid amount')
    return result


def categories_of(rows):
    """The ids of the storefront categories this feed reads."""
    if not isinstance(rows, list) or len(rows) > 200:
        raise SourceError('invalid categories')
    selected = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            raise SourceError('invalid category')
        if row['name'].strip().lower() in CATEGORIES and row.get('status') == 1 and row.get('hide') in (0, None):
            selected.add(_identity(row.get('id')))
    return selected


def normalize_products(commodities, categories):
    if not isinstance(commodities, list) or len(commodities) > MAX_ROWS:
        raise SourceError('invalid products')
    rows, seen = [], set()
    for item in commodities:
        if not isinstance(item, dict):
            raise SourceError('invalid product')
        upstream_id = _identity(item.get('id'))
        if upstream_id in seen:
            raise SourceError('duplicate product')
        seen.add(upstream_id)
        if item.get('category_id') not in categories or item.get('status') != 1 or item.get('hide') not in (0, None):
            continue
        name = item.get('name')
        if not isinstance(name, str) or not 0 < len(name) <= 2000:
            raise SourceError('invalid title')
        retail_title = public_title(name)
        if retail_title is None:
            continue
        stock = item.get('stock')
        if type(stock) is not int or stock < 0:
            raise SourceError('invalid availability')
        product_id = str(PRODUCT_BASE + upstream_id)
        rows.append(
            {
                'key': f'{product_id}:1',
                'product_id': product_id,
                # Administrator-only: the supplier's own name for the item.
                'title': name.strip(),
                'public_title': retail_title,
                'label': '标准规格',
                'source_url': f'{ORIGIN}/item/{upstream_id}',
                # What any visitor pays on the storefront.
                'cost_cents': _cents(item.get('price')),
                'available': stock > 0,
                'quantity': None,
                'sales': None,
            }
        )
    return sorted(rows, key=lambda row: (row['public_title'], row['cost_cents']))


async def _json(client, path, params=None):
    async with client.stream(
        'GET',
        ORIGIN + path,
        params=params,
        follow_redirects=False,
        timeout=httpx.Timeout(8, connect=4),
        headers={'User-Agent': 'Mozilla/5.0', 'Referer': ORIGIN + '/', 'Accept': 'application/json'},
    ) as response:
        if response.status_code != 200:
            raise SourceError('upstream unavailable')
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise SourceError('response too large')
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid response') from exc
    if not isinstance(value, dict) or value.get('code') != 200 or not isinstance(value.get('data'), list):
        raise SourceError('invalid envelope')
    return value


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    try:
        async with asyncio.timeout(25):
            categories = categories_of((await _json(client, '/user/api/index/data'))['data'])
            commodities = []
            for category in sorted(categories):
                for page in range(1, MAX_PAGES + 1):
                    response = await _json(
                        client, '/user/api/index/commodity', {'categoryId': category, 'limit': PAGE_SIZE, 'page': page},
                    )
                    total = response.get('total')
                    if type(total) is not int or not 0 <= total <= MAX_ROWS:
                        raise SourceError('invalid pagination')
                    commodities.extend(response['data'])
                    if len(commodities) > MAX_ROWS:
                        raise SourceError('too many products')
                    if not response['data'] or page * PAGE_SIZE >= total:
                        break
            return normalize_products(commodities, categories)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
