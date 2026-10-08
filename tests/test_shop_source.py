import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest
from web_api.shop_source import SourceError, fetch_catalog, normalize_products

FIXTURE = Path(__file__).parent / 'fixtures/shop/products.json'


def products():
    return json.loads(FIXTURE.read_text())['data']


def test_real_contract_skus_cents_hidden_stock_and_no_html():
    rows = normalize_products(products())
    assert len(rows) == 7
    assert {r['cost_cents'] for r in rows} == {100000, 100500, 305000, 3100, 61500, 10750, 10900}
    assert len({r['key'] for r in rows}) == 7
    assert all(r['quantity'] is None and r['sales'] is None for r in rows)
    assert '<h3>' not in json.dumps(rows)
    assert 'cnadsiuvhga' not in json.dumps(rows)
    assert [r for r in rows if r['key'] == '1:4'][0]['available'] is False
    assert {r['label'] for r in rows if r['product_id'] == '2'} == {'可新开', '续费卡密不可新开'}


@pytest.mark.parametrize('amount', [None, '', '0', '-1', 'NaN', '1.005', True, 1.5, '1e3'])
def test_reject_invalid_cost(amount):
    data = products()
    data[0]['skus'][0]['price_amount'] = amount
    with pytest.raises(SourceError):
        normalize_products(data)


def test_duplicate_ids_disabled_sku_and_untrusted_copy():
    data = products()
    data[0]['skus'][0]['is_active'] = False
    assert len(normalize_products(data)) == 6
    data = products()
    data.append(copy.deepcopy(data[0]))
    with pytest.raises(SourceError):
        normalize_products(data)
    data = products()
    data[0]['title']['zh-CN'] += ' https://evil.test @supplier'
    data[0]['skus'][0]['spec_values'] = {'zh-CN': 'https://evil.test @supplier'}
    row = normalize_products(data)[0]
    assert row['public_title'] == 'ChatGPT Pro 200'
    assert row['label'] == '规格 7'


def plus_with(*labels):
    data = products()
    plus = next(product for product in data if product['id'] == 1)
    for sku, label in zip(plus['skus'], labels):
        sku['spec_values'] = {'zh-CN': label}
    return {
        row['key']: row['label'] for row in normalize_products(data) if row['product_id'] == '1'
    }


def test_variants_are_listed_cheapest_first_within_each_product():
    data = products()
    pro = next(product for product in data if product['id'] == 2)
    pro['skus'].reverse()
    rows = normalize_products(data)
    assert [row['key'] for row in rows if row['product_id'] == '2'] == ['2:7', '2:2']
    assert [row['product_id'] for row in rows][:2] == ['2', '2']


def test_plain_supplier_variant_names_reach_buyers():
    assert plus_with('250点数', 'plus不可以覆盖') == {'1:1': '250点数', '1:4': 'plus不可以覆盖'}
    # Repeated names would make variants indistinguishable, so repeats keep their number.
    assert plus_with('月卡', '月卡') == {'1:1': '月卡', '1:4': '规格 4'}
    assert plus_with('  ', '一卡一付') == {'1:1': '标准规格', '1:4': '一卡一付'}


@pytest.mark.parametrize(
    'unsafe',
    [
        '加微信 abc',
        '客服@shop',
        'QQ群 123456',
        'shop.com 直充',
        't.me/abc',
        'Telegram 发货',
        'x' * 25,
        '<b>粗体</b>',
    ],
)
def test_supplier_variant_names_with_contacts_or_markup_stay_numbered(unsafe):
    assert plus_with(unsafe)['1:1'] == '规格 1'


@pytest.mark.parametrize('status', [302, 403, 429, 500])
def test_http_denial_never_followed(status):
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(status, headers={'location': 'https://evil.test'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SourceError):
                await fetch_catalog(client=client)

    asyncio.run(run())
    assert len(calls) == 1


def test_fetch_currency_pagination_and_bounds():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        assert request.url.host == 'qiangyunai.com'
        if request.url.path.endswith('config'):
            return httpx.Response(200, json={'status_code': 0, 'data': {'currency': 'CNY'}})
        return httpx.Response(200, json=json.loads(FIXTURE.read_text()))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert len(await fetch_catalog(client=client)) == 7

    asyncio.run(run())
    assert len(calls) == 2


@pytest.mark.parametrize('bad', ['currency', 'pagination', 'html', 'size'])
def test_invalid_responses(bad):
    def handler(request):
        if bad == 'html':
            return httpx.Response(200, text='<html>sign in</html>')
        if bad == 'size':
            return httpx.Response(200, content=b'x' * 1048577)
        if request.url.path.endswith('config'):
            return httpx.Response(
                200,
                json={
                    'status_code': 0,
                    'data': {'currency': 'USD' if bad == 'currency' else 'CNY'},
                },
            )
        data = json.loads(FIXTURE.read_text())
        data['pagination']['total_page'] = 999
        return httpx.Response(200, json=data)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SourceError):
                await fetch_catalog(client=client)

    asyncio.run(run())


def test_empty_catalog_clears_snapshot_and_timeout_is_bounded():
    def handler(request):
        if request.url.path.endswith('config'):
            return httpx.Response(200, json={'status_code': 0, 'data': {'currency': 'CNY'}})
        return httpx.Response(
            200,
            json={
                'status_code': 0,
                'data': [],
                'pagination': {'page': 1, 'page_size': 100, 'total': 0, 'total_page': 0},
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await fetch_catalog(client=client) == []

        def timeout(request):
            raise httpx.ReadTimeout('test', request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with pytest.raises(SourceError):
                await fetch_catalog(client=client)

    asyncio.run(run())
