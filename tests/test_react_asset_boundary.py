"""React documents must own their runtime assets."""

import json

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


def test_react_css_entry_lists_every_manifest_section_in_order():
    names = json.loads((ROOT / 'hysteria/styles/manifest.json').read_text(encoding='utf-8'))
    entry = (ROOT / 'frontend/src/styles/index.css').read_text(encoding='utf-8')
    positions = [entry.index(f'../../../hysteria/styles/{name}') for name in names]
    assert positions == sorted(positions)
