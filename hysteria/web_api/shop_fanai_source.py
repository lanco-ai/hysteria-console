"""Anonymous Fanai (dujiao-next public catalog) acquisition for ChatGPT, Claude and Grok.

Plans are renamed to the same public titles the other feeds use, so the merchant
can compare one plan's cost across suppliers and publish the cheapest.
"""

import re
from urllib.parse import quote

import httpx

from .shop_source import MAX_ROWS, SourceError, cents, fetch_public_products, identifier, localized, safe_label

ORIGIN = 'https://fanai.us.ci'
# Disjoint from GPT, Anli (10^12 ..), ProdSeller (2*10^12 ..) and Lorry (3*10^12 ..).
PRODUCT_BASE = 4_000_000_000_000
MAX_EXTERNAL_ID = 10**12 - 1
PLANS = (
    (re.compile(r'claude\s*max\s*20\s*x?', re.I), 'Claude Max 20x'),
    (re.compile(r'claude\s*max\s*5\s*x?', re.I), 'Claude Max 5x'),
    (re.compile(r'claude\s*pro', re.I), 'Claude Pro'),
    (re.compile(r'super\s*grok\s*heavy', re.I), 'Grok SuperGrok Heavy'),
    (re.compile(r'super\s*grok\s*plus', re.I), 'Grok SuperGrok Plus'),
    (re.compile(r'super\s*grok', re.I), 'Grok SuperGrok'),
    (re.compile(r'(?:chat\s*)?gpt\s*pro', re.I), 'ChatGPT Pro'),
    (re.compile(r'(?:chat\s*)?gpt\s*plus', re.I), 'ChatGPT Plus'),
    (re.compile(r'(?:chat\s*)?gpt\s*go\b', re.I), 'ChatGPT Go'),
)
PUBLIC_TITLES = frozenset(title for _, title in PLANS)


def public_title(title):
    """The first, most specific plan named in a supplier title, or None."""
    return next((name for pattern, name in PLANS if pattern.search(title)), None)


def normalize_products(products):
    if not isinstance(products, list) or len(products) > MAX_ROWS:
        raise SourceError('invalid products')
    rows, seen_products, seen_skus = [], set(), set()
    for product in products:
        if not isinstance(product, dict):
            raise SourceError('invalid product')
        upstream_id = identifier(product.get('id'))
        if int(upstream_id) > MAX_EXTERNAL_ID or upstream_id in seen_products:
            raise SourceError('invalid product identity')
        seen_products.add(upstream_id)
        title = localized(product.get('title'))
        retail_title = public_title(title)
        if retail_title is None:
            continue
        slug, skus = product.get('slug'), product.get('skus')
        if (
            not isinstance(slug, str)
            or not 0 < len(slug) <= 300
            or not isinstance(skus, list)
            or not skus
            or len(skus) > 100
        ):
            raise SourceError('invalid product fields')
        product_id = str(PRODUCT_BASE + int(upstream_id))
        labels, variants = set(), []
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
            spec = sku.get('spec_values')
            label = (localized(spec) if isinstance(spec, dict) else '') or '标准规格'
            if not safe_label(label) or label in labels:
                label = f'规格 {sku_id}'
            labels.add(label)
            variants.append(
                {
                    'key': f'{product_id}:{sku_id}',
                    'product_id': product_id,
                    'title': title,
                    'public_title': retail_title,
                    'label': label,
                    'source_url': f'{ORIGIN}/products/{quote(slug, safe="")}',
                    'cost_cents': cents(sku.get('price_amount')),
                    'available': not sku['is_sold_out'] and product.get('is_sold_out') is False,
                    'quantity': None,
                    'sales': None,
                }
            )
        if len(rows) + len(variants) > MAX_ROWS:
            raise SourceError('too many variants')
        rows.extend(sorted(variants, key=lambda row: row['cost_cents']))
    return rows


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    return normalize_products(await fetch_public_products(client, ORIGIN))
