"""Small, bounded proxy for GitHub owner avatars."""

import asyncio
import re
import time
from collections import OrderedDict

import httpx

_OWNER = re.compile(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z')
_CDN_PATH = re.compile(r'/u/[0-9]+\Z')
_MAX_BYTES = 65_536
_TYPES = {'image/png', 'image/jpeg', 'image/webp'}


def valid_owner(owner):
    return bool(_OWNER.fullmatch(owner)) and '--' not in owner


def _valid_image(data, media_type):
    if media_type == 'image/png':
        return data.startswith(b'\x89PNG\r\n\x1a\n')
    if media_type == 'image/jpeg':
        return data.startswith(b'\xff\xd8\xff')
    if media_type == 'image/webp':
        return data.startswith(b'RIFF') and data[8:12] == b'WEBP'
    return False


def _safe_redirect(value):
    try:
        url = httpx.URL(value)
        return (
            url.scheme == 'https'
            and url.host == 'avatars.githubusercontent.com'
            and url.port in (None, 443)
            and not url.userinfo
            and not url.fragment
            and bool(_CDN_PATH.fullmatch(url.path))
            and (not url.query or re.fullmatch(rb'(?:s=64&)?v=[0-9]{1,3}', url.query))
        )
    except (ValueError, httpx.InvalidURL):
        return False


class AvatarProxy:
    def __init__(self, *, transport=None):
        self.transport = transport
        self.cache = OrderedDict()
        self.inflight = {}
        self.lock = asyncio.Lock()
        self.capacity = asyncio.Semaphore(4)

    async def get(self, owner):
        if not valid_owner(owner):
            return None
        async with self.lock:
            cached = self.cache.get(owner)
            if cached and cached[0] > time.monotonic():
                self.cache.move_to_end(owner)
                return cached[1]
            self.cache.pop(owner, None)
            task = self.inflight.get(owner)
            if task is None:
                if len(self.inflight) >= 32:
                    return None
                task = asyncio.create_task(self._load(owner))
                self.inflight[owner] = task
        return await asyncio.shield(task)

    async def _load(self, owner):
        try:
            async with self.capacity:
                try:
                    async with asyncio.timeout(5):
                        result = await self._fetch(owner)
                except TimeoutError:
                    result = None
            async with self.lock:
                self.cache[owner] = (time.monotonic() + (3600 if result else 60), result)
                self.cache.move_to_end(owner)
                if len(self.cache) > 256:
                    self.cache.popitem(last=False)
            return result
        finally:
            async with self.lock:
                self.inflight.pop(owner, None)

    async def _fetch(self, owner):
        url = f'https://github.com/{owner}.png?size=64'
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=httpx.Timeout(3, connect=1),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                for hop in range(3):
                    async with client.stream(
                        'GET',
                        url,
                        headers={
                            'Accept': 'image/png,image/jpeg,image/webp',
                            'Accept-Encoding': 'identity',
                        },
                    ) as response:
                        if response.status_code in (301, 302, 303, 307, 308):
                            location = response.headers.get('location', '')
                            if hop >= 2 or not _safe_redirect(location):
                                return None
                            url = location
                            continue
                        if response.status_code != 200:
                            return None
                        if response.headers.get('content-encoding', '').strip().lower() not in ('', 'identity'):
                            return None
                        media_type = response.headers.get('content-type', '').split(';', 1)[0].lower()
                        if media_type not in _TYPES:
                            return None
                        size = response.headers.get('content-length')
                        if size and (not size.isdecimal() or int(size) > _MAX_BYTES):
                            return None
                        body = bytearray()
                        async for chunk in response.aiter_raw(chunk_size=4096):
                            if len(body) + len(chunk) > _MAX_BYTES:
                                return None
                            body.extend(chunk)
                        data = bytes(body)
                        return (data, media_type) if _valid_image(data, media_type) else None
        except (httpx.HTTPError, httpx.StreamError, OSError, TimeoutError, ValueError):
            return None
        return None
