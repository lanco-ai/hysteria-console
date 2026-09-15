from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_user_panel_react_page_has_usage_subscription_and_egress_sections():
    page = (ROOT / 'frontend/src/features/user/UserPanelPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/user/requests.ts').read_text()
    assert "'/api/v1/user/panel'" in requests
    assert '本周期用量' in page
    assert '订阅链接' in page
    assert '显示二维码' in page
    assert '家宽出口' in page


def test_user_panel_react_route_is_registered():
    main = (ROOT / 'frontend/src/main.tsx').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    assert '/__react/user/panel' in main
    assert '/__react/user/panel' in preview
