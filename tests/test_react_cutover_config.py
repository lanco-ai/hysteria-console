"""Contracts for the opt-in dual-backend React route templates."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = (
    ROOT / 'nginx/hysteria-panel-react.conf',
    ROOT / 'nginx/hysteria-panel-react-https.conf',
)
REACT_DOCUMENTS = (
    '/',
    '/login',
    '/auth',
    '/user/login',
    '/logout',
    '/user/logout',
    '/user/change-password',
    '/user/panel',
    '/admin',
    '/admin/logs',
    '/admin/settings',
    '/admin/usage',
    '/admin/health',
    '/admin/incidents',
    '/admin/config',
    '/admin/rules',
    '/admin/landing-egresses',
    '/admin/chat',
)


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


def test_react_documents_and_api_use_8083():
    for path in TEMPLATES:
        text = path.read_text(encoding='utf-8')
        if path.name.endswith('-https.conf'):
            assert 'location = / {\n        return 302 https://lancoai.site/admin/chat;\n    }' in text
        else:
            assert 'location = / {\n        proxy_pass http://127.0.0.1:8083;' in text
        for document in REACT_DOCUMENTS[1:]:
            assert f'location = {document} {{\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location = /api/v1 {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location ^~ /api/v1/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location ^~ /api/chat/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        assert 'location = /api/chat/completions {' in text
        assert 'proxy_buffering off;' in text
        assert 'proxy_read_timeout 660s;' in text
        assert (
            'location ^~ /static/react/assets/ {\n        proxy_pass http://127.0.0.1:8083;' in text
        )
        assert 'location = /chat {\n        return 404;\n    }' in text
        assert 'location = /chat {\n        proxy_pass' not in text


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
