"""Private source snapshot and separately revisioned merchant settings."""

import asyncio
import json
import math
import re
import time
from pathlib import Path

import state_store

from .shop_anli_source import ORIGIN as ANLI_ORIGIN
from .shop_anli_source import PRODUCT_BASE, SELECTED
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
        or not {'revision', 'telegram', 'skus'} <= set(value)
        or set(value) - {'revision', 'telegram', 'skus', 'products'}
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
    metadata = value.get('products', {})
    if not isinstance(metadata, dict) or len(metadata) > MAX_ROWS:
        raise ValueError('invalid product copy')
    for key, item in metadata.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r'[1-9]\d{0,15}', key)
            or not isinstance(item, dict)
            or set(item) != {'description', 'after_sales'}
            or any(not isinstance(text, str) or len(text) > 4000 for text in item.values())
        ):
            raise ValueError('invalid product copy')
    # Match the route body limit, including preserved metadata from legacy clients.
    if len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) > 65536:
        raise ValueError('settings too large')
    return value


def validate_items(items, provider='gpt'):
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
        product_number = int(row['product_id'])
        if provider == 'gpt':
            if product_number >= PRODUCT_BASE:
                raise ValueError('reserved product namespace')
        elif provider == 'anli':
            external = product_number - PRODUCT_BASE
            if (
                external not in SELECTED
                or key != f'{product_number}:1'
                or row['source_url'] != f'{ANLI_ORIGIN}/item/{external}'
                or row['public_title'] != SELECTED[external]
                or row['label'] != '标准规格'
            ):
                raise ValueError('invalid secondary source identity')
        else:
            raise ValueError('unknown source')
        seen.add(key)
        if (
            not isinstance(row['title'], str)
            or not 0 < len(row['title']) <= 2000
            or not isinstance(row['source_url'], str)
            or len(row['source_url']) > 3000
            or (provider == 'gpt' and not row['source_url'].startswith(ORIGIN + '/products/'))
            or not isinstance(row['public_title'], str)
            or (
                provider == 'gpt'
                and not re.fullmatch(
                    r'(ChatGPT (Plus|Go|Pro(?: \d{1,6})?)|数字商品)', row['public_title']
                )
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
        secondary_fetcher=None,
        clock=time.time,
    ):
        self.path = Path(path)
        self.settings_path = self.path.with_name(self.path.stem + '-merchant.json')
        self.fetcher, self.clock = fetcher, clock
        self.secondary_fetcher = secondary_fetcher
        self.secondary_path = self.path.with_name(self.path.stem + '-anli.json')

    def _feed_ids(self):
        return ('gpt', 'anli') if self.secondary_fetcher is not None else ('gpt',)

    @staticmethod
    def _empty_source():
        return {
            'items': [],
            'last_success': None,
            'error': None,
            'retry_after': 0,
            'manual_after': 0,
        }

    def _source(self, provider='gpt'):
        value = state_store.load_json_strict(
            self.path if provider == 'gpt' else self.secondary_path,
            self._empty_source(),
        )
        try:
            if set(value) != {'items', 'last_success', 'error', 'retry_after', 'manual_after'}:
                raise ValueError('invalid snapshot')
            validate_items(value['items'], provider)
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

    def _sources(self):
        sources = {}
        for provider in self._feed_ids():
            try:
                sources[provider] = self._source(provider)
            except state_store.InvalidJsonState:
                if self.secondary_fetcher is None:
                    raise
                # Ignore untrusted cache bytes independently; another fresh feed
                # stays readable. A successful refresh can replace this cache.
                sources[provider] = {**self._empty_source(), 'error': 'source_unavailable'}
        return sources

    def _feed_status(self, source):
        now = self.clock()
        return {
            'last_success': source['last_success'],
            'error': source['error'],
            'is_stale': (
                source['error'] is not None
                or source['last_success'] is None
                or not 0 <= now - source['last_success'] < TTL
            ),
            'retry_after_seconds': max(0, math.ceil(source['retry_after'] - now)),
            'cooldown_seconds': max(0, math.ceil(source['manual_after'] - now)),
        }

    def _admin(self):
        sources, settings = self._sources(), self._settings()
        statuses = {provider: self._feed_status(source) for provider, source in sources.items()}
        successes = [
            source['last_success']
            for source in sources.values()
            if source['last_success'] is not None
        ]
        return {
            **settings,
            'products': settings.get('products', {}),
            'items': [row for source in sources.values() for row in source['items']],
            'sources': statuses,
            'last_success': max(successes, default=None),
            'error': 'source_unavailable'
            if any(source['error'] for source in sources.values())
            else None,
            'retry_after': min(source['retry_after'] for source in sources.values()),
            'manual_after': min(source['manual_after'] for source in sources.values()),
            'is_stale': all(status['is_stale'] for status in statuses.values()),
            'retry_after_seconds': min(
                status['retry_after_seconds'] for status in statuses.values()
            ),
            'cooldown_seconds': min(status['cooldown_seconds'] for status in statuses.values()),
        }

    async def admin(self):
        return await asyncio.to_thread(self._admin)

    async def public(self):
        state = await self.admin()
        groups, applicable = {}, set()
        for row in state['items']:
            merchant = state['skus'].get(row['key'], {})
            if not merchant.get('published'):
                continue
            provider = 'anli' if int(row['product_id']) >= PRODUCT_BASE else 'gpt'
            applicable.add(provider)
            group = groups.setdefault(
                row['product_id'],
                {
                    'id': row['product_id'],
                    'title': row['public_title'],
                    'category': ('Claude' if row['public_title'].startswith('Claude ') else 'Grok')
                    if provider == 'anli'
                    else 'GPT',
                    'description': state['products']
                    .get(row['product_id'], {})
                    .get('description', ''),
                    'after_sales': state['products']
                    .get(row['product_id'], {})
                    .get('after_sales', ''),
                    'variants': [],
                },
            )
            group['variants'].append(
                {
                    'id': row['key'],
                    'label': row['label'],
                    'price_cents': merchant['price_cents'],
                    'available': row['available'] and not state['sources'][provider]['is_stale'],
                    'quantity': None,
                    'sales': None,
                }
            )
        relevant = [
            state['sources'][provider] for provider in (applicable or set(state['sources']))
        ]
        ready = any(not status['is_stale'] for status in relevant)
        successes = [
            status['last_success'] for status in relevant if status['last_success'] is not None
        ]
        return {
            'currency': 'CNY',
            'telegram': state['telegram'],
            'products': list(groups.values()),
            'status': 'ready' if ready else ('stale' if successes else 'unavailable'),
            'updated_at': max(successes, default=None),
        }

    def _update(self, payload):
        validate_settings(payload)
        with state_store.file_lock(str(self.settings_path) + '.lock', timeout=2):
            current = self._settings()
            if payload['revision'] != current['revision']:
                raise Conflict('settings changed; reload before saving')
            known = {
                row['key'] for source in self._sources().values() for row in source['items']
            } | set(current['skus'])
            if set(payload['skus']) - known:
                raise ValueError('unknown SKU')
            products = payload.get('products', current.get('products', {}))
            known_products = {key.split(':')[0] for key in known} | set(current.get('products', {}))
            if set(products) - known_products:
                raise ValueError('unknown product')
            saved = {**payload, 'products': products, 'revision': current['revision'] + 1}
            validate_settings(saved)
            state_store.save_json(self.settings_path, saved)
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
            for provider in self._feed_ids():
                source = (await asyncio.to_thread(self._sources))[provider]
                status = self._feed_status(source)
                if (
                    status['retry_after_seconds']
                    or (manual and status['cooldown_seconds'])
                    or (not manual and not status['is_stale'])
                ):
                    continue
                path = self.path if provider == 'gpt' else self.secondary_path
                fetcher = self.fetcher if provider == 'gpt' else self.secondary_fetcher
                source['manual_after'] = self.clock() + MANUAL_COOLDOWN
                # Persist before acquisition so cancellation cannot busy-loop.
                source['retry_after'] = self.clock() + BACKOFF
                await self._finish_thread(state_store.save_json, path, source)
                try:
                    async with asyncio.timeout(30):
                        items = await fetcher()
                    validate_items(items, provider)
                    source.update(items=items, last_success=self.clock(), error=None, retry_after=0)
                except Exception:
                    source.update(error='source_unavailable', retry_after=self.clock() + BACKOFF)
                await self._finish_thread(state_store.save_json, path, source)
            return await self.admin()
        finally:
            lock.__exit__(None, None, None)
