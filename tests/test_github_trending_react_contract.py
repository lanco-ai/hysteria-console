"""The discovery page is an exact, administrator-only React document."""

from pathlib import Path

from web_api.document_routes import REACT_DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]
ROUTE = '/admin/github-trending'


def test_github_trending_is_exact_admin_document():
    assert REACT_DOCUMENTS[ROUTE] == ('GitHub 热榜', 'has-shell', 'admin')
    assert '/admin/github-trending/unknown' not in REACT_DOCUMENTS


def test_github_trending_has_matching_navigation_preview_and_proxy_routes():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    navigation = (ROOT / 'frontend/src/shared/navigation.ts').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    assert "'/admin/github-trending'" in main
    assert "href: '/admin/github-trending', label: '开源发现'" in navigation
    assert "'/__react/admin/github-trending'" in preview
    assert "'/admin/github-trending'" in preview
    for name in ('hysteria-panel-react.conf', 'hysteria-panel-react-https.conf'):
        config = (ROOT / 'nginx' / name).read_text()
        assert (
            'location = /admin/github-trending {\n        proxy_pass http://127.0.0.1:8083;'
            in config
        )
