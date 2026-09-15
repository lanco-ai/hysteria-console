"""React documents must own their runtime assets."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_react_entry_does_not_reference_legacy_assets():
    html = (ROOT / 'frontend/index.html').read_text(encoding='utf-8')
    assert '/static/style.css' not in html
    assert '/static/shell.js' not in html
    assert '/static/ui-core.js' not in html


def test_built_react_document_contains_hashed_css_asset():
    html = (ROOT / 'frontend/dist/index.html').read_text(encoding='utf-8')
    assert '/static/react/assets/' in html
    assert '.css' in html
    assert '/static/style.css' not in html
    assert '/static/shell.js' not in html
    assert '/static/ui-core.js' not in html
