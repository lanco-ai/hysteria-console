"""One-shot preorders with durable intent, explicit activation and no blind retries."""

import copy
import re
import time
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import state_store

from .shop_procurement_api import ProcurementAPI, SupplierError, receipt
from .shop_prodseller_source import PRODUCT_BASE as SOURCE_BASE
from .shop_prodseller_source import SELECTED, USD_CNY
from .shop_store import Conflict, is_source_enabled, source_generation

STATUSES = {'draft', 'waiting', 'paused', 'submitting', 'uncertain', 'pending', 'paid',
            'delivered', 'failed', 'cancelled', 'expired'}
REASONS = {'', 'stock_low', 'price_high', 'balance_low', 'source_disabled', 'source_changed',
           'supplier_unavailable', 'credentials_unavailable', 'product_changed', 'rate_limited',
           'stock_changed', 'invalid_response', 'result_uncertain', 'price_changed', 'manual'}
TERMINAL = {'delivered', 'failed', 'cancelled', 'expired'}


def integer(value, lo, hi):
    return type(value) is int and lo <= value <= hi


class Procurement:
    def __init__(self, store, *, api=None, clock=time.time, scheduler_enabled=False):
        self.store, self.api, self.clock = store, api or ProcurementAPI(), clock
        self.scheduler_enabled = scheduler_enabled
        self.path = Path(store.path).with_name('procurement.json')
        self.lock = self.path.with_suffix('.lock')
        # Prices read on the administrator's explicit refresh; memory only, so the
        # procurement file keeps its format and an older release can still read it.
        self._live_quotes = {}

    def _load(self):
        value = state_store.load_json_strict(self.path, {
            'schema': 1, 'revision': 0, 'jobs': [], 'balance_cents': None,
            'balance_at': None, 'balance_error': '',
        })
        try:
            if (set(value) != {'schema', 'revision', 'jobs', 'balance_cents', 'balance_at', 'balance_error'}
                    or value['schema'] != 1 or not integer(value['revision'], 0, 10**12)
                    or not isinstance(value['jobs'], list) or len(value['jobs']) > 500
                    or value['balance_error'] not in REASONS
                    or (value['balance_cents'] is not None and not integer(value['balance_cents'], 0, 10**9))
                    or (value['balance_at'] is not None and not integer(value['balance_at'], 0, 253402300799))):
                raise ValueError
            seen = set()
            for job in value['jobs']:
                if (not isinstance(job, dict) or set(job) != {
                        'id', 'product_id', 'quantity', 'trigger_price_cents', 'expires_at',
                        'created_at', 'updated_at', 'checked_at', 'status', 'reason', 'generation',
                        'order_id', 'amount_cents', 'keys', 'attempted_at'}
                        or not isinstance(job['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', job['id'])
                        or job['id'] in seen or job['product_id'] not in SELECTED
                        or not integer(job['quantity'], 1, 100)
                        or not integer(job['trigger_price_cents'], 1, 1_000_000)
                        or job['status'] not in STATUSES or job['reason'] not in REASONS
                        or any(not integer(job[k], 0, 253402300799) for k in (
                            'expires_at', 'created_at', 'updated_at', 'checked_at', 'attempted_at', 'generation'))
                        or (job['order_id'] is not None and (not isinstance(job['order_id'], str)
                            or not re.fullmatch(r'[0-9a-f]{24}', job['order_id'])))
                        or (job['amount_cents'] is not None and not integer(job['amount_cents'], 0, 10**9))
                        or not isinstance(job['keys'], list) or len(job['keys']) > job['quantity']
                        or any(not isinstance(k, str) or not 0 < len(k) <= 16384 for k in job['keys'])
                        or (job['status'] in {'draft', 'waiting', 'paused', 'cancelled', 'expired'}
                            and (job['attempted_at'] or job['order_id'] is not None
                                 or job['amount_cents'] is not None or job['keys']))
                        or (job['status'] in {'submitting', 'uncertain', 'pending', 'paid', 'delivered', 'failed'}
                            and not job['attempted_at'])
                        or (job['status'] in {'pending', 'paid', 'delivered', 'failed'}
                            and (not job['order_id'] or job['amount_cents'] is None))
                        or (job['status'] != 'delivered' and job['keys'])
                        or (job['status'] == 'delivered' and len(job['keys']) != job['quantity'])):
                    raise ValueError
                seen.add(job['id'])
        except (ValueError, TypeError, KeyError) as exc:
            raise state_store.InvalidJsonState('invalid procurement state') from exc
        return value

    def _save(self, state):
        state['revision'] += 1
        state_store.save_json(self.path, state)  # mkstemp + fsync; mode 0600

    def _enabled(self):
        return is_source_enabled(self.store._settings(), 'prodseller') and self.store.prodseller_fetcher is not None

    def _catalog_quotes(self):
        # The catalog refresh already reads both prices, out of stock or not. Its cost is
        # the USD quote at the fixed rate, so dividing recovers the quote to the cent.
        try:
            source = self.store._source('prodseller')
        except (state_store.StateStoreError, OSError, ValueError):
            return {}
        if source['last_success'] is None:
            return {}
        rows = {row['product_id']: row for row in source['items']}
        quotes = {}
        for supplier_id, (number, _) in SELECTED.items():
            row = rows.get(str(SOURCE_BASE + number))
            if row is not None:
                usd = (Decimal(row['cost_cents']) / USD_CNY).quantize(Decimal(1), rounding=ROUND_HALF_UP)
                quotes[supplier_id] = {'price_cents': int(usd), 'stock': None, 'in_stock': row['available'],
                                       'quoted_at': int(source['last_success'])}
        return quotes

    def _quotes(self):
        quotes = self._catalog_quotes()
        for supplier_id, live in dict(self._live_quotes).items():
            if supplier_id not in quotes or live['quoted_at'] >= quotes[supplier_id]['quoted_at']:
                quotes[supplier_id] = live
        return quotes

    def view(self):
        state = self._load()
        quotes = self._quotes()
        jobs = [{k: v for k, v in job.items() if k != 'keys'} | {
            'title': SELECTED[job['product_id']][1], 'delivered_count': len(job['keys']),
        } for job in reversed(state['jobs'])]
        return {**state, 'jobs': jobs, 'enabled': self._enabled(),
                'scheduler_enabled': self.scheduler_enabled,
                'products': [{'id': key, 'title': item[1], 'quote': quotes.get(key)}
                             for key, item in SELECTED.items()]}

    @staticmethod
    def _revision(payload, state):
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise ValueError('invalid request')
        if payload['revision'] != state['revision']:
            raise Conflict()

    def create(self, payload):
        fields = {'revision', 'id', 'product_id', 'quantity', 'trigger_price_cents', 'expires_at'}
        now = int(self.clock())
        if (not isinstance(payload, dict) or set(payload) != fields
                or not isinstance(payload['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', payload['id'])
                or not isinstance(payload['product_id'], str) or payload['product_id'] not in SELECTED
                or not integer(payload['quantity'], 1, 100)
                or not integer(payload['trigger_price_cents'], 1, 1_000_000)
                or not integer(payload['expires_at'], now + 60, now + 90 * 86400)):
            raise ValueError('invalid preorder')
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            old = next((j for j in state['jobs'] if j['id'] == payload['id']), None)
            if old:
                if any(old[k] != payload[k] for k in fields - {'revision'}):
                    raise Conflict()
                return self.view()
            self._revision(payload, state)
            if len(state['jobs']) >= 500 or sum(j['status'] not in TERMINAL for j in state['jobs']) >= 10:
                raise ValueError('too many preorders')
            settings = self.store._settings()
            state['jobs'].append({k: v for k, v in payload.items() if k != 'revision'} | {
                'status': 'draft', 'reason': '', 'created_at': now, 'updated_at': now, 'checked_at': 0,
                'attempted_at': 0, 'generation': source_generation(settings, 'prodseller'),
                'order_id': None, 'amount_cents': None, 'keys': [],
            })
            self._save(state)
        return self.view()

    def action(self, job_id, action, payload):
        if not isinstance(payload, dict) or set(payload) != ({'revision', 'accept_supplier_price'} if action == 'enable' else {'revision'}):
            raise ValueError('invalid action')
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            self._revision(payload, state)
            job = next((j for j in state['jobs'] if j['id'] == job_id), None)
            if job is None:
                raise ValueError('unknown preorder')
            now = int(self.clock())
            if action == 'reveal':
                if job['status'] != 'delivered':
                    raise ValueError('not delivered')
                return {'keys': list(job['keys'])}
            if action == 'enable':
                settings = self.store._settings()
                if (payload['accept_supplier_price'] is not True or not self.scheduler_enabled
                        or not self._enabled() or job['status'] not in {'draft', 'paused'}
                        or job['attempted_at'] or job['expires_at'] <= now
                        or job['generation'] != source_generation(settings, 'prodseller')):
                    raise ValueError('cannot enable')
                job.update(status='waiting', reason='', checked_at=0)
            elif action in {'pause', 'cancel'}:
                if job['status'] not in {'draft', 'waiting', 'paused'} or job['attempted_at']:
                    raise ValueError('already submitted')
                job.update(status='paused' if action == 'pause' else 'cancelled', reason='manual')
            else:
                raise ValueError('invalid action')
            job['updated_at'] = now
            self._save(state)
        return self.view()

    def balance(self):
        # Explicit admin refresh of the balance and both products' price and stock (GET only).
        # The state-only GET never contacts a supplier.
        if self._enabled():
            now, quotes = int(self.clock()), {}
            for supplier_id in SELECTED:
                try:
                    product = self.api.product(supplier_id)
                except SupplierError:
                    continue  # The catalog's price stays shown.
                quotes[supplier_id] = {'price_cents': product['price_cents'], 'stock': product['stock'],
                                       'in_stock': product['stock'] > 0, 'quoted_at': now}
            self._live_quotes = quotes
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            if not self._enabled():
                state['balance_error'] = 'source_disabled'
            else:
                try:
                    state['balance_cents'] = self.api.balance()
                    state['balance_at'] = int(self.clock())
                    state['balance_error'] = ''
                except SupplierError as exc:
                    state['balance_error'] = exc.reason
            self._save(state)
        return self.view()

    def tick(self):
        if not self.scheduler_enabled:
            return
        # Lock is also held across the provider call: concurrent workers cannot submit twice.
        try:
            with state_store.file_lock(self.lock, timeout=0):
                state = self._load()
                for job in state['jobs']:
                    self._process(state, job)
        except state_store.LockTimeout:
            return

    def _process(self, state, job):
        now = int(self.clock())
        if job['status'] == 'submitting':
            job.update(status='uncertain', reason='result_uncertain', updated_at=now)
            self._save(state)
            return
        if job['status'] not in {'waiting', 'pending', 'paid'}:
            return
        if job['status'] == 'waiting' and job['expires_at'] <= now:
            job.update(status='expired', updated_at=now)
            self._save(state)
            return
        if job['checked_at'] and now - job['checked_at'] < 60:
            return
        job.update(checked_at=now, updated_at=now)
        settings = self.store._settings()
        if not self._enabled():
            if job['status'] == 'waiting':
                job['status'] = 'paused'
            job['reason'] = 'source_disabled'
            self._save(state)
            return
        if job['status'] in {'paid', 'pending'}:
            try:
                result = receipt(self.api.order(job['order_id']), job, polling=True)
                self._accept(job, result)
            except SupplierError as exc:
                job['reason'] = exc.reason  # GET can safely be polled again.
            self._save(state)
            return
        if job['generation'] != source_generation(settings, 'prodseller'):
            job.update(status='paused', reason='source_changed')
            self._save(state)
            return
        try:
            product = self.api.product(job['product_id'])
            if product['stock'] < job['quantity']:
                job['reason'] = 'stock_low'
            elif product['price_cents'] > job['trigger_price_cents']:
                job.update(status='paused', reason='price_high')
            else:
                balance = self.api.balance()
                state.update(balance_cents=balance, balance_at=now, balance_error='')
                if balance < product['price_cents'] * job['quantity']:
                    job.update(status='paused', reason='balance_low')
                else:
                    # Settings writes use the same lock. A completed disable/delete wins.
                    with state_store.file_lock(str(self.store.settings_path) + '.lock', timeout=2):
                        settings = self.store._settings()
                        if not self._enabled() or source_generation(settings, 'prodseller') != job['generation']:
                            job.update(status='paused', reason='source_changed')
                        elif int(self.clock()) >= job['expires_at']:
                            job['status'] = 'expired'
                        else:
                            job.update(status='submitting', reason='', attempted_at=int(self.clock()))
                            state.update(balance_cents=None, balance_at=None)
                            self._save(state)  # Must be durable BEFORE the first possible charge.
                            try:
                                result = receipt(self.api.buy(copy.deepcopy(job)), job)
                                self._accept(job, result)
                            except SupplierError:
                                # Even a 4xx reply is not evidence that retrying later is safe.
                                job.update(status='uncertain', reason='result_uncertain')
        except SupplierError as exc:
            job['reason'] = exc.reason
            if exc.reason in {'credentials_unavailable', 'product_changed', 'invalid_response'}:
                job['status'] = 'paused'
        self._save(state)

    @staticmethod
    def _accept(job, result):
        job.update(result)
        job['reason'] = ('price_changed' if result['amount_cents'] >
                         job['trigger_price_cents'] * job['quantity'] else '')
