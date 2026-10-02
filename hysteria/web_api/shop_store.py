"""Private source snapshot and separately revisioned merchant settings."""

import asyncio
import math
import re
import time
from pathlib import Path

import state_store

from .shop_source import MAX_CENTS, MAX_ROWS, ORIGIN, fetch_catalog

TTL = 900
BACKOFF = 900
MANUAL_COOLDOWN = 60


class Conflict(ValueError):
    pass


def _integer(value, minimum, maximum):
    return type(value) is int and minimum <= value <= maximum


def validate_settings(value):
    if (
        not isinstance(value, dict)
        or set(value) != {'revision', 'telegram', 'skus'}
        or not _integer(value['revision'], 0, 2**53 - 2)
        or not isinstance(value['telegram'], str)
        or (
            value['telegram'] and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', value['telegram'])
        )
        or not isinstance(value['skus'], dict)
        or len(value['skus']) > MAX_ROWS
    ):
        raise ValueError('invalid settings')
    for key, item in value['skus'].items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}:[1-9]\d{0,15}', key)
            or not isinstance(item, dict)
            or set(item) != {'price_cents', 'published'}
            or type(item['published']) is not bool
            or (item['price_cents'] is not None and not _integer(item['price_cents'], 1, MAX_CENTS))
            or (item['published'] and item['price_cents'] is None)
        ):
            raise ValueError('invalid SKU settings')
    return value


def validate_items(items):
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
        seen.add(key)
        if (
            not isinstance(row['title'], str)
            or not 0 < len(row['title']) <= 2000
            or not isinstance(row['source_url'], str)
            or len(row['source_url']) > 3000
            or not row['source_url'].startswith(ORIGIN + '/products/')
            or not isinstance(row['public_title'], str)
            or not re.fullmatch(
                r'(ChatGPT (Plus|Go|Pro(?: \d{1,6})?)|数字商品)', row['public_title']
            )
            or not isinstance(row['label'], str)
            or not re.fullmatch(
                r'(标准规格|可新开|续费卡密不可新开|一卡二付|一卡一付|规格 [1-9]\d{0,15})',
                row['label'],
            )
        ):
            raise ValueError('invalid source text')
    return items


class ShopStore:
    def __init__(
        self,
        path='/root/hysteria/state/shop/source.json',
        *,
        fetcher=fetch_catalog,
        clock=time.time,
    ):
        self.path = Path(path)
        self.settings_path = self.path.with_name(self.path.stem + '-merchant.json')
        self.fetcher, self.clock = fetcher, clock

    def _source(self):
        value = state_store.load_json_strict(
            self.path,
            {
                'items': [],
                'last_success': None,
                'error': None,
                'retry_after': 0,
                'manual_after': 0,
            },
        )
        try:
            if set(value) != {'items', 'last_success', 'error', 'retry_after', 'manual_after'}:
                raise ValueError('invalid snapshot')
            validate_items(value['items'])
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

    def _admin(self):
        source, settings, now = self._source(), self._settings(), self.clock()
        stale = (
            source['error'] is not None
            or source['last_success'] is None
            or not 0 <= now - source['last_success'] < TTL
        )
        return {
            **settings,
            **source,
            'is_stale': stale,
            'retry_after_seconds': max(0, math.ceil(source['retry_after'] - now)),
            'cooldown_seconds': max(0, math.ceil(source['manual_after'] - now)),
        }

    async def admin(self):
        return await asyncio.to_thread(self._admin)

    async def public(self):
        state = await self.admin()
        groups = {}
        for row in state['items']:
            merchant = state['skus'].get(row['key'], {})
            if not merchant.get('published'):
                continue
            group = groups.setdefault(
                row['product_id'],
                {
                    'id': row['product_id'],
                    'title': row['public_title'],
                    'category': 'GPT',
                    'variants': [],
                },
            )
            group['variants'].append(
                {
                    'id': row['key'],
                    'label': row['label'],
                    'price_cents': merchant['price_cents'],
                    'available': row['available'] and not state['is_stale'],
                    'quantity': None,
                    'sales': None,
                }
            )
        return {
            'currency': 'CNY',
            'telegram': state['telegram'],
            'products': list(groups.values()),
            'status': ('unavailable' if state['last_success'] is None else 'stale')
            if state['is_stale']
            else 'ready',
            'updated_at': state['last_success'],
        }

    def _update(self, payload):
        validate_settings(payload)
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            current = self._settings()
            if payload['revision'] != current['revision']:
                raise Conflict('settings changed; reload before saving')
            known = {row['key'] for row in self._source()['items']} | set(current['skus'])
            if set(payload['skus']) - known:
                raise ValueError('unknown SKU')
            state_store.save_json(
                self.settings_path, {**payload, 'revision': current['revision'] + 1}
            )
        return self._admin()

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
            current = await self.admin()
            if (
                current['retry_after_seconds']
                or (manual and current['cooldown_seconds'])
                or (not manual and not current['is_stale'])
            ):
                return current
            source = await asyncio.to_thread(self._source)
            source['manual_after'] = self.clock() + MANUAL_COOLDOWN
            # Persist cooldown before network so cancellation/crashes cannot busy-loop.
            source['retry_after'] = self.clock() + BACKOFF
            await self._finish_thread(state_store.save_json, self.path, source)
            try:
                async with asyncio.timeout(30):
                    items = await self.fetcher()
                validate_items(items)
                source.update(items=items, last_success=self.clock(), error=None, retry_after=0)
            except Exception:
                source.update(error='source_unavailable', retry_after=self.clock() + BACKOFF)
            await self._finish_thread(state_store.save_json, self.path, source)
            return await self.admin()
        finally:
            lock.__exit__(None, None, None)
