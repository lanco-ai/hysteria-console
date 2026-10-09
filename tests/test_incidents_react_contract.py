from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_incidents_live_in_usage_with_one_ranking_and_safe_actions():
    page = (ROOT / 'frontend/src/features/network-admin/incidents/IncidentsPage.tsx').read_text()
    usage = (ROOT / 'frontend/src/features/network-admin/usage/UsagePage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/incidents/requests.ts').read_text()
    assert "'/api/v1/admin/incidents'" in requests
    assert 'parseIncidents' in requests
    assert '异常与告警' in page
    assert '暂停 1 小时' in page
    assert '轮换 Token' in page
    # The usage page shows the 24-hour ranking once, with the incident actions.
    assert '用户排行 · 近 24 小时' in usage
    assert 'IncidentsSection' in usage and 'ActionButton' in usage
    assert '处置候选用户' not in page + usage
    assert '线路质量摘要' not in page + usage


def test_incidents_react_entry_and_preview_route_exist():
    assert "'/admin/incidents'" in (ROOT / 'frontend/src/main.tsx').read_text()
    assert "'/__react/admin/incidents'" in (ROOT / 'tests/react_preview_server.py').read_text()
