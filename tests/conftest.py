"""Shared pytest setup.

Keeps tests runnable on older distro pytest packages that do not understand
pytest.ini's `pythonpath` option, and stubs `fcntl` on Windows so production
modules can be imported for testing.
"""
from pathlib import Path
import sys
import types


ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / 'hysteria'):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _install_fcntl_stub() -> None:
    if 'fcntl' in sys.modules:
        return
    stub = types.ModuleType('fcntl')
    stub.LOCK_EX = 0
    stub.LOCK_UN = 0
    stub.flock = lambda *_a, **_k: None
    sys.modules['fcntl'] = stub


_install_fcntl_stub()
