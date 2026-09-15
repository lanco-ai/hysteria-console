"""React routing-rule page contracts."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_rules_page_exposes_all_mutation_controls_and_api_boundaries():
    page = (ROOT / 'frontend/src/features/network-admin/rules/RulesPage.tsx').read_text()
    requests = (ROOT / 'frontend/src/features/network-admin/rules/requests.ts').read_text()
    assert '/api/v1/admin/rules/add' in requests
    assert '/api/v1/admin/rules/delete' in requests
    assert '/api/v1/admin/rules/pack' in requests
    assert '添加自定义规则' in page
    assert '应用规则包' in page
    assert '删除' in page
    assert '内置' in page
    assert 'useFormAction' in page


def test_rules_page_keeps_raw_editor_and_route_entry():
    page = (ROOT / 'frontend/src/features/network-admin/rules/RulesPage.tsx').read_text()
    assert '覆盖全部规则' in page
    assert "'/__react/admin/rules'" in (ROOT / 'frontend/src/main.tsx').read_text()
