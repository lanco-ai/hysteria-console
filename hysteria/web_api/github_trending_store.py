"""Two bounded snapshots, with process-safe refresh exclusion and cooldowns."""

import asyncio
import math
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import state_store

from .github_trending_source import (
    MAX_SAFE_INTEGER,
    PERIODS,
    SourceError,
    fetch_trending,
    source_url,
)

TTL = 6 * 60 * 60
COOLDOWN = 10 * 60


def _iso(value):
    return (
        datetime.fromtimestamp(value, timezone.utc).isoformat().replace('+00:00', 'Z')
        if value is not None
        else None
    )


class TrendingStore:
    def __init__(
        self,
        path='/root/hysteria/state/github-trending.json',
        *,
        fetcher=fetch_trending,
        clock=time.time,
    ):
        self.path = Path(path)
        self.fetcher = fetcher
        self.clock = clock

    def _load(self):
        data = state_store.load_json_strict(self.path, {})
        if any(key not in PERIODS or not isinstance(value, dict) for key, value in data.items()):
            raise state_store.InvalidJsonState('invalid trending state')
        for row in data.values():
            for field in ('last_success', 'refresh_until', 'manual_after', 'retry_after'):
                value = row.get(field)
                if value is not None and (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not 0 <= value < 253402300799
                ):
                    raise state_store.InvalidJsonState('invalid trending timestamp')
            if row.get('error') not in (
                None,
                'parse_error',
                'rate_limited',
                'upstream_denied',
                'upstream_timeout',
                'upstream_unavailable',
                'unsafe_redirect',
                'response_too_large',
            ):
                raise state_store.InvalidJsonState('invalid trending status')
            self._validate_items(row.get('items', []))
        return data

    @staticmethod
    def _validate_items(items):
        if not isinstance(items, list) or len(items) > 100:
            raise state_store.InvalidJsonState('invalid trending items')
        last_rank, seen = 0, set()
        for item in items:
            if not isinstance(item, dict):
                raise state_store.InvalidJsonState('invalid trending item')
            name, rank = item.get('full_name'), item.get('source_rank')
            if (
                not isinstance(name, str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+', name)
                or name.split('/')[1] in ('.', '..')
                or name.lower() in seen
                or type(rank) is not int
                or not last_rank < rank <= 100
                or item.get('html_url') != f'https://github.com/{name}'
            ):
                raise state_store.InvalidJsonState('invalid trending repository')
            for field in ('stars_total', 'stars_period', 'forks_count'):
                value = item.get(field)
                if value is not None and (
                    type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER
                ):
                    raise state_store.InvalidJsonState('invalid trending count')
            for field in ('description', 'language'):
                value = item.get(field)
                if value is not None and (not isinstance(value, str) or len(value) > 100000):
                    raise state_store.InvalidJsonState('invalid trending text')
            last_rank = rank
            seen.add(name.lower())

    def _update(self, period, changes):
        with state_store.file_lock(str(self.path) + '.lock', timeout=2):
            data = self._load()
            row = data.setdefault(period, {})
            row.update(changes)
            state_store.save_json(self.path, data)
            self.path.chmod(0o600)

    def _read(self, period):
        source_url(period)
        row = self._load().get(period, {})
        now = self.clock()
        success = row.get('last_success')
        items = row.get('items', [])
        error = row.get('error')
        return dict(
            period=period,
            source='GitHub Trending',
            source_url=source_url(period),
            fetched_at=_iso(success),
            last_success_at=_iso(success),
            is_stale=success is None or now - success >= TTL,
            refreshing=row.get('refresh_until', 0) > now,
            items=items,
            error=error,
            status='ready' if items else ('unavailable' if error else 'loading'),
            cooldown_seconds=max(0, math.ceil(row.get('manual_after', 0) - now)),
            retry_after_seconds=max(0, math.ceil(row.get('retry_after', 0) - now)),
        )

    async def _write(self, period, changes):
        task = asyncio.create_task(asyncio.to_thread(self._update, period, changes))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def read(self, period):
        return await asyncio.to_thread(self._read, period)

    def _acquire(self, period):
        lock = state_store.file_lock(str(self.path) + f'.{period}.refresh.lock', timeout=0)
        try:
            lock.__enter__()
        except state_store.LockTimeout:
            return None
        return lock

    async def refresh(self, period, *, manual=False):
        source_url(period)
        # A lock is held throughout the bounded network operation. Unlike a lease
        # alone, this prevents duplicate fetches even if a worker is suspended.
        acquiring = asyncio.create_task(asyncio.to_thread(self._acquire, period))
        try:
            lock = await asyncio.shield(acquiring)
        except asyncio.CancelledError:
            lock = await acquiring
            if lock is not None:
                lock.__exit__(None, None, None)
            raise
        if lock is None:
            return await self.read(period)
        started = False
        try:
            current = await self.read(period)
            if current['retry_after_seconds'] or (manual and current['cooldown_seconds']):
                return current
            if not manual and not current['is_stale']:
                return current
            now = self.clock()
            changes = {'refresh_until': now + 60}
            if manual:
                changes['manual_after'] = now + COOLDOWN
            started = True
            await self._write(period, changes)
            try:
                async with asyncio.timeout(30):
                    items = await self.fetcher(period)
                if not items or len(items) > 100:
                    raise SourceError('parse_error')
                self._validate_items(items)
                changes = {
                    'items': items,
                    'last_success': self.clock(),
                    'error': None,
                    'retry_after': 0,
                }
            except (SourceError, TimeoutError) as exc:
                code = exc.code if isinstance(exc, SourceError) else 'upstream_timeout'
                changes = {
                    'error': code,
                    'retry_after': self.clock()
                    + (3600 if code in ('upstream_denied', 'rate_limited') else COOLDOWN),
                }
            except Exception:
                changes = {'error': 'upstream_unavailable', 'retry_after': self.clock() + COOLDOWN}
            await self._write(period, {**changes, 'refresh_until': 0})
            return await self.read(period)
        finally:
            try:
                if started:
                    await self._write(period, {'refresh_until': 0})
            finally:
                lock.__exit__(None, None, None)
