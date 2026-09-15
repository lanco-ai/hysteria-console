"""The React document boundary is separate from compatibility endpoints."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_readme_describes_production_asset_and_compatibility_boundaries():
    readme = (ROOT / 'frontend/README.md').read_text(encoding='utf-8')
    assert readme.startswith('# React frontend')
    assert 'production' in readme.lower()
    assert '/static/react/assets/' in readme
    assert '`/sub/<user>`' in readme
    assert '`/panel/<user>`' in readme
    assert 'does not cut production traffic over to React' not in readme


def test_legacy_routes_are_not_reclassified_as_react_documents():
    from web_api.document_routes import REACT_DOCUMENTS

    assert '/sub/example' not in REACT_DOCUMENTS
    assert '/panel/example' not in REACT_DOCUMENTS
    assert '/admin/usage.csv' not in REACT_DOCUMENTS
    assert '/admin/incidents/evidence.json' not in REACT_DOCUMENTS
