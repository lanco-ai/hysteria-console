"""Bounded anonymous reads of the reviewed offers from shop S162540.

The supplier's public query API uses POST for reads. Only the four explicit
shop/category/list/detail paths below are allowed; no order, login or payment
endpoint is called. Descriptions verify plan identity and are never published.
"""
import asyncio
import json
import re
from decimal import Decimal
from html.parser import HTMLParser

import httpx

from .shop_source import MAX_BODY, MAX_CENTS, MAX_ROWS, SourceError

ORIGIN = 'https://www.16688.com.cn'
SHOP_NO = 'S162540'
PRODUCT_BASE = 7_000_000_000_000
MAX_CATEGORIES = 8
QUERY_PATHS = frozenset({'/shopApi/shop/detail', '/shopApi/goodsCategory/list',
                         '/shopApi/goods/list', '/shopApi/goods/detail'})
# ID -> (public title, reviewed upstream name, required descriptive plan markers).
# Three different Muse offers, student qualification, Free and an activation link
# intentionally do not share ordinary subscription titles with other feeds.
SELECTED = {
    'G38977219': ('Muse AI 账号', 'muse成品号', ('临时邮箱', '只保证首次登录Muse')),
    'G62234820': ('Muse AI 10亿 Tokens（临时邮箱）',
                  'Muse AI成品号 带10亿token（对标SuperGro-k）bot', ('临时邮箱', '只质保首登')),
    'G90025522': ('Muse AI 10亿 Tokens（微软邮箱）', 'muse成品号',
                  ('10亿MuseToken', '微软邮箱', '质保24小时')),
    'G19269255': ('ChatGPT Plus 学生资格 5个月',
                  'Plus 学生认证+一个月正价绑卡 （共5个月Plus）', ('学生认证', '一个月菲区Plus绑卡')),
    'G78946482': ('ChatGPT Plus 成品号 28–30天',
                  '顶自营👑｜Plus｜双质保25天起｜越南实卡｜苹果Pay订阅｜稳如老狗｜Codex｜成品号',
                  ('标准Plus会员独享账号', '官方28-30天')),
    'G68673204': ('Claude Free 账号', 'Claude 成品账号 Free版【微软邮箱登录】',
                  ('无套餐', '不是Pro/Max', '微软邮箱')),
    'G64936444': ('Gemini 18个月激活链接', 'gemini 18个月激活链接', ('不是成品号', '激活链接')),
}

# Independently verified full-name alias for this one offer; not a suffix rule.
REVIEWED_NAME_ALIASES = {
    'G78946482': ('顶自营👑｜Plus｜双质保25天起｜越南实卡｜苹果Pay订阅｜稳如老狗｜Codex｜成品号｜批发',),
}


def label_for(goods_no):
    return '人工开通' if goods_no == 'G19269255' else '标准规格'

def _compact(text):
    return ''.join(text.split()).casefold()


class _DescriptionText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def _description(value):
    if not isinstance(value, str) or not 0 < len(value) <= MAX_BODY:
        raise SourceError('missing plan description')
    parser = _DescriptionText()
    parser.feed(value)
    parser.close()
    return _compact(''.join(parser.parts))


def _identity(value, prefix='G'):
    if not isinstance(value, str) or not re.fullmatch(prefix + r'[1-9]\d{0,11}', value):
        raise SourceError('invalid product identity')
    return value


def _cents(value):
    if type(value) not in (int, Decimal):
        raise SourceError('invalid amount')
    amount = Decimal(value)
    if not amount.is_finite() or not 0 < amount <= Decimal(MAX_CENTS) / 100:
        raise SourceError('invalid amount')
    cents = amount * 100
    if cents != cents.to_integral_value():
        raise SourceError('invalid amount')
    return int(cents)


def _indexed(rows):
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise SourceError('invalid catalog')
    found = {}
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError('invalid product')
        goods_no = _identity(row.get('goods_no'))
        if goods_no in found:
            raise SourceError('duplicate product')
        category = row.get('goods_category_no')
        if category != '':
            _identity(category, 'C')
        if row.get('shop_no', SHOP_NO) != SHOP_NO:
            raise SourceError('wrong shop')
        found[goods_no] = row
    return found


def normalize_products(catalog, details):
    found = _indexed(catalog)
    if not isinstance(details, dict):
        raise SourceError('invalid details')
    rows = []
    for goods_no, (public_title, reviewed_name, markers) in SELECTED.items():
        listing = found.get(goods_no)
        if listing is None:
            continue
        detail = details.get(goods_no)
        if (not isinstance(detail, dict) or detail.get('goods_no') != goods_no
                or detail.get('shop_no') != SHOP_NO):
            raise SourceError('missing or mismatched detail')
        reviewed_names = {_compact(name) for name in
                          (reviewed_name, *REVIEWED_NAME_ALIASES.get(goods_no, ()))}
        for item in (listing, detail):
            name = item.get('name')
            if (not isinstance(name, str) or not 0 < len(name) <= 2000
                    or _compact(name) not in reviewed_names):
                raise SourceError('unrecognised product plan')
            if any(re.search(r'sku|variant|spec', key, re.I) for key in item):
                raise SourceError('product variants changed')
            expected_delivery = 3 if goods_no == 'G19269255' else 1
            if (type(item.get('delivery_method')) is not int
                    or item['delivery_method'] != expected_delivery
                    or type(item.get('limit_quantity')) is not int or item['limit_quantity'] != 1):
                raise SourceError('delivery changed')
        if (detail.get('goods_category_no') != listing['goods_category_no']
                or _cents(detail.get('price')) != _cents(listing.get('price'))):
            raise SourceError('changed product detail')
        description = _description(detail.get('description'))
        if any(_compact(marker) not in description for marker in markers):
            raise SourceError('unrecognised product plan')
        quantity = detail.get('stock_available_quantity')
        product_id = str(PRODUCT_BASE + int(goods_no[1:]))
        rows.append({
            'key': f'{product_id}:1', 'product_id': product_id,
            'title': detail['name'].strip(), 'public_title': public_title, 'label': label_for(goods_no),
            'source_url': f'{ORIGIN}/goods/{goods_no}', 'cost_cents': _cents(detail['price']),
            # The reviewed manual offer can be ordered without finite stock. Unknown
            # combinations remain unavailable; no numeric stock is ever published.
            'available': (detail.get('stock_available_status') in ('low', 'normal', 'high')
                          and type(quantity) is int and quantity > 0)
                         or (goods_no == 'G19269255' and detail['delivery_method'] == 3
                             and detail.get('stock_available_status') == ''
                             and type(quantity) is int and quantity == -1),
            'quantity': None, 'sales': None,
        })
    return rows


async def _query(client, path, payload):
    if path not in QUERY_PATHS:
        raise SourceError('unsupported query')
    request = client.build_request(
        'POST', ORIGIN + path, json=payload, timeout=httpx.Timeout(8, connect=4),
        headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json',
                 'Referer': f'{ORIGIN}/shop/{SHOP_NO}'},
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
        result = json.loads(body, parse_float=Decimal)
    except (ValueError, UnicodeError) as exc:
        raise SourceError('invalid response') from exc
    if not isinstance(result, dict) or type(result.get('code')) is not int or result['code'] != 1:
        raise SourceError('invalid envelope')
    return result.get('data')


async def _listing(client, category=''):
    value = await _query(client, '/shopApi/goods/list', {
        'shop_no': SHOP_NO, 'goods_category_no': category, 'keywords': '', 'sort': 'default',
    })
    # This API returns a complete list, not paginated results. A shape change
    # requires a reviewed adapter, never a silently partial cache replacement.
    if not isinstance(value, dict) or set(value) != {'list'}:
        raise SourceError('invalid catalog envelope')
    return _indexed(value['list'])


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    try:
        async with asyncio.timeout(25):
            shop = await _query(client, '/shopApi/shop/detail', {'shop_no': SHOP_NO})
            if not isinstance(shop, dict) or shop.get('shop_no') != SHOP_NO:
                raise SourceError('wrong shop')
            categories = await _query(client, '/shopApi/goodsCategory/list', {'shop_no': SHOP_NO})
            if not isinstance(categories, list) or len(categories) > MAX_CATEGORIES:
                raise SourceError('invalid categories')
            category_ids = []
            for category in categories:
                if not isinstance(category, dict):
                    raise SourceError('invalid category')
                category_id = _identity(category.get('goods_category_no'), 'C')
                if category_id in category_ids:
                    raise SourceError('duplicate category')
                category_ids.append(category_id)
            whole = await _listing(client)
            union = {}
            for category_id in [*category_ids, 'uncategorized']:
                subset = await _listing(client, category_id)
                if (set(union) & set(subset) or len(union) + len(subset) > MAX_ROWS
                        or any(row['goods_category_no'] != ('' if category_id == 'uncategorized' else category_id)
                               for row in subset.values())):
                    raise SourceError('inconsistent categories')
                union.update(subset)
            if union != whole:
                raise SourceError('incomplete catalog')
            details = {}
            for goods_no in SELECTED:
                if goods_no in whole:
                    details[goods_no] = await _query(client, '/shopApi/goods/detail', {'goods_no': goods_no})
            if await _listing(client) != whole:
                raise SourceError('catalog changed during read')
            return normalize_products(list(whole.values()), details)
    except (httpx.HTTPError, TimeoutError) as exc:
        raise SourceError('upstream unavailable') from exc
