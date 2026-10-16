"""The merchant's own stock for supplier products: one line per unit (an account, a link).

Lines never leave the server in a list, the catalog or a log. Counts drive the storefront;
the merchant reveals a line explicitly, one at a time, and a delivery takes the oldest unit.
"""

import re
import secrets
import time
import unicodedata
from pathlib import Path

import state_store

from .shop_store import Conflict, category_of, provider_of, validate_items

MAX_ITEMS = 2000
MAX_ADD = 500
MAX_LINE = 2000
MAX_NOTE = 60
SKU = re.compile(r'[1-9]\d{0,15}:[1-9]\d{0,15}')
TOKEN = re.compile(r'[0-9a-f]{32}')
FIELDS = {'id', 'sku', 'line', 'added_at', 'delivered_at', 'delivery', 'note'}
CATALOG_FIELDS = {'key', 'product_id', 'title', 'public_title', 'label', 'source_url',
                  'cost_cents', 'available', 'quantity', 'sales'}


def _integer(value, lo, hi):
    return type(value) is int and lo <= value <= hi


def _text(value, limit, *, empty):
    return (isinstance(value, str) and (empty or value) and len(value) <= limit
            and value == value.strip()
            and not any(unicodedata.category(char) in {'Cc', 'Cf', 'Cs', 'Zl', 'Zp'} for char in value))


class ShopInventory:
    def __init__(self, path, clock=time.time):
        self.path = Path(path)
        self.lock = str(self.path) + '.lock'
        # Separate, non-secret metadata keeps inventory.json readable by older releases.
        self.catalog_path = self.path.with_name(self.path.stem + '-catalog.json')
        self.clock = clock

    def catalog(self):
        value = state_store.load_json_strict(self.catalog_path, {})
        try:
            if not isinstance(value, dict) or len(value) > MAX_ITEMS:
                raise ValueError
            for key, row in value.items():
                validate_items([row], provider_of(row['product_id']))
                if key != row['key'] or category_of(provider_of(row['product_id']), row['public_title'], row['product_id']) not in {'Gemini', 'Muse AI'}:
                    raise ValueError
        except (ValueError, TypeError, KeyError) as exc:
            raise state_store.InvalidJsonState('invalid stock catalog') from exc
        return value

    def remember(self, rows):
        """Snapshot validated SKU metadata before import or replacing a supplier catalog.

        Never called by GET. Old inventory without metadata can still be managed by SKU.
        Keep identities independently of stock counts: a concurrent import may
        already have saved its metadata but not yet committed its first unit.
        """
        rows = [{key: row[key] for key in CATALOG_FIELDS} for row in rows]
        if not rows:
            return
        for row in rows:
            validate_items([row], provider_of(row['product_id']))
            if category_of(provider_of(row['product_id']), row['public_title'], row['product_id']) not in {'Gemini', 'Muse AI'}:
                raise ValueError('invalid stock category')
        with state_store.file_lock(self.lock, timeout=2):
            old = self.catalog()
            saved = dict(old)
            saved.update({row['key']: row for row in rows})
            if len(saved) > MAX_ITEMS:
                raise ValueError('too much stock metadata')
            if saved != old:
                state_store.save_json(self.catalog_path, saved)

    @staticmethod
    def _empty():
        return {'schema': 1, 'revision': 0, 'items': []}

    def _load(self):
        value = state_store.load_json_strict(self.path, self._empty())
        try:
            if (set(value) != {'schema', 'revision', 'items'} or value['schema'] != 1
                    or not _integer(value['revision'], 0, 10**12) or not isinstance(value['items'], list)
                    or len(value['items']) > MAX_ITEMS):
                raise ValueError
            seen, deliveries = set(), set()
            for item in value['items']:
                if (not isinstance(item, dict) or set(item) != FIELDS
                        or not isinstance(item['id'], str) or not TOKEN.fullmatch(item['id']) or item['id'] in seen
                        or not isinstance(item['sku'], str) or not SKU.fullmatch(item['sku'])
                        or not _text(item['line'], MAX_LINE, empty=False)
                        or not _integer(item['added_at'], 1, 253402300799)
                        or not _integer(item['delivered_at'], 0, 253402300799)
                        or not isinstance(item['delivery'], str)
                        or (item['delivery'] and (not TOKEN.fullmatch(item['delivery']) or item['delivery'] in deliveries))
                        or (item['delivered_at'] == 0) != (item['delivery'] == '')
                        or not _text(item['note'], MAX_NOTE, empty=True)
                        or (item['delivered_at'] == 0 and item['note'])):
                    raise ValueError
                seen.add(item['id'])
                if item['delivery']:
                    deliveries.add(item['delivery'])
        except (ValueError, TypeError, KeyError) as exc:
            raise state_store.InvalidJsonState('invalid shop inventory') from exc
        return value

    def _save(self, state):
        state['revision'] += 1
        state_store.save_json(self.path, state)  # mkstemp + fsync; mode 0600

    @staticmethod
    def _revision(payload, state):
        if not isinstance(payload, dict) or type(payload.get('revision')) is not int:
            raise ValueError('invalid request')
        if payload['revision'] != state['revision']:
            raise Conflict()

    def counts(self):
        """Units in stock and delivered per SKU; an unreadable file counts as no stock."""
        try:
            items = self._load()['items']
        except (state_store.StateStoreError, OSError, ValueError):
            return {}
        counts = {}
        for item in items:
            entry = counts.setdefault(item['sku'], {'stock': 0, 'delivered': 0})
            entry['delivered' if item['delivered_at'] else 'stock'] += 1
        return counts

    def view(self):
        state = self._load()
        skus = {}
        for item in state['items']:
            entry = skus.setdefault(item['sku'], {'stock': [], 'delivered': []})
            meta = {key: item[key] for key in ('id', 'added_at', 'delivered_at', 'note')}
            entry['delivered' if item['delivered_at'] else 'stock'].append(meta)
        for entry in skus.values():
            # Newest delivery first; units go oldest first, so the list's reverse breaks ties.
            entry['delivered'] = sorted(reversed(entry['delivered']), key=lambda meta: -meta['delivered_at'])
        return {'revision': state['revision'], 'skus': skus}

    def add(self, payload, allowed):
        if (not isinstance(payload, dict) or set(payload) != {'revision', 'sku', 'lines'}
                or not isinstance(payload['sku'], str) or payload['sku'] not in allowed
                or not isinstance(payload['lines'], list) or not 0 < len(payload['lines']) <= MAX_ADD
                or not all(isinstance(line, str) for line in payload['lines'])):
            raise ValueError('invalid stock')
        lines = [line.strip() for line in payload['lines'] if line.strip()]
        if not lines or not all(_text(line, MAX_LINE, empty=False) for line in lines):
            raise ValueError('invalid stock line')
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            self._revision(payload, state)
            known = {item['line'] for item in state['items'] if item['sku'] == payload['sku']}
            fresh = []
            for line in lines:
                if line not in known:
                    known.add(line)
                    fresh.append(line)
            if len(state['items']) + len(fresh) > MAX_ITEMS:
                raise ValueError('too much stock')
            now = int(self.clock())
            state['items'].extend({'id': secrets.token_hex(16), 'sku': payload['sku'], 'line': line,
                                   'added_at': now, 'delivered_at': 0, 'delivery': '', 'note': ''}
                                  for line in fresh)
            if fresh:
                self._save(state)
        return {**self.view(), 'added': len(fresh), 'skipped': len(lines) - len(fresh)}

    def deliver(self, payload, allowed):
        # The request token makes a repeated click return the same unit, never a second one.
        if (not isinstance(payload, dict) or set(payload) != {'sku', 'request', 'note'}
                or not isinstance(payload['sku'], str) or payload['sku'] not in allowed
                or not isinstance(payload['request'], str) or not TOKEN.fullmatch(payload['request'])
                or not isinstance(payload['note'], str) or not _text(payload['note'].strip(), MAX_NOTE, empty=True)):
            raise ValueError('invalid delivery')
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            item = next((entry for entry in state['items'] if entry['delivery'] == payload['request']), None)
            if item is None:
                item = next((entry for entry in state['items']
                             if entry['sku'] == payload['sku'] and not entry['delivered_at']), None)
                if item is None:
                    raise ValueError('out of stock')
                item.update(delivered_at=int(self.clock()), delivery=payload['request'], note=payload['note'].strip())
                self._save(state)
            elif item['sku'] != payload['sku']:
                raise ValueError('invalid delivery')
            delivered = {key: item[key] for key in ('id', 'line', 'delivered_at', 'note')}
        return {**self.view(), 'delivered': delivered}

    def action(self, item_id, action, payload):
        if not isinstance(payload, dict) or set(payload) != {'revision'} or not TOKEN.fullmatch(item_id):
            raise ValueError('invalid request')
        with state_store.file_lock(self.lock, timeout=2):
            state = self._load()
            item = next((entry for entry in state['items'] if entry['id'] == item_id), None)
            if item is None:
                raise ValueError('unknown stock')
            if action == 'reveal':
                return {'line': item['line']}
            self._revision(payload, state)
            if action == 'remove' and not item['delivered_at']:
                state['items'].remove(item)
            elif action == 'restock' and item['delivered_at']:
                item.update(delivered_at=0, delivery='', note='')
            else:
                raise ValueError('invalid action')
            self._save(state)
        return self.view()
