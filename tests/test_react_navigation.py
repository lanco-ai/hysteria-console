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
