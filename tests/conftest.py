"""Shared pytest setup.

Keeps tests runnable on older distro pytest packages that do not understand
pytest.ini's `pythonpath` option, and stubs `fcntl` on Windows so production
modules can be imported for testing.
"""

import importlib
import os
import sys
import types
from functools import partial
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'hysteria'):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _install_fcntl_stub() -> None:
    try:
        importlib.import_module('fcntl')
        return
    except ModuleNotFoundError as exc:
        if exc.name != 'fcntl':
            raise

    def unsupported_lock(*_args, **_kwargs):
        pytest.skip('Real POSIX file locks are unavailable on this platform')

    stub = types.ModuleType('fcntl')
    stub.LOCK_SH = 1
    stub.LOCK_EX = 2
    stub.LOCK_NB = 4
    stub.LOCK_UN = 8
    stub.flock = unsupported_lock
    sys.modules['fcntl'] = stub


_install_fcntl_stub()


@pytest.fixture
def isolated_panel_state(tmp_path, monkeypatch):
    """Opt-in complete core state for positive render tests only."""
    import subscription_service as ss

    root = tmp_path / 'panel'
    for name, value in vars(ss).copy().items():
        if not name.isupper() or not isinstance(value, Path) or name == '_STATIC_DIR':
            continue
        try:
            relative = value.relative_to('/root/hysteria')
        except ValueError:
            relative = Path('xray') / value.name
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(ss, name, destination)
    monkeypatch.setattr(ss, 'HY_API_SECRET_FILE', str(root / 'api_secret'))
    for name in ('USERS_FILE', 'META_FILE', 'USAGE_FILE', 'USAGE_DAILY_FILE'):
        getattr(ss, name).write_text('{}', encoding='utf-8')
    return root


@pytest.fixture
def xray_runtime_group(monkeypatch):
    """Use a real existing group while retaining real ownership checks."""
    import grp

    import xray_config as xc

    monkeypatch.setattr(xc, 'CONFIG_GROUP', grp.getgrgid(os.getgid()).gr_name)


@pytest.fixture(autouse=True)
def isolated_default_video_stores(tmp_path, monkeypatch):
    """Only route defaults are redirected; injected stores/runners stay real."""
    import web_api.video_routes as routes
    from web_api.video_service import AssetStore, RunService, VideoSettingsStore, WorkflowStore

    root = tmp_path / 'default-video'
    monkeypatch.setattr(
        routes, 'VideoSettingsStore', partial(VideoSettingsStore, path=root / 'settings.json')
    )
    monkeypatch.setattr(
        routes, 'WorkflowStore', partial(WorkflowStore, path=root / 'workflows.json')
    )
    monkeypatch.setattr(routes, 'AssetStore', partial(AssetStore, root=root / 'assets'))
    monkeypatch.setattr(routes, 'RunService', partial(RunService, path=root / 'runs.json'))
