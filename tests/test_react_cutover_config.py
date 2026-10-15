"""Contracts for the opt-in dual-backend React route templates."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    ROOT / 'nginx/hysteria-panel-react.conf',
    ROOT / 'nginx/hysteria-panel-react-https.conf',
)
NETWORK = 'proxy_pass http://127.0.0.1:8083;'
SHOP = 'proxy_pass http://127.0.0.1:8084;'
WORKBENCH = 'proxy_pass http://127.0.0.1:8085;'
# Exact React documents and the service that owns each one.
REACT_DOCUMENTS = {
    '/': SHOP,
    '/admin/shop': SHOP,
    '/trending': WORKBENCH,
    '/admin/plans': WORKBENCH,
    '/admin/chat': WORKBENCH,
    '/admin/video': WORKBENCH,
    '/admin/services': WORKBENCH,
    '/login': NETWORK,
    '/auth': NETWORK,
    '/user/login': NETWORK,
    '/logout': NETWORK,
    '/user/logout': NETWORK,
    '/user/change-password': NETWORK,
    '/user/panel': NETWORK,
    '/admin': NETWORK,
    '/admin/logs': NETWORK,
    '/admin/settings': NETWORK,
    '/admin/usage': NETWORK,
    '/admin/health': NETWORK,
    '/admin/incidents': NETWORK,
    '/admin/config': NETWORK,
    '/admin/rules': NETWORK,
    '/admin/landing-egresses': NETWORK,
    '/admin/github-trending': NETWORK,
}
API_LOCATIONS = {
    'location = /api/v1 {': NETWORK,
    'location ^~ /api/v1/ {': NETWORK,
    'location ^~ /api/v1/shop/ {': SHOP,
    'location ^~ /api/v1/admin/services {': WORKBENCH,
    'location ^~ /api/v1/github-trending {': WORKBENCH,
    'location = /api/plans {': WORKBENCH,
    'location ^~ /api/plans/ {': WORKBENCH,
    'location = /api/journal {': WORKBENCH,
    'location ^~ /api/journal/ {': WORKBENCH,
    'location ^~ /api/ai/ {': WORKBENCH,
    'location ^~ /api/chat/ {': WORKBENCH,
    'location = /api/chat/completions {': WORKBENCH,
    'location = /api/chat/documents/upload {': WORKBENCH,
    'location ^~ /api/video/ {': WORKBENCH,
    'location ^~ /static/shop/ {': SHOP,
    'location ^~ /static/workbench/ {': WORKBENCH,
    'location ^~ /static/react/assets/ {': NETWORK,
}


def _block(text, opening):
    assert text.count(f'    {opening}\n') == 1, opening
    start = text.index(f'    {opening}\n')
    return text[start : text.index('    }\n', start)]


def test_templates_are_explicitly_staged_and_preserve_listener_placeholders():
    for path in TEMPLATES:
        text = path.read_text(encoding='utf-8')
        assert 'installs' in text and 'HY_UNIFIED_FASTAPI=1' in text
        assert '__HY_SERVER_HOST__' in text
        if path.name.endswith('-https.conf'):
            assert 'listen __HY_HTTPS_PORT__ ssl;' in text
            assert '__HY_TLS_CERT__' in text
            assert '__HY_TLS_KEY__' in text
            assert 'listen 443' not in text
        else:
            assert 'listen 80;' in text


def test_documents_and_apis_route_to_the_owning_service():
    for path in TEMPLATES:
        text = path.read_text(encoding='utf-8')
        for document, target in REACT_DOCUMENTS.items():
            assert target in _block(text, f'location = {document} {{'), (path.name, document)
        for opening, target in API_LOCATIONS.items():
            assert target in _block(text, opening), (path.name, opening)
        chat = _block(text, 'location ^~ /api/chat/ {')
        assert 'proxy_buffering off;' in chat and 'proxy_read_timeout 660s;' in chat
        assert 'client_max_body_size 10m;' in _block(
            text, 'location = /api/chat/documents/upload {'
        )
        assert 'location = /chat {\n        return 404;\n    }' in text
        assert 'location = /chat {\n        proxy_pass' not in text
        # Service-to-service endpoints never reach the public.
        assert 'location ^~ /internal/ {\n        return 404;\n    }' in text


def test_all_documents_and_downloads_use_unified_fastapi():
    for path in TEMPLATES:
        text = path.read_text(encoding='utf-8')
        assert (
            'location ~ ^/admin/user/[^/]+\\.json$ {\n        proxy_pass http://127.0.0.1:8083;'
            in text
        )
        assert 'location ~ ^/admin/user/[^/]+$ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location ^~ /sub/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location ^~ /panel/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location ^~ /static/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location / {\n        proxy_pass http://127.0.0.1:8083;' in text
