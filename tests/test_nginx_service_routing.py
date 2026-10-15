"""Every public path reaches the one service that owns it (service split).

The checks run a small model of nginx location selection over the HTTPS
vhost templates: an exact match wins, then the longest prefix; a ``^~``
prefix ends the search, otherwise the first matching regex in file order
wins over a plain prefix. hysteria-panel-https.conf is the vhost that
hy2-enable-https.sh installs; the react templates are what deploy.sh
installs in unified mode.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    'hysteria-panel-https.conf',
    'hysteria-panel-react-https.conf',
    'hysteria-panel-react.conf',
)
NETWORK, SHOP, WORKBENCH, NOT_PUBLIC = 8083, 8084, 8085, 404
LOCATION = re.compile(r'^    location (?:(=|\^~|~\*?) )?(\S+) \{\n(.*?)^    \}\n', re.M | re.S)
TARGET = re.compile(r'proxy_pass http://127\.0\.0\.1:(\d+);|return (404);')

# Sample paths for every page and API prefix of docs/service-split-design.md section 4.
EXPECTED = {
    SHOP: (
        '/',
        '/admin/shop',
        '/api/v1/shop/catalog',
        '/api/v1/shop/visit',
        '/api/v1/shop/admin',
        '/api/v1/shop/admin/sources/anli',
        '/api/v1/shop/refresh',
        '/static/shop/assets/index.js',
    ),
    WORKBENCH: (
        '/trending',
        '/admin/plans',
        '/admin/chat',
        '/admin/video',
        '/admin/services',
        '/api/plans',
        '/api/plans/reminders',
        '/api/journal',
        '/api/journal/entries',
        '/api/chat/settings',
        '/api/chat/completions',
        '/api/chat/documents/upload',
        '/api/chat/workspace/conversations',
        '/api/video/workflows',
        '/api/ai/services',
        '/api/v1/admin/services',
        '/api/v1/admin/services/probe',
        '/api/v1/github-trending',
        '/api/v1/github-trending/avatar/octocat',
        '/static/workbench/assets/index.js',
    ),
    NETWORK: (
        '/login',
        '/logout',
        '/auth',
        '/readyz',
        '/user/login',
        '/user/panel',
        '/user/change-password',
        '/admin',
        '/admin/usage',
        '/admin/config',
        '/admin/health',
        '/admin/settings',
        '/admin/github-trending',
        '/admin/user/alice',
        '/api/v1',
        '/api/v1/session',
        '/api/v1/login',
        '/api/v1/admin/rules',
        '/api/v1/admin/agent/plan',
        '/sub/token/clash.yaml',
        '/panel/exchange',
        '/static/react/assets/index.js',
    ),
    NOT_PUBLIC: ('/internal/v1/agent-intent', '/internal/'),
}


def _locations(name):
    text = (ROOT / 'nginx' / name).read_text(encoding='utf-8')
    locations = []
    for kind, pattern, body in LOCATION.findall(text):
        targets = TARGET.findall(body)
        if pattern.startswith('/.well-known/'):
            continue
        assert len(targets) == 1, (name, pattern)
        port, status = targets[0]
        locations.append((kind, pattern, int(port or status), body))
    return locations


def _route(locations, path):
    for kind, pattern, target, _ in locations:
        if kind == '=' and pattern == path:
            return target
    prefixes = [item for item in locations if item[0] in ('', '^~') and path.startswith(item[1])]
    longest = max(prefixes, key=lambda item: len(item[1]), default=None)
    if longest is not None and longest[0] == '^~':
        return longest[2]
    for kind, pattern, target, _ in locations:
        flags = re.I if kind == '~*' else 0
        if kind in ('~', '~*') and re.search(pattern, path, flags):
            return target
    assert longest is not None, path
    return longest[2]


@pytest.mark.parametrize('name', TEMPLATES)
def test_every_page_and_api_prefix_reaches_its_service(name):
    locations = _locations(name)
    for target, paths in EXPECTED.items():
        for path in paths:
            assert _route(locations, path) == target, (name, path)


@pytest.mark.parametrize('name', TEMPLATES)
def test_each_location_is_declared_once(name):
    keys = [(kind, pattern) for kind, pattern, _, _ in _locations(name)]
    assert len(keys) == len(set(keys)), name


def test_live_vhost_keeps_its_chat_upload_and_stream_settings_on_the_workbench():
    locations = {(kind, pattern): body for kind, pattern, _, body in _locations(TEMPLATES[0])}
    upload = locations[('=', '/api/chat/documents/upload')]
    assert 'client_max_body_size 10m;' in upload and 'proxy_read_timeout 60s;' in upload
    stream = locations[('^~', '/api/chat/')]
    for directive in (
        'proxy_buffering off;',
        'proxy_cache off;',
        'proxy_send_timeout 660s;',
        'proxy_read_timeout 660s;',
    ):
        assert directive in stream, directive


def test_live_vhost_applies_limits_and_proxy_headers_to_every_location():
    text = (ROOT / 'nginx' / TEMPLATES[0]).read_text(encoding='utf-8')
    server_level = text[: text.index('    location ')]
    for directive in (
        'limit_conn hy2_panel_conn 20;',
        'limit_req zone=hy2_panel_req burst=40 nodelay;',
        'proxy_set_header Host __HY_SERVER_HOST__;',
        'proxy_set_header X-Forwarded-Proto https;',
        'proxy_set_header X-Forwarded-Port $hy2_https_external_port;',
    ):
        assert directive in server_level, directive
    # A location-level limit or header would silently drop the inherited ones.
    for _, pattern, _, body in _locations(TEMPLATES[0]):
        assert 'limit_req ' not in body and 'limit_conn ' not in body, pattern
        assert 'proxy_set_header' not in body and 'add_header' not in body, pattern
