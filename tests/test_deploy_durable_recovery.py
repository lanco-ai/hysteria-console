import ast
import json
import os
import re
import runpy
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "hy2-deploy-recovery.py"
UNIT = ROOT / "systemd" / "hy2-deploy-recovery.service"
WATCHDOG_UNIT = ROOT / "systemd" / "hy2-deploy-watchdog.service"
DEPLOY = ROOT / "deploy.sh"
LOCK_HELPER = ROOT / "scripts" / "hy2-lock-exec.py"


def _shell_function(script, name):
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}$",
        script,
    )
    assert match, f"missing shell function: {name}"
    return match.group(0)


def _python_literal(name):
    tree = ast.parse(HELPER.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.target]
            )
            if any(
                isinstance(target, ast.Name) and target.id == name
                for target in targets
            ):
                return ast.literal_eval(node.value)
    raise AssertionError(f"missing Python constant: {name}")


def _shell_array(script, name):
    match = re.search(
        rf"(?ms)^declare -a {re.escape(name)}=\(\n(?P<body>.*?)^\)$",
        script,
    )
    assert match, f"missing shell array: {name}"
    return {
        line.strip()
        for line in match.group("body").splitlines()
        if line.strip()
    }


def test_standard_ubuntu_syslog_parent_is_the_only_writable_exception(
    monkeypatch,
):
    namespace = runpy.run_path(str(HELPER))
    validate = namespace["_validate_existing_parent_chain"]
    safe_directory = namespace["_safe_directory"]
    recovery_error = namespace["RecoveryError"]
    fake_stat = SimpleNamespace(
        st_mode=stat.S_IFDIR | 0o775,
        st_uid=0,
        st_gid=111,
    )
    monkeypatch.setattr(
        validate.__globals__["grp"],
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=111),
    )
    real_lstat = validate.__globals__["os"].lstat

    def standard_ubuntu_lstat(path):
        if path == "/var/log":
            return fake_stat
        return real_lstat(path)

    monkeypatch.setattr(
        validate.__globals__["os"],
        "lstat",
        standard_ubuntu_lstat,
    )

    validate(
        "/var/log/xray",
        test_mode=False,
        test_root=None,
        allow_syslog_parent=True,
    )
    safe_directory(
        "/var/log",
        "Directory",
        root_only=False,
        allow_syslog_parent=True,
    )
    with pytest.raises(recovery_error, match="parent chain is unsafe"):
        validate(
            "/var/log/xray",
            test_mode=False,
            test_root=None,
        )
    with pytest.raises(recovery_error, match="not group/world writable"):
        safe_directory(
            "/var/log",
            "Directory",
            root_only=False,
        )


@pytest.fixture
def recovery(tmp_path):
    tmp_path.chmod(0o700)
    artifact_dir = tmp_path / "artifacts"
    artifact_dir.mkdir(mode=0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "HY2_DEPLOY_RECOVERY_TEST_MODE": "1",
            "HY2_DEPLOY_RECOVERY_TEST_ROOT": str(tmp_path),
            "HY2_DEPLOY_RECOVERY_ROOT": str(tmp_path / "recovery"),
            "HY2_DEPLOY_RECOVERY_BOOT_ID": (
                "12345678-1234-4234-8234-123456789abc"
            ),
        }
    )

    def run(*arguments, extra_env=None):
        child_environment = environment.copy()
        if extra_env:
            child_environment.update(extra_env)
        return subprocess.run(
            [sys.executable, "-I", str(HELPER), *map(str, arguments)],
            env=child_environment,
            capture_output=True,
            text=True,
            timeout=20,
        )

    def prepare_and_snapshot(paths):
        prepared = run("prepare")
        assert prepared.returncode == 0, prepared.stderr
        command = ["snapshot"]
        for path in paths:
            command.extend(("--path", str(path)))
        snapshotted = run(*command)
        assert snapshotted.returncode == 0, snapshotted.stderr

    return {
        "root": tmp_path,
        "artifact_dir": artifact_dir,
        "env": environment,
        "run": run,
        "prepare_and_snapshot": prepare_and_snapshot,
    }


def _fsync_parent(path):
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _candidate(path, content):
    candidate = path.with_name(f".{path.name}.candidate")
    candidate.write_bytes(content)
    candidate.chmod(0o640)
    descriptor = os.open(candidate, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_parent(candidate)
    return candidate


def _manifest(recovery):
    return (
        recovery["root"]
        / "recovery"
        / "pending"
        / "manifest.json"
    )


def _kill_after_before(recovery, artifact, candidate, *, rename):
    harness = """
import os
import signal
import subprocess
import sys

helper, artifact, candidate, rename = sys.argv[1:]
result = subprocess.run(
    [
        sys.executable,
        "-I",
        helper,
        "before",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ],
    check=False,
)
if result.returncode:
    raise SystemExit(result.returncode)
if rename == "1":
    os.replace(candidate, artifact)
    descriptor = os.open(os.path.dirname(artifact), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
os.kill(os.getpid(), signal.SIGKILL)
"""
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            harness,
            str(HELPER),
            str(artifact),
            str(candidate),
            "1" if rename else "0",
        ],
        env=recovery["env"],
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_prepare_and_snapshot_are_root_only_and_fsynced_shape(recovery):
    artifact = recovery["artifact_dir"] / "config.json"
    artifact.write_text("original\n", encoding="utf-8")

    prepared = recovery["run"]("prepare")
    assert prepared.returncode == 0, prepared.stderr

    recovery_root = recovery["root"] / "recovery"
    pending = recovery_root / "pending"
    manifest = pending / "manifest.json"
    assert stat.S_IMODE(recovery_root.stat().st_mode) == 0o700
    assert stat.S_IMODE(pending.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600

    snapshotted = recovery["run"](
        "snapshot",
        "--path",
        artifact,
    )
    assert snapshotted.returncode == 0, snapshotted.stderr
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert payload["state"] == "active"
    assert payload["allowlist"] == [str(artifact)]
    assert payload["artifacts"][0]["path"] == str(artifact)
    snapshot = pending / payload["artifacts"][0]["snapshot"]
    assert snapshot.read_text(encoding="utf-8") == "original\n"
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o600


def test_snapshot_accepts_a_strict_root_owned_allowlist_file(recovery):
    first = recovery["artifact_dir"] / "first"
    second = recovery["artifact_dir"] / "second"
    first.write_text("one", encoding="utf-8")
    second.write_text("two", encoding="utf-8")
    allowlist = recovery["root"] / "allowlist"
    allowlist.write_text(f"{first}\n{second}\n", encoding="utf-8")
    allowlist.chmod(0o600)

    assert recovery["run"]("prepare").returncode == 0
    result = recovery["run"](
        "snapshot",
        "--allowlist-file",
        allowlist,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    assert payload["allowlist"] == [str(first), str(second)]


def test_sigkill_before_rename_recovers_without_touching_original(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")

    killed = _kill_after_before(
        recovery,
        artifact,
        candidate,
        rename=False,
    )
    assert killed.returncode == -9, killed.stderr

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert artifact.read_bytes() == b"original generation\n"
    assert not candidate.exists()
    assert not _manifest(recovery).parent.exists()


def test_sigkill_after_rename_before_after_restores_original(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")

    killed = _kill_after_before(
        recovery,
        artifact,
        candidate,
        rename=True,
    )
    assert killed.returncode == -9, killed.stderr

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert artifact.read_bytes() == b"original generation\n"
    assert not _manifest(recovery).parent.exists()


def test_unknown_third_generation_fails_closed(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")
    assert recovery["run"](
        "before",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ).returncode == 0
    os.replace(candidate, artifact)
    _fsync_parent(artifact)
    artifact.write_bytes(b"operator-owned third generation\n")
    descriptor = os.open(artifact, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)

    recovered = recovery["run"]("recover")

    assert recovered.returncode != 0
    assert "unknown post-crash generation" in recovered.stderr
    assert artifact.read_bytes() == b"operator-owned third generation\n"
    assert _manifest(recovery).exists()


def test_after_requires_exact_recorded_replacement_generation(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")
    assert recovery["run"](
        "before",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ).returncode == 0
    os.replace(candidate, artifact)
    artifact.write_bytes(b"different bytes\n")
    _fsync_parent(artifact)

    committed = recovery["run"]("after", "--path", artifact)

    assert committed.returncode != 0
    assert "does not match its replacement generation" in committed.stderr
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    assert payload["pending_commit"]["path"] == str(artifact)
    assert payload["committed"] == []


def test_candidate_cannot_alias_another_allowlisted_artifact(recovery):
    first = recovery["artifact_dir"] / "first"
    second = recovery["artifact_dir"] / ".second"
    first.write_bytes(b"first-original")
    second.write_bytes(b"second-original")
    recovery["prepare_and_snapshot"]([first, second])

    replaced = recovery["run"](
        "replace",
        "--path",
        first,
        "--candidate",
        second,
    )

    assert replaced.returncode != 0
    assert "another allowlisted artifact" in replaced.stderr
    assert first.read_bytes() == b"first-original"
    assert second.read_bytes() == b"second-original"
    recovered = recovery["run"]("recover")
    assert recovered.returncode == 0, recovered.stderr


def test_recovery_removes_a_newly_created_artifact(recovery):
    artifact = recovery["artifact_dir"] / "new-unit.service"
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"new generation\n")
    installed = recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    )
    assert installed.returncode == 0, installed.stderr
    assert artifact.exists()

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert not artifact.exists()


def test_remove_absent_artifact_allows_an_absent_parent(recovery):
    artifact = recovery["artifact_dir"] / "missing-parent" / "legacy.conf"
    recovery["prepare_and_snapshot"]([artifact])

    removed = recovery["run"]("remove", "--path", artifact)

    assert removed.returncode == 0, removed.stderr
    assert not artifact.exists()
    completed = recovery["run"]("complete")
    assert completed.returncode == 0, completed.stderr
    assert recovery["run"]("status").stdout.strip() == "clean"


def test_recovery_restores_an_original_symlink_generation(recovery):
    artifact = recovery["artifact_dir"] / "enabled.conf"
    artifact.symlink_to("../available/original.conf")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = artifact.with_name(".enabled.conf.candidate")
    candidate.symlink_to("../available/replacement.conf")
    _fsync_parent(candidate)

    installed = recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    )
    assert installed.returncode == 0, installed.stderr
    assert os.readlink(artifact) == "../available/replacement.conf"

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert artifact.is_symlink()
    assert os.readlink(artifact) == "../available/original.conf"


def test_corrupt_snapshot_fails_before_touching_live_replacement(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")
    installed = recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    )
    assert installed.returncode == 0, installed.stderr
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    snapshot = _manifest(recovery).parent / payload["artifacts"][0]["snapshot"]
    snapshot.write_bytes(b"corrupt snapshot\n")
    snapshot.chmod(0o600)

    recovered = recovery["run"]("recover")

    assert recovered.returncode != 0
    assert "snapshot is" in recovered.stderr
    assert artifact.read_bytes() == b"replacement generation\n"
    assert _manifest(recovery).exists()


def test_read_only_preflight_failure_does_not_stop_runtime_units(recovery):
    command_dir = recovery["root"] / "preflight-commands"
    command_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "preflight-systemctl.log"
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active) printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'enabled\\n'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    runtime_env = {"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)}
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    assert recovery["run"](
        "prepare",
        "--unit",
        "example.service",
        extra_env=runtime_env,
    ).returncode == 0
    assert recovery["run"](
        "snapshot",
        "--path",
        artifact,
        extra_env=runtime_env,
    ).returncode == 0
    candidate = _candidate(artifact, b"replacement generation\n")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
        extra_env=runtime_env,
    ).returncode == 0
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    snapshot = _manifest(recovery).parent / payload["artifacts"][0]["snapshot"]
    snapshot.write_bytes(b"corrupt\n")

    recovered = recovery["run"](
        "recover",
        extra_env=runtime_env,
    )

    assert recovered.returncode != 0
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert not any(command.startswith("stop ") for command in commands)
    assert artifact.read_bytes() == b"replacement generation\n"


def test_final_original_cas_detects_change_during_restore_window(recovery):
    first = recovery["artifact_dir"] / "first"
    second = recovery["artifact_dir"] / "second"
    first.write_bytes(b"first-original")
    second.write_bytes(b"second-original")
    command_dir = recovery["root"] / "final-cas-commands"
    command_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "final-cas-systemctl.log"
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active) printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'enabled\\n'; exit 0 ;;\n"
        f"  daemon-reload) printf 'third-generation' > '{second}'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    runtime_env = {"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)}
    assert recovery["run"](
        "prepare",
        "--unit",
        "example.service",
        extra_env=runtime_env,
    ).returncode == 0
    assert recovery["run"](
        "snapshot",
        "--path",
        first,
        "--path",
        second,
        extra_env=runtime_env,
    ).returncode == 0
    candidate = _candidate(first, b"first-replacement")
    assert recovery["run"](
        "replace",
        "--path",
        first,
        "--candidate",
        candidate,
        extra_env=runtime_env,
    ).returncode == 0

    recovered = recovery["run"](
        "recover",
        extra_env=runtime_env,
    )

    assert recovered.returncode != 0
    assert "changed before rollback could be committed" in recovered.stderr
    assert first.read_bytes() == b"first-original"
    assert second.read_bytes() == b"third-generation"
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    assert payload["state"] == "recovery-failed"
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "--no-block start example.service" not in commands


def test_recovery_is_idempotent_after_interruption_mid_restore(recovery):
    first = recovery["artifact_dir"] / "first"
    second = recovery["artifact_dir"] / "second"
    first.write_bytes(b"first-original")
    second.write_bytes(b"second-original")
    recovery["prepare_and_snapshot"]([first, second])
    for path, replacement in (
        (first, b"first-replacement"),
        (second, b"second-replacement"),
    ):
        candidate = _candidate(path, replacement)
        result = recovery["run"](
            "replace",
            "--path",
            path,
            "--candidate",
            candidate,
        )
        assert result.returncode == 0, result.stderr

    interrupted = recovery["run"](
        "recover",
        extra_env={"HY2_DEPLOY_RECOVERY_TEST_FAIL_AFTER_RESTORE": "1"},
    )
    assert interrupted.returncode != 0
    assert _manifest(recovery).exists()

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert first.read_bytes() == b"first-original"
    assert second.read_bytes() == b"second-original"
    assert not _manifest(recovery).parent.exists()


def test_complete_residue_is_cleaned_without_rolling_back(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement generation\n")
    replaced = recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    )
    assert replaced.returncode == 0, replaced.stderr

    completed = recovery["run"](
        "complete",
        extra_env={"HY2_DEPLOY_RECOVERY_TEST_KEEP_COMPLETE": "1"},
    )
    assert completed.returncode == 0, completed.stderr
    assert recovery["run"]("status").stdout.strip() == "complete"

    recovered = recovery["run"]("recover")

    assert recovered.returncode == 0, recovered.stderr
    assert artifact.read_bytes() == b"replacement generation\n"
    assert not _manifest(recovery).parent.exists()


def test_complete_rejects_unjournaled_mutation(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original generation\n")
    recovery["prepare_and_snapshot"]([artifact])
    artifact.write_bytes(b"unjournaled generation\n")

    completed = recovery["run"]("complete")

    assert completed.returncode != 0
    assert "changed before deployment commit" in completed.stderr
    assert _manifest(recovery).exists()


def test_test_mode_allowlist_cannot_escape_its_root(recovery, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside") / "unowned"
    outside.write_text("do not touch", encoding="utf-8")
    assert recovery["run"]("prepare").returncode == 0

    snapshotted = recovery["run"](
        "snapshot",
        "--path",
        outside,
    )

    assert snapshotted.returncode != 0
    assert "outside the deployment allowlist" in snapshotted.stderr
    assert outside.read_text(encoding="utf-8") == "do not touch"


def test_allowlist_rejects_a_symlinked_parent_inside_the_test_root(recovery):
    real_parent = recovery["root"] / "real-parent"
    real_parent.mkdir(mode=0o700)
    artifact = real_parent / "unowned"
    artifact.write_text("do not touch", encoding="utf-8")
    linked_parent = recovery["root"] / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    assert recovery["run"]("prepare").returncode == 0

    snapshotted = recovery["run"](
        "snapshot",
        "--path",
        linked_parent / "unowned",
    )

    assert snapshotted.returncode != 0
    assert "parent chain is unsafe" in snapshotted.stderr
    assert artifact.read_text(encoding="utf-8") == "do not touch"


def test_prepare_persists_and_recover_restores_runtime_state(recovery):
    command_dir = recovery["root"] / "commands"
    command_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "runtime-command.log"
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active) printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'enabled\\n'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    sysctl_log = recovery["root"] / "sysctl-command.log"
    sysctl = command_dir / "sysctl"
    sysctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{sysctl_log}'\n"
        "if [ \"$1\" = -n ]; then printf '4096\\n'; fi\n",
        encoding="utf-8",
    )
    sysctl.chmod(0o700)
    log_dir = recovery["root"] / "xray-log"
    log_dir.mkdir(mode=0o750)
    runtime_env = {
        "HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl),
        "HY2_DEPLOY_RECOVERY_SYSCTL": str(sysctl),
    }
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original")

    prepared = recovery["run"](
        "prepare",
        "--unit",
        "example.service",
        "--sysctl-key",
        "net.example.value",
        "--log-dir",
        log_dir,
        extra_env=runtime_env,
    )
    assert prepared.returncode == 0, prepared.stderr
    snapshotted = recovery["run"](
        "snapshot",
        "--path",
        artifact,
        extra_env=runtime_env,
    )
    assert snapshotted.returncode == 0, snapshotted.stderr
    candidate = _candidate(artifact, b"replacement")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
        extra_env=runtime_env,
    ).returncode == 0

    recovered = recovery["run"](
        "recover",
        extra_env=runtime_env,
    )

    assert recovered.returncode == 0, recovered.stderr
    assert artifact.read_bytes() == b"original"
    systemctl_commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "is-active example.service" in systemctl_commands
    assert "is-enabled example.service" in systemctl_commands
    assert "stop example.service" in systemctl_commands
    assert "daemon-reload" in systemctl_commands
    assert "enable example.service" in systemctl_commands
    assert "--no-block start example.service" in systemctl_commands
    assert sysctl_log.read_text(encoding="utf-8").splitlines() == [
        "-n net.example.value",
        "-q -w net.example.value=4096",
    ]


def test_prepare_rejects_non_authoritative_systemctl_failure(recovery):
    command_dir = recovery["root"] / "broken-commands"
    command_dir.mkdir(mode=0o700)
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        "printf 'D-Bus unavailable\\n' >&2\n"
        "exit 1\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)

    prepared = recovery["run"](
        "prepare",
        "--unit",
        "example.service",
        extra_env={"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)},
    )

    assert prepared.returncode != 0
    assert "authoritative active state" in prepared.stderr
    assert recovery["run"]("status").stdout.strip() == "clean"


def test_recovery_unit_is_a_fail_closed_pre_service_gate():
    unit = UNIT.read_text(encoding="utf-8")

    before = {
        token
        for line in unit.splitlines()
        if line.startswith("Before=")
        for token in line.removeprefix("Before=").split()
    }
    required_by = {
        token
        for line in unit.splitlines()
        if line.startswith("RequiredBy=")
        for token in line.removeprefix("RequiredBy=").split()
    }
    gated = {
        "nginx.service",
        "hysteria-porthop.service",
        "hysteria-tcp-mss.service",
        "hysteria-auth.service",
        "hysteria-server.service",
        "hysteria-subscription.service",
        "hysteria-traffic-limiter.service",
        "hysteria-traffic-limiter.timer",
        "xray.service",
        "tuic-server.service",
        "hy2-https-recovery.service",
        "codex-quota-collector.service",
        "codex-quota-collector.timer",
        "hy2-backup.service",
        "hy2-backup.timer",
        "hy2-health-check.service",
        "hy2-health-check.timer",
        "snap.certbot.renew.timer",
        "fail2ban.service",
    }
    assert before == gated
    assert required_by == gated
    assert "hy2-deploy-recovery.service" not in gated
    assert "systemd-journald.service" not in gated
    assert "/usr/local/sbin/hy2-lock-exec.py" in unit
    assert "--lock-file /run/hy2-locks/deploy.lock" in unit
    assert "--timeout 0" in unit
    assert "--success-if-locked" in unit
    assert "/usr/local/sbin/hy2-deploy-recovery.py recover" in unit
    assert "RemainAfterExit=yes" in unit
    assert "ConditionPathExists=" not in unit
    for directive in (
        "User=root",
        "UMask=0077",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "RestrictNamespaces=true",
    ):
        assert directive in unit


def test_deploy_outer_gate_prepare_and_watchdog_precede_mutation():
    deploy = DEPLOY.read_text(encoding="utf-8")

    deploy_lock = deploy.index("--marker-env HY2_DEPLOY_LOCK_MARKER")
    https_lock = deploy.index("--marker-env HY2_HTTPS_LOCK_MARKER")
    outer_gate = deploy.index("\nouter_recovery recover", https_lock)
    https_gate = deploy.index(
        '/usr/bin/bash -p "$REPO_DIR/scripts/hy2-enable-https.sh" '
        "--recover-only",
        outer_gate,
    )
    validation = deploy.index(
        "# ---------- 1. Validate parsed deployment environment ----------"
    )
    bootstrap = deploy.index(
        "\nbootstrap_install_atomic 755 \\\n",
        validation,
    )
    watchdog = deploy.index(
        "systemctl --no-block start hy2-deploy-watchdog.service",
        bootstrap,
    )
    recovery_state_root = deploy.index(
        "install -d -o root -g root -m 700 /var/lib/hysteria",
        bootstrap,
    )
    xray_log_root = deploy.index(
        "install -d -o root -g root -m 755 /var/log/xray",
        recovery_state_root,
    )
    waiting = deploy.index("activating|active)", watchdog)
    prepared_flag = deploy.index(
        "\nDURABLE_RECOVERY_PREPARED=1\n",
        waiting,
    )
    prepare = deploy.index(
        'outer_recovery "${prepare_args[@]}"',
        prepared_flag,
    )
    capture = deploy.index("\ncapture_service_state\n", waiting)
    apt = deploy.index("apt-get update -y", capture)

    assert deploy_lock < https_lock < outer_gate < https_gate
    assert (
        https_gate
        < validation
        < bootstrap
        < recovery_state_root
        < xray_log_root
        < watchdog
        < waiting
    )
    assert waiting < prepared_flag < prepare < capture < apt
    assert (
        'die "Pending outer deployment could not be recovered; '
        'stopped before mutation."'
        in deploy[outer_gate:https_gate]
    )
    bootstrap_block = deploy[bootstrap:prepared_flag]
    assert bootstrap_block.count("bootstrap_install_atomic ") == 4
    for destination in (
        "/usr/local/sbin/hy2-lock-exec.py",
        "/usr/local/sbin/hy2-deploy-recovery.py",
        '"$SYSTEMD_DIR/hy2-deploy-recovery.service"',
        '"$SYSTEMD_DIR/hy2-deploy-watchdog.service"',
    ):
        assert destination in bootstrap_block
    assert "enable --now hy2-deploy-recovery.service" not in deploy
    arming = deploy[watchdog:prepared_flag]
    assert "for _watchdog_attempt in {1..50}; do" in arming
    assert "inactive)" in arming and "sleep 0.1" in arming
    assert "failed)" in arming


def test_prepare_runtime_contract_matches_helper_allowlists():
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert _shell_array(deploy, "DEPLOY_MANAGED_UNITS") == set(
        _python_literal("EXACT_ALLOWED_UNITS")
    )
    assert _shell_array(deploy, "HY2_SYSCTL_KEYS") == set(
        _python_literal("EXACT_ALLOWED_SYSCTLS")
    )
    assert _python_literal("ALLOWED_LOG_DIRS") == {"/var/log/xray"}
    prepare_block = deploy[
        deploy.index("prepare_args=(prepare)"):
        deploy.index("\ncapture_service_state\n")
    ]
    assert 'for unit in "${DEPLOY_MANAGED_UNITS[@]}"; do' in prepare_block
    assert 'prepare_args+=(--unit "$unit")' in prepare_block
    assert 'for key in "${HY2_SYSCTL_KEYS[@]}"; do' in prepare_block
    assert 'prepare_args+=(--sysctl-key "$key")' in prepare_block
    assert "prepare_args+=(--log-dir /var/log/xray)" in prepare_block


def test_frozen_static_allowlist_exactly_matches_helper_contract(tmp_path):
    deploy = DEPLOY.read_text(encoding="utf-8")
    hy_dir = tmp_path / "hysteria"
    (hy_dir / "state").mkdir(parents=True)
    program = "\n".join(
        (
            "set -euo pipefail",
            'HY_DIR="$1"',
            "SYSTEMD_DIR=/etc/systemd/system",
            "HYSTERIA_INSTALL_REQUIRED=1",
            "XRAY_INSTALL_REQUIRED=1",
            "TUIC_INSTALL_REQUIRED=1",
            "HY_ENABLE_HTTPS=0",
            "declare -a DURABLE_ARTIFACT_PATHS=()",
            "declare -A DURABLE_ARTIFACT_SET=()",
            "die() { printf '%s\\n' \"$*\" >&2; exit 1; }",
            _shell_function(deploy, "add_durable_artifact"),
            _shell_function(deploy, "build_durable_artifact_set"),
            "build_durable_artifact_set",
            "printf '%s\\n' \"${DURABLE_ARTIFACT_PATHS[@]}\"",
        )
    )
    expanded = subprocess.run(
        ["/usr/bin/bash", "-c", program, "allowlist-test", str(hy_dir)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert expanded.returncode == 0, expanded.stderr
    normalized = {
        (
            "/root/hysteria" + path.removeprefix(str(hy_dir))
            if path.startswith(str(hy_dir))
            else path
        )
        for path in expanded.stdout.splitlines()
    }
    helper_paths = set(_python_literal("EXACT_ALLOWED_PATHS"))

    assert normalized == helper_paths
    for forbidden in (
        "/root/hysteria/users.json",
        "/root/hysteria/subscription_meta.json",
        "/root/hysteria/admin_initial_password.txt",
        "/root/hysteria/state/usage.json",
        "/root/hysteria/state/usage_daily.json",
        "/root/hysteria/state/auto_reset_state.json",
        "/root/hysteria/tuic.json",
        "/usr/local/etc/xray/config.json",
        "/var/log/xray/hy2-access.log",
        "/var/log/xray/hy2-error.log",
        "/etc/nginx/sites-available/hysteria-panel-https.conf",
        "/etc/letsencrypt/renewal-hooks/deploy/hy2-cert-renew-hook.sh",
    ):
        assert forbidden not in helper_paths
    assert not any(
        path.endswith((".lock", ".reload.pending"))
        for path in helper_paths
    )


def test_snapshot_is_single_and_precedes_every_static_commit():
    deploy = DEPLOY.read_text(encoding="utf-8")
    quiesced = deploy.index(
        'die "Could not authoritatively quiesce $unit '
    )
    snapshot = deploy.index("\nbegin_durable_artifact_snapshot\n", quiesced)
    first_commit = deploy.index("durable_replace_candidate", snapshot)
    complete = deploy.index("\nouter_recovery complete", first_commit)

    assert deploy.count("\nbegin_durable_artifact_snapshot\n") == 1
    assert quiesced < snapshot < first_commit < complete
    snapshot_function = _shell_function(
        deploy,
        "begin_durable_artifact_snapshot",
    )
    assert 'local -a snapshot_args=(snapshot)' in snapshot_function
    assert 'snapshot_args+=(--path "$path")' in snapshot_function
    assert 'outer_recovery "${snapshot_args[@]}"' in snapshot_function
    assert snapshot_function.index('outer_recovery "${snapshot_args[@]}"') < (
        snapshot_function.index("DURABLE_RECOVERY_ACTIVE=1")
    )


@pytest.mark.parametrize(
    ("returncode", "state"),
    (
        (3, "inactive"),
        (3, "failed"),
        (4, "unknown"),
    ),
)
def test_quiescence_gate_accepts_only_authoritative_inactive_states(
    returncode,
    state,
):
    deploy = DEPLOY.read_text(encoding="utf-8")
    function = _shell_function(deploy, "require_unit_quiescent")
    harness = f"""\
set -u
SYSTEMCTL_STATE="$1"
SYSTEMCTL_RC="$2"
systemctl() {{
  printf '%s\\n' "$SYSTEMCTL_STATE"
  return "$SYSTEMCTL_RC"
}}
die() {{
  printf '%s\\n' "$*" >&2
  exit 97
}}
{function}
require_unit_quiescent example.service
"""

    result = subprocess.run(
        ["/usr/bin/bash", "-c", harness, "quiescence-test", state, str(returncode)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("returncode", "state"),
    (
        (1, ""),
        (1, "inactive"),
        (0, ""),
        (0, "active"),
        (3, "activating"),
        (3, "unknown"),
        (4, "inactive"),
        (4, "failed"),
        (4, ""),
    ),
)
def test_quiescence_gate_rejects_ambiguous_or_active_systemctl_results(
    returncode,
    state,
):
    deploy = DEPLOY.read_text(encoding="utf-8")
    function = _shell_function(deploy, "require_unit_quiescent")
    harness = f"""\
set -u
SYSTEMCTL_STATE="$1"
SYSTEMCTL_RC="$2"
systemctl() {{
  printf '%s\\n' "$SYSTEMCTL_STATE"
  return "$SYSTEMCTL_RC"
}}
die() {{
  printf '%s\\n' "$*" >&2
  exit 97
}}
{function}
require_unit_quiescent example.service
"""

    result = subprocess.run(
        ["/usr/bin/bash", "-c", harness, "quiescence-test", state, str(returncode)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 97
    assert "Could not authoritatively quiesce example.service" in result.stderr
    assert f"systemctl rc: {returncode}" in result.stderr
    if not state:
        assert "state: <empty>" in result.stderr


def test_static_mutation_helpers_are_wal_routed_and_fail_closed():
    deploy = DEPLOY.read_text(encoding="utf-8")

    replace = _shell_function(deploy, "durable_replace_candidate")
    remove = _shell_function(deploy, "durable_remove_artifact")
    assert '[[ "$DURABLE_RECOVERY_ACTIVE" == "1" ]]' in replace
    assert 'is_durable_artifact "$destination"' in replace
    assert (
        'outer_recovery replace --path "$destination" '
        '--candidate "$candidate"'
        in replace
    )
    assert '[[ "$DURABLE_RECOVERY_ACTIVE" == "1" ]]' in remove
    assert 'is_durable_artifact "$destination"' in remove
    assert 'outer_recovery remove --path "$destination"' in remove
    for name in (
        "render",
        "install_atomic",
        "write_atomic_from_stdin",
        "symlink_atomic",
    ):
        function = _shell_function(deploy, name)
        assert "durable_replace_candidate" in function

    transaction = deploy[
        deploy.index("\nbegin_durable_artifact_snapshot\n"):
        deploy.index("\nouter_recovery complete")
    ]
    for forbidden in (
        'mv -Tf -- "$HYSTERIA_CANDIDATE" /usr/local/bin/hysteria',
        'mv -Tf -- "$XRAY_CANDIDATE" /usr/local/bin/xray',
        'mv -Tf -- "$TUIC_CANDIDATE" /usr/local/bin/tuic-server',
        'rm -f -- "$HY_DIR/state/https_required"',
        "rm -f /etc/nginx/sites-enabled/default",
        "rm -f /etc/nginx/sites-enabled/hysteria-panel-https.conf",
    ):
        assert forbidden not in transaction
    assert "-keyout \"$HY_DIR/server.key\"" not in transaction
    assert "-out \"$HY_DIR/server.crt\"" not in transaction
    assert 'durable_remove_artifact "$legacy_xray_artifact"' in transaction
    render = _shell_function(deploy, "render")
    assert '"$HY_DIR"/*.py) mode=700' in render
    assert "/etc/nginx/*) mode=644" in render
    assert "*) mode=600" in render


@pytest.mark.parametrize("recover_status", (0, 1))
def test_exit_trap_prefers_durable_recovery_before_snapshot(
    tmp_path,
    recover_status,
):
    deploy = DEPLOY.read_text(encoding="utf-8")
    log = tmp_path / "trap.log"
    program = "\n".join(
        (
            "set -uo pipefail",
            "DEPLOY_SUCCEEDED=0",
            "DURABLE_RECOVERY_PREPARED=1",
            "ROLLBACK_ACTIVE=1",
            'ROLLBACK_DIR=""',
            f"RECOVER_STATUS={recover_status}",
            'LOG="$1"',
            "DEPLOY_MANAGED_UNITS=(example.service)",
            "PREVIOUSLY_ACTIVE_UNITS=()",
            "warn() { printf '%s\\n' \"$*\" >&2; }",
            "outer_recovery() { "
            "printf 'outer:%s\\n' \"$*\" >> \"$LOG\"; "
            "return \"$RECOVER_STATUS\"; }",
            "systemctl() { printf 'systemctl:%s\\n' \"$*\" >> \"$LOG\"; }",
            "restore_artifacts_on_failure() { "
            "printf 'legacy-artifacts\\n' >> \"$LOG\"; }",
            "restore_created_xray_directories() { "
            "printf 'legacy-dirs\\n' >> \"$LOG\"; }",
            "restore_xray_log_directory_state() { "
            "printf 'legacy-logdir\\n' >> \"$LOG\"; }",
            "restore_sysctl_state() { "
            "printf 'legacy-sysctl\\n' >> \"$LOG\"; }",
            "restore_unit_enable_state() { "
            "printf 'legacy-unit\\n' >> \"$LOG\"; }",
            "was_previously_active() { return 1; }",
            "is_deferred_rollback_unit() { return 1; }",
            "cleanup_rollback_snapshot() { "
            "printf 'cleanup\\n' >> \"$LOG\"; }",
            _shell_function(deploy, "restore_services_on_failure"),
            "trap restore_services_on_failure EXIT",
            "false",
        )
    )
    result = subprocess.run(
        ["/usr/bin/bash", "-c", program, "trap-test", str(log)],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode != 0
    actions = log.read_text(encoding="utf-8").splitlines()
    assert actions[0] == "outer:recover"
    assert not any(action.startswith(("legacy-", "systemctl:")) for action in actions)
    if recover_status == 0:
        assert actions == ["outer:recover", "cleanup"]
    else:
        assert actions == ["outer:recover"]
        assert "preserving the durable recovery journal" in result.stderr


def test_complete_is_the_core_commit_point_before_https():
    deploy = DEPLOY.read_text(encoding="utf-8")
    readiness = deploy.index("wait_for_stable_readiness 3 15 1")
    final_active = deploy.index(
        '[[ "$unit_state" == "active" ]]',
        readiness,
    )
    complete = deploy.index("\nouter_recovery complete", final_active)
    rollback_off = deploy.index("\nROLLBACK_ACTIVE=0\n", complete)
    active_off = deploy.index("\nDURABLE_RECOVERY_ACTIVE=0\n", rollback_off)
    prepared_off = deploy.index(
        "\nDURABLE_RECOVERY_PREPARED=0\n",
        active_off,
    )
    success = deploy.index("\nDEPLOY_SUCCEEDED=1\n", prepared_off)
    https = deploy.index(
        "/usr/local/sbin/hy2-enable-https.sh",
        success,
    )

    assert (
        readiness
        < final_active
        < complete
        < rollback_off
        < active_off
        < prepared_off
        < success
        < https
    )


def test_watchdog_is_nonblocking_rearmed_and_not_runtime_managed():
    deploy = DEPLOY.read_text(encoding="utf-8")
    unit = WATCHDOG_UNIT.read_text(encoding="utf-8")

    assert "--wait" in unit
    assert "--lock-file /run/hy2-locks/deploy.lock" in unit
    assert "/usr/local/sbin/hy2-deploy-recovery.py recover" in unit
    assert "TimeoutStartSec=infinity" in unit
    assert (
        "ExecStopPost=/usr/bin/systemctl "
        "--job-mode=ignore-dependencies stop "
        "hy2-deploy-recovery.service"
        in unit
    )
    assert (
        "ExecStopPost=/usr/bin/systemctl stop "
        "hy2-deploy-recovery.service"
        not in unit
    )
    assert "Before=" not in unit
    assert "RequiredBy=" not in unit
    assert "RemainAfterExit=" not in unit
    assert "hy2-deploy-watchdog.service" not in _shell_array(
        deploy,
        "DEPLOY_MANAGED_UNITS",
    )
    assert (
        'install_atomic 644 '
        '"$REPO_DIR/systemd/hy2-deploy-watchdog.service"'
        in deploy
    )


@pytest.mark.parametrize(
    ("original_active", "same_boot", "expect_restart"),
    (
        (False, True, False),
        (True, True, True),
        (True, False, False),
    ),
)
def test_preparing_recovery_stops_package_started_units_and_resumes_safely(
    recovery,
    original_active,
    same_boot,
    expect_restart,
):
    command_dir = recovery["root"] / "preparing-commands"
    command_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "preparing-systemctl.log"
    active_count = recovery["root"] / "active-count"
    systemctl = command_dir / "systemctl"
    first_state = "active" if original_active else "inactive"
    first_status = 0 if original_active else 3
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active)\n"
        f"    count=$(cat '{active_count}' 2>/dev/null || printf 0)\n"
        "    count=$((count + 1))\n"
        f"    printf '%s\\n' \"$count\" > '{active_count}'\n"
        "    if [ \"$count\" -eq 1 ]; then\n"
        f"      printf '{first_state}\\n'; exit {first_status}\n"
        "    fi\n"
        "    printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'disabled\\n'; exit 1 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    runtime_env = {"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)}
    prepared = recovery["run"](
        "prepare",
        "--unit",
        "package-started.service",
        extra_env=runtime_env,
    )
    assert prepared.returncode == 0, prepared.stderr
    recover_env = dict(runtime_env)
    if not same_boot:
        recover_env["HY2_DEPLOY_RECOVERY_BOOT_ID"] = (
            "87654321-4321-4321-8321-cba987654321"
        )

    recovered = recovery["run"]("recover", extra_env=recover_env)

    assert recovered.returncode == 0, recovered.stderr
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "stop package-started.service" in commands
    restarted = "--no-block start package-started.service" in commands
    assert restarted is expect_restart


def test_preparing_stop_failure_never_restarts_an_originally_inactive_unit(
    recovery,
):
    command_dir = recovery["root"] / "preparing-stop-failure"
    command_dir.mkdir(mode=0o700)
    state_dir = recovery["root"] / "unit-state"
    state_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "preparing-stop-failure.log"
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active)\n"
        f"    state='{state_dir}/'\"$2\"'.count'\n"
        "    count=$(cat \"$state\" 2>/dev/null || printf 0)\n"
        "    count=$((count + 1)); printf '%s\\n' \"$count\" > \"$state\"\n"
        "    if [ \"$count\" -eq 1 ]; then\n"
        "      printf 'inactive\\n'; exit 3\n"
        "    fi\n"
        "    printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'disabled\\n'; exit 1 ;;\n"
        "  stop)\n"
        "    [ \"$2\" = second.service ] && exit 1\n"
        "    exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    runtime_env = {"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)}
    prepared = recovery["run"](
        "prepare",
        "--unit",
        "first.service",
        "--unit",
        "second.service",
        extra_env=runtime_env,
    )
    assert prepared.returncode == 0, prepared.stderr

    recovered = recovery["run"]("recover", extra_env=runtime_env)

    assert recovered.returncode != 0
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "stop first.service" in commands
    assert "stop second.service" in commands
    assert "--no-block start first.service" not in commands
    payload = json.loads(_manifest(recovery).read_text(encoding="utf-8"))
    assert payload["state"] == "preparing"


def test_second_preflight_detects_a_shutdown_generation_race(recovery):
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original")
    command_dir = recovery["root"] / "shutdown-race-commands"
    command_dir.mkdir(mode=0o700)
    command_log = recovery["root"] / "shutdown-race.log"
    systemctl = command_dir / "systemctl"
    systemctl.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{command_log}'\n"
        "case \"$1\" in\n"
        "  is-active) printf 'active\\n'; exit 0 ;;\n"
        "  is-enabled) printf 'enabled\\n'; exit 0 ;;\n"
        f"  stop) printf 'shutdown-third-generation' > '{artifact}'; exit 0 ;;\n"
        "  *) exit 0 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    systemctl.chmod(0o700)
    runtime_env = {"HY2_DEPLOY_RECOVERY_SYSTEMCTL": str(systemctl)}
    assert recovery["run"](
        "prepare",
        "--unit",
        "writer.service",
        extra_env=runtime_env,
    ).returncode == 0
    assert recovery["run"](
        "snapshot",
        "--path",
        artifact,
        extra_env=runtime_env,
    ).returncode == 0
    candidate = _candidate(artifact, b"replacement")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
        extra_env=runtime_env,
    ).returncode == 0
    command_log.write_text("", encoding="utf-8")

    recovered = recovery["run"]("recover", extra_env=runtime_env)

    assert recovered.returncode != 0
    assert "unknown post-crash generation" in recovered.stderr
    assert artifact.read_bytes() == b"shutdown-third-generation"
    commands = command_log.read_text(encoding="utf-8").splitlines()
    assert "stop writer.service" in commands
    assert "daemon-reload" not in commands
    assert _manifest(recovery).exists()


def test_same_boot_watchdog_waits_for_real_lock_then_recovers_sigkill(recovery):
    lock_dir = recovery["root"] / "watchdog-locks"
    lock_dir.mkdir(mode=0o700)
    lock_file = lock_dir / "deploy.lock"
    ready = recovery["root"] / "deploy-owner-ready"
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ).returncode == 0

    owner = subprocess.Popen(
        [
            sys.executable,
            str(LOCK_HELPER),
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
        env=recovery["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    watchdog = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        watchdog = subprocess.Popen(
            [
                sys.executable,
                str(LOCK_HELPER),
                "--lock-file",
                str(lock_file),
                "--wait",
                "--marker-env",
                "HY2_DEPLOY_LOCK_MARKER",
                "--",
                sys.executable,
                "-I",
                str(HELPER),
                "recover",
            ],
            env=recovery["env"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.1)
        assert watchdog.poll() is None
        assert artifact.read_bytes() == b"replacement"

        owner.kill()
        owner.wait(timeout=3)
        stdout, stderr = watchdog.communicate(timeout=10)

        assert watchdog.returncode == 0, stderr
        assert "Recovered an interrupted outer deployment" in stdout
        assert artifact.read_bytes() == b"original"
        assert not _manifest(recovery).parent.exists()
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        if watchdog is not None and watchdog.poll() is None:
            watchdog.kill()
            watchdog.wait(timeout=3)


def test_same_boot_watchdog_observes_clean_normal_commit(recovery):
    lock_dir = recovery["root"] / "normal-watchdog-locks"
    lock_dir.mkdir(mode=0o700)
    lock_file = lock_dir / "deploy.lock"
    ready = recovery["root"] / "normal-owner-ready"
    release = recovery["root"] / "normal-owner-release"
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ).returncode == 0

    owner = subprocess.Popen(
        [
            sys.executable,
            str(LOCK_HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--",
            sys.executable,
            "-c",
            (
                "import pathlib,time;"
                f"ready=pathlib.Path({str(ready)!r});"
                f"release=pathlib.Path({str(release)!r});"
                "ready.write_text('ready');"
                "\nwhile not release.exists(): time.sleep(0.01)"
            ),
        ],
        env=recovery["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    watchdog = None
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        watchdog = subprocess.Popen(
            [
                sys.executable,
                str(LOCK_HELPER),
                "--lock-file",
                str(lock_file),
                "--wait",
                "--marker-env",
                "HY2_DEPLOY_LOCK_MARKER",
                "--",
                sys.executable,
                "-I",
                str(HELPER),
                "recover",
            ],
            env=recovery["env"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        time.sleep(0.1)
        assert watchdog.poll() is None
        completed = recovery["run"]("complete")
        assert completed.returncode == 0, completed.stderr
        release.write_text("release", encoding="utf-8")
        owner_stdout, owner_stderr = owner.communicate(timeout=5)
        watchdog_stdout, watchdog_stderr = watchdog.communicate(timeout=10)

        assert owner.returncode == 0, owner_stderr or owner_stdout
        assert watchdog.returncode == 0, watchdog_stderr
        assert "recovery state is clean" in watchdog_stdout
        assert artifact.read_bytes() == b"replacement"
    finally:
        if owner.poll() is None:
            owner.kill()
            owner.wait(timeout=3)
        if watchdog is not None and watchdog.poll() is None:
            watchdog.kill()
            watchdog.wait(timeout=3)


def test_watchdog_failure_preserves_journal_and_rearmed_gate_fails_closed(
    recovery,
):
    lock_dir = recovery["root"] / "failed-watchdog-locks"
    lock_dir.mkdir(mode=0o700)
    lock_file = lock_dir / "deploy.lock"
    artifact = recovery["artifact_dir"] / "service.py"
    artifact.write_bytes(b"original")
    recovery["prepare_and_snapshot"]([artifact])
    candidate = _candidate(artifact, b"replacement")
    assert recovery["run"](
        "replace",
        "--path",
        artifact,
        "--candidate",
        candidate,
    ).returncode == 0
    artifact.write_bytes(b"unknown-third-generation")

    watchdog = subprocess.run(
        [
            sys.executable,
            str(LOCK_HELPER),
            "--lock-file",
            str(lock_file),
            "--wait",
            "--marker-env",
            "HY2_DEPLOY_LOCK_MARKER",
            "--",
            sys.executable,
            "-I",
            str(HELPER),
            "recover",
        ],
        env=recovery["env"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert watchdog.returncode != 0
    assert _manifest(recovery).exists()
    assert artifact.read_bytes() == b"unknown-third-generation"

    consumer_gate = subprocess.run(
        [
            sys.executable,
            str(LOCK_HELPER),
            "--lock-file",
            str(lock_file),
            "--timeout",
            "0",
            "--success-if-locked",
            "--marker-env",
            "HY2_DEPLOY_LOCK_MARKER",
            "--",
            sys.executable,
            "-I",
            str(HELPER),
            "recover",
        ],
        env=recovery["env"],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert consumer_gate.returncode != 0
    assert "unknown post-crash generation" in consumer_gate.stderr
    assert _manifest(recovery).exists()
