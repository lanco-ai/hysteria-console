from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_health_react_page_uses_structured_api_and_accessible_refresh():
    page = (ROOT / 'frontend/src/features/network-admin/health/HealthPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/health/requests.ts').read_text()
    assert "'/api/v1/admin/health'" in requests
    assert 'parseHealth' in requests
    assert '立即刷新' in page
    assert '核心服务' in page


def test_health_react_entry_and_preview_route_exist():
    assert "'/__react/admin/health'" in (ROOT / 'frontend/src/main.tsx').read_text()
    assert "'/__react/admin/health'" in (ROOT / 'tests/react_preview_server.py').read_text()

