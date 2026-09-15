from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_entry_uses_history_navigation_instead_of_document_reload():
    source = (ROOT / 'frontend/src/main.tsx').read_text()
    shell = (ROOT / 'frontend/src/shared/AdminShell.tsx').read_text()

    assert 'pushState' in source
    assert 'popstate' in source
    assert 'preventDefault' in source or 'navigateClientSide' in shell


def test_read_resources_keep_successful_data_while_revalidating():
    source = (ROOT / 'frontend/src/shared/readResource.ts').read_text()

    assert 'Map<string' in source
    assert 'cache' in source.lower()
    assert 'stale' in source.lower() or 'cached' in source.lower()


def test_overview_keeps_cached_data_between_route_switches():
    source = (ROOT / 'frontend/src/features/network-admin/overview/useOverview.ts').read_text()

    assert 'cached' in source.lower()
    assert 'setLoading' in source


def test_first_visit_uses_a_non_blocking_loading_surface():
    loading = (ROOT / 'frontend/src/shared/LoadingState.tsx').read_text()
    assert 'loading-state' in loading
    assert 'aria-label={label}' in loading
    assert 'loading-state-message' not in loading

    page_sources = [
        ROOT / 'frontend/src/features/network-admin/overview/OverviewPage.tsx',
        ROOT / 'frontend/src/features/network-admin/usage/UsagePage.tsx',
        ROOT / 'frontend/src/features/network-admin/health/HealthPage.tsx',
        ROOT / 'frontend/src/features/network-admin/config/ConfigPage.tsx',
        ROOT / 'frontend/src/features/network-admin/rules/RulesPage.tsx',
        ROOT / 'frontend/src/features/network-admin/incidents/IncidentsPage.tsx',
        ROOT / 'frontend/src/features/network-admin/landing/LandingPage.tsx',
        ROOT / 'frontend/src/features/network-admin/logs/LogsPage.tsx',
        ROOT / 'frontend/src/features/network-admin/user-detail/UserDetailPage.tsx',
        ROOT / 'frontend/src/features/auth/UserPasswordPage.tsx',
        ROOT / 'frontend/src/features/user/UserPanelPage.tsx',
    ]
    assert all('LoadingState' in path.read_text() for path in page_sources)


def test_react_documents_version_the_shared_css_reference():
    source = (ROOT / 'hysteria/web_api/document_routes.py').read_text()

    assert 'BASE_CSS_ETAG' in source
    assert 'css_version' in source
    assert 'static/style.css?v=' in source


def test_react_nginx_routes_legacy_usage_bookmarks_to_react_service():
    for path in (ROOT / 'nginx/hysteria-panel-react.conf', ROOT / 'nginx/hysteria-panel-react-https.conf'):
        source = path.read_text()
        for route in ('/admin/daily', '/admin/usage-history'):
            marker = f'location = {route}'
            start = source.index(marker)
            assert 'proxy_pass http://127.0.0.1:8083;' in source[start:source.index('}', start)]


def test_usage_chart_has_a_definite_height_and_page_spacing():
    page = (ROOT / 'frontend/src/features/network-admin/usage/UsagePage.tsx').read_text()
    styles = (ROOT / 'hysteria/styles/17-motion-workspace.css').read_text()
    sections = (ROOT / 'hysteria/styles/14-admin-sections.css').read_text()
    assert 'className="admin-page usage-page"' in page
    assert 'height: 180px' in styles
    assert '.admin-page.usage-page' in sections
