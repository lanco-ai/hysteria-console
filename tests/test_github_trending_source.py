import asyncio
from pathlib import Path

import httpx
import pytest

from web_api.github_trending_source import SourceError, fetch_trending, parse_trending

HTML = (Path(__file__).parent / 'fixtures/github_trending/weekly.html').read_text()


@pytest.mark.parametrize('period,label', [('weekly', 'this week'), ('daily', 'today')])
def test_parser_exact_values_missing_and_original_rank(period, label):
    items = parse_trending(HTML.replace('this week', label), period)
    assert [x['source_rank'] for x in items] == [1, 3]
    assert items[0] == dict(
        source_rank=1,
        full_name='anthropics/financial-services',
        html_url='https://github.com/anthropics/financial-services',
        description='Tools & examples',
        language='Python',
        stars_total=37976,
        stars_period=2606,
        forks_count=0,
    )
    assert items[1]['stars_total'] is None
    assert items[1]['forks_count'] is None
    assert items[1]['description'] is None
    assert items[1]['stars_period'] == 0


@pytest.mark.parametrize(
    'html',
    [
        '<html>blocked</html>',
        '',
        '<article class="Box-row"><h2><a href="https://evil.test/a/b">bad</a></h2></article>',
        HTML.replace('this week', 'today'),
        HTML.replace('</article>', ''),
    ],
)
def test_parser_rejects_unexpected_html(html):
    with pytest.raises(SourceError):
        parse_trending(html, 'weekly')


def test_fetch_fixed_url_and_retry():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(
            503 if len(calls) == 1 else 200, text=HTML, headers={'Content-Type': 'text/html'}
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_trending('weekly', client=client, sleep=lambda _: asyncio.sleep(0))

    assert len(asyncio.run(run())) == 2
    assert calls == ['https://github.com/trending?since=weekly'] * 2


@pytest.mark.parametrize(
    'status,code',
    [
        (403, 'upstream_denied'),
        (429, 'rate_limited'),
        (302, 'unsafe_redirect'),
        (500, 'upstream_unavailable'),
    ],
)
def test_fetch_failures_are_safe_and_bounded(status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'Location': 'http://localhost/private'})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SourceError) as error:
                await fetch_trending('daily', client=client, sleep=lambda _: asyncio.sleep(0))
            assert error.value.code == code

    asyncio.run(run())
    assert len(calls) == (2 if status == 500 else 1)


@pytest.mark.parametrize('kind', ['timeout', 'oversize', 'type'])
def test_fetch_resource_limits(kind):
    def handler(request):
        if kind == 'timeout':
            raise httpx.ReadTimeout('secret', request=request)
        return httpx.Response(
            200,
            content=b'x' * (1600001 if kind == 'oversize' else 1),
            headers={'Content-Type': 'text/html' if kind == 'oversize' else 'application/json'},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SourceError):
                await fetch_trending('daily', client=client, sleep=lambda _: asyncio.sleep(0))

    asyncio.run(run())


def test_missing_period_count_is_null_when_page_structure_is_known():
    html = HTML.replace('<span class="float-sm-right">0 stars this week</span>', '')
    assert parse_trending(html, 'weekly')[1]['stars_period'] is None


def test_unrepresentable_count_and_blank_metadata_are_null():
    html = (
        HTML.replace('37,976', '9007199254740992')
        .replace('<p>Tools &amp; examples</p>', '<p> </p>')
        .replace(
            '<span itemprop="programmingLanguage">Python</span>',
            '<span itemprop="programmingLanguage"> </span>',
        )
    )
    item = parse_trending(html, 'weekly')[0]
    assert item['stars_total'] is None
    assert item['description'] is None
    assert item['language'] is None


def test_fetch_whole_timeout(monkeypatch):
    import web_api.github_trending_source as source

    monkeypatch.setattr(source, 'TOTAL_TIMEOUT', 0.01)

    async def handler(request):
        await asyncio.sleep(1)
        return httpx.Response(200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(SourceError, match='upstream_timeout'):
                await fetch_trending('weekly', client=client)

    asyncio.run(run())
