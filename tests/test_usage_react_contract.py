"""Static contracts for the first React usage page slice."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_usage_feature_has_structured_validator_and_refresh_boundary():
    requests = (ROOT / 'frontend/src/features/network-admin/usage/requests.ts').read_text()
    page = (ROOT / 'frontend/src/features/network-admin/usage/UsagePage.tsx').read_text()
    assert "'/api/v1/admin/usage?summary=yes'" in requests
    assert 'parseUsage' in requests
    assert 'id="usage-history"' in page
    assert '立即刷新' in page
    assert '7 天 × 24 小时' in page


def test_react_entry_and_preview_register_usage_route():
    entry = (ROOT / 'frontend/src/main.tsx').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    assert "'/admin/usage'" in entry
    assert "'/__react/admin/usage'" in preview
