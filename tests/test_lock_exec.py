"""Kernel-level regressions for the shared privileged lock executor."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "hy2-lock-exec.py"


pytestmark = pytest.mark.skipif(
    os.geteuid() != 0,
    reason="the production lock contract intentionally requires root",
)


def _private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "locks"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    return parent


def _run(lock_file: Path, *command: str, timeout: str = "0"):
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            timeout,
            "--",
            *command,
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_new_lock_is_0600_and_survives_exec_without_being_truncated(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"

    first = _run(lock_file, "/bin/true")

    assert first.returncode == 0, first.stderr
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600

    canary = b"existing-lock-content-must-survive\n"
    lock_file.write_bytes(canary)
    lock_file.chmod(0o600)
    second = _run(lock_file, "/bin/true")

    assert second.returncode == 0, second.stderr
    assert lock_file.read_bytes() == canary


def test_new_lock_mode_is_0600_even_under_a_restrictive_umask(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"

    result = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'umask 0777; exec "$@"',
            "lock-test",
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--",
            "/bin/true",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o600


def test_inherited_marker_is_verified_across_a_second_exec(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"

    result = _run(
        lock_file,
        sys.executable,
        str(HELPER),
        "--lock-file",
        str(lock_file),
        "--marker-env",
        "HY2_LOCK_EXEC_MARKER",
        "--verify",
    )

    assert result.returncode == 0, result.stderr


def test_verify_mode_requires_a_real_inherited_marker(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"

    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--verify",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "requires an inherited lock marker" in result.stderr


def test_exec_scrubs_interpreter_injection_environment(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    bash_injected = tmp_path / "bash-injected"
    python_injected = tmp_path / "python-injected"
    bash_env = tmp_path / "bash-env.sh"
    bash_env.write_text(
        f"printf injected > {str(bash_injected)!r}\n",
        encoding="utf-8",
    )
    untrusted_python = tmp_path / "untrusted-python"
    untrusted_python.mkdir()
    (untrusted_python / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(python_injected)!r}).write_text('injected')\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["BASH_ENV"] = str(bash_env)
    environment["PYTHONPATH"] = str(untrusted_python)

    result = subprocess.run(
        [
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--",
            "/bin/bash",
            "-c",
            "/usr/bin/python3 -c 'pass'",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert not bash_injected.exists()
    assert not python_injected.exists()


def test_forged_marker_cannot_skip_descriptor_and_kernel_validation(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    lock_file.touch(mode=0o600)
    lock_file.chmod(0o600)
    environment = os.environ.copy()
    environment["HY2_LOCK_EXEC_MARKER"] = (
        "hy2-lock-v1:999:1:1:" + ("0" * 64)
    )

    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--verify",
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    assert "Inherited lock" in result.stderr


@pytest.mark.parametrize("kind", ("symlink", "fifo", "directory", "hardlink"))
def test_non_regular_or_multiply_linked_lock_paths_are_rejected(
    tmp_path,
    kind,
):
    parent = _private_parent(tmp_path)
    lock_file = parent / "operation.lock"
    canary = tmp_path / "canary"
    canary.write_bytes(b"do-not-touch")
    canary.chmod(0o600)

    if kind == "symlink":
        lock_file.symlink_to(canary)
    elif kind == "fifo":
        os.mkfifo(lock_file, 0o600)
    elif kind == "directory":
        lock_file.mkdir(mode=0o700)
    else:
        os.link(canary, lock_file)

    result = _run(lock_file, "/bin/true")

    assert result.returncode != 0
    assert canary.read_bytes() == b"do-not-touch"


def test_existing_permissive_lock_file_is_rejected_not_repaired(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    lock_file.write_bytes(b"canary")
    lock_file.chmod(0o644)

    result = _run(lock_file, "/bin/true")

    assert result.returncode != 0
    assert stat.S_IMODE(lock_file.stat().st_mode) == 0o644
    assert lock_file.read_bytes() == b"canary"


def test_non_private_parent_is_rejected(tmp_path):
    parent = tmp_path / "locks"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)

    result = _run(parent / "operation.lock", "/bin/true")

    assert result.returncode != 0
    assert not (parent / "operation.lock").exists()


def test_concurrent_owner_is_rejected_with_a_bounded_nonblocking_wait(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    ready = tmp_path / "ready"
    owner = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--",
            sys.executable,
            "-c",
            (
                "import pathlib,time;"
                f"pathlib.Path({str(ready)!r}).write_text('ready');"
                "time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()

        started = time.monotonic()
        contender = _run(lock_file, "/bin/true", timeout="0.15")
        elapsed = time.monotonic() - started

        assert contender.returncode != 0
        assert elapsed < 2
        assert "already holds" in contender.stderr
    finally:
        owner.terminate()
        try:
            owner.wait(timeout=3)
        except subprocess.TimeoutExpired:
            owner.kill()
            owner.wait(timeout=3)


def test_success_if_locked_skips_command_only_for_a_valid_busy_lock(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    ready = tmp_path / "ready"
    command_ran = tmp_path / "command-ran"
    owner = subprocess.Popen(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--",
            sys.executable,
            "-c",
            (
                "import pathlib,time;"
                f"pathlib.Path({str(ready)!r}).write_text('ready');"
                "time.sleep(30)"
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()

        gate = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--lock-file",
                str(lock_file),
                "--timeout",
                "0",
                "--success-if-locked",
                "--",
                "/usr/bin/touch",
                str(command_ran),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )

        assert gate.returncode == 0, gate.stderr
        assert "command skipped" in gate.stdout
        assert not command_ran.exists()
    finally:
        owner.terminate()
        try:
            owner.wait(timeout=3)
        except subprocess.TimeoutExpired:
            owner.kill()
            owner.wait(timeout=3)


def test_success_if_locked_does_not_mask_unsafe_lock_metadata(tmp_path):
    lock_file = _private_parent(tmp_path) / "operation.lock"
    lock_file.write_text("unsafe", encoding="utf-8")
    lock_file.chmod(0o644)

    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--success-if-locked",
            "--",
            "/bin/true",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )

    assert result.returncode != 0
    assert "mode must be exactly 0600" in result.stderr
