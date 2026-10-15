"""Session checks delegated to the network service.

The network service owns the admin password, the ``sid`` session file and the
user panel sessions.  This service never reads that state: it forwards the two
session cookies to the network service's loopback ``/api/v1/session`` endpoint
and maps the answer onto the exceptions the routes already handle.
"""

import hashlib
import json
import threading
import time
from http.cookies import SimpleCookie

import httpx

NETWORK_SESSION_URL = 'http://127.0.0.1:8083/api/v1/session'
SESSION_COOKIES = ('sid', 'usid')
MAX_SESSION_RESPONSE_BYTES = 4096


class LoginRequired(Exception):
    """No existing cookie session authorizes the requested read."""


class StateUnavailable(Exception):
    """The session authority could not be asked safely."""


class UserAccessDenied(Exception):
    """An authenticated user's current lifecycle state blocks panel access."""

    _PUBLIC_CODES = frozenset(('disabled', 'expired', 'forbidden', 'password_change_required'))

    def __init__(self, code, *, cookie=None):
        self.code = code if code in self._PUBLIC_CODES else 'forbidden'
        self.cookie = cookie
        super().__init__()


def session_cookies(headers):
    """Return only the session cookies, parsed exactly as the network service does."""

    raw = headers.get('cookie') or ''
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:
        return {}
    return {name: jar[name].value for name in SESSION_COOKIES if name in jar and jar[name].value}


class RemoteSessionServices:
    """Drop-in ``read_session`` that asks the network service on loopback.

    Successful answers are cached for a few seconds per cookie pair so a page
    load does not turn into one loopback call per request.  Failures are never
    cached, and logging out on the network service takes effect here once the
    short cache entry expires.
    """

    def __init__(
        self,
        session_url=NETWORK_SESSION_URL,
        *,
        timeout=2.0,
        cache_seconds=5.0,
        transport=None,
        clock=time.monotonic,
    ):
        if cache_seconds < 0:
            raise ValueError('cache_seconds must not be negative')
        self.session_url = session_url
        self.cache_seconds = float(cache_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._cache = {}
        self._client = httpx.Client(
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    def close(self):
        self._client.close()

    def _cache_key(self, cookies):
        material = '\0'.join(f'{name}={cookies.get(name, "")}' for name in SESSION_COOKIES)
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

    def read_session(self, *, headers, path):
        cookies = session_cookies(headers)
        if not cookies:
            raise LoginRequired
        key = self._cache_key(cookies)
        now = self._clock()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                return dict(cached[1])
            self._cache.pop(key, None)
        payload = self._ask(cookies)
        if self.cache_seconds:
            with self._lock:
                # Bound the cache: expired entries go first, then everything.
                if len(self._cache) >= 1024:
                    self._cache = {k: v for k, v in self._cache.items() if v[0] > now}
                    if len(self._cache) >= 1024:
                        self._cache.clear()
                self._cache[key] = (now + self.cache_seconds, dict(payload))
        return payload

    def _ask(self, cookies):
        cookie_header = '; '.join(f'{name}={value}' for name, value in cookies.items())
        try:
            response = self._client.get(
                self.session_url,
                headers={'Cookie': cookie_header, 'Accept': 'application/json'},
            )
        except httpx.HTTPError as exc:
            raise StateUnavailable from exc
        if len(response.content) > MAX_SESSION_RESPONSE_BYTES:
            raise StateUnavailable
        try:
            body = json.loads(response.content or b'{}')
        except (ValueError, UnicodeDecodeError) as exc:
            raise StateUnavailable from exc
        if not isinstance(body, dict):
            raise StateUnavailable
        if response.status_code == 401:
            raise LoginRequired
        if response.status_code == 403:
            raise UserAccessDenied(str(body.get('error') or 'forbidden'))
        if response.status_code != 200:
            raise StateUnavailable
        role = body.get('role')
        if role == 'admin':
            return {'role': 'admin'}
        username = body.get('username')
        if role == 'user' and isinstance(username, str) and username:
            return {'role': 'user', 'username': username}
        raise StateUnavailable
