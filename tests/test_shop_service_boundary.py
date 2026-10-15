"""The shop service's own boundary: delegated sessions, pages and entrypoint."""

import pathlib

import httpx
import pytest
from fastapi.testclient import TestClient
from web_api import create_app
from web_api.requests import RequestHeaders
from web_api.services import (
    LoginRequired,
    RemoteSessionServices,
    StateUnavailable,
    UserAccessDenied,
    session_cookies,
)

SESSION_URL = 'http://network.test/api/v1/session'


def remote(handler, **kwargs):
    return RemoteSessionServices(SESSION_URL, transport=httpx.MockTransport(handler), **kwargs)


def headers(cookie):
    return RequestHeaders({'Cookie': cookie} if cookie is not None else {})


def test_only_session_cookies_are_forwarded():
    seen = []

    def handler(request):
        seen.append(request.headers.get('cookie'))
        return httpx.Response(200, json={'role': 'admin'})

    services = remote(handler)
    payload = services.read_session(
        headers=headers('theme=dark; sid=abc; tracking=xyz; usid=u1'), path='/admin/shop'
    )
    assert payload == {'role': 'admin'}
    assert seen == ['sid=abc; usid=u1']


@pytest.mark.parametrize(
    'response, expected',
    [
        (
            httpx.Response(200, json={'role': 'user', 'username': 'alice'}),
            {'role': 'user', 'username': 'alice'},
        ),
        (httpx.Response(200, json={'role': 'admin', 'extra': 1}), {'role': 'admin'}),
    ],
)
def test_successful_answers_are_normalized(response, expected):
    services = remote(lambda request: response)
    assert services.read_session(headers=headers('sid=s'), path='/') == expected


def test_missing_cookie_never_calls_the_network_service():
    def handler(request):
        raise AssertionError('no request expected')

    with pytest.raises(LoginRequired):
        remote(handler).read_session(headers=headers(None), path='/')
    with pytest.raises(LoginRequired):
        remote(handler).read_session(headers=headers('theme=dark'), path='/')
    assert session_cookies(headers('sid="unterminated')) == {}


def test_denials_and_failures_map_to_route_exceptions():
    with pytest.raises(LoginRequired):
        remote(lambda r: httpx.Response(401, json={'error': 'login_required'})).read_session(
            headers=headers('sid=s'), path='/'
        )
    with pytest.raises(UserAccessDenied) as denied:
        remote(lambda r: httpx.Response(403, json={'error': 'disabled'})).read_session(
            headers=headers('usid=u'), path='/'
        )
    assert denied.value.code == 'disabled'
    with pytest.raises(UserAccessDenied) as unknown:
        remote(lambda r: httpx.Response(403, json={'error': 'secret-detail'})).read_session(
            headers=headers('usid=u'), path='/'
        )
    assert unknown.value.code == 'forbidden'

    def unreachable(request):
        raise httpx.ConnectError('refused', request=request)

    for handler in (
        unreachable,
        lambda r: httpx.Response(503, json={'error': 'state_unavailable'}),
        lambda r: httpx.Response(200, content=b'not json'),
        lambda r: httpx.Response(200, json=['admin']),
        lambda r: httpx.Response(200, json={'role': 'root'}),
        lambda r: httpx.Response(200, json={'role': 'user', 'username': ''}),
        lambda r: httpx.Response(200, content=b'{"role": "admin", "pad": "' + b'x' * 5000 + b'"}'),
        lambda r: httpx.Response(302, headers={'Location': '/login'}, content=b'{}'),
    ):
        with pytest.raises(StateUnavailable):
            remote(handler).read_session(headers=headers('sid=s'), path='/')


def test_successes_are_cached_briefly_and_failures_are_not():
    calls = []
    now = [100.0]
    answers = [
        httpx.Response(503, json={}),
        httpx.Response(200, json={'role': 'admin'}),
        httpx.Response(401, json={}),
    ]

    def handler(request):
        calls.append(request.headers['cookie'])
        return answers.pop(0)

    services = remote(handler, cache_seconds=5, clock=lambda: now[0])
    with pytest.raises(StateUnavailable):
        services.read_session(headers=headers('sid=a'), path='/')
    assert services.read_session(headers=headers('sid=a'), path='/') == {'role': 'admin'}
    now[0] += 4.9
    assert services.read_session(headers=headers('sid=a'), path='/') == {'role': 'admin'}
    assert len(calls) == 2
    now[0] += 0.2
    with pytest.raises(LoginRequired):
        services.read_session(headers=headers('sid=a'), path='/')
    assert len(calls) == 3


def test_proxy_environment_is_ignored(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://proxy.invalid:1')
    monkeypatch.setenv('ALL_PROXY', 'http://proxy.invalid:1')
    services = RemoteSessionServices(SESSION_URL)
    try:
        assert services._client._trust_env is False
    finally:
        services.close()


class Sessions:
    def __init__(self, outcome):
        self.outcome = outcome

    def read_session(self, *, headers, path):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.fixture
def dist(tmp_path):
    root = tmp_path / 'dist'
    (root / 'assets').mkdir(parents=True)
    (root / 'assets' / 'app-abc123.js').write_text('console.log(1)', encoding='utf-8')
    (root / 'manifest.json').write_text('{}', encoding='utf-8')
    (root / 'index.html').write_text(
        '<!doctype html><html><head><title>x</title></head><body>'
        '<div id="root" data-public-host=""></div></body></html>',
        encoding='utf-8',
    )
    return root


class Store:
    async def refresh(self):
        return None

    def public_catalog(self):
        return {'products': [], 'status': 'unavailable'}


def app_for(outcome, dist, monkeypatch, host=''):
    monkeypatch.setenv('LANCOAI_PUBLIC_HOST', host)
    return create_app(Sessions(outcome), react_dist=dist, shop_store=Store())


def test_portal_document_and_moved_views(dist, monkeypatch):
    with TestClient(app_for(LoginRequired(), dist, monkeypatch, host='lancoai.site')) as client:
        page = client.get('/')
        assert page.status_code == 200
        assert '<title>购物 · LancoAI</title>' in page.text
        assert 'data-public-host="lancoai.site" data-password-max-length="256"' in page.text
        moved = client.get('/?view=trending&period=daily', follow_redirects=False)
        assert (moved.status_code, moved.headers['location']) == (302, '/trending?period=daily')
        chat = client.get('/?view=chat&conversation=7', follow_redirects=False)
        assert (chat.status_code, chat.headers['location']) == (302, '/admin/chat?conversation=7')
        assert client.get('/?view=sms').status_code == 200
        assert client.get('/admin').status_code == 404
        assert client.get('/static/shop/assets/app-abc123.js').status_code == 200
        assert client.get('/static/react/assets/app-abc123.js').status_code == 404


def test_public_host_rejects_unsafe_values(dist, monkeypatch):
    with TestClient(app_for(LoginRequired(), dist, monkeypatch)) as client:
        page = client.get('/', headers={'Host': 'evil"><script>'})
        assert 'data-public-host="127.0.0.1"' in page.text


@pytest.mark.parametrize(
    'outcome, status, location',
    [
        (LoginRequired(), 303, '/login?next=%2Fadmin%2Fshop'),
        (UserAccessDenied('disabled'), 303, '/login?next=%2Fadmin%2Fshop'),
        ({'role': 'user', 'username': 'alice'}, 303, '/login?next=%2Fadmin%2Fshop'),
        (StateUnavailable(), 503, None),
        ({'role': 'admin'}, 200, None),
    ],
)
def test_admin_document_is_guarded_by_the_network_session(
    dist, monkeypatch, outcome, status, location
):
    with TestClient(app_for(outcome, dist, monkeypatch)) as client:
        response = client.get('/admin/shop', follow_redirects=False)
        assert response.status_code == status
        if location:
            assert response.headers['location'] == location
        if status == 200:
            assert '<title>商品管理</title>' in response.text


def test_healthz_and_security_headers(dist, monkeypatch):
    with TestClient(app_for(LoginRequired(), dist, monkeypatch)) as client:
        response = client.get('/healthz')
        assert response.json() == {'ok': True, 'service': 'shop'}
        assert response.headers['cache-control'] == 'no-store'


def test_entrypoint_keeps_the_scheduler_off_unless_enabled(tmp_path, monkeypatch, dist):
    import importlib.util
    import shutil

    import web_api

    captured = {}

    def fake_create_app(services, **kwargs):
        captured.update(kwargs, services=services)
        return object()

    monkeypatch.setattr(web_api, 'create_app', fake_create_app)
    # Production layout: /opt/lancoai/shop/shop_server.py beside panel/current.
    runtime = tmp_path / 'runtime'
    release = runtime / 'panel' / 'releases' / ('a' * 24)
    (release / 'assets').mkdir(parents=True)
    for name in ('index.html', 'manifest.json'):
        shutil.copy(dist / name, release / name)
    (runtime / 'panel' / 'current').symlink_to(f'releases/{"a" * 24}')
    entry = runtime / 'shop_server.py'
    shutil.copy(pathlib.Path(__file__).resolve().parents[1] / 'server' / 'shop_server.py', entry)
    monkeypatch.delenv('SHOP_SCHEDULER', raising=False)
    spec = importlib.util.spec_from_file_location('shop_server_under_test', entry)
    shop_server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shop_server)

    assert shop_server.REACT_DIST == release.resolve()
    assert captured == {}
    with TestClient(shop_server.app) as client:
        assert client.get('/healthz').json()['standby'] is True
        assert client.get('/api/v1/shop/catalog').status_code == 503
        assert client.post('/api/v1/shop/sms/receive', json={}).status_code == 503
    assert shop_server.scheduler_enabled({}) is False
    assert shop_server.scheduler_enabled({'SHOP_SCHEDULER': 'true'}) is False
    assert shop_server.scheduler_enabled({'SHOP_SCHEDULER': '1'}) is True
    shop_server.build_app({'SHOP_SCHEDULER': '1', 'LANCOAI_SESSION_URL': SESSION_URL})
    assert captured['shop_scheduler_enabled'] is True
    assert captured['services'].session_url == SESSION_URL
    assert captured['services'].cache_seconds == 5.0
