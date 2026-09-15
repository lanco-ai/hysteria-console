from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_landing_react_page_has_public_node_table_and_management_actions():
    page = (ROOT / 'frontend/src/features/network-admin/landing/LandingPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/landing/requests.ts').read_text()
    assert "'/api/v1/admin/landing-egresses'" in requests
    assert '家宽出口节点' in page
    assert '保存节点' in page
    assert '用户授权' in page
    assert "mutateLanding(action" in page
    assert "'/api/v1/admin/landing-egresses/save'" in requests
    assert "'/api/v1/admin/landing-egresses/access'" in requests


def test_landing_react_route_is_registered():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    assert '/__react/admin/landing-egresses' in main
    assert '/__react/admin/landing-egresses' in preview
