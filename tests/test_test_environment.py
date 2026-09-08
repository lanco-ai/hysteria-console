"""Test bootstrap must retain real operating-system lock semantics."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(sys.platform != 'linux', reason='Requires Linux file locks')
def test_bootstrap_preserves_cross_process_file_lock(tmp_path):
    # A fresh interpreter catches dependence on pytest's prior imports.
    code = r'''
import runpy
import sys
import pytest
# pytest may itself import fcntl. Force the cold state after importing it,
# otherwise restoring the original buggy condition could escape this test.
sys.modules.pop('fcntl', None)
runpy.run_path(sys.argv[1])
import fcntl
import subprocess
with open(sys.argv[2], "w") as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    child = subprocess.run([sys.executable, "-c", """
import fcntl, sys
with open(sys.argv[1]) as lock:
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(0)
    sys.exit(1)
""", sys.argv[2]], timeout=5)
    assert child.returncode == 0, "bootstrap replaced real locks with no-ops"
'''
    result = subprocess.run(
        [sys.executable, '-c', code, str(ROOT / 'tests/conftest.py'), str(tmp_path / 'lock')],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_unsupported_platform_skips_lock_operations_instead_of_passing():
    code = r"""
import importlib.abc
import runpy
import sys
import pytest
class MissingFcntl(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fcntl":
            raise ModuleNotFoundError("No fcntl", name="fcntl")
sys.modules.pop("fcntl", None)
sys.meta_path.insert(0, MissingFcntl())
runpy.run_path(sys.argv[1])
import fcntl
try:
    fcntl.flock(0, fcntl.LOCK_EX)
except pytest.skip.Exception:
    pass
else:
    raise AssertionError("Unavailable locks must not silently succeed")
"""
    result = subprocess.run(
        [sys.executable, '-c', code, str(ROOT / 'tests/conftest.py')],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
