import asyncio
import copy
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

FIXTURE = Path(__file__).parent / 'fixtures/shop/prodseller-products.json'
KEY = 'psk_' + 'a' * 48


def products():
    return json.loads(FIXTURE.read_text(), parse_float=Decimal)


def key_file(tmp_path, value=KEY):
    path = tmp_path / 'prodseller.key'
    path.write_text(value + '\n')
    return path


def test_only_selected_gemini_enters_with_cny_cost_and_private_supplier_text():
    from web_api.shop_prodseller_source import normalize_products

    rows = normalize_products(products())
    assert rows == [
        {
            'key': '2000000000001:1',
            'product_id': '2000000000001',
            'title': 'Gemini Pro 18Months (link) · $0.38',
            'public_title': 'Gemini Pro 18 个月',
            'label': '标准规格',
            'source_url': 'https://prodseller.com/v1/products/6a31035939dc014325da2c66',
            'cost_cents': 274,
            'available': True,
            'quantity': None,
            'sales': None,
        }
    ]
    # Products mentioning Gemini only in their marketing copy stay out.
    assert 'supplier marketing' not in json.dumps(rows) and 'Example' not in json.dumps(rows)
    data = products()
    data['products'][0]['price'] = Decimal('0.45')
    assert normalize_products(data)[0]['cost_cents'] == 324
    data['products'][0]['price'] = 2
    assert normalize_products(data)[0]['cost_cents'] == 1440


@pytest.mark.parametrize('amount', [None, '0.38', 0, -1, True, 0.38, Decimal('NaN'), 10001])
def test_invalid_money_rejected(amount):
    from web_api.shop_prodseller_source import normalize_products
    from web_api.shop_source import SourceError

    data = products()
    data['products'][0]['price'] = amount
    with pytest.raises(SourceError):
        normalize_products(data)


@pytest.mark.parametrize(
    'change, available',
    [
        ({'inStock': False}, False),
        ({'requiresEmailActivation': True}, False),
        ({'delivery': {'type': 'manual'}}, False),
        ({}, True),
    ],
)
def test_stock_and_checkout_kind_gate_availability(change, available):
    from web_api.shop_prodseller_source import normalize_products

    data = products()
    data['products'][0].update(change)
    assert normalize_products(data)[0]['available'] is available


@pytest.mark.parametrize(
    'bad',
    [
        lambda data: data.update(products={}),
        lambda data: data.pop('products'),
        lambda data: data['products'].append(copy.deepcopy(data['products'][0])),
        lambda data: data['products'][1].update(id='NOT-AN-OBJECT-ID'),
        lambda data: data['products'][0].update(name='Netflix 1 Month'),
        lambda data: data['products'][0].update(name='x' * 301),
        lambda data: data['products'][0].update(inStock='yes'),
        lambda data: data['products'][0].update(requiresEmailActivation=None),
        lambda data: data['products'][0].update(delivery='instant'),
    ],
)
def test_schema_and_repurposed_ids_fail_closed(bad):
    from web_api.shop_prodseller_source import normalize_products
    from web_api.shop_source import SourceError

    data = products()
    bad(data)
    with pytest.raises(SourceError):
        normalize_products(data)


def test_delisted_gemini_disappears_instead_of_staying_buyable():
    from web_api.shop_prodseller_source import normalize_products

    data = products()
    data['products'] = data['products'][1:]
    assert normalize_products(data) == []


@pytest.mark.parametrize(
    'bad', [None, 'missing_key', 'bad_key', 302, 401, 429, 500, 'oversize', 'html', 'timeout']
)
def test_read_only_keyed_fixed_origin_get_contract(tmp_path, bad):
    from web_api.shop_prodseller_source import fetch_catalog
    from web_api.shop_source import SourceError

    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == 'GET' and str(request.url) == 'https://prodseller.com/v1/products'
        assert request.headers['x-api-key'] == KEY
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        if type(bad) is int:
            return httpx.Response(bad, headers={'location': 'https://evil.test'})
        if bad == 'timeout':
            raise httpx.ReadTimeout('test', request=request)
        if bad == 'oversize':
            return httpx.Response(200, content=b'x' * 1048577)
        if bad == 'html':
            return httpx.Response(200, text='<html>login</html>')
        return httpx.Response(200, content=FIXTURE.read_bytes())

    path = (
        tmp_path / 'absent.key'
        if bad == 'missing_key'
        else key_file(tmp_path, 'not-a-key' if bad == 'bad_key' else KEY)
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if bad is None:
                rows = await fetch_catalog(client=client, key_path=path)
                assert [row['key'] for row in rows] == ['2000000000001:1']
            else:
                with pytest.raises(SourceError) as error:
                    await fetch_catalog(client=client, key_path=path)
                assert KEY not in str(error.value)

    asyncio.run(run())
    assert len(calls) == (0 if bad in {'missing_key', 'bad_key'} else 1)


def test_configured_follows_the_key_file(tmp_path):
    from web_api.shop_prodseller_source import configured

    assert configured(tmp_path / 'absent.key') is False
    assert configured(key_file(tmp_path)) is True
