from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_user_detail_page_and_parser_are_registered():
    page = ROOT / 'frontend/src/features/network-admin/user-detail/UserDetailPage.tsx'
    requests = ROOT / 'frontend/src/features/network-admin/user-detail/requests.ts'
    types = ROOT / 'frontend/src/features/network-admin/user-detail/types.ts'
    assert page.is_file() and requests.is_file() and types.is_file()
    assert "'/api/v1/admin/user/'" in requests.read_text()
    assert 'parseUserDetail' in requests.read_text()
    assert '个人 7×24 热图' in page.read_text()
    assert 'hourly-bars' in page.read_text()


def test_user_detail_route_supports_preview_and_direct_paths():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    documents = (ROOT / 'hysteria/web_api/document_routes.py').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    assert r'^\/admin\/user\/[^/]+$' in main
    assert "'/admin/user/{uid}'" in documents
    assert '/__react/admin/user/' in preview
