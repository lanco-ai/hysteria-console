"""Safety contracts for the staged React release pointer."""

import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/hy2_panel_release.py'
spec = importlib.util.spec_from_file_location('hy2_panel_release', SCRIPT)
assert spec and spec.loader
release = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = release
spec.loader.exec_module(release)


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / 'dist'
    assets = dist / 'assets'
    assets.mkdir(parents=True)
    (dist / 'index.html').write_text(
        '<!doctype html><html><head></head><body><div id="root"></div></body></html>',
        encoding='utf-8',
    )
    (assets / 'index.js').write_text('export default 1;', encoding='utf-8')
    (assets / 'index.css').write_text('body { color: black; }', encoding='utf-8')
    (dist / 'manifest.json').write_text(
        json.dumps(
            {
                'index.html': {
                    'file': 'assets/index.js',
                    'css': ['assets/index.css'],
                    'isEntry': True,
                }
            }
        ),
        encoding='utf-8',
    )
    return dist


def test_validate_release_returns_deterministic_immutable_manifest(tmp_path):
    dist = _dist(tmp_path)

    first = release.validate_dist(dist)
    second = release.validate_dist(dist)

    assert first == second
    assert len(first.release_id) == 24
    assert all(len(digest) == hashlib.sha256().digest_size * 2 for _, digest, _ in first.files)
    assert all(mode == 0o644 for _, _, mode in first.files)


def test_validate_release_rejects_symlink_and_source_map(tmp_path):
    dist = _dist(tmp_path)
    (dist / 'assets' / 'escape.js').symlink_to('/etc/passwd')
    with pytest.raises(release.ReleaseError, match='symlink'):
        release.validate_dist(dist)

    (dist / 'assets' / 'escape.js').unlink()
    (dist / 'assets' / 'bundle.js.map').write_text('{}', encoding='utf-8')
    with pytest.raises(release.ReleaseError, match='unsupported'):
        release.validate_dist(dist)


def test_install_and_activate_use_only_a_relative_managed_pointer(tmp_path):
    dist = _dist(tmp_path)
    root = tmp_path / 'panel'

    manifest = release.install_release(dist, root)
    assert (root / 'releases' / manifest.release_id / 'index.html').is_file()
    assert not any(path.name.startswith('.staging-') for path in (root / 'releases').iterdir())

    release.activate_release(root, manifest.release_id)
    current = root / 'current'
    assert current.is_symlink()
    assert os.readlink(current) == f'releases/{manifest.release_id}'


def test_activate_rejects_unknown_or_out_of_tree_release(tmp_path):
    root = tmp_path / 'panel'
    (root / 'releases').mkdir(parents=True)
    with pytest.raises(release.ReleaseError, match='unknown release'):
        release.activate_release(root, '0' * 24)

    (root / 'current').symlink_to('/etc')
    with pytest.raises(release.ReleaseError, match='unknown release'):
        release.activate_release(root, '1' * 24)


def test_validate_release_rejects_forbidden_files_and_missing_entry(tmp_path):
    dist = _dist(tmp_path)
    (dist / '.env').write_text('secret', encoding='utf-8')
    with pytest.raises(release.ReleaseError, match='unsupported'):
        release.validate_dist(dist)

    (dist / '.env').unlink()
    (dist / 'index.html').unlink()
    with pytest.raises(release.ReleaseError, match='index.html'):
        release.validate_dist(dist)


def test_validate_release_rejects_manifest_reference_to_missing_asset(tmp_path):
    dist = _dist(tmp_path)
    (dist / 'manifest.json').write_text(
        json.dumps(
            {
                'index.html': {
                    'file': 'assets/missing.js',
                    'css': ['assets/index.css'],
                    'isEntry': True,
                }
            }
        ),
        encoding='utf-8',
    )
    with pytest.raises(release.ReleaseError, match='missing manifest asset'):
        release.validate_dist(dist)


def test_validate_release_requires_css_for_the_react_entry(tmp_path):
    dist = _dist(tmp_path)
    payload = json.loads((dist / 'manifest.json').read_text(encoding='utf-8'))
    payload['index.html']['css'] = []
    (dist / 'manifest.json').write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(release.ReleaseError, match='stylesheet'):
        release.validate_dist(dist)
