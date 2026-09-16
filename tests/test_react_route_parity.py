"""Keep the migrated route inventory aligned across all staged entrypoints."""

from pathlib import Path

from web_api.document_routes import REACT_DOCUMENTS

ROOT = Path(__file__).resolve().parents[1]
REACT_PREVIEW_PREFIX = '/__react'
REACT_ONLY_DOCUMENTS = {'/chat'}
LEGACY_READ_SOURCES = (
    ROOT / 'hysteria/public_page_routes.py',
    ROOT / 'hysteria/auth_routes.py',
    ROOT / 'hysteria/admin_console_routes.py',
    ROOT / 'hysteria/admin_read_routes.py',
    ROOT / 'hysteria/user_panel_routes.py',
)


def _legacy_sources():
    return '\n'.join(path.read_text(encoding='utf-8') for path in LEGACY_READ_SOURCES)


def _react_preview_path(path):
    return f'{REACT_PREVIEW_PREFIX}{path}'


def test_migrated_react_routes_are_registered_in_client_and_preview():
    main = (ROOT / 'frontend/src/main.tsx').read_text(encoding='utf-8')
    preview = (ROOT / 'tests/react_preview_server.py').read_text(encoding='utf-8')

    for path in REACT_DOCUMENTS:
        preview_path = _react_preview_path(path)
        assert f"'{path}'" in main, f'{path} is missing from the React client entry'
        assert f"'{preview_path}'" in preview, (
            f'{preview_path} is missing from the isolated preview'
        )

    assert "'/__react/admin/user/demo_alex'" in main
    assert "'/__react/admin/user/demo_alex'" in preview


def test_each_react_document_has_a_legacy_read_boundary():
    legacy = _legacy_sources()
    for path in REACT_DOCUMENTS:
        if path in REACT_ONLY_DOCUMENTS:
            continue
        assert (
            f"path == '{path}'" in legacy
            or f'path == "{path}"' in legacy
            or f"'{path}':" in legacy
            or f'"{path}":' in legacy
        ), f'{path} has no legacy read-route counterpart'

    # User detail is intentionally dynamic in both implementations.
    assert "path.startswith('/admin/user/')" in legacy
    assert "'/admin/user/{uid}'" in (ROOT / 'hysteria/web_api/document_routes.py').read_text(
        encoding='utf-8'
    )


def test_react_document_boundary_does_not_claim_legacy_downloads():
    documents = set(REACT_DOCUMENTS)
    assert '/admin/usage.csv' not in documents
    assert '/admin/incidents/evidence.json' not in documents
    assert '/user/panel.json' not in documents


def test_retired_codex_route_is_not_registered_in_react_entrypoints():
    main = (ROOT / 'frontend/src/main.tsx').read_text(encoding='utf-8')
    preview = (ROOT / 'tests/react_preview_server.py').read_text(encoding='utf-8')
    assert '/__react/admin/codex' not in main
    assert '/__react/admin/codex' not in preview
