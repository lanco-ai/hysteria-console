"""Unchanged settings validator from 8fedc2e, before local name overrides."""

import json
import re
from web_api.shop_source import MAX_CENTS, MAX_ROWS, safe_label

def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum

WECHAT_ID = re.compile(r'[A-Za-z][-_A-Za-z0-9]{5,19}')

CLIENT_VERSION = 3

OLDER_CLIENTS = frozenset({2})

MAX_BADGE = 8

MAX_SOLD = 10_000_000

def valid_badge(text):
    """A short promotion label shown before a product title, e.g. 学生价; never a link or contact."""
    return isinstance(text, str) and (text == '' or (len(text) <= MAX_BADGE and safe_label(text)))

def validate_settings(value):
    if (
        not isinstance(value, dict)
        or not {'revision', 'telegram', 'skus'} <= set(value)
        or set(value) - {'revision', 'telegram', 'wechat', 'skus', 'products', 'display', 'client'}
        or (value.get('client', CLIENT_VERSION) != CLIENT_VERSION and value.get('client') not in OLDER_CLIENTS)
        or not _integer(value['revision'], 0, 2**53 - 2)
        or not isinstance(value['telegram'], str)
        or (
            value['telegram'] and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', value['telegram'])
        )
        or not isinstance(value.get('wechat', ''), str)
        or (value.get('wechat') and not WECHAT_ID.fullmatch(value['wechat']))
        or not isinstance(value['skus'], dict)
        or len(value['skus']) > MAX_ROWS
    ):
        raise ValueError('invalid settings')
    for key, item in value['skus'].items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}:[1-9]\d{0,15}', key)
            or not isinstance(item, dict)
            or not {'price_cents', 'published'} <= set(item)
            or set(item) - {'price_cents', 'published', 'promo_price_cents', 'list_price_cents'}
            or type(item['published']) is not bool
            or (item['price_cents'] is not None and not _integer(item['price_cents'], 1, MAX_CENTS))
            or (item['published'] and item['price_cents'] is None)
        ):
            raise ValueError('invalid SKU settings')
        # 优惠价 (学生价, 限时价…): shown under the price, for buyers it applies to; it must be lower.
        promo = item.get('promo_price_cents')
        if promo is not None and (item['price_cents'] is None or not _integer(promo, 1, item['price_cents'] - 1)):
            raise ValueError('invalid SKU settings')
        # A 原价 only arrives from a page of 628f723, and is converted before anything is stored.
        listed = item.get('list_price_cents')
        if listed is not None and (
            item['price_cents'] is None or not _integer(listed, item['price_cents'] + 1, MAX_CENTS)
        ):
            raise ValueError('invalid SKU settings')
    display = value.get('display', {})
    if not isinstance(display, dict) or len(display) > MAX_ROWS:
        raise ValueError('invalid product display')
    for key, item in display.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}', key)
            or not isinstance(item, dict)
            or set(item) != {'badge', 'sold'}
            or not valid_badge(item['badge'])
            or not _integer(item['sold'], 0, MAX_SOLD)
        ):
            raise ValueError('invalid product display')
    metadata = value.get('products', {})
    if not isinstance(metadata, dict) or len(metadata) > MAX_ROWS:
        raise ValueError('invalid product copy')
    for key, item in metadata.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}', key)
            or not isinstance(item, dict)
            or set(item) != {'description', 'after_sales'}
            or any(not isinstance(text, str) or len(text) > 4000 for text in item.values())
        ):
            raise ValueError('invalid product copy')
    # Match the route body limit, including preserved metadata from legacy clients.
    if len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 65536:
        raise ValueError('settings too large')
    return value
