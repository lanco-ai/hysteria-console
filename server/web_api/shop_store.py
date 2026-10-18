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
from .shop_local_products import BY_ID as LOCAL_PRODUCTS
from .shop_local_products import PRODUCT_BASE as LOCAL_BASE
from .shop_local_products import catalog as local_catalog
from .shop_local_products import category as local_category
from .shop_local_products import ends_at as local_ends_at
from .shop_notices import (
    DUOLINGO_NOTICE,
    DUOLINGO_PRODUCT_IDS,
    CLAUDE_NOTICE,
    CLAUDE_PRODUCT_IDS,
    GEMINI_NOTICE,
    GEMINI_PRODUCT_IDS,
    GROK_NOTICE,
    GROK_PRODUCT_IDS,
    MUSE_NOTICE,
    MUSE_PRODUCT_IDS,
    PRODUCT_TAGS,
    PRODUCT_TITLES,
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
from .shop_sms_source import TAGS as SMS_TAGS
from .shop_sms_source import TITLE as SMS_TITLE
from .shop_sms_source import current_copy as current_sms_copy

TTL = 900
BACKOFF = 900
MANUAL_COOLDOWN = 60
REMOTE_SOURCES = frozenset({'gpt', 'anli', 'prodseller', 'lorry', 'fanai', 'sms', 'shop16688'})
# Private source links waiting for an integration: no fetcher or product namespace.
BOOKMARKED_SOURCES = frozenset({'wzyp'})
MANAGED_SOURCES = REMOTE_SOURCES | BOOKMARKED_SOURCES
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
    'wzyp': 'https://wzyp.cn/shop/LHCdeAiPuzi',
    'local': '',
}
# Who each feed is, and what it supplies, for the administrator's view. Kept here rather than in
# the frontend, whose code anyone can download: the public side never names a supplier.
SOURCE_LABELS = {
    'gpt': ('强云', 'GPT'),
    'anli': ('Anli', 'Claude / Grok'),
    'prodseller': ('ProdSeller', 'Gemini / Muse AI / 多邻国'),
    'lorry': ('Lorry', 'X / Muse AI'),
    'fanai': ('Fanai', 'GPT / Claude / Grok'),
    'sms': ('Zephyr', '短信接码'),
    'shop16688': ('16688', 'GPT / Claude / Gemini / Muse AI'),
    'wzyp': ('WZYP', '待接入'),
    'local': ('本店', '本店商品'),
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


def category_of(provider, public_title, product_id=''):
    if provider == 'shop16688':
        return 'GPT' if public_title.startswith('ChatGPT ') else ('Muse AI' if public_title.startswith('Muse AI ') else public_title.split(' ', 1)[0])
    if provider == 'local':
        # Store-owned products keep their category when the merchant renames them.
        return local_category(product_id)
    if provider == 'sms':
        return '短信接码'
    if provider == 'fanai':
        return 'GPT' if public_title.startswith('ChatGPT ') else public_title.split(' ', 1)[0]
    if provider == 'lorry':
        return 'X' if public_title.startswith('X ') else 'Muse AI'
    if provider == 'prodseller':
        if public_title == 'Duolingo Super 12 个月':
            return '多邻国'
        return 'Muse AI' if public_title == 'Muse AI' else 'Gemini'
    if provider == 'anli':
        return 'Claude' if public_title.startswith('Claude ') else 'Grok'
    return 'GPT'


def storefront_title(row):
    title = PRODUCT_TITLES.get(row['product_id'], row['public_title'])
    if provider_of(row['product_id']) in {'gpt', 'fanai', 'shop16688'} and title.startswith('ChatGPT '):
        # Only reviewed region/payment prefixes are public, never arbitrary supplier copy.
        prefix = re.match(r'^(菲律宾|埃及|智利|卡付)\s*chat\s*gpt\s', row['title'], re.I)
        if prefix:
            label = '卡付充值' if prefix[1] == '卡付' else prefix[1] + '区'
            return f'{title}｜{label}'
    return title


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
# 新品 until this moment (seconds); an expired mark is kept but no longer shown.
MAX_NEW_UNTIL = 2**40
# The storefront announcement: what else the merchant sells beyond the listed products.
MAX_ANNOUNCEMENT = 200
MAX_ANNOUNCEMENT_ITEMS = 40
# Shown until the merchant saves an announcement of their own (an empty one hides it).
DEFAULT_ANNOUNCEMENT = {
    'text': '主营 AI 会员与额度：ChatGPT、Claude、Gemini、Grok 等。'
            '其他数字会员和账号也能代购，页面没上架的直接问我。',
    'items': ['YouTube Premium', 'Gmail 账号', 'Outlook 邮箱', 'Office 365', 'Adobe Express',
              'CapCut 剪映', 'Notion', 'Figma', 'Framer', 'JetBrains', 'ElevenLabs', 'Prime Video',
              'Autodesk', 'Miro', 'Wispr Flow', 'Scribd'],
}
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


# A product name in the announcement: letters, digits and the usual separators, never a link.
SAFE_ITEM = re.compile(r'[0-9A-Za-z\u4e00-\u9fff][0-9A-Za-z\u4e00-\u9fff .+\-/&·]{0,23}')


def announcement(value):
    """The merchant's own words to buyers, and the unlisted products they can ask for."""
    if (not isinstance(value, dict) or set(value) != {'text', 'items'}
            or not isinstance(value['text'], str) or len(value['text']) > MAX_ANNOUNCEMENT
            or any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} and char != '\n'
                   or char in '<>' for char in value['text'])
            or not isinstance(value['items'], list) or len(value['items']) > MAX_ANNOUNCEMENT_ITEMS
            or any(not isinstance(item, str) or not SAFE_ITEM.fullmatch(item) for item in value['items'])
            or len(set(value['items'])) != len(value['items'])):
        raise ValueError('invalid announcement')
    return value


def source_enabled(value):
    """Only OFF choices are stored; bookmarks cannot be enabled, even explicitly."""
    if (not isinstance(value, dict) or set(value) - MANAGED_SOURCES
            or any(type(enabled) is not bool for enabled in value.values())
            or any(value.get(provider) is True for provider in BOOKMARKED_SOURCES)):
        raise ValueError('invalid source controls')
    return {provider: False for provider, enabled in value.items() if not enabled}


def source_lifecycle(value):
    """Persisted generations fence requests started before deletion or re-addition."""
    if not isinstance(value, dict) or set(value) - MANAGED_SOURCES:
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
    return provider == 'local' or (provider not in BOOKMARKED_SOURCES
                                 and not is_source_deleted(settings, provider)
                                 and settings.get('source_enabled', {}).get(provider, True))


def validate_settings(value):
    if (
        not isinstance(value, dict)
        or not {'revision', 'telegram', 'skus'} <= set(value)
        or set(value) - {'revision', 'telegram', 'wechat', 'skus', 'products', 'display', 'announcement', 'client', 'local_names', 'source_enabled', 'source_lifecycle'}
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
    if 'announcement' in value:
        announcement(value['announcement'])
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
            or not {'badge', 'sold'} <= set(item) <= {'badge', 'sold', 'new_until'}
            or not valid_badge(item['badge'])
            or not _integer(item['sold'], 0, MAX_SOLD)
            or ('new_until' in item and not _integer(item['new_until'], 0, MAX_NEW_UNTIL))
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


def validate_items(items, provider='gpt'):
    if not isinstance(items, list) or len(items) > MAX_ROWS:
        raise ValueError('invalid source items')
    seen = set()
    for row in items:
        if not isinstance(row, dict) or set(row) != {
            'key',
            'product_id',
            'title',
            'public_title',
            'label',
            'source_url',
            'cost_cents',
            'available',
            'quantity',
            'sales',
        }:
            raise ValueError('invalid source item')
        key = row['key']
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}:[1-9]\d{0,15}', key)
            or key in seen
            or row['product_id'] != key.split(':')[0]
            or not _integer(row['cost_cents'], 1, MAX_CENTS)
            or type(row['available']) is not bool
            or row['quantity'] is not None
            or row['sales'] is not None
        ):
            raise ValueError('invalid source identity or price')
        product_number = int(row['product_id'])
        if provider == 'gpt':
            if product_number >= PRODUCT_BASE:
                raise ValueError('reserved product namespace')
        elif provider == 'sms':
            if (row['product_id'] != SMS_PRODUCT_ID or key != f'{SMS_PRODUCT_ID}:1'
                    or row['source_url'] != SMS_ORIGIN + '/' or row['public_title'] != SMS_TITLE
                    or row['label'] != SMS_LABEL):
                raise ValueError('invalid sms source identity')
        elif provider == 'anli':
            external = product_number - PRODUCT_BASE
            if (
                external not in SELECTED
                or key != f'{product_number}:1'
                or row['source_url'] != f'{ANLI_ORIGIN}/item/{external}'
                or row['public_title'] != SELECTED[external]
                or row['label'] != '标准规格'
            ):
                raise ValueError('invalid secondary source identity')
        elif provider == 'fanai':
            external = product_number - FANAI_BASE
            if (
                not 0 < external < 10**12
                or key != f'{product_number}:1'
                or row['source_url'] != f'{FANAI_ORIGIN}/item/{external}'
                or not fanai_title(row['public_title'])
                or row['label'] != '标准规格'
            ):
                raise ValueError('invalid secondary source identity')
        elif provider == 'lorry':
            if (
                not LORRY_BASE < product_number < LORRY_BASE + 10**12
                or row['public_title'] not in LORRY_TITLES
                or not isinstance(row['source_url'], str)
                or not row['source_url'].startswith(LORRY_ORIGIN + '/products/')
            ):
                raise ValueError('invalid secondary source identity')
        elif provider == 'shop16688':
            goods_no = f'G{product_number - SHOP16688_BASE}'
            selected = SHOP16688_SELECTED.get(goods_no)
            if (selected is None or key != f'{product_number}:1'
                    or row['source_url'] != f'{SHOP16688_ORIGIN}/goods/{goods_no}'
                    or row['public_title'] != selected[0] or row['label'] != shop16688_label(goods_no)):
                raise ValueError('invalid secondary source identity')
        elif provider == 'prodseller':
            selected = PRODSELLER_BY_NUMBER.get(product_number - PRODSELLER_BASE)
            if (
                selected is None
                or key != f'{product_number}:1'
                or row['source_url'] != f'{PRODSELLER_ORIGIN}/v1/products/{selected[0]}'
                or row['public_title'] != selected[1]
                or row['label'] != '标准规格'
            ):
                raise ValueError('invalid secondary source identity')
        else:
            raise ValueError('unknown source')
        seen.add(key)
        if (
            not isinstance(row['title'], str)
            or not 0 < len(row['title']) <= 2000
            or not isinstance(row['source_url'], str)
            or len(row['source_url']) > 3000
            or (provider == 'gpt' and not row['source_url'].startswith(ORIGIN + '/products/'))
            or not isinstance(row['public_title'], str)
            or (
                provider == 'gpt'
                and not re.fullmatch(
                    r'(ChatGPT (Plus|Go|Pro(?: \d{1,6})?)|数字商品)', row['public_title']
                )
            )
            or not safe_label(row['label'])
        ):
            raise ValueError('invalid source text')
    return items


SHOP_DAY = ZoneInfo('Asia/Shanghai')
MAX_DAILY_MARKS = 20_000


class ShopStats:
    """How many people looked at the shop and at each product.

    A visitor counts once a day for the shop and once a day for each product they open.
    Visitors are told apart by a hash of their address with a salt that is replaced every
    day; neither the address nor an earlier salt is kept, so old marks lead back to no one.
    """

    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock

    @staticmethod
    def _empty():
        return {'schema_version': 1, 'site': 0, 'products': {}, 'day': '', 'salt': '', 'seen': []}

    def _load(self):
        value = state_store.load_json_strict(self.path, self._empty())
        if (
            not isinstance(value, dict)
            or set(value) != set(self._empty())
            or value['schema_version'] != 1
            or not _integer(value['site'], 0, 2**53 - 1)
            or not isinstance(value['products'], dict)
            or len(value['products']) > MAX_ROWS * 4
            or not all(
                isinstance(key, str) and re.fullmatch(r'[1-9]\d{0,15}', key) and _integer(count, 0, 2**53 - 1)
                for key, count in value['products'].items()
            )
            or not (value['day'] == '' or re.fullmatch(r'\d{4}-\d{2}-\d{2}', str(value['day'])))
            or not re.fullmatch(r'(?:[0-9a-f]{32})?', str(value['salt']))
            or not isinstance(value['seen'], list)
            or len(value['seen']) > MAX_DAILY_MARKS
            or not all(isinstance(mark, str) and re.fullmatch(r'[0-9a-f]{16}', mark) for mark in value['seen'])
        ):
            raise state_store.InvalidJsonState('invalid shop statistics')
        return value

    def read(self):
        """Counts for the storefront; none when the file cannot be read, rather than failing it."""
        try:
            value = self._load()
        except (state_store.StateStoreError, OSError, ValueError):
            return {'site': 0, 'products': {}}
        return {'site': value['site'], 'products': value['products']}

    def record(self, address, product_id=None):
        today = datetime.fromtimestamp(self.clock(), SHOP_DAY).date().isoformat()
        with state_store.file_lock(str(self.path) + '.lock', timeout=2):
            value = self._load()
            if value['day'] != today:
                value.update(day=today, salt=secrets.token_hex(16), seen=[])
            seen = set(value['seen'])
            counted = False
            for scope in ('site', product_id) if product_id else ('site',):
                mark = hashlib.sha256(f"{value['salt']}|{address}|{scope}".encode()).hexdigest()[:16]
                if mark in seen or len(seen) >= MAX_DAILY_MARKS:
                    continue
                seen.add(mark)
                value['seen'].append(mark)
                counted = True
                if scope == 'site':
                    value['site'] += 1
                else:
                    value['products'][scope] = value['products'].get(scope, 0) + 1
            if counted:
                state_store.save_json(self.path, value)


class ShopSmsSales:
    """One durable sale per supplier order, with no credential, phone or SMS retained."""

    def __init__(self, path):
        self.path = Path(path)

    @staticmethod
    def _empty():
        return {'schema_version': 1, 'sold': 0, 'orders': []}

    def _load(self):
        value = state_store.load_json_strict(self.path, self._empty())
        if (
            not isinstance(value, dict)
            or set(value) != set(self._empty())
            or type(value['schema_version']) is not int
            or value['schema_version'] != 1
            or not _integer(value['sold'], 0, 2**53 - 1)
            or not isinstance(value['orders'], list)
            or not all(isinstance(mark, str) and re.fullmatch(r'[0-9a-f]{64}', mark)
                       for mark in value['orders'])
            or value['sold'] != len(value['orders'])
            or len(set(value['orders'])) != len(value['orders'])
        ):
            raise state_store.InvalidJsonState('invalid SMS sales statistics')
        return value

    def read(self):
        try:
            return self._load()['sold']
        except (state_store.StateStoreError, OSError, ValueError):
            return 0

    def record(self, order_id):
        if not _integer(order_id, 1, 2**53 - 1):
            raise ValueError('invalid SMS order identity')
        # Use provider-scoped order_id as the order identity. This assumes the
        # supplier does not recycle it; CDK reuse cannot create a second identity.
        mark = hashlib.sha256(f'{SMS_ORIGIN}|{order_id}'.encode()).hexdigest()
        with state_store.file_lock(str(self.path) + '.lock', timeout=2):
            value = self._load()
            if mark in value['orders']:
                return
            value['orders'].append(mark)
            value['sold'] += 1
            state_store.save_json_compact(self.path, value)


class ShopStore:
    def __init__(
        self,
        path='/root/hysteria/state/shop/source.json',
        *,
        fetcher=fetch_catalog,
        secondary_fetcher=None,
        prodseller_fetcher=None,
        lorry_fetcher=None,
        fanai_fetcher=None,
        sms_fetcher=None,
        shop16688_fetcher=None,
        local_products=False,
        bookmarked_sources=False,
        clock=time.time,
    ):
        self.path = Path(path)
        self.settings_path = self.path.with_name(self.path.stem + '-merchant.json')
        self.fetcher, self.clock = fetcher, clock
        self.secondary_fetcher = secondary_fetcher
        self.secondary_path = self.path.with_name(self.path.stem + '-anli.json')
        self.prodseller_fetcher = prodseller_fetcher
        self.prodseller_path = self.path.with_name(self.path.stem + '-prodseller.json')
        self.lorry_fetcher = lorry_fetcher
        self.lorry_path = self.path.with_name(self.path.stem + '-lorry.json')
        self.fanai_fetcher = fanai_fetcher
        self.fanai_path = self.path.with_name(self.path.stem + '-fanai.json')
        self.sms_fetcher = sms_fetcher
        self.sms_path = self.path.with_name(self.path.stem + '-sms.json')
        self.shop16688_fetcher = shop16688_fetcher
        self.shop16688_path = self.path.with_name(self.path.stem + '-shop16688.json')
        self.local_products = local_products
        self.bookmarked_sources = bookmarked_sources
        self.stats = ShopStats(self.path.with_name('stats.json'), clock)
        self.sms_sales = ShopSmsSales(self.path.with_name('sms-sales.json'))
        # The merchant's own stock (shop_inventory.ShopInventory), attached by the routes;
        # without it every product's availability is its supplier's.
        self.inventory = None

    def _feed_ids(self):
        return tuple(
            provider
            for provider in ('gpt', 'anli', 'prodseller', 'lorry', 'fanai', 'sms', 'shop16688')
            if self._fetcher(provider)
        )

    def _fetcher(self, provider):
        return {
            'gpt': self.fetcher,
            'anli': self.secondary_fetcher,
            'prodseller': self.prodseller_fetcher,
            'lorry': self.lorry_fetcher,
            'fanai': self.fanai_fetcher,
            'sms': self.sms_fetcher,
            'shop16688': self.shop16688_fetcher,
        }[provider]

    def _path(self, provider):
        return {
            'gpt': self.path,
            'anli': self.secondary_path,
            'prodseller': self.prodseller_path,
            'lorry': self.lorry_path,
            'fanai': self.fanai_path,
            'sms': self.sms_path,
            'shop16688': self.shop16688_path,
            # Lifecycle cleanup only: bookmarks never load a source snapshot.
            'wzyp': self.path.with_name(self.path.stem + '-wzyp.json'),
        }[provider]

    @staticmethod
    def _empty_source():
        return {
            'items': [],
            'last_success': None,
            'error': None,
            'retry_after': 0,
            'manual_after': 0,
        }

    def _source(self, provider='gpt'):
        value = state_store.load_json_strict(self._path(provider), self._empty_source())
        try:
            if set(value) != {'items', 'last_success', 'error', 'retry_after', 'manual_after'}:
                raise ValueError('invalid snapshot')
            validate_items(value['items'], provider)
            for field in ('last_success', 'retry_after', 'manual_after'):
                number = value[field]
                if number is None and field == 'last_success':
                    continue
                if (
                    type(number) not in (int, float)
                    or not math.isfinite(number)
                    or not 0 <= number < 253402300799
                ):
                    raise ValueError('invalid timestamp')
            if value['error'] not in (None, 'source_unavailable'):
                raise ValueError('invalid error')
        except (ValueError, TypeError) as exc:
            raise state_store.InvalidJsonState('invalid shop source') from exc
        return value

    def _settings(self):
        try:
            return validate_settings(
                state_store.load_json_strict(
                    self.settings_path, {'revision': 0, 'telegram': '', 'skus': {}}
                )
            )
        except (ValueError, TypeError) as exc:
            raise state_store.InvalidJsonState('invalid merchant settings') from exc

    def _sources(self, settings=None):
        settings = self._settings() if settings is None else settings
        sources = {}
        for provider in self._feed_ids():
            if is_source_deleted(settings, provider):
                continue
            try:
                sources[provider] = self._source(provider)
            except state_store.InvalidJsonState:
                if (len(self._feed_ids()) == 1 and not self.local_products
                        and is_source_enabled(settings, provider)):
                    raise
                # Ignore untrusted cache bytes independently; another fresh feed
                # stays readable. A successful refresh can replace this cache.
                sources[provider] = {**self._empty_source(), 'error': 'source_unavailable'}
        if self.local_products:
            sources['local'] = {
                **self._empty_source(), 'items': local_catalog(self.clock()), 'last_success': self.clock(),
            }
        if self.bookmarked_sources:
            for provider in BOOKMARKED_SOURCES:
                if not is_source_deleted(settings, provider):
                    sources[provider] = self._empty_source()
        return sources

    def _feed_status(self, source):
        now = self.clock()
        return {
            'last_success': source['last_success'],
            'error': source['error'],
            'is_stale': (
                source['error'] is not None
                or source['last_success'] is None
                or not 0 <= now - source['last_success'] < TTL
            ),
            'retry_after_seconds': max(0, math.ceil(source['retry_after'] - now)),
            'cooldown_seconds': max(0, math.ceil(source['manual_after'] - now)),
        }

    def _admin(self):
        settings = self._settings()
        sources = self._sources(settings)
        items = [row for source in sources.values() for row in source['items']]
        stock = self.inventory.counts() if self.inventory is not None else {}
        metadata = self.inventory.catalog() if self.inventory is not None else {}
        current = {row['key']: row for row in items}
        for key in stock:
            if key not in current and key in metadata and provider_of(metadata[key]['product_id']) in sources:
                # A vanished supplier SKU has no live quote/availability. Own stock may
                # still be sold at the merchant's saved price; source controls still apply.
                items.append({**metadata[key], 'available': False, 'archived': True})
        names = local_names(settings.get('local_names', {}))
        # Names are merchant metadata, never supplier/source identities or cached source rows.
        for row in items:
            if provider_of(row['product_id']) == 'local':
                title = names.get('products', {}).get(row['product_id'], row['title'])
                row.update(title=title, public_title=title,
                           label=names.get('variants', {}).get(row['key'], row['label']))
        products = dict(settings.get('products', {}))
        if any(row['product_id'] == SMS_PRODUCT_ID for row in items):
            products[SMS_PRODUCT_ID] = current_sms_copy(dict(products.get(SMS_PRODUCT_ID, SMS_NOTICE)))
        for product_id in {row['product_id'] for row in items} & set(LOCAL_PRODUCTS):
            products.setdefault(product_id, dict(LOCAL_PRODUCTS[product_id]['notice']))
        for product_id in {row['product_id'] for row in items} & CLAUDE_PRODUCT_IDS:
            # A saved custom or explicitly empty copy always wins. Defaults only
            # belong to this read projection, never the persisted merchant state.
            products.setdefault(product_id, dict(CLAUDE_NOTICE))
        for product_id in {row['product_id'] for row in items} & GROK_PRODUCT_IDS:
            products.setdefault(product_id, dict(GROK_NOTICE))
        for product_id in {row['product_id'] for row in items} & GEMINI_PRODUCT_IDS:
            products.setdefault(product_id, dict(GEMINI_NOTICE))
        for product_id in {row['product_id'] for row in items} & MUSE_PRODUCT_IDS:
            products.setdefault(product_id, dict(MUSE_NOTICE))
        for product_id in {row['product_id'] for row in items} & DUOLINGO_PRODUCT_IDS:
            products.setdefault(product_id, dict(DUOLINGO_NOTICE))
        for product_id in {row['product_id'] for row in items if row['public_title'].startswith('X Premium ')}:
            products.setdefault(product_id, dict(X_PREMIUM_NOTICE))
        # Older saved copy may still carry supplier links; never project them.
        products = sanitize_product_copy(products)
        statuses = {
            provider: {
                **self._feed_status(source),
                # Store-owned services have no quote age, even if wall time moves backward.
                **({'is_stale': False} if provider == 'local' else {}),
                **({'is_stale': False, 'can_read': False}
                   if provider in BOOKMARKED_SOURCES else {}),
                'enabled': is_source_enabled(settings, provider),
                'site': SOURCE_SITES[provider],
                'supplier': SOURCE_LABELS[provider][0],
                'covers': SOURCE_LABELS[provider][1],
            }
            for provider, source in sources.items()
        }
        effective = {provider: source for provider, source in sources.items()
                     if is_source_enabled(settings, provider)}
        effective_statuses = [statuses[provider] for provider in effective]
        successes = [
            source['last_success']
            for source in effective.values()
            if source['last_success'] is not None
        ]
        automatic_sold = {SMS_PRODUCT_ID: self.sms_sales.read()}
        for key, count in stock.items():
            if count['delivered']:
                product_id = key.split(':')[0]
                automatic_sold[product_id] = automatic_sold.get(product_id, 0) + count['delivered']
        stock_items = []
        for key in stock:
            row = current.get(key) or metadata.get(key)
            stock_items.append({
                **(row or {'key': key, 'product_id': key.split(':')[0], 'title': f'库存商品 {key}',
                           'public_title': f'库存商品 {key}', 'label': key, 'cost_cents': 0}),
                'provider': provider_of(key.split(':')[0]),
                'category': category_of(provider_of(row['product_id']), row['public_title'], row['product_id']) if row else '库存',
                'available': bool(current.get(key, {}).get('available')),
            })
        return {
            **settings,
            'announcement': settings.get('announcement', DEFAULT_ANNOUNCEMENT),
            # Visit counts, for the merchant as for the storefront.
            'stats': self.stats.read(),
            # Read-only automatic counts are independent from revisioned manual settings;
            # units delivered from the merchant's own stock count as sold.
            'automatic_sold': automatic_sold,
            # Units in the merchant's own stock per SKU: counts only, never the lines.
            'inventory': {key: count['stock'] for key, count in stock.items() if count['stock']},
            # Inventory management remains reachable even after an intentional source
            # deletion. No supplier setting or publication is resurrected by this view.
            'inventory_items': stock_items,
            'products': products,
            # The feed and storefront category of each item, so the admin page needs no list of feeds.
            'items': [
                {
                    **row,
                    'public_title': storefront_title(row),
                    'buy_url': buy_url(row),
                    'provider': provider_of(row['product_id']),
                    'category': category_of(provider_of(row['product_id']), row['public_title'], row['product_id']),
                }
                for row in items
            ],
            'sources': statuses,
            'removed_sources': {
                provider: {'supplier': SOURCE_LABELS[provider][0],
                           'covers': SOURCE_LABELS[provider][1], 'site': SOURCE_SITES[provider],
                           'can_add': provider in BOOKMARKED_SOURCES or self._fetcher(provider) is not None,
                           **({'can_read': False} if provider in BOOKMARKED_SOURCES else {})}
                for provider in sorted(REMOTE_SOURCES | (BOOKMARKED_SOURCES if self.bookmarked_sources else frozenset()))
                if is_source_deleted(settings, provider)
            },
            'last_success': max(successes, default=None),
            'error': 'source_unavailable'
            if any(source['error'] for source in effective.values())
            else None,
            'retry_after': min((source['retry_after'] for source in effective.values()), default=0),
            'manual_after': min((source['manual_after'] for source in effective.values()), default=0),
            'is_stale': all(status['is_stale'] for status in effective_statuses),
            'retry_after_seconds': min(
                (status['retry_after_seconds'] for status in effective_statuses), default=0
            ),
            'cooldown_seconds': min((status['cooldown_seconds'] for status in effective_statuses), default=0),
        }

    async def admin(self):
        return await asyncio.to_thread(self._admin)

    async def public(self):
        state = await self.admin()
        stats = state['stats']
        display = state.get('display', {})
        automatic_sold = state.get('automatic_sold', {})
        now = self.clock()
        groups, applicable = {}, set()
        for row in state['items']:
            provider = provider_of(row['product_id'])
            if not state['sources'][provider]['enabled']:
                continue
            merchant = state['skus'].get(row['key'], {})
            if not merchant.get('published'):
                continue
            applicable.add(provider)
            group = groups.setdefault(
                row['product_id'],
                {
                    'id': row['product_id'],
                    'title': row['public_title'],
                    'category': category_of(provider, row['public_title'], row['product_id']),
                    'description': state['products']
                    .get(row['product_id'], {})
                    .get('description', ''),
                    'after_sales': state['products']
                    .get(row['product_id'], {})
                    .get('after_sales', ''),
                    # Merchandising set by the merchant: a label before the title and units sold.
                    'badge': display.get(row['product_id'], {}).get('badge', ''),
                    'sold': (display.get(row['product_id'], {}).get('sold', 0)
                             + automatic_sold.get(row['product_id'], 0)),
                    'views': stats['products'].get(row['product_id'], 0),
                    # Short labels beside the title, e.g. 支持二验三验.
                    **({'tags': list(tags)} if (tags := SMS_TAGS if row['product_id'] == SMS_PRODUCT_ID
                                                else PRODUCT_TAGS.get(row['product_id'])) else {}),
                    # A product the merchant marked 新品, while the mark lasts.
                    **({'new_until': new_until} if (new_until := display.get(row['product_id'], {})
                                                    .get('new_until', 0)) > now else {}),
                    # A limited-time service shows when it leaves the shop.
                    **({'ends_at': local_ends_at(row['product_id'])}
                       if provider == 'local' and local_ends_at(row['product_id']) else {}),
                    'variants': [],
                },
            )
            group['variants'].append(
                {
                    'id': row['key'],
                    'label': row['label'],
                    'price_cents': merchant['price_cents'],
                    'promo_price_cents': merchant.get('promo_price_cents'),
                    # Units in the merchant's own stock sell whatever the supplier has.
                    'available': bool(state['inventory'].get(row['key']))
                    or (row['available'] and not state['sources'][provider]['is_stale']),
                    'quantity': state['inventory'].get(row['key']) or None,
                    'sales': None,
                }
            )
        relevant = [
            status for provider, status in state['sources'].items()
            if status['enabled'] and (not applicable or provider in applicable)
        ]
        ready = any(not status['is_stale'] for status in relevant) or any(
            variant['quantity'] for group in groups.values() for variant in group['variants']
        )
        successes = [
            status['last_success'] for status in relevant if status['last_success'] is not None
        ]
        products = list(groups.values())
        return {
            'currency': 'CNY',
            'telegram': state['telegram'],
            'wechat': state.get('wechat', ''),
            # Shown only when the merchant has something to say.
            **({'announcement': state['announcement']}
               if state['announcement']['text'] or state['announcement']['items'] else {}),
            # Units sold over the shop's life, as in 商品管理: a product taken off sale keeps its count.
            'stats': {'views': stats['site'], 'sold': (sum(item['sold'] for item in display.values())
                                                     + sum(automatic_sold.values()))},
            'products': products,
            'status': 'ready' if ready else ('stale' if successes else 'unavailable'),
            'updated_at': max(successes, default=None),
        }

    def _update(self, payload):
        validate_settings(payload)
        if 'source_lifecycle' in payload:
            raise ValueError('source lifecycle is managed separately')
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            current = self._settings()
            if payload['revision'] != current['revision']:
                raise Conflict('settings changed; reload before saving')
            # Earlier pages also use client 3: omission must preserve names on every version.
            # An explicit empty local_names resets names to the built-in catalog defaults.
            names = local_names(payload.get('local_names', current.get('local_names', {})))
            controls = source_enabled(payload.get('source_enabled', current.get('source_enabled', {})))
            if payload.get('client') != CLIENT_VERSION:
                payload = from_older_page(payload, current)
            elif any('list_price_cents' in item for item in payload['skus'].values()):
                raise ValueError('a 原价 is not part of today\'s settings')
            payload = {key: value for key, value in payload.items()
                       if key not in {'client', 'local_names', 'source_enabled'}}
            if names:
                payload['local_names'] = names
            if current.get('source_lifecycle'):
                payload['source_lifecycle'] = current['source_lifecycle']
            # A page from before the announcement keeps the merchant's own.
            if 'announcement' not in payload and 'announcement' in current:
                payload['announcement'] = current['announcement']
            # Older pages do not know new_until, even when they send display.
            # Omission preserves it; an explicit zero from today's page clears it.
            display = {key: dict(item) for key, item in payload.get('display', {}).items()}
            for key, item in current.get('display', {}).items():
                if item.get('new_until') and 'new_until' not in display.get(key, {}):
                    display.setdefault(key, {'badge': '', 'sold': 0})['new_until'] = item['new_until']
            for item in display.values():
                if item.get('new_until') == 0:
                    item.pop('new_until')
            if display or 'display' in payload:
                payload['display'] = display
            controls = {provider: enabled for provider, enabled in controls.items()
                        if not is_source_deleted(current, provider)}
            if controls:
                payload['source_enabled'] = controls
            for field in ('skus', 'products', 'display'):
                if any(is_source_deleted(current, provider_of(key.split(':')[0]))
                       for key in payload.get(field, {})):
                    raise ValueError('source has been deleted')
            known = {
                row['key'] for source in self._sources(payload).values() for row in source['items']
            } | set(current['skus'])
            if set(payload['skus']) - known:
                raise ValueError('unknown SKU')
            products = sanitize_product_copy(payload.get('products', current.get('products', {})))
            known_products = {key.split(':')[0] for key in known} | set(current.get('products', {}))
            if set(payload.get('display', {})) - known_products - set(current.get('display', {})):
                raise ValueError('unknown product')
            if set(products) - known_products:
                raise ValueError('unknown product')
            saved = {**payload, 'products': products, 'revision': current['revision'] + 1}
            validate_settings(saved)
            state_store.save_json(self.settings_path, saved)
        return self._admin()

    def _change_source(self, provider, payload, deleted):
        expected = {'revision', 'confirm'} if deleted else {'revision'}
        if (provider not in MANAGED_SOURCES
                or (provider in BOOKMARKED_SOURCES and not self.bookmarked_sources)
                or not isinstance(payload, dict)
                or set(payload) != expected or not _integer(payload['revision'], 0, 2**53 - 2)
                or (deleted and payload['confirm'] != 'delete-source-and-products')):
            raise ValueError('invalid source operation')
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            current = self._settings()
            if payload['revision'] != current['revision']:
                raise Conflict('settings changed; reload before changing source')
            if not deleted and provider not in BOOKMARKED_SOURCES and self._fetcher(provider) is None:
                raise ValueError('source is not configured')
            if is_source_deleted(current, provider) != deleted:
                saved = {**current, 'revision': current['revision'] + 1}
                for field in ('skus', 'products', 'display'):
                    if field in saved:
                        # Scan all persisted identities, including products absent from the cache.
                        saved[field] = {key: value for key, value in saved[field].items()
                                        if provider_of(key.split(':')[0]) != provider}
                controls = {key: value for key, value in current.get('source_enabled', {}).items()
                            if key != provider}
                saved.pop('source_enabled', None)
                if controls:
                    saved['source_enabled'] = controls
                saved['source_lifecycle'] = {
                    **current.get('source_lifecycle', {}),
                    provider: {'generation': source_generation(current, provider) + 1,
                               'deleted': deleted},
                }
                validate_settings(saved)
                # A failed second write may lose a rebuildable cache, never restore retail
                # data. Do not roll back an atomic replacement with uncertain durability.
                path = self._path(provider)
                path.unlink(missing_ok=True)
                state_store._fsync_dir(path.parent)
                state_store.save_json(self.settings_path, saved)
        # Readers intentionally do not take the merchant lock: release verification holds it.
        return self._admin()

    async def delete_source(self, provider, payload):
        return await self._finish_thread(self._change_source, provider, payload, True)

    async def add_source(self, provider, payload):
        return await self._finish_thread(self._change_source, provider, payload, False)

    async def visit(self, address, product_id=None):
        """Count a storefront visit, and a view of ``product_id`` when it is a product on sale."""
        if product_id is not None and product_id not in {
            product['id'] for product in (await self.public())['products']
        }:
            product_id = None
        await self._finish_thread(self.stats.record, address, product_id)

    async def record_sms_received(self, order_id):
        await self._finish_thread(self.sms_sales.record, order_id)

    async def update(self, payload):
        return await self._finish_thread(self._update, payload)

    @staticmethod
    async def _finish_thread(fn, *args):
        task = asyncio.create_task(asyncio.to_thread(fn, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def _acquire(self):
        lock = state_store.file_lock(str(self.path) + '.refresh.lock', timeout=0)
        try:
            lock.__enter__()
        except state_store.LockTimeout:
            return None
        return lock

    def _prepare_refresh(self, provider, manual):
        # Check the latest saved choice before every feed. The merchant lock only
        # protects this reservation; supplier I/O never runs while it is held.
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            settings = self._settings()
            if not is_source_enabled(settings, provider):
                return None
            source = self._sources(settings)[provider]
            status = self._feed_status(source)
            if (status['retry_after_seconds'] or (manual and status['cooldown_seconds'])
                    or (not manual and not status['is_stale'])):
                return None
            source['manual_after'] = self.clock() + MANUAL_COOLDOWN
            if self.inventory is not None:
                stocked = self.inventory.counts()
                self.inventory.remember([row for row in source['items'] if row['key'] in stocked
                                         and category_of(provider, row['public_title'], row['product_id']) in {'Gemini', 'Muse AI'}])
            # Persist before acquisition so cancellation cannot busy-loop.
            source['retry_after'] = self.clock() + BACKOFF
            state_store.save_json(self._path(provider), source)
            return source, source_generation(settings, provider)

    def _commit_refresh(self, provider, generation, source):
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            settings = self._settings()
            if (is_source_deleted(settings, provider)
                    or source_generation(settings, provider) != generation):
                return
            state_store.save_json(self._path(provider), source)

    async def refresh(self, *, manual=False):
        acquiring = asyncio.create_task(asyncio.to_thread(self._acquire))
        try:
            lock = await asyncio.shield(acquiring)
        except asyncio.CancelledError:
            lock = await acquiring
            if lock is not None:
                lock.__exit__(None, None, None)
            raise
        if lock is None:
            return await self.admin()
        try:
            for provider in self._feed_ids():
                prepared = await self._finish_thread(self._prepare_refresh, provider, manual)
                if prepared is None:
                    continue
                source, generation = prepared
                fetcher = self._fetcher(provider)
                try:
                    async with asyncio.timeout(30):
                        items = await fetcher()
                    validate_items(items, provider)
                    source.update(items=items, last_success=self.clock(), error=None, retry_after=0)
                except Exception:
                    source.update(error='source_unavailable', retry_after=self.clock() + BACKOFF)
                await self._finish_thread(self._commit_refresh, provider, generation, source)
            return await self.admin()
        finally:
            lock.__exit__(None, None, None)
