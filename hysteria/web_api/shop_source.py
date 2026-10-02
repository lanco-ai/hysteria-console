"""Bounded, fixed-origin public supplier acquisition. All output remains private."""

import asyncio
import re
from decimal import Decimal
from urllib.parse import quote

import httpx

ORIGIN = 'https://qiangyunai.com'
MAX_BODY = 1024 * 1024
MAX_ROWS = 500
MAX_CENTS = 100_000_000


class SourceError(ValueError):
    pass


def localized(value):
    if not isinstance(value, dict):
        raise SourceError('invalid localized text')
    text = value.get('zh-CN') or value.get('en-US') or ''
    if not isinstance(text, str) or len(text) > 2000:
        raise SourceError('invalid text')
    return text.strip()


def identifier(value):
    if type(value) is not int or not 0 < value <= 2**53 - 1:
        raise SourceError('invalid identity')
    return str(value)


def cents(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,9}(?:\.\d{1,2})?', value):
        raise SourceError('invalid amount')
    result = int(Decimal(value) * 100)
    if not 0 < result <= MAX_CENTS:
        raise SourceError('invalid amount')
    return result


def normalize_products(products):
    if not isinstance(products, list) or len(products) > MAX_ROWS:
        raise SourceError('invalid products')
    rows, seen_products, seen_skus = [], set(), set()
    for product in products:
        if not isinstance(product, dict):
            raise SourceError('invalid product')
        product_id = identifier(product.get('id'))
        if product_id in seen_products:
            raise SourceError('duplicate product')
        seen_products.add(product_id)
        title = localized(product.get('title'))
        if not title:
            raise SourceError('missing title')
        # Do not republish supplier marketing, contacts, HTML, assets or URLs.
        # Only recognised plan names and exact known specification vocabulary
        # enter the retail projection. Original titles remain administrator-only.
        plan = re.search(r'chat\s*gpt\s*(plus|go|pro\s*\d*)', title, re.I)
        tier = re.sub(r'(?i)pro\s*', 'Pro ', plan[1]).strip() if plan else ''
        public_title = f'ChatGPT {tier.title()}' if tier else '数字商品'
        slug = product.get('slug')
        if not isinstance(slug, str) or not 0 < len(slug) <= 300:
            raise SourceError('invalid slug')
        skus = product.get('skus')
        if not isinstance(skus, list) or not skus or len(skus) > 100:
            raise SourceError('invalid variants')
        for sku in skus:
            if not isinstance(sku, dict):
                raise SourceError('invalid variant')
            sku_id = identifier(sku.get('id'))
            if sku_id in seen_skus:
                raise SourceError('duplicate variant')
            seen_skus.add(sku_id)
            if type(sku.get('is_active')) is not bool or type(sku.get('is_sold_out')) is not bool:
                raise SourceError('invalid availability')
            if not sku['is_active']:
                continue
            label = localized(sku.get('spec_values'))
            label = (
                label
                if label in {'可新开', '续费卡密不可新开', '一卡二付', '一卡一付'}
                else ('标准规格' if not label else f'规格 {sku_id}')
            )
            rows.append(
                dict(
                    key=f'{product_id}:{sku_id}',
                    product_id=product_id,
                    title=title,
                    public_title=public_title,
                    label=label,
                    source_url=f'{ORIGIN}/products/{quote(slug, safe="")}',
                    cost_cents=cents(sku.get('price_amount')),
                    available=not sku['is_sold_out'] and product.get('is_sold_out') is False,
                    quantity=None,
                    sales=None,
                )
            )
            if len(rows) > MAX_ROWS:
                raise SourceError('too many variants')
    return rows


async def _json(client, path, params=None):
    async with client.stream(
        'GET',
        ORIGIN + path,
        params=params,
        follow_redirects=False,
        timeout=httpx.Timeout(8, connect=4),
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': ORIGIN + '/',
            'Accept': 'application/json',
        },
    ) as response:
        if response.status_code != 200:
            raise SourceError('upstream unavailable')
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_BODY:
                raise SourceError('response too large')
    import json

    try:
        value = json.loads(body)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid response') from exc
    if (
        not isinstance(value, dict)
        or type(value.get('status_code')) is not int
        or value['status_code'] != 0
    ):
        raise SourceError('invalid envelope')
    return value


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    try:
        async with asyncio.timeout(25):
            config = await _json(client, '/api/v1/public/config')
            if not isinstance(config.get('data'), dict) or config['data'].get('currency') != 'CNY':
                raise SourceError('unsupported currency')
            products, total_pages, total = [], 1, None
            for page in range(1, 6):
                response = await _json(
                    client, '/api/v1/public/products', {'page': page, 'page_size': 100}
                )
                pagination = response.get('pagination')
                if not isinstance(pagination, dict) or not isinstance(response.get('data'), list):
                    raise SourceError('invalid pagination')
                count, pages = pagination.get('total'), pagination.get('total_page')
                if (
                    type(count) is not int
                    or not 0 <= count <= MAX_ROWS
                    or type(pages) is not int
                    or not 0 <= pages <= 5
                    or (pages == 0 and count != 0)
                    or pagination.get('page') != page
                ):
                    raise SourceError('invalid pagination')
                if total is not None and (count != total or pages != total_pages):
                    raise SourceError('changed pagination')
                total, total_pages = count, pages
                products.extend(response['data'])
                if len(products) > MAX_ROWS:
                    raise SourceError('too many products')
                if page >= total_pages:
                    break
            if len(products) != total:
                raise SourceError('incomplete pagination')
            return normalize_products(products)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
