"""Runtime contracts for the isolated Hysteria updater systemd unit."""

import shlex
import subprocess
from pathlib import Path

import hysteria_update as hu


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "systemd" / "hy2-hysteria-update.service"
DEPLOY = ROOT / "deploy.sh"


def _exec_start():
    for line in UNIT.read_text(encoding="utf-8").splitlines():
        if line.startswith("ExecStart="):
            return shlex.split(line.removeprefix("ExecStart="))
    raise AssertionError("updater service has no ExecStart")


def test_isolated_service_command_can_import_sibling_modules():
    """Dropping the trusted app directory from the bootstrap must fail here."""
    command = [
        arg.replace("/root/hysteria", str(ROOT / "hysteria"))
        for arg in _exec_start()
    ]
    command = ["--bogus" if arg == "--auto" else arg for arg in command]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert result.returncode == 2, result.stderr
    assert "usage: hysteria_update.py" in result.stdout
    assert "ModuleNotFoundError" not in result.stderr


def test_static_updater_uses_trusted_working_directory_without_isolated_mode():
    unit = UNIT.read_text(encoding="utf-8")

    assert (
        "ExecStart=/usr/local/sbin/hy2-lock-exec.py "
        "--lock-file /run/hy2-locks/deploy.lock --wait "
        "/usr/bin/python3 /root/hysteria/hysteria_update.py --auto"
        in unit.splitlines()
    )
    assert "WorkingDirectory=/root/hysteria" in unit.splitlines()
    assert " -I " not in unit


def test_transient_worker_has_static_unit_security_and_exit_contract(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    command = []

    class Process:
        def wait(self, timeout):
            return 0

    def popen(argv, **_kwargs):
        command.extend(argv)
        return Process()

    state = hu.schedule_apply_async(
        state_path=str(tmp_path / 'update.json'), popen=popen,
        lock_timeout=0,
    )
    properties = {
        item.removeprefix('--property=')
        for item in command if item.startswith('--property=')
    }

    assert state['status'] == 'scheduled'
    assert 'ProtectSystem=strict' in properties
    assert 'PrivateTmp=yes' in properties
    assert 'NoNewPrivileges=yes' in properties
    assert 'CapabilityBoundingSet=' in properties
    assert 'SuccessExitStatus=1' in properties
    assert (
        'ReadWritePaths=/usr/local/bin /root/hysteria/state /run/hy2-locks'
        in properties
    )
    assert 'RuntimeDirectory=hy2-locks' in properties
    assert 'RuntimeDirectoryMode=0700' in properties
    assert 'RuntimeDirectoryPreserve=yes' in properties
    assert hu._status_to_exit('done') == 0
    assert hu._status_to_exit('skipped', 'policy_disabled') == 1
    assert hu._status_to_exit('failed') >= 2


def test_static_and_transient_updaters_hold_deploy_lock():
    unit = UNIT.read_text(encoding='utf-8')
    assert (
        'ReadWritePaths=/usr/local/bin /root/hysteria/state /run/hy2-locks'
        in unit.splitlines()
    )
    assert 'RuntimeDirectory=hy2-locks' in unit.splitlines()
    assert 'RuntimeDirectoryMode=0700' in unit.splitlines()
    assert 'RuntimeDirectoryPreserve=yes' in unit.splitlines()
    assert all(
        line != 'ReadWritePaths=/run'
        and '/run ' not in line.removeprefix('ReadWritePaths=')
        for line in unit.splitlines() if line.startswith('ReadWritePaths=')
    )
    static_command = _exec_start()
    assert static_command[:4] == [
        '/usr/local/sbin/hy2-lock-exec.py',
        '--lock-file', '/run/hy2-locks/deploy.lock', '--wait',
    ]

    # The transient command is observed at its real subprocess boundary.
    captured = []

    class Process:
        def wait(self, timeout):
            return 0

    old_lock = hu.LOCK_PATH
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            hu.LOCK_PATH = str(Path(directory) / 'update.lock')
            hu.schedule_apply_async(
                state_path=str(Path(directory) / 'state.json'),
                popen=lambda command, **_kwargs: (
                    captured.extend(command) or Process()
                ),
                lock_timeout=0,
            )
    finally:
        hu.LOCK_PATH = old_lock

    python_index = captured.index('/usr/bin/python3')
    assert captured[python_index - 4:python_index] == [
        '/usr/local/sbin/hy2-lock-exec.py',
        '--lock-file', '/run/hy2-locks/deploy.lock', '--wait',
    ]

    deploy = DEPLOY.read_text(encoding='utf-8')
    quiesce_first = deploy.split(
        'declare -a QUIESCE_FIRST_UNITS=(', 1
    )[1].split(')', 1)[0]
    assert 'hy2-hysteria-update.timer' in quiesce_first
    assert 'hy2-hysteria-update.service' in quiesce_first
