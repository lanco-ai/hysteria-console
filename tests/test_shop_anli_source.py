import asyncio
import copy
import json
from pathlib import Path

import httpx
import pytest

FIXTURE = Path(__file__).parent / 'fixtures/shop/anli-products.json'


def products():
    return json.loads(FIXTURE.read_text())


def details(data):
    return {row['id']: {**row, 'config': []} for row in data['data']}


def test_verified_contract_anonymous_cost_synthetic_identity_and_private_stock():
    from web_api.shop_anli_source import normalize_products

    data = products()
    rows = normalize_products(data, details(data))
    assert [row['key'] for row in rows] == [
        '1000000000005:1',
        '1000000000007:1',
        '1000000000008:1',
        '1000000000011:1',
        '1000000000010:1',
    ]
    assert [row['cost_cents'] for row in rows] == [13300, 83000, 169000, 19500, 65000]
    assert [row['available'] for row in rows] == [True, True, False, True, True]
    assert all(row['quantity'] is None and row['sales'] is None for row in rows)
    assert {row['public_title'] for row in rows} == {
        'Claude Pro',
        'Claude Max 5x',
        'Claude Max 20x',
        'Grok SuperGrok',
        'Grok SuperGrok Plus',
    }
    assert all(row['label'] == '标准规格' for row in rows)
    assert 'supplier marketing' not in json.dumps([row['public_title'] for row in rows])
    data['data'][0]['price'] = 134.25
    assert normalize_products(data, details(data))[0]['cost_cents'] == 13425


@pytest.mark.parametrize('amount', [None, '', 0, -1, True, 'NaN', '1e3', 1.005, '1000001'])
def test_invalid_money_rejected(amount):
    from web_api.shop_anli_source import normalize_products
    from web_api.shop_source import SourceError

    data = products()
    data['data'][0]['price'] = amount
    with pytest.raises(SourceError):
        normalize_products(data, details(data))


@pytest.mark.parametrize(
    'bad',
    [
        'total',
        'code',
        'duplicate',
        'range',
        'identity',
        'title',
        'config',
        'missing_config',
        'detail_id',
        'stock',
        'hide',
    ],
)
def test_bad_schema_and_variant_changes_fail_closed(bad):
    from web_api.shop_anli_source import normalize_products
    from web_api.shop_source import SourceError

    data = products()
    detail = details(data)
    if bad == 'total':
        data['total'] += 1
    if bad == 'code':
        data['code'] = True
    if bad == 'duplicate':
        data['data'][1] = copy.deepcopy(data['data'][0])
    if bad == 'range':
        data['data'][-1]['id'] = 1_000_000_000_000
    if bad == 'identity':
        data['data'][0]['id'] = True
    if bad == 'title':
        data['data'][0]['name'] = 'Claude Max 20x'
    if bad == 'config':
        detail[5]['config'] = [{'name': 'new variant'}]
    if bad == 'missing_config':
        del detail[5]['config']
    if bad == 'detail_id':
        detail[5]['id'] = 7
    if bad == 'stock':
        data['data'][0]['stock_state'] = 5
    if bad == 'hide':
        data['data'][0]['hide'] = False
    with pytest.raises(SourceError):
        normalize_products(data, detail)


def test_disabled_hidden_missing_products_do_not_become_buyable():
    from web_api.shop_anli_source import normalize_products

    data = products()
    data['data'][0]['status'] = 0
    data['data'][1]['hide'] = 1
    data['data'].pop(2)
    data['total'] = len(data['data'])
    assert {row['public_title'] for row in normalize_products(data, details(data))} == {
        'Grok SuperGrok',
        'Grok SuperGrok Plus',
    }


@pytest.mark.parametrize(
    'bad',
    [
        None,
        'currency',
        'currency_rate',
        'currency_missing',
        'config',
        'script',
        'oversize',
        302,
        403,
        429,
        500,
        'timeout',
    ],
)
def test_bounded_fixed_origin_get_contract(bad):
    from web_api.shop_anli_source import fetch_catalog
    from web_api.shop_source import SourceError

    calls = []
    data = products()

    def handler(request):
        calls.append(str(request.url))
        assert request.method == 'GET' and request.url.host == 'faka.anligpt.com'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        if type(bad) is int:
            return httpx.Response(bad, headers={'location': 'https://evil.test'})
        if bad == 'timeout':
            raise httpx.ReadTimeout('test', request=request)
        if bad == 'oversize':
            return httpx.Response(200, content=b'x' * 1048577)
        currency = {
            'code': 'USD' if bad == 'currency' else 'CNY',
            'rate': '2' if bad == 'currency_rate' else '1',
            'decimals': 2,
        }
        root = f'setVar("CURRENCY",{json.dumps(currency)});' if bad != 'currency_missing' else ''
        if request.url.path == '/':
            return httpx.Response(
                200, text=root, headers={'set-cookie': 'supplier_session=unneeded; Path=/'}
            )
        if request.url.path == '/user/api/index/commodity':
            assert str(request.url.query, 'ascii') == 'categoryId=0'
            return httpx.Response(200, json=data)
        identity = int(request.url.path.split('/')[-1])
        item = details(data)[identity]
        if bad == 'config':
            item['config'] = ['variant']
        encoded = 'runSupplierCode()' if bad == 'script' else json.dumps(item)
        return httpx.Response(200, text=f'setVar("_var_item",{encoded});')

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if bad is None:
                assert len(await fetch_catalog(client=client)) == 5
            else:
                with pytest.raises(SourceError):
                    await fetch_catalog(client=client)

    asyncio.run(run())
    if bad is None:
        assert len(calls) == 7
    if type(bad) is int or bad in {'oversize', 'timeout'}:
        assert len(calls) == 1
