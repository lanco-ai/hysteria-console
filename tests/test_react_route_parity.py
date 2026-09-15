"""Keep the migrated route inventory aligned across client and preview entrypoints."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATED_REACT_PATHS = (
    '/__react/',
    '/__react/login',
    '/__react/logout',
    '/__react/user/logout',
    '/__react/user/change-password',
    '/__react/user/panel',
    '/__react/admin',
    '/__react/admin/logs',
    '/__react/admin/settings',
    '/__react/admin/usage',
    '/__react/admin/health',
    '/__react/admin/incidents',
    '/__react/admin/config',
    '/__react/admin/rules',
    '/__react/admin/landing-egresses',
)


def test_migrated_react_routes_are_registered_in_client_and_preview():
    main = (ROOT / 'frontend/src/main.tsx').read_text(encoding='utf-8')
    preview = (ROOT / 'tests/react_preview_server.py').read_text(encoding='utf-8')

    for path in MIGRATED_REACT_PATHS:
        assert f"'{path}'" in main, f'{path} is missing from the React client entry'
        assert f"'{path}'" in preview, f'{path} is missing from the isolated preview'


def test_retired_codex_route_is_not_registered_in_react_entrypoints():
    main = (ROOT / 'frontend/src/main.tsx').read_text(encoding='utf-8')
    preview = (ROOT / 'tests/react_preview_server.py').read_text(encoding='utf-8')
    assert '/__react/admin/codex' not in main
    assert '/__react/admin/codex' not in preview
