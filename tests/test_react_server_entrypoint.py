"""Contracts for the opt-in React ASGI runtime entrypoint."""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_entrypoint():
    subscription = types.ModuleType('subscription_service')
    import web_api

    original = {
        'subscription_service': sys.modules.get('subscription_service'),
        'web_api.create_app': web_api.create_app,
    }
    sys.modules['subscription_service'] = subscription
    web_api.create_app = lambda *_args, **_kwargs: object()
    try:
        spec = importlib.util.spec_from_file_location(
            'react_server_test_entrypoint', ROOT / 'hysteria/react_server.py'
        )
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        if original['subscription_service'] is None:
            sys.modules.pop('subscription_service', None)
        else:
            sys.modules['subscription_service'] = original['subscription_service']
        web_api.create_app = original['web_api.create_app']


def _release(root: Path, release_id: str = 'a' * 24) -> Path:
    target = root / 'panel' / 'releases' / release_id
    (target / 'assets').mkdir(parents=True)
    (target / 'index.html').write_text('index', encoding='utf-8')
    (target / 'assets' / 'index.js').write_text('js', encoding='utf-8')
    (target / 'manifest.json').write_text(
        json.dumps({'index.html': {'file': 'assets/index.js', 'isEntry': True}}),
        encoding='utf-8',
    )
    return target


def test_react_server_uses_authoritative_service_and_build_assets():
    source = (ROOT / 'hysteria/react_server.py').read_text(encoding='utf-8')
    assert 'LegacyPanelServices(subscription_service)' in source
    assert 'react_dist=REACT_DIST' in source
    assert '_REACT_DIST_CANDIDATES' in source
    assert 'ThreadingHTTPServer' not in source
    assert "'panel' / 'current'" in source
    assert 'releases' in source


def test_react_server_prefers_a_valid_managed_release_pointer(tmp_path):
    module = _load_entrypoint()
    target = _release(tmp_path)
    (tmp_path / 'panel' / 'current').symlink_to('releases/' + target.name)

    assert module.resolve_react_dist(tmp_path) == target.resolve()


def test_react_server_rejects_an_unmanaged_release_pointer(tmp_path):
    module = _load_entrypoint()
    (tmp_path / 'panel').mkdir()
    (tmp_path / 'panel' / 'current').symlink_to('/etc')

    with pytest.raises(RuntimeError, match='outside the managed release root'):
        module.resolve_react_dist(tmp_path)


def test_react_server_rejects_a_release_without_manifest(tmp_path):
    module = _load_entrypoint()
    target = _release(tmp_path)
    (target / 'manifest.json').unlink()
    (tmp_path / 'panel' / 'current').symlink_to('releases/' + target.name)

    with pytest.raises(RuntimeError, match='manifest.json'):
        module.resolve_react_dist(tmp_path)


def test_react_server_falls_back_to_source_dist_without_a_pointer(tmp_path):
    module = _load_entrypoint()
    source_dist = tmp_path / 'frontend' / 'dist'
    (source_dist / 'assets').mkdir(parents=True)
    (source_dist / 'index.html').write_text('index', encoding='utf-8')
    (source_dist / 'manifest.json').write_text('{}', encoding='utf-8')

    assert module.resolve_react_dist(tmp_path) == source_dist


def test_react_systemd_unit_is_loopback_staged_and_single_worker():
    unit = (ROOT / 'systemd/hysteria-react.service').read_text(encoding='utf-8')
    assert '--host 127.0.0.1 --port 8083 --workers 1' in unit
    assert 'WorkingDirectory=/root/hysteria' in unit
    assert 'Requires=hysteria-subscription.service' not in unit
    assert 'TasksMax=32' in unit
    assert 'deploy.sh' in unit
