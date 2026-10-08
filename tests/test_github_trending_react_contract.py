"""Discovery is a public portal view; the old admin document only redirects."""

from pathlib import Path

from web_api.document_routes import MOVED_DOCUMENTS, REACT_DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]
ROUTE = '/admin/github-trending'


def test_github_trending_moved_from_the_admin_console_to_the_portal():
    assert ROUTE not in REACT_DOCUMENTS
    assert MOVED_DOCUMENTS[ROUTE] == '/?view=trending'
    assert '/admin/github-trending/unknown' not in REACT_DOCUMENTS


def test_github_trending_has_portal_navigation_and_keeps_the_proxied_bookmark():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    navigation = (ROOT / 'frontend/src/shared/navigation.ts').read_text()
    portal = (ROOT / 'frontend/src/features/public/PortalShell.tsx').read_text()
    assert "view === 'trending'" in main and '<GithubTrendingPage' in main
    assert ROUTE not in navigation and ROUTE not in main
    assert 'href="/?view=trending"' in portal and '开源发现' in portal
    for name in ('hysteria-panel-react.conf', 'hysteria-panel-react-https.conf'):
        config = (ROOT / 'nginx' / name).read_text()
        # The application answers the old bookmark with the redirect.
        assert (
            'location = /admin/github-trending {\n        proxy_pass http://127.0.0.1:8083;'
            in config
        )
