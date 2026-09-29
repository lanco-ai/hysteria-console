import asyncio
import gzip

import httpx
from fastapi.testclient import TestClient

from web_api import create_app
from web_api.github_trending_avatar import AvatarProxy
from web_api.github_trending_store import TrendingStore
from web_api.services import LoginRequired


PNG = b'\x89PNG\r\n\x1a\n' + b'valid-image'
HEADERS = {'Cookie': 'sid=admin'}


def image_response(body=PNG, *, media_type='image/png', encoding=None):
    headers = {'Content-Type': media_type}
    if encoding:
        headers['Content-Encoding'] = encoding
    return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


def client_for(tmp_path, handler):
    proxy = AvatarProxy(transport=httpx.MockTransport(handler))
    return TestClient(create_app(Sessions(), github_trending_store=TrendingStore(tmp_path / 'cache.json'), github_trending_avatar_proxy=proxy))


def test_avatar_route_auth_validation_cache_and_redirect(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if request.url.host == 'github.com':
            return httpx.Response(302, headers={'Location': 'https://avatars.githubusercontent.com/u/1?s=64&v=4'})
        return image_response()

    with client_for(tmp_path, handler) as client:
        url = '/api/v1/github-trending/avatar/octocat'
        assert client.get(url).status_code == 401
        assert client.get(url, headers={'Cookie': 'sid=user'}).status_code == 403
        for owner in ('-bad', 'bad--name', 'bad.', 'bad%2Fname', 'a' * 40):
            assert client.get('/api/v1/github-trending/avatar/' + owner, headers=HEADERS).status_code in (404, 422)
        first = client.get(url, headers=HEADERS)
        assert first.status_code == 200 and first.content == PNG
        assert first.headers['content-type'].startswith('image/png')
        assert first.headers['cache-control'] == 'no-store'
        assert client.get(url, headers=HEADERS).content == PNG
    assert calls == ['https://github.com/octocat.png?size=64', 'https://avatars.githubusercontent.com/u/1?s=64&v=4']


def test_avatar_rejects_redirect_type_size_and_timeout(tmp_path):
    cases = [
        lambda _: httpx.Response(302, headers={'Location': 'https://evil.test/avatar.png'}),
        lambda _: httpx.Response(302, headers={'Location': 'http://avatars.githubusercontent.com/u/1'}),
        lambda _: httpx.Response(302, headers={'Location': 'https://avatars.githubusercontent.com/u/1?s=128&v=4'}),
        lambda _: image_response(media_type='text/html'),
        lambda _: image_response(b'<html>'),
        lambda _: image_response(PNG + b'x' * 100_000),
    ]
    for handler in cases:
        with client_for(tmp_path, handler) as client:
            response = client.get('/api/v1/github-trending/avatar/octocat', headers=HEADERS)
            assert response.status_code in (404, 502)
            assert 'github' not in response.text.lower()

    def timeout(_):
        raise httpx.ReadTimeout('private upstream detail')

    with client_for(tmp_path, timeout) as client:
        assert client.get('/api/v1/github-trending/avatar/octocat', headers=HEADERS).status_code == 502


def test_avatar_rejects_compressed_image_before_decoding(tmp_path):
    def handler(_):
        return image_response(gzip.compress(PNG), encoding='gzip')

    with client_for(tmp_path, handler) as client:
        assert client.get('/api/v1/github-trending/avatar/octocat', headers=HEADERS).status_code == 502


def test_avatar_same_owner_singleflight_and_negative_cache():
    calls = 0

    async def run():
        nonlocal calls

        async def handler(request):
            nonlocal calls
            calls += 1
            await asyncio.sleep(.01)
            return httpx.Response(404)

        proxy = AvatarProxy(transport=httpx.MockTransport(handler))
        results = await asyncio.gather(*(proxy.get('octocat') for _ in range(8)))
        assert all(result is None for result in results)
        assert await proxy.get('octocat') is None

    asyncio.run(run())
    assert calls == 1


def test_avatar_fetch_concurrency_is_bounded():
    active = 0
    peak = 0

    async def run():
        nonlocal active, peak

        async def handler(request):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.02)
            active -= 1
            return image_response()

        proxy = AvatarProxy(transport=httpx.MockTransport(handler))
        results = await asyncio.gather(*(proxy.get(f'owner{index}') for index in range(12)))
        assert all(result == (PNG, 'image/png') for result in results)

    asyncio.run(run())
    assert peak == 4
