"""Bounded Lorry (dujiao-next upstream API) acquisition for X Premium and Muse AI.

Read-only by construction: only the signed ``GET /api/v1/upstream/products`` is
ever requested. Upstream orders spend the account balance and are never created
here; buyers still order through the merchant's Telegram.
"""

import asyncio
import hashlib
import hmac
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from .shop_source import MAX_BODY, MAX_ROWS, SourceError, cents, localized, safe_label

ORIGIN = 'https://shop.lorry.site'
PRODUCTS_PATH = '/api/v1/upstream/products'
# Disjoint from GPT (< 10^12), Anli (10^12 ..) and ProdSeller (2*10^12 ..) ids.
PRODUCT_BASE = 3_000_000_000_000
MAX_EXTERNAL_ID = 10**12 - 1
CREDENTIALS_PATH = Path('/root/hysteria/state/shop/lorry.json')
PAGE_SIZE = 100
MAX_PAGES = 5
# Only these products are imported, recognised by their upstream title so that a
# renumbered upstream catalog cannot attach a different product to a saved SKU.
X_PREMIUM = re.compile(r'X\s*Premium\D{0,12}?(\d{1,2})\s*(?:个月|月|months?)', re.I)
X_MONTHS = {3: 'X Premium 3 个月', 6: 'X Premium 6 个月'}
MUSE = re.compile(r'muse\s*ai', re.I)
MUSE_TITLE = 'Muse AI'
PUBLIC_TITLES = frozenset({*X_MONTHS.values(), MUSE_TITLE})


def configured(path=CREDENTIALS_PATH):
    return Path(path).is_file()


def _credentials(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise SourceError('missing api credentials') from exc
    if not isinstance(value, dict) or set(value) != {'api_key', 'api_secret'}:
        raise SourceError('invalid api credentials')
    key, secret = value['api_key'], value['api_secret']
    if not all(isinstance(item, str) and re.fullmatch(r'[0-9A-Za-z]{16,256}', item) for item in (key, secret)):
        raise SourceError('invalid api credentials')
    return key, secret


def sign(secret, method, path, timestamp, body=b''):
    """HMAC-SHA256 over ``METHOD\\npath\\ntimestamp\\nmd5(body)``, lowercase hex."""
    message = f'{method}\n{path}\n{timestamp}\n{hashlib.md5(body).hexdigest()}'
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def public_title(title):
    match = X_PREMIUM.search(title)
    if match:
        return X_MONTHS.get(int(match[1]))
    return MUSE_TITLE if MUSE.search(title) else None


def _identity(value):
    if type(value) is not int or not 0 < value <= MAX_EXTERNAL_ID:
        raise SourceError('invalid identity')
    return value


def normalize_products(products):
    if not isinstance(products, list) or len(products) > MAX_ROWS:
        raise SourceError('invalid products')
    rows, seen_products, seen_skus = [], set(), set()
    for product in products:
        if not isinstance(product, dict):
            raise SourceError('invalid product')
        upstream_id = _identity(product.get('id'))
        if upstream_id in seen_products:
            raise SourceError('duplicate product')
        seen_products.add(upstream_id)
        title = localized(product.get('title'))
        retail_title = public_title(title)
        if retail_title is None:
            continue
        if product.get('currency') != 'CNY':
            raise SourceError('unsupported currency')
        slug = product.get('slug')
        skus = product.get('skus')
        if (
            not isinstance(slug, str)
            or not 0 < len(slug) <= 300
            or type(product.get('is_active')) is not bool
            or not isinstance(skus, list)
            or not skus
            or len(skus) > 100
        ):
            raise SourceError('invalid product fields')
        product_id = str(PRODUCT_BASE + upstream_id)
        labels, variants = set(), []
        for sku in skus:
            if not isinstance(sku, dict):
                raise SourceError('invalid variant')
            sku_id = _identity(sku.get('id'))
            if sku_id in seen_skus:
                raise SourceError('duplicate variant')
            seen_skus.add(sku_id)
            stock = sku.get('stock_quantity')
            if type(sku.get('is_active')) is not bool or type(stock) is not int or stock < -1:
                raise SourceError('invalid availability')
            if not sku['is_active']:
                continue
            spec = sku.get('spec_values')
            label = (localized(spec) if isinstance(spec, dict) else '') or '标准规格'
            # Buyers must be able to tell variants apart; repeats keep their number.
            if not safe_label(label) or label in labels:
                label = f'规格 {sku_id}'
            labels.add(label)
            variants.append(
                {
                    'key': f'{product_id}:{sku_id}',
                    'product_id': product_id,
                    # Administrator-only: the upstream title and its ids for order placement.
                    'title': f'{title} · 商品 {upstream_id}',
                    'public_title': retail_title,
                    'label': label,
                    'source_url': f'{ORIGIN}/products/{quote(slug, safe="")}',
                    'cost_cents': cents(sku.get('price_amount')),
                    # -1 means unlimited stock upstream.
                    'available': product['is_active'] and stock != 0,
                    'quantity': None,
                    'sales': None,
                }
            )
        if len(rows) + len(variants) > MAX_ROWS:
            raise SourceError('too many variants')
        rows.extend(sorted(variants, key=lambda row: row['cost_cents']))
    return rows


async def _page(client, key, secret, page):
    timestamp = str(int(time.time()))
    request = client.build_request(
        'GET',
        ORIGIN + PRODUCTS_PATH,
        params={'page': page, 'page_size': PAGE_SIZE},
        timeout=httpx.Timeout(8, connect=4),
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Accept': 'application/json',
            'Dujiao-Next-Api-Key': key,
            'Dujiao-Next-Timestamp': timestamp,
            # The signed path never includes the query string.
            'Dujiao-Next-Signature': sign(secret, 'GET', PRODUCTS_PATH, timestamp),
        },
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
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid response') from exc
    if (
        not isinstance(value, dict)
        or value.get('ok') is False
        or type(value.get('total')) is not int
        or not 0 <= value['total'] <= MAX_ROWS
        or not isinstance(value.get('items'), list)
    ):
        raise SourceError('invalid envelope')
    return value['total'], value['items']


async def fetch_catalog(*, client=None, credentials_path=CREDENTIALS_PATH):
    key, secret = _credentials(credentials_path)
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned, credentials_path=credentials_path)
    try:
        async with asyncio.timeout(25):
            products, total = [], None
            for page in range(1, MAX_PAGES + 1):
                count, items = await _page(client, key, secret, page)
                if total is not None and count != total:
                    raise SourceError('changed pagination')
                total = count
                products.extend(items)
                if len(products) > MAX_ROWS:
                    raise SourceError('too many products')
                if not items or len(products) >= total:
                    break
            if len(products) != total:
                raise SourceError('incomplete pagination')
            return normalize_products(products)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
