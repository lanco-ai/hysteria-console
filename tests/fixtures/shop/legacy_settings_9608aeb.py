"""Private source snapshot and separately revisioned merchant settings."""

import asyncio
import hashlib
import json
import math
import re
import secrets
import time
import unicodedata
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import state_store

from .shop16688_source import ORIGIN as SHOP16688_ORIGIN
from .shop16688_source import PRODUCT_BASE as SHOP16688_BASE
from .shop16688_source import SELECTED as SHOP16688_SELECTED
from .shop16688_source import SHOP_NO as SHOP16688_SHOP
from .shop16688_source import label_for as shop16688_label
from .shop_anli_source import ORIGIN as ANLI_ORIGIN
from .shop_anli_source import PRODUCT_BASE, SELECTED
from .shop_fanai_source import ORIGIN as FANAI_ORIGIN
from .shop_fanai_source import PRODUCT_BASE as FANAI_BASE
from .shop_fanai_source import valid_public_title as fanai_title
from .shop_lorry_source import ORIGIN as LORRY_ORIGIN
from .shop_lorry_source import PRODUCT_BASE as LORRY_BASE
from .shop_lorry_source import PUBLIC_TITLES as LORRY_TITLES
from .shop_local_products import NOTICE as LOCAL_NOTICE
from .shop_local_products import PRODUCT_BASE as LOCAL_BASE
from .shop_local_products import PRODUCT_ID as LOCAL_PRODUCT_ID
from .shop_local_products import catalog as local_catalog
from .shop_notices import (
    CLAUDE_NOTICE,
    CLAUDE_PRODUCT_IDS,
    GEMINI_NOTICE,
    GEMINI_PRODUCT_IDS,
    GROK_NOTICE,
    GROK_PRODUCT_IDS,
    MUSE_NOTICE,
    MUSE_PRODUCT_IDS,
    X_PREMIUM_NOTICE,
    sanitize_product_copy,
)
from .shop_prodseller_source import ORIGIN as PRODSELLER_ORIGIN
from .shop_prodseller_source import PRODUCT_BASE as PRODSELLER_BASE
from .shop_prodseller_source import SELECTED as PRODSELLER_SELECTED
from .shop_source import MAX_CENTS, MAX_ROWS, ORIGIN, fetch_catalog, safe_label
from .shop_sms_source import LABEL as SMS_LABEL
from .shop_sms_source import NOTICE as SMS_NOTICE
from .shop_sms_source import ORIGIN as SMS_ORIGIN
from .shop_sms_source import PRODUCT_BASE as SMS_BASE
from .shop_sms_source import PRODUCT_ID as SMS_PRODUCT_ID
from .shop_sms_source import TITLE as SMS_TITLE

TTL = 900
BACKOFF = 900
MANUAL_COOLDOWN = 60
REMOTE_SOURCES = frozenset({'gpt', 'anli', 'prodseller', 'lorry', 'fanai', 'sms', 'shop16688'})
PRODSELLER_BY_NUMBER = {
    number: (supplier_id, title) for supplier_id, (number, title) in PRODSELLER_SELECTED.items()
}
# Each feed's own storefront, where the merchant buys stock. Administrator-only: the
# public catalog never names a supplier.
SOURCE_SITES = {
    'gpt': ORIGIN + '/',
    'anli': ANLI_ORIGIN + '/',
    'prodseller': PRODSELLER_ORIGIN + '/',
    'lorry': LORRY_ORIGIN + '/',
    'fanai': FANAI_ORIGIN + '/',
    'sms': SMS_ORIGIN + '/',
    'shop16688': f'{SHOP16688_ORIGIN}/shop/{SHOP16688_SHOP}',
    'local': '',
}
# Who each feed is, and what it supplies, for the administrator's view. Kept here rather than in
# the frontend, whose code anyone can download: the public side never names a supplier.
SOURCE_LABELS = {
    'gpt': ('强云', 'GPT'),
    'anli': ('Anli', 'Claude / Grok'),
    'prodseller': ('ProdSeller', 'Gemini / Muse AI'),
    'lorry': ('Lorry', 'X / Muse AI'),
    'fanai': ('Fanai', 'GPT / Claude / Grok'),
    'sms': ('Zephyr', '短信接码'),
    'shop16688': ('16688', 'GPT / Claude / Gemini / Muse AI'),
    'local': ('本店', '开通指导'),
}


def buy_url(row):
    """Where the merchant can buy a source item: its page, or the storefront for API-only feeds."""
    provider = provider_of(row['product_id'])
    # ProdSeller items come from its reseller API; their source_url is an API endpoint, not a page.
    return SOURCE_SITES[provider] if provider == 'prodseller' else row['source_url']


def provider_of(product_id):
    number = int(product_id)
    if number >= SHOP16688_BASE:
        return 'shop16688'
    if number >= LOCAL_BASE:
        return 'local'
    if number >= SMS_BASE:
        return 'sms'
    if number >= FANAI_BASE:
        return 'fanai'
    if number >= LORRY_BASE:
        return 'lorry'
    return (
        'prodseller' if number >= PRODSELLER_BASE else 'anli' if number >= PRODUCT_BASE else 'gpt'
    )


def category_of(provider, public_title):
    if provider == 'shop16688':
        return 'GPT' if public_title.startswith('ChatGPT ') else ('Muse AI' if public_title.startswith('Muse AI ') else public_title.split(' ', 1)[0])
    if provider == 'local':
        return 'Claude'
    if provider == 'sms':
        return '短信接码'
    if provider == 'fanai':
        return 'GPT' if public_title.startswith('ChatGPT ') else public_title.split(' ', 1)[0]
    if provider == 'lorry':
        return 'X' if public_title.startswith('X ') else 'Muse AI'
    if provider == 'prodseller':
        return 'Muse AI' if public_title == 'Muse AI' else 'Gemini'
    if provider == 'anli':
        return 'Claude' if public_title.startswith('Claude ') else 'Grok'
    return 'GPT'


class Conflict(ValueError):
    pass


def from_older_page(payload, current):
    """A save from an admin page older than this server, in today's terms.

    - Pages from before 628f723 send no WeChat ID, labels or sold counts (no `client` mark and no
      `wechat` or `display` key): those are kept as they are. Clearing them would also clear the
      WeChat ID, the only contact when Telegram is empty.
    - Pages from 628f723 (mark 2) priced a promotion as a selling price under a higher 原价. That
      becomes the selling price (the 原价) with an 优惠价 (the old selling price), which shows the same.
    - No older page knows the 优惠价, so a saved one is kept while it is still below the price.
    """
    merged = dict(payload)
    if payload.get('client') is None and not {'wechat', 'display'} & set(payload):
        for field in ('wechat', 'display'):
            if current.get(field):
                merged[field] = current[field]
    skus = {}
    for key, item in payload['skus'].items():
        item = dict(item)
        listed = item.pop('list_price_cents', None)
        if listed is not None:
            item.update(price_cents=listed, promo_price_cents=item['price_cents'])
        else:
            promo = current['skus'].get(key, {}).get('promo_price_cents')
            if promo and item['price_cents'] is not None and promo < item['price_cents']:
                item['promo_price_cents'] = promo
        skus[key] = item
    merged['skus'] = skus
    return merged


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


# A WeChat ID: 6–20 characters, starting with a letter.
WECHAT_ID = re.compile(r'[A-Za-z][-_A-Za-z0-9]{5,19}')
# Admin pages mark every save: 2 from 628f723 (a 原价 above the price), 3 from 优惠价 (a promotion
# price below the price). Pages loaded before 628f723 send no mark.
CLIENT_VERSION = 3
OLDER_CLIENTS = frozenset({2})
MAX_BADGE = 8
MAX_SOLD = 10_000_000
MAX_LOCAL_TITLE = 120
MAX_LOCAL_LABEL = 80


def local_names(value):
    """Validate names against actual store-owned identities and omit unchanged defaults."""
    if not isinstance(value, dict) or set(value) - {'products', 'variants'}:
        raise ValueError('invalid local names')
    rows = local_catalog()
    defaults = {
        'products': {row['product_id']: row['title'] for row in rows},
        'variants': {row['key']: row['label'] for row in rows},
    }
    result = {}
    for field, limit in (('products', MAX_LOCAL_TITLE), ('variants', MAX_LOCAL_LABEL)):
        names = value.get(field, {})
        if not isinstance(names, dict) or set(names) - set(defaults[field]):
            raise ValueError('unknown local name identity')
        normalized = {}
        for key, name in names.items():
            if (not isinstance(name, str) or not 0 < len(name.strip()) <= limit
                    or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} or char in '<>' for char in name)):
                raise ValueError('invalid local name text')
            if name.strip() != defaults[field][key]:
                normalized[key] = name.strip()
        if normalized:
            result[field] = normalized
    return result


def valid_badge(text):
    """A short promotion label shown before a product title, e.g. 学生价; never a link or contact."""
    return isinstance(text, str) and (text == '' or (len(text) <= MAX_BADGE and safe_label(text)))


def source_enabled(value):
    """Only explicit OFF choices are stored; an absent source defaults to enabled."""
    if (not isinstance(value, dict) or set(value) - REMOTE_SOURCES
            or any(type(enabled) is not bool for enabled in value.values())):
        raise ValueError('invalid source controls')
    return {provider: False for provider, enabled in value.items() if not enabled}


def source_lifecycle(value):
    """Persisted generations fence requests started before deletion or re-addition."""
    if not isinstance(value, dict) or set(value) - REMOTE_SOURCES:
        raise ValueError('invalid source lifecycle')
    for entry in value.values():
        if (not isinstance(entry, dict) or set(entry) != {'generation', 'deleted'}
                or not _integer(entry['generation'], 1, 2**53 - 2)
                or type(entry['deleted']) is not bool):
            raise ValueError('invalid source lifecycle')
    return value


def source_generation(settings, provider):
    return settings.get('source_lifecycle', {}).get(provider, {}).get('generation', 0)


def is_source_deleted(settings, provider):
    return settings.get('source_lifecycle', {}).get(provider, {}).get('deleted', False)


def is_source_enabled(settings, provider):
    return provider == 'local' or (not is_source_deleted(settings, provider)
                                 and settings.get('source_enabled', {}).get(provider, True))


def validate_settings(value):
    if (
        not isinstance(value, dict)
        or not {'revision', 'telegram', 'skus'} <= set(value)
        or set(value) - {'revision', 'telegram', 'wechat', 'skus', 'products', 'display', 'client', 'local_names', 'source_enabled', 'source_lifecycle'}
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
    if 'local_names' in value:
        local_names(value['local_names'])
    if 'source_enabled' in value:
        source_enabled(value['source_enabled'])
    if 'source_lifecycle' in value:
        source_lifecycle(value['source_lifecycle'])
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
