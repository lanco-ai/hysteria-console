from fastapi.testclient import TestClient

from web_api import create_app
from web_api.github_trending_store import TrendingStore
from web_api.services import LoginRequired


class Sessions:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        if headers.get('cookie') == 'sid=user':
            return {'role': 'user'}
        raise LoginRequired


HEADERS = {'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'same-origin'}


def test_reads_are_public_refresh_stays_admin_and_input_is_validated(tmp_path):
    async def fetch(_):
        return [
            {
                'source_rank': 1,
                'full_name': 'owner/repo',
                'html_url': 'https://github.com/owner/repo',
                'description': None,
                'language': None,
                'stars_total': 0,
                'stars_period': 1,
                'forks_count': None,
            }
        ]

    store = TrendingStore(tmp_path / 'cache.json', fetcher=fetch)
    with TestClient(create_app(Sessions(), github_trending_store=store)) as client:
        url = '/api/v1/github-trending'
        assert client.get(url).status_code == 200
        assert client.get(url, headers={'Cookie': 'sid=user'}).status_code == 200
        assert client.get(url + '?period=monthly').status_code == 422
        assert client.post(url + '/refresh', json={'period': 'weekly'}).status_code == 401
        assert (
            client.post(
                url + '/refresh', headers={'Cookie': 'sid=user'}, json={'period': 'weekly'}
            ).status_code
            == 403
        )
        assert (
            client.post(
                url + '/refresh',
                headers={'Cookie': 'sid=admin', 'Sec-Fetch-Site': 'cross-site'},
                json={'period': 'weekly'},
            ).status_code
            == 403
        )
        assert client.get(url + '?period=monthly', headers=HEADERS).status_code == 422
        for payload in [{'period': 'monthly'}, {'period': 'daily', 'url': 'https://evil.test'}, []]:
            assert client.post(url + '/refresh', headers=HEADERS, json=payload).status_code == 422
        assert client.post(url + '/refresh', headers=HEADERS, content='x' * 5000).status_code == 413
        result = client.get(url, headers=HEADERS)
        assert result.status_code == 200
        assert result.json()['period'] == 'weekly'
        assert result.json()['source_url'] == 'https://github.com/trending?since=weekly'
        assert result.headers['Cache-Control'] == 'no-store'


def test_scheduler_lifecycle_failure_does_not_break_startup(tmp_path):
    import asyncio
    import threading
    from web_api.github_trending_source import SourceError

    called = threading.Event()

    async def fetch(_):
        called.set()
        raise SourceError('upstream_unavailable')

    store = TrendingStore(tmp_path / 'cache.json', fetcher=fetch)
    with TestClient(
        create_app(Sessions(), github_trending_store=store, github_trending_scheduler_enabled=True)
    ) as client:
        assert called.wait(2)
        assert client.get('/api/v1/github-trending', headers=HEADERS).status_code == 200
    assert not asyncio.run(store.read('weekly'))['refreshing']
    assert not asyncio.run(store.read('daily'))['refreshing']


def test_corrupt_cache_is_not_overwritten(tmp_path):
    store = TrendingStore(tmp_path / 'cache.json')
    store.path.write_text('{"weekly": {"items": "broken"}}')
    with TestClient(create_app(Sessions(), github_trending_store=store)) as client:
        assert client.get('/api/v1/github-trending', headers=HEADERS).status_code == 503
    assert store.path.read_text() == '{"weekly": {"items": "broken"}}'
