"""Only the reviewed seven anonymous offers are imported, with distinct plan identities."""
import asyncio
import copy
import importlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from tests.test_shop_source_lifecycle import removal
from web_api.shop_source import SourceError
from web_api.shop_store import ShopStore, provider_of, validate_items

FIXTURE = Path(__file__).parent / 'fixtures/shop/shop16688.json'
BASE = 7_000_000_000_000


def data():
    return json.loads(FIXTURE.read_text(), parse_float=Decimal)


def adapter():
    assert importlib.util.find_spec('web_api.shop16688_source') is not None, '16688 adapter is missing'
    return importlib.import_module('web_api.shop16688_source')


def normalize(value=None):
    value = value or data()
    return adapter().normalize_products(value['catalog']['data']['list'],
                                        {key: row['data'] for key, row in value['details'].items()})


def transport(value, requests=None, change=None):
    # Calls are offline; even accidental purchase endpoints fail this allowlist.
    def respond(request):
        if requests is not None:
            requests.append(request)
        assert request.method == 'POST'
        assert request.url.host == 'www.16688.com.cn'
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        body = json.loads(request.content)
        route = request.url.path
        if route == '/shopApi/shop/detail':
            assert body == {'shop_no': 'S162540'}
            result = value['shop']
        elif route == '/shopApi/goodsCategory/list':
            assert body == {'shop_no': 'S162540'}
            result = value['categories']
        elif route == '/shopApi/goods/list':
            assert set(body) == {'shop_no', 'goods_category_no', 'keywords', 'sort'}
            assert body['shop_no'] == 'S162540' and body['keywords'] == '' and body['sort'] == 'default'
            category = body['goods_category_no']
            result = copy.deepcopy(value['catalog'])
            if category:
                result['data']['list'] = [row for row in result['data']['list']
                                         if row['goods_category_no'] == ('' if category == 'uncategorized' else category)]
        elif route == '/shopApi/goods/detail':
            assert set(body) == {'goods_no'}
            result = value['details'][body['goods_no']]
        else:
            pytest.fail('unapproved route ' + route)
        result = copy.deepcopy(result)
        if change:
            changed = change(request, body, result)
            if changed is not None:
                return changed
        # Preserve decimal JSON values as numbers without rounding in this test transport.
        return httpx.Response(200, content=json.dumps(result, ensure_ascii=False, default=float).encode())
    return httpx.MockTransport(respond)


def test_seven_explicit_plans_have_distinct_titles_finite_cny_costs_and_no_public_stock():
    rows = normalize()
    assert len(rows) == 7
    assert [row['cost_cents'] for row in rows] == [1590, 1890, 1599, 27280, 9800, 630, 330]
    assert [row['public_title'] for row in rows] == [
        'Muse AI 账号', 'Muse AI 10亿 Tokens（临时邮箱）', 'Muse AI 10亿 Tokens（微软邮箱）',
        'ChatGPT Plus 学生资格 5个月', 'ChatGPT Plus 成品号 28–30天',
        'Claude Free 账号', 'Gemini 18个月激活链接']
    assert all(row['quantity'] is None and row['sales'] is None for row in rows)
    assert [row['available'] for row in rows] == [True, True, True, True, True, True, True]
    assert [row['label'] for row in rows] == ['标准规格'] * 3 + ['人工开通'] + ['标准规格'] * 3
    validate_items(rows, 'shop16688')
    assert all(provider_of(row['product_id']) == 'shop16688' for row in rows)
    assert provider_of(6000000000001) == 'local' and provider_of(5000000000001) == 'sms'
    assert 'Token' not in rows[0]['public_title']


@pytest.mark.parametrize('field,value', [
    ('goods_no','G123'), ('shop_no','S999'), ('name','Muse AI Premium'),
    ('description','现在是完全不同的套餐'), ('price',0), ('price',-1), ('price',True),
    ('price','15.9'), ('price',Decimal('15.999')), ('price',Decimal('NaN')),
    ('price',10**12), ('skus',[{'id':1}]), ('has_sku',True), ('variants',[]),
    ('delivery_method',2), ('limit_quantity',2),
])
def test_selected_details_fail_closed_on_plan_price_identity_or_variant_change(field,value):
    fixture = data()
    fixture['details']['G38977219']['data'][field] = value
    with pytest.raises(SourceError):
        normalize(fixture)


@pytest.mark.parametrize('stock,quantity', [('out',0), ('',-1), ('mystery',10), ('low',0), ('high',-1), ('normal',None)])
def test_unknown_or_empty_stock_is_unavailable(stock, quantity):
    fixture=data()
    fixture['details']['G38977219']['data'].update(stock_available_status=stock,stock_available_quantity=quantity)
    assert normalize(fixture)[0]['available'] is False


@pytest.mark.parametrize('broken', ['duplicate','missing_detail','list_mismatch','bad_id'])
def test_partial_or_malformed_catalog_is_rejected(broken):
    fixture=data()
    if broken=='duplicate': fixture['catalog']['data']['list'].append(fixture['catalog']['data']['list'][0])
    if broken=='missing_detail': del fixture['details']['G38977219']
    if broken=='list_mismatch': fixture['catalog']['data']['list'][0]['name']='wrong plan'
    if broken=='bad_id': fixture['catalog']['data']['list'][0]['goods_no']='G038977219'
    with pytest.raises(SourceError): normalize(fixture)


def test_unknown_products_are_not_imported_and_cache_identity_remains_fixed():
    fixture=data()
    fixture['catalog']['data']['list'].append({**fixture['catalog']['data']['list'][0], 'goods_no':'G123'})
    assert len(normalize(fixture)) == 7
    row=normalize()[0]
    for delta in ({'public_title':'Muse AI'}, {'label':'10亿Token'}, {'source_url':'https://evil.test/'},
                  {'key':row['product_id']+':2'}, {'product_id':'6000000000001','key':'6000000000001:1'}):
        with pytest.raises(ValueError): validate_items([{**row, **delta}], 'shop16688')
    with pytest.raises(ValueError): validate_items([row], 'gpt')


def test_fetch_verifies_category_union_and_repeated_all_list_without_any_credentials():
    requests=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(data(),requests), cookies={'session':'private'},
                                     headers={'Authorization':'Bearer private'}, auth=('private','private')) as client:
            rows=await adapter().fetch_catalog(client=client)
        assert rows == normalize()
    asyncio.run(run())
    assert len(requests) == 15
    assert [json.loads(r.content)['goods_category_no'] for r in requests if r.url.path.endswith('/goods/list')] == ['', 'C990703', 'C855396', 'C922212', 'uncategorized', '']


@pytest.mark.parametrize('failure', ['subset','changed_all','wrong_shop','invalid_envelope','redirect','too_large','duplicate_category','too_many_categories'])
def test_bounded_fetch_rejects_incomplete_or_untrusted_responses(failure):
    all_calls=[0]
    def change(request, body, result):
        if failure=='subset' and body.get('goods_category_no')=='C990703': result['data']['list'].pop()
        if request.url.path.endswith('/goods/list') and body['goods_category_no']=='':
            all_calls[0]+=1
            if failure=='changed_all' and all_calls[0]==2: result['data']['list'].pop()
        if request.url.path.endswith('/shop/detail'):
            if failure=='wrong_shop': result['data']['shop_no']='S123'
            if failure=='invalid_envelope': result['code']=True
            if failure=='redirect': return httpx.Response(302,headers={'Location':'https://evil.test/'})
            if failure=='too_large': return httpx.Response(200,content=b'x'*(adapter().MAX_BODY+1))
        if request.url.path.endswith('/goodsCategory/list'):
            if failure=='duplicate_category': result['data'].append(result['data'][0])
            if failure=='too_many_categories': result['data']=[{'goods_category_no':f'C{i+1}','name':'category'} for i in range(50)]
    async def run():
        async with httpx.AsyncClient(transport=transport(data(),change=change)) as client:
            with pytest.raises(SourceError): await adapter().fetch_catalog(client=client)
    asyncio.run(run())


def test_store_starts_blank_keeps_muse_and_local_and_can_remove_only_new_source(tmp_path):
    async def empty(): return []
    async def feed(): return normalize()
    async def run():
        from tests.test_shop_prodseller_muse import fixture_feed
        store=ShopStore(tmp_path/'source.json',fetcher=empty,shop16688_fetcher=feed,
                        prodseller_fetcher=fixture_feed,local_products=True,clock=lambda:1000)
        admin=await store.refresh()
        selected=[row for row in admin['items'] if row['provider']=='shop16688']
        assert len(selected)==7 and admin['skus']=={}
        assert all(row['product_id'] not in admin['products'] for row in selected)
        assert admin['sources']['shop16688']['site']=='https://www.16688.com.cn/shop/S162540'
        assert (await store.public())['products']==[]
        new=selected[0]
        await store.update({'revision':0,'telegram':'merchant_shop','client':3,'skus':{
            '2000000000002:1':{'price_cents':1888,'published':True},
            '6000000000001:1':{'price_cents':9000,'published':True},
            new['key']:{'price_cents':2000,'published':True}},
            'products':{'2000000000002':{'description':'Muse current','after_sales':'saved just now'}}})
        public=await store.public()
        new_public=next(row for row in public['products'] if row['id']==new['product_id'])
        assert new_public['description']==new_public['after_sales']==''
        for private in ('shop16688','16688.com.cn','source_lifecycle','removed_sources','cost_cents','shop_no','G38977219'):
            assert private not in json.dumps(public)
        await store.delete_source('shop16688', removal(1))
        assert not store.shop16688_path.exists()
        assert set(row['id'] for row in (await store.public())['products'])=={'2000000000002','6000000000001'}
        assert (await store.admin())['products']['2000000000002']['after_sales']=='saved just now'
        await store.add_source('shop16688',{'revision':2})
        await store.refresh()
        current = await store.admin()
        assert all(row['key'] not in current['skus'] for row in selected)
    asyncio.run(run())


def test_html_plan_fragments_and_entities_verify_without_publishing_description():
    fixture=data()
    fixture['details']['G90025522']['data']['description']='<p>带10&nbsp;亿 <b>Muse</b> Token</p><p>微软<span>邮箱</span></p><p>质保24小时</p>'
    assert normalize(fixture)[2]['public_title']=='Muse AI 10亿 Tokens（微软邮箱）'


@pytest.mark.parametrize('change', [{'stock_available_status':'mystery'}, {'stock_available_status':'out'},
                                    {'stock_available_quantity':None}, {'stock_available_quantity':0}])
def test_only_reviewed_manual_delivery_combination_is_available(change):
    fixture=data()
    fixture['details']['G19269255']['data'].update(change)
    assert normalize(fixture)[3]['available'] is False


@pytest.mark.parametrize('suffix', ['', '｜批发'])
def test_reviewed_plus_product_accepts_only_its_two_verified_full_names(suffix):
    fixture = data()
    goods_no = 'G78946482'
    old_name = fixture['details'][goods_no]['data']['name']
    fixture['details'][goods_no]['data']['name'] = old_name + suffix
    next(row for row in fixture['catalog']['data']['list'] if row['goods_no'] == goods_no)['name'] = old_name + suffix
    row = next(row for row in normalize(fixture) if row['product_id'] == str(BASE + 78946482))
    assert row['public_title'] == 'ChatGPT Plus 成品号 28–30天'
    assert row['title'] == old_name + suffix


@pytest.mark.parametrize('change', ['different_plan', '｜特价', '｜批发｜新套餐'])
def test_reviewed_plus_name_alias_does_not_accept_other_plans_or_suffixes(change):
    fixture = data()
    goods_no = 'G78946482'
    name = fixture['details'][goods_no]['data']['name']
    name = name.replace('Plus', 'Pro') if change == 'different_plan' else name + change
    fixture['details'][goods_no]['data']['name'] = name
    next(row for row in fixture['catalog']['data']['list'] if row['goods_no'] == goods_no)['name'] = name
    with pytest.raises(SourceError, match='unrecognised product plan'):
        normalize(fixture)


def test_plus_wholesale_alias_does_not_apply_to_other_products():
    fixture = data()
    goods_no = 'G38977219'
    fixture['details'][goods_no]['data']['name'] += '｜批发'
    next(row for row in fixture['catalog']['data']['list'] if row['goods_no'] == goods_no)['name'] += '｜批发'
    with pytest.raises(SourceError, match='unrecognised product plan'):
        normalize(fixture)
