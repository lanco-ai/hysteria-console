"""开源发现 is this service's public portal page at /trending."""

from pathlib import Path

from web_api.document_routes import LOGIN_DOCUMENTS, REACT_DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]


def test_trending_is_a_public_portal_document_with_the_login_limit():
    title, body_class, guard = REACT_DOCUMENTS['/trending']
    assert guard is None
    assert 'page-portal' in body_class
    assert '/trending' in LOGIN_DOCUMENTS
    assert '/admin/github-trending' not in REACT_DOCUMENTS


def test_trending_has_portal_navigation_and_no_admin_console_entry():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    navigation = (ROOT / 'frontend/src/shared/navigation.ts').read_text()
    portal = (ROOT / 'frontend/src/features/public/PortalShell.tsx').read_text()
    assert '<GithubTrendingPage' in main and "'/trending'" in main
    assert '/admin/github-trending' not in navigation and '/admin/github-trending' not in main
    assert 'href="/trending"' in portal and '开源发现' in portal
    # Shopping and SMS codes are the shop service's pages: full page loads.
    assert 'href="/"' in portal and 'href="/?view=sms"' in portal
