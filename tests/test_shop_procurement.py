import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import httpx
import pytest
import state_store
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web_api.shop_procurement import Procurement
from web_api.shop_procurement_api import ProcurementAPI, SupplierError
from web_api.shop_prodseller_source import SELECTED
from web_api.shop_routes import register_shop_routes
from web_api.shop_store import Conflict, ShopStore
from test_shop_routes import HEADERS, Sessions

PRODUCT = next(iter(SELECTED))
MUSE = list(SELECTED)[1]
DUOLINGO = list(SELECTED)[2]
ORDER = 'a' * 24
SECRET = 'fictional-delivery-only-never-public'


@pytest.fixture
def setup(tmp_path):
    now = [1800000000]
    calls = []
    detail = {'id': PRODUCT, 'name': 'Gemini Pro 18 Months', 'stock': 10, 'price': 1.25,
              'requiresEmailActivation': False, 'delivery': {'type': 'instant'}}
    result = {'orderId': ORDER, 'status': 'delivered', 'product': {'id': PRODUCT},
              'quantity': 2, 'amount': 2.50, 'deliveredKeys': [SECRET, SECRET + '-2']}
    controls = {'balance': 20, 'result': result}

    def handler(request):
        assert request.url.host == 'prodseller.com'
        assert request.headers['X-API-Key'] == 'psk_' + 'x' * 20
        calls.append((request.method, request.url.path, request.headers.get('Idempotency-Key')))
        if request.url.path == '/v1/balance':
            return httpx.Response(200, json={'balance': controls['balance']})
        if request.url.path == '/v1/products/' + PRODUCT:
            return httpx.Response(200, json=detail)
        if request.url.path == '/v1/products/' + MUSE:
            if controls.get('muse_down'):
                return httpx.Response(503)
            return httpx.Response(200, json={'id': MUSE, 'name': 'Muse AI', 'stock': 0, 'price': 1.00,
                                             'requiresEmailActivation': False, 'delivery': {'type': 'instant'}})
        if request.url.path == '/v1/products/' + DUOLINGO:
            return httpx.Response(200, json={'id': DUOLINGO, 'name': 'Duolingo Super 12M ',
                                            'stock': 6, 'price': .29, 'requiresEmailActivation': False,
                                            'delivery': {'type': 'instant'}})
        if request.url.path == '/v1/orders':
            assert request.method == 'POST'
            assert json.loads(request.content) == {'productId': PRODUCT, 'quantity': 2}
            if controls.get('timeout'):
                raise httpx.ReadTimeout('private provider payload must not escape')
            if controls.get('block'):
                controls['entered'].set()
                assert controls['release'].wait(5)
            if controls.get('status'):
                return httpx.Response(controls['status'], text=SECRET)
            return httpx.Response(200, json=controls['result'])
        if request.url.path == '/v1/orders/' + ORDER:
            return httpx.Response(200, json=controls['result'])
        raise AssertionError(request.url.path)

    key = tmp_path / 'key'
    key.write_text('psk_' + 'x' * 20)
    async def empty():
        return []
    store = ShopStore(tmp_path / 'source.json', fetcher=empty, prodseller_fetcher=empty)
    api = ProcurementAPI(key_path=key, transport=httpx.MockTransport(handler))
    manager = Procurement(store, api=api, clock=lambda: now[0], scheduler_enabled=True)
    payload = {'revision': 0, 'id': 'b' * 32, 'product_id': PRODUCT, 'quantity': 2,
               'trigger_price_cents': 150, 'expires_at': now[0] + 86400}
    return manager, payload, calls, controls, detail, now


def enable(manager, payload):
    value = manager.create(payload)
    return manager.action(payload['id'], 'enable', {'revision': value['revision'], 'accept_supplier_price': True})


def job(manager):
    return manager.view()['jobs'][0]


def test_draft_is_inert_and_purchase_is_once_with_private_delivery(setup):
    manager, payload, calls, *_ = setup
    value = manager.create(payload)
    manager.tick()
    assert calls == [] and job(manager)['status'] == 'draft'
    assert manager.create(payload)['revision'] == value['revision']  # response-loss retry
    assert manager.path.stat().st_mode & 0o777 == 0o600
    manager.action(payload['id'], 'enable', {'revision': value['revision'], 'accept_supplier_price': True})
    manager.tick()
    assert job(manager)['status'] == 'delivered'
    assert job(manager)['amount_cents'] == 250 and job(manager)['delivered_count'] == 2
    assert SECRET not in json.dumps(manager.view()) and 'keys' not in job(manager)
    assert calls[-1] == ('POST', '/v1/orders', 'lancoai-preorder-' + payload['id'])
    manager.tick()
    assert len([c for c in calls if c[0] == 'POST']) == 1
    result = manager.action(payload['id'], 'reveal', {'revision': manager.view()['revision']})
    assert result == {'keys': [SECRET, SECRET + '-2']}
    for action in ['enable', 'pause', 'cancel']:
        with pytest.raises(ValueError):
            manager.action(payload['id'], action, {'revision': manager.view()['revision'], **({'accept_supplier_price': True} if action == 'enable' else {})})


@pytest.mark.parametrize('field,value', [('quantity', True), ('quantity', 0), ('quantity', 101),
                                         ('trigger_price_cents', -1), ('trigger_price_cents', 1.2),
                                         ('product_id', 'https://localhost/'), ('product_id', []),
                                         ('expires_at', 1), ('id', '../private')])
def test_reject_invalid_money_identity_and_limits(setup, field, value):
    manager, payload, calls, *_ = setup
    with pytest.raises(ValueError):
        manager.create({**payload, field: value})
    assert not calls and not manager.path.exists()


@pytest.mark.parametrize('condition,expected', [('stock', 'waiting'), ('price', 'paused'),
                                              ('balance', 'paused'), ('activation', 'paused'),
                                              ('identity', 'paused'), ('custom', 'paused')])
def test_preflight_never_buys_when_condition_fails(setup, condition, expected):
    manager, payload, calls, controls, detail, _ = setup
    if condition == 'stock': detail['stock'] = 1
    if condition == 'price': detail['price'] = 2
    if condition == 'balance': controls['balance'] = 1
    if condition == 'activation': detail['requiresEmailActivation'] = True
    if condition == 'identity': detail['name'] = 'Unrelated product'
    if condition == 'custom': detail['delivery'] = None
    enable(manager, payload)
    manager.tick()
    assert job(manager)['status'] == expected
    assert not any(c[0] == 'POST' for c in calls)


def test_stock_recovery_expiry_pause_and_cancel(setup):
    manager, payload, calls, _, detail, now = setup
    detail['stock'] = 0
    enable(manager, payload)
    manager.tick()
    count = len(calls)
    manager.tick()
    assert len(calls) == count  # 60s backoff
    now[0] += 61
    detail['stock'] = 2
    manager.tick()
    assert job(manager)['status'] == 'delivered'


@pytest.mark.parametrize('action', ['pause', 'cancel', 'expire'])
def test_no_requests_after_pause_cancel_expiry(setup, action):
    manager, payload, calls, _, _, now = setup
    enable(manager, payload)
    if action == 'expire': now[0] = payload['expires_at']
    else: manager.action(payload['id'], action, {'revision': manager.view()['revision']})
    manager.tick()
    assert calls == []
    assert job(manager)['status'] in {'paused', 'cancelled', 'expired'}


def test_source_disable_and_delete_readd_do_not_resume(setup):
    manager, payload, calls, *_ = setup
    enable(manager, payload)
    state_store.save_json(manager.store.settings_path, {'revision': 1, 'telegram': '', 'skus': {},
                                                      'source_enabled': {'prodseller': False}})
    manager.tick()
    assert job(manager)['status'] == 'paused' and calls == []
    state_store.save_json(manager.store.settings_path, {'revision': 2, 'telegram': '', 'skus': {},
                   'source_lifecycle': {'prodseller': {'generation': 2, 'deleted': False}}})
    with pytest.raises(ValueError):
        manager.action(payload['id'], 'enable', {'revision': manager.view()['revision'], 'accept_supplier_price': True})
    manager.tick()
    assert calls == []


@pytest.mark.parametrize('failure', ['timeout', 400, 401, 402, 409, 429, 500, 'malformed'])
def test_uncertain_submission_is_never_retried(setup, failure):
    manager, payload, calls, controls, _, now = setup
    enable(manager, payload)
    if failure == 'timeout': controls['timeout'] = True
    elif failure == 'malformed': controls['result'] = {'deliveredKey': SECRET}
    else: controls['status'] = failure
    manager.tick()
    assert job(manager)['status'] == 'uncertain'
    now[0] += 3600
    manager.tick()
    assert len([c for c in calls if c[0] == 'POST']) == 1
    assert SECRET not in json.dumps(manager.view())
    with pytest.raises(ValueError):
        manager.action(payload['id'], 'enable', {'revision': manager.view()['revision'], 'accept_supplier_price': True})


def test_crash_after_durable_intent_never_retries(setup, monkeypatch):
    manager, payload, calls, *_ = setup
    enable(manager, payload)
    original = manager.api.buy
    def crash(job):
        assert manager._load()['jobs'][0]['status'] == 'submitting'
        original(job)
        raise SystemExit('simulated crash after charge')
    monkeypatch.setattr(manager.api, 'buy', crash)
    with pytest.raises(SystemExit): manager.tick()
    manager.tick()
    assert job(manager)['status'] == 'uncertain'
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_failed_intent_write_does_not_purchase(setup, monkeypatch):
    manager, payload, calls, *_ = setup
    enable(manager, payload)
    monkeypatch.setattr(manager, '_save', lambda state: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(OSError): manager.tick()
    assert not any(c[0] == 'POST' for c in calls)


def test_failed_result_write_leaves_durable_intent_not_repurchase(setup, monkeypatch):
    manager, payload, calls, *_ = setup
    enable(manager, payload)
    original = manager._save
    def save(state):
        if state['jobs'][0]['status'] == 'delivered':
            raise OSError('simulated full disk after purchase')
        return original(state)
    monkeypatch.setattr(manager, '_save', save)
    with pytest.raises(OSError): manager.tick()
    manager.tick()
    assert job(manager)['status'] == 'uncertain'
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_inconsistent_purchase_state_fails_closed(setup):
    manager, payload, calls, *_ = setup
    enable(manager, payload)
    value = manager._load()
    value['jobs'][0]['order_id'] = ORDER
    state_store.save_json(manager.path, value)
    with pytest.raises(state_store.InvalidJsonState): manager.tick()
    assert calls == []


def test_concurrent_workers_submit_only_once_and_settings_disable_waits(setup):
    manager, payload, calls, controls, *_ = setup
    enable(manager, payload)
    controls.update(block=True, entered=threading.Event(), release=threading.Event())
    other = Procurement(manager.store, api=manager.api, clock=manager.clock, scheduler_enabled=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(manager.tick)
        assert controls['entered'].wait(5)
        other.tick()
        with pytest.raises(state_store.LockTimeout):
            with state_store.file_lock(str(manager.store.settings_path) + '.lock', timeout=0):
                pytest.fail('settings lock must serialize disabling with charging')
        controls['release'].set()
        first.result(timeout=5)
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_paid_orders_poll_without_new_charge_even_after_deadline(setup):
    manager, payload, calls, controls, _, now = setup
    controls['result']['status'] = 'paid'
    enable(manager, payload)
    manager.tick()
    assert job(manager)['status'] == 'paid' and job(manager)['delivered_count'] == 0
    now[0] = payload['expires_at'] + 60
    controls['result']['status'] = 'delivered'
    controls['result'].pop('quantity')  # documented GET order schema
    manager.tick()
    assert job(manager)['status'] == 'delivered'
    assert calls[-1][1] == '/v1/orders/' + ORDER
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_supplier_repricing_is_visible_and_never_triggers_another_buy(setup):
    manager, payload, calls, controls, *_ = setup
    controls['result']['amount'] = 4
    enable(manager, payload)
    manager.tick()
    assert job(manager)['reason'] == 'price_changed' and job(manager)['amount_cents'] == 400
    manager.tick()
    assert len([c for c in calls if c[0] == 'POST']) == 1


def test_standby_and_revision_and_required_confirmation(setup):
    manager, payload, calls, *_ = setup
    manager.create(payload)
    with pytest.raises(Conflict): manager.action(payload['id'], 'enable', {'revision': 0, 'accept_supplier_price': True})
    with pytest.raises(ValueError): manager.action(payload['id'], 'enable', {'revision': 1, 'accept_supplier_price': False})
    manager.scheduler_enabled = False
    with pytest.raises(ValueError): manager.action(payload['id'], 'enable', {'revision': 1, 'accept_supplier_price': True})
    manager.tick()
    assert calls == []


def test_corrupt_state_fails_closed(setup):
    manager, _, calls, *_ = setup
    manager.path.write_text('{broken')
    with pytest.raises(state_store.InvalidJsonState): manager.tick()
    assert calls == []


def test_routes_authorization_csrf_and_no_public_delivery(setup, monkeypatch):
    manager, payload, calls, *_ = setup
    from web_api import shop_procurement_routes
    monkeypatch.setattr(shop_procurement_routes, 'Procurement', lambda *a, **k: manager)
    async def dispatch(fn, request): return fn(headers=request.headers, path=request.url.path)
    app = FastAPI()
    register_shop_routes(app, Sessions(), dispatch, store=manager.store)
    root = '/api/v1/shop/admin/procurement'
    with TestClient(app) as client:
        for path in [root, root + '/preorders', root + '/balance', root + '/preorders/' + payload['id'] + '/reveal']:
            method = client.get if path == root else client.post
            assert method(path).status_code == 401
            assert method(path, headers={'Cookie': 'sid=user'}).status_code == 403
            if path != root:
                assert method(path, headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'}, json={}).status_code == 403
        assert client.get(root, headers=HEADERS).headers['cache-control'] == 'no-store'
        assert calls == []
        assert client.post(root + '/preorders', json=payload, headers=HEADERS).status_code == 200
        assert client.post(root + '/preorders/' + payload['id'] + '/enable',
                           json={'revision': 1, 'accept_supplier_price': True}, headers=HEADERS).status_code == 200
        manager.tick()
        for path in [root, '/api/v1/shop/admin', '/api/v1/shop/catalog']:
            assert SECRET not in client.get(path, headers=HEADERS).text
        reveal = client.post(root + '/preorders/' + payload['id'] + '/reveal', headers=HEADERS,
                             json={'revision': manager.view()['revision']})
        assert reveal.json()['keys'][0] == SECRET and reveal.headers['cache-control'] == 'no-store'
        assert client.get(root + '/preorders/' + payload['id'] + '/reveal', headers=HEADERS).status_code == 405


def test_balance_refresh_is_explicit_and_source_disable_blocks_it(setup):
    manager, _, calls, controls, *_ = setup
    assert manager.view()['balance_cents'] is None and not calls
    assert manager.balance()['balance_cents'] == 2000
    controls['balance'] = 0
    assert manager.balance()['balance_cents'] == 0
    state_store.save_json(manager.store.settings_path, {'revision': 1, 'telegram': '', 'skus': {},
                                                      'source_enabled': {'prodseller': False}})
    count = len(calls)
    assert manager.balance()['balance_error'] == 'source_disabled'
    assert len(calls) == count


@pytest.mark.parametrize('value', [True, '1', Decimal('NaN'), Decimal('1.001'), -1])
def test_supplier_amount_validation(value):
    from web_api.shop_procurement_api import cents
    with pytest.raises(SupplierError): cents(value)


def seeded_store(tmp_path, *, in_stock=False):
    """A store whose ProdSeller catalog cache already holds both products' quotes."""
    from web_api.shop_prodseller_source import normalize_products
    rows = normalize_products({'products': [
        {'id': PRODUCT, 'name': 'Gemini Pro 18Months (link)', 'price': Decimal('0.85'), 'inStock': in_stock,
         'requiresEmailActivation': False, 'delivery': {'type': 'instant'}},
        {'id': MUSE, 'name': 'Muse AI', 'price': Decimal('1.00'), 'inStock': False,
         'requiresEmailActivation': False, 'delivery': {'type': 'instant'}},
    ]})

    async def empty():
        return []

    async def prodseller():
        return rows

    store = ShopStore(tmp_path / 'source.json', fetcher=empty, prodseller_fetcher=prodseller, clock=lambda: 1800000000)
    import asyncio
    asyncio.run(store.refresh(manual=True))
    return store


def test_overview_shows_cached_prices_even_out_of_stock_without_contacting_supplier(setup, tmp_path):
    manager, _, calls, *_ = setup
    manager.store = seeded_store(tmp_path / 'seeded')
    quotes = {item['id']: item['quote'] for item in manager.view()['products']}
    # The cache keeps CNY at the fixed rate; the page shows the supplier's USDT quote.
    assert quotes[PRODUCT] == {'price_cents': 85, 'stock': None, 'in_stock': False, 'quoted_at': 1800000000}
    assert quotes[MUSE] == {'price_cents': 100, 'stock': None, 'in_stock': False, 'quoted_at': 1800000000}
    assert calls == []


def test_overview_without_catalog_has_no_quotes(setup):
    manager, *_ = setup
    assert [item['quote'] for item in manager.view()['products']] == [None] * len(SELECTED)


def test_explicit_refresh_reads_live_price_and_stock_in_memory_only(setup, tmp_path):
    manager, _, calls, controls, detail, now = setup
    manager.store = seeded_store(tmp_path / 'seeded')
    now[0] = 1800000300
    detail.update(stock=3, price=0.90)
    controls['muse_down'] = True
    value = manager.balance()
    quotes = {item['id']: item['quote'] for item in value['products']}
    assert quotes[PRODUCT] == {'price_cents': 90, 'stock': 3, 'in_stock': True, 'quoted_at': 1800000300}
    # A product the supplier could not quote keeps the catalog's price.
    assert quotes[MUSE] == {'price_cents': 100, 'stock': None, 'in_stock': False, 'quoted_at': 1800000000}
    assert value['balance_cents'] == 2000
    assert [path for method, path, _ in calls if method == 'GET'] == [
        '/v1/products/' + PRODUCT, '/v1/products/' + MUSE, '/v1/products/' + DUOLINGO, '/v1/balance']
    assert not [call for call in calls if call[0] != 'GET']
    # The procurement file keeps its format, so an older release can still read it.
    saved = json.loads(manager.path.read_text())
    assert set(saved) == {'schema', 'revision', 'jobs', 'balance_cents', 'balance_at', 'balance_error'}
    assert manager.view()['products'][0]['quote']['stock'] == 3


def test_disabled_source_refresh_reads_no_quotes(setup, tmp_path):
    manager, _, calls, *_ = setup
    state_store.save_json(manager.store.settings_path, {'revision': 1, 'telegram': '', 'skus': {},
                                                      'source_enabled': {'prodseller': False}})
    manager.balance()
    assert calls == []


@pytest.mark.parametrize('amount,expected', [
    (0.3699999999999994, 37), (0.3700000000000006, 37),
    (0.0099999999999994, 1), (0.0000000000000006, 0), (0, 0),
])
def test_balance_float_residue_rounds_to_cent_without_purchasing(setup, amount, expected):
    manager, _, calls, controls, _, _ = setup
    controls['balance'] = amount
    value = manager.balance()
    assert value['balance_cents'] == expected
    assert value['balance_error'] == ''
    assert value['jobs'] == []
    assert all(method == 'GET' for method, _, _ in calls)


def test_invalid_balance_keeps_last_success_then_valid_refresh_recovers(setup):
    manager, _, calls, controls, _, now = setup
    first = manager.balance()
    now[0] += 60
    controls['balance'] = 1.001  # Meaningful sub-cent amount is still rejected.
    failed = manager.balance()
    assert failed['balance_error'] == 'invalid_response'
    assert failed['balance_cents'] == first['balance_cents']
    assert failed['balance_at'] == first['balance_at']
    controls['balance'] = 0.3699999999999994
    recovered = manager.balance()
    assert recovered['balance_error'] == '' and recovered['balance_cents'] == 37
    assert recovered['balance_at'] == now[0] and recovered['jobs'] == []
    assert all(method == 'GET' for method, _, _ in calls)
