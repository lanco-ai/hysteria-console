"""Bounded, token-free adapter for the two public GitHub Trending pages."""

import asyncio
import re
from html.parser import HTMLParser

import httpx

PERIODS = ('weekly', 'daily')
MAX_BODY = 1_500_000
TOTAL_TIMEOUT = 25
MAX_SAFE_INTEGER = 9_007_199_254_740_991
_REPO = re.compile(r'/([A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+)\Z')
_COUNT = re.compile(r'(?:[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+)\Z')


class SourceError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def source_url(period):
    if period not in PERIODS:
        raise ValueError('invalid period')
    return f'https://github.com/trending?since={period}'


def _count(text):
    text = text.strip()
    if len(text) > 20 or not _COUNT.fullmatch(text):
        return None
    value = int(text.replace(',', ''))
    return value if value <= MAX_SAFE_INTEGER else None


def _text_or_none(node):
    return ' '.join(node.text().split()) or None


class _Node:
    def __init__(self, tag, attrs):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def text(self):
        return ''.join(
            child.text() if isinstance(child, _Node) else child for child in self.children
        )

    def all(self, tag):
        for child in self.children:
            if isinstance(child, _Node):
                if child.tag == tag:
                    yield child
                yield from child.all(tag)


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.articles = []
        self.invalid = False

    def handle_starttag(self, tag, attrs):
        if tag == 'article' and 'Box-row' in dict(attrs).get('class', '').split():
            if self.stack:
                self.invalid = True
            node = _Node(tag, attrs)
            self.articles.append(node)
            self.stack = [node]
        elif self.stack:
            node = _Node(tag, attrs)
            self.stack[-1].children.append(node)
            if tag not in {
                'area',
                'base',
                'br',
                'col',
                'embed',
                'hr',
                'img',
                'input',
                'link',
                'meta',
                'param',
                'source',
                'track',
                'wbr',
            }:
                self.stack.append(node)

    def handle_endtag(self, tag):
        if self.stack:
            for i in range(len(self.stack) - 1, -1, -1):
                if self.stack[i].tag == tag:
                    self.stack = self.stack[:i]
                    break

    def handle_data(self, data):
        if self.stack:
            self.stack[-1].children.append(data)


def parse_trending(html, period):
    source_url(period)
    parser = _Parser()
    parser.feed(html)
    parser.close()
    if parser.stack or parser.invalid or not 1 <= len(parser.articles) <= 100:
        raise SourceError('parse_error')
    items, seen = [], set()
    matched_period = False
    label = 'stars this week' if period == 'weekly' else 'stars today'
    for rank, article in enumerate(parser.articles, 1):
        anchors = [a for h in article.all('h2') for a in h.all('a')]
        names = [_REPO.fullmatch(a.attrs.get('href', '')) for a in anchors]
        names = [m.group(1) for m in names if m]
        spans = list(article.all('span'))
        period_nodes = [s for s in spans if 'float-sm-right' in s.attrs.get('class', '').split()]
        if len(names) != 1 or len(period_nodes) > 1:
            raise SourceError('parse_error')
        period_text = ' '.join(period_nodes[0].text().split()) if period_nodes else ''
        if period_nodes and not period_text.endswith(label):
            raise SourceError('parse_error')
        matched_period = matched_period or bool(period_nodes)
        name = names[0]
        if name.split('/')[1] in ('.', '..'):
            raise SourceError('parse_error')
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        links = {a.attrs.get('href'): a.text() for a in article.all('a')}
        descriptions = list(article.all('p'))
        languages = [s for s in spans if s.attrs.get('itemprop') == 'programmingLanguage']
        items.append(
            dict(
                source_rank=rank,
                full_name=name,
                html_url=f'https://github.com/{name}',
                description=_text_or_none(descriptions[0]) if descriptions else None,
                language=_text_or_none(languages[0]) if languages else None,
                stars_total=_count(links.get(f'/{name}/stargazers', '')),
                stars_period=_count(period_text[: -len(label)]),
                forks_count=_count(links.get(f'/{name}/forks', '')),
            )
        )
    if not items or not matched_period:
        raise SourceError('parse_error')
    return items


async def fetch_trending(period, *, client=None, sleep=asyncio.sleep):
    url = source_url(period)
    if client is None:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(8, connect=5), follow_redirects=False, trust_env=False
        ) as owned:
            return await fetch_trending(period, client=owned, sleep=sleep)
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT):
            for attempt in range(2):
                try:
                    async with client.stream(
                        'GET',
                        url,
                        follow_redirects=False,
                        timeout=httpx.Timeout(8, connect=5),
                        headers={'Accept': 'text/html', 'User-Agent': 'hy2-github-trending/1.0'},
                    ) as response:
                        if response.status_code == 403:
                            raise SourceError('upstream_denied')
                        if response.status_code == 429:
                            raise SourceError('rate_limited')
                        if 300 <= response.status_code < 400:
                            raise SourceError('unsafe_redirect')
                        if response.status_code >= 500:
                            raise httpx.ConnectError('upstream unavailable')
                        if response.status_code != 200:
                            raise SourceError('upstream_unavailable')
                        if (
                            response.headers.get('content-type', '').split(';')[0].strip().lower()
                            != 'text/html'
                        ):
                            raise SourceError('parse_error')
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            body.extend(chunk)
                            if len(body) > MAX_BODY:
                                raise SourceError('response_too_large')
                        return await asyncio.to_thread(
                            parse_trending, body.decode('utf-8', errors='strict'), period
                        )
                except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
                    if attempt:
                        raise SourceError('upstream_unavailable') from None
                    await sleep(0.5)
    except TimeoutError:
        raise SourceError('upstream_timeout') from None
    except UnicodeError:
        raise SourceError('parse_error') from None
