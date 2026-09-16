from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_config_react_page_has_revision_safe_editor():
    page = (ROOT / 'frontend/src/features/network-admin/config/ConfigPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/config/requests.ts').read_text()
    assert "'/api/v1/admin/config'" in requests
    assert 'template_revision' in page
    assert '保存订阅模板' in page
    assert '版本：' not in page


def test_rules_react_page_has_ordered_rule_editor():
    page = (ROOT / 'frontend/src/features/network-admin/rules/RulesPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/rules/requests.ts').read_text()
    assert "'/api/v1/admin/rules'" in requests
    assert '当前规则列表' in page
    assert '覆盖全部规则' in page


def test_config_and_rules_react_routes_are_registered():
    entry = (ROOT / 'frontend/src/main.tsx').read_text()
    preview = (ROOT / 'tests/react_preview_server.py').read_text()
    for path in ('config', 'rules'):
        assert f"'/__react/admin/{path}'" in entry
        assert f"'/__react/admin/{path}'" in preview
