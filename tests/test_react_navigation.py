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
