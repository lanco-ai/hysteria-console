"""Read-only CNY quote for a single-use SMS activation code."""

import asyncio
import json
import re
from decimal import Decimal

import httpx

from .shop_source import SourceError, cents

ORIGIN = 'https://phoneali.zephyr.baby'
PRODUCT_BASE = 5_000_000_000_000
PRODUCT_ID = str(PRODUCT_BASE + 1)
TITLE = '短信接码激活码'
LABEL = '单次接码'
NOTICE = {
    'description': '**一次性短信接码激活码。** 交付后，在**本店“短信接码”页面**输入激活码，验证后获取号码并查看验证码。\n\n'
                   '**当前默认服务为 ChatGPT Codex**；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。\n\n'
                   '> **支持二验、三验：** 账号需要再次验证时，可用同一个美国号码再次接码。在接码页面开启“指定手机号”并填入原号码即可；'
                   '每次需有**可用激活次数**，能否再次取得原号码以号码当前状态为准，取不到时不会自动换成其他号码。',
    'after_sales': '未收到验证码时可在接码页面申请取消，以页面返回的最终状态为准。'
                   '只有确认取消并退回次数后才能再次取号。无法使用时，请联系本店核对处理。',
}
# Short labels beside the title on the card and the product page; a hot one is set in red.
TAGS = ({'text': '支持二验三验'}, {'text': '支持Codex', 'hot': True})
# Known historical defaults, including the store's approved emphasis formatting.
# Match only these complete versions (ignoring spacing), preserving all other merchant edits.
PREVIOUS_DESCRIPTIONS = (
    '一次性短信接码激活码。交付后，在本店“短信接码”页面输入激活码，验证后获取号码并查看验证码。\n\n'
    '当前默认服务为 ChatGPT；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。',
    '一次性短信接码激活码。交付后，在本店“短信接码”页面输入激活码，验证后获取号码并查看验证码。\n\n'
    '当前默认服务为 ChatGPT；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。'
    '号码有时效，取号后请及时使用，不是长期持有的手机号。请妥善保管激活码，勿转发他人。',
    '**一次性短信接码激活码。** 交付后，在**本店“短信接码”页面**输入激活码，验证后获取号码并查看验证码。\n\n'
    '**当前默认服务为 ChatGPT**；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。\n\n'
    '> **号码有时效，取号后请及时使用，不是长期持有的手机号。** 请妥善保管激活码，勿转发他人。',
    '**一次性短信接码激活码。** 交付后，在**本店“短信接码”页面**输入激活码，验证后获取号码并查看验证码。\n\n'
    '**支持二验、三验：** 账号需要再次验证时，可用同一个美国号码再次接码。在接码页面开启“指定手机号”并填入原号码即可；'
    '每次需有**可用激活次数**，能否再次取得原号码以号码当前状态为准，取不到时不会自动换成其他号码。\n\n'
    '**当前默认服务为 ChatGPT Codex**；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。\n\n'
    '> **号码有时效，取号后请及时使用，不是长期持有的手机号。** 请妥善保管激活码，勿转发他人。',
    '一次性短信接码激活码。交付后，在本店“短信接码”页面输入激活码，验证后获取号码并查看验证码。\n\n'
    '支持二验、三验：账号需要再次验证时，可用同一个美国号码再次接码。在接码页面开启“指定手机号”并填入原号码即可；'
    '每次需有可用激活次数，能否再次取得原号码以号码当前状态为准，取不到时不会自动换成其他号码。\n\n'
    '当前默认服务为 ChatGPT Codex；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。'
    '号码有时效，取号后请及时使用，不是长期持有的手机号。请妥善保管激活码，勿转发他人。',
    '一次性短信接码激活码。交付后，在本店“短信接码”页面输入激活码，验证后获取号码并查看验证码。\n\n'
    '支持指定原美国号码进行二次、三次接码。每次需有可用激活次数，能否再次取得以号码当前状态为准；'
    '号码不可用时不会自动改用随机号码。\n\n'
    '当前默认服务为 ChatGPT；适用平台和领取条件请先向本店确认，不保证所有平台或账号均可使用。'
    '号码有时效，取号后请及时使用，不是长期持有的手机号。请妥善保管激活码，勿转发他人。',
)


def current_copy(copy):
    """The saved copy, with an untouched earlier default replaced by today's."""
    description = copy.get('description', '')
    if any(re.sub(r'\s+', '', description) == re.sub(r'\s+', '', old) for old in PREVIOUS_DESCRIPTIONS):
        return {**copy, 'description': NOTICE['description']}
    return copy


def normalize_config(value):
    if not isinstance(value, dict) or value.get('ok') is not True or type(value.get('enabled')) is not bool:
        raise SourceError('invalid payment config')
    products, providers, maintenance = value.get('products'), value.get('providers'), value.get('maintenance')
    if not isinstance(products, list) or not isinstance(providers, list) or not isinstance(maintenance, dict):
        raise SourceError('invalid payment config')
    selected = [item for item in products if isinstance(item, dict) and item.get('type') == 'sms']
    alipay = [item for item in providers if isinstance(item, dict) and item.get('value') == 'zpay_alipay']
    if len(selected) != 1 or len(alipay) != 1:
        raise SourceError('missing or ambiguous sms quote')
    product, provider = selected[0], alipay[0]
    if any(type(flag) is not bool for flag in (product.get('enabled'), product.get('maintenance'),
                                              maintenance.get('sms'), provider.get('enabled'))):
        raise SourceError('invalid availability')
    prices = product.get('prices')
    quote = prices.get('zpay_alipay') if isinstance(prices, dict) else None
    if not isinstance(quote, dict) or quote.get('currency') != 'CNY' or provider.get('currency') != 'CNY':
        raise SourceError('invalid quote currency')
    amount = quote.get('amount')
    if type(amount) not in (str, int, float, Decimal):
        raise SourceError('invalid amount')
    return [{
        'key': f'{PRODUCT_ID}:1', 'product_id': PRODUCT_ID, 'title': TITLE,
        'public_title': TITLE, 'label': LABEL, 'source_url': ORIGIN + '/',
        'cost_cents': cents(str(amount)),
        'available': value['enabled'] and provider['enabled'] and product['enabled']
                     and not product['maintenance'] and not maintenance['sms'],
        'quantity': None, 'sales': None,
    }]


async def fetch_catalog(*, client=None):
    if client is None:
        async with httpx.AsyncClient(trust_env=False) as owned:
            return await fetch_catalog(client=owned)
    try:
        async with asyncio.timeout(20):
            request = client.build_request('GET', ORIGIN + '/api/payments/config', timeout=10,
                                           headers={'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'})
            request.headers.pop('Cookie', None)
            request.headers.pop('Authorization', None)
            response = await client.send(request, stream=True, follow_redirects=False, auth=None)
            try:
                if response.status_code != 200:
                    raise SourceError('sms quote unavailable')
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 65536:
                        raise SourceError('sms quote too large')
                return normalize_config(json.loads(body, parse_float=Decimal))
            finally:
                await response.aclose()
    except (httpx.HTTPError, TimeoutError, ValueError, TypeError) as exc:
        raise SourceError('sms quote unavailable') from exc
