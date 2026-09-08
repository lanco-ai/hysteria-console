"""Shared pytest setup.

Keeps tests runnable on older distro pytest packages that do not understand
pytest.ini's `pythonpath` option, and stubs `fcntl` on Windows so production
modules can be imported for testing.
"""

import importlib
import sys
import types
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
