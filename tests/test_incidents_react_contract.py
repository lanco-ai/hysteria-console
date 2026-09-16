from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_incidents_react_page_has_triage_tables_and_safe_actions():
    page = (ROOT / 'frontend/src/features/network-admin/incidents/IncidentsPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/incidents/requests.ts').read_text()
    assert "'/api/v1/admin/incidents'" in requests
    assert 'parseIncidents' in requests
    assert '处置候选用户' in page
    assert '暂停 1 小时' in page
    assert '轮换 Token' in page


def test_incidents_react_entry_and_preview_route_exist():
    assert "'/admin/incidents'" in (ROOT / 'frontend/src/main.tsx').read_text()
    assert "'/__react/admin/incidents'" in (ROOT / 'tests/react_preview_server.py').read_text()
