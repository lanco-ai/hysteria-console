"""End-to-end fault injection for the HTTPS nginx activation transaction.

The helper runs against a temporary nginx/Let's Encrypt tree with harmless
fake external commands. These tests exercise the real staging, atomic rename,
signal trap, rollback, and cleanup paths without touching host services.
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "scripts" / "hy2-enable-https.sh"
RECOVERY_UNIT = ROOT / "systemd" / "hy2-https-recovery.service"


def _write_executable(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _artifact_state(path):
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return ("absent",)
    if stat.S_ISLNK(metadata.st_mode):
        return ("symlink", os.readlink(path))
    return (
        "file",
        stat.S_IMODE(metadata.st_mode),
        path.read_bytes(),
    )


def _assert_no_transaction_debris(paths):
    parents = {path.parent for path in paths}
    leftovers = [
        child
        for parent in parents
        for child in parent.iterdir()
        if ".hy2-candidate." in child.name
        or ".hy2-rollback." in child.name
    ]
    assert leftovers == []


@pytest.fixture
def activation(tmp_path):
    nginx_root = tmp_path / "nginx"
    for directory in ("conf.d", "sites-available", "sites-enabled"):
        (nginx_root / directory).mkdir(parents=True, exist_ok=True)

    letsencrypt_root = tmp_path / "letsencrypt"
    (letsencrypt_root / "renewal-hooks" / "deploy").mkdir(
        parents=True,
        exist_ok=True,
    )
    cert_dir = letsencrypt_root / "live" / "panel.example.com"
    cert_dir.mkdir(parents=True)
    (cert_dir / "fullchain.pem").write_text(
        "PUBLIC_CERTIFICATE_FIXTURE\n",
        encoding="utf-8",
    )
    private_canary = "PRIVATE_KEY_MUST_NEVER_APPEAR_IN_OUTPUT"
    (cert_dir / "privkey.pem").write_text(
        private_canary + "\n",
        encoding="utf-8",
    )
    ip_cert_dir = letsencrypt_root / "live" / "192.0.2.1"
    ip_cert_dir.mkdir(parents=True)
    (ip_cert_dir / "fullchain.pem").write_text(
        "PUBLIC_IP_CERTIFICATE_FIXTURE\n",
        encoding="utf-8",
    )
    (ip_cert_dir / "privkey.pem").write_text(
        private_canary + "\n",
        encoding="utf-8",
    )

    share = tmp_path / "share"
    share.mkdir()
    (share / "hysteria-panel-log.conf").write_text(
        "log_format hy2_no_args '$uri';\n",
        encoding="utf-8",
    )
    (share / "hysteria-panel-redirect.conf").write_text(
        "redirect __HY_SERVER_HOST__ __HY_HTTPS_PORT__;\n",
        encoding="utf-8",
    )
    (share / "hysteria-panel-https.conf").write_text(
        "tls __HY_SERVER_HOST__ __HY_HTTPS_PORT__ "
        "__HY_TLS_CERT__ __HY_TLS_KEY__;\n",
        encoding="utf-8",
    )
    _write_executable(
        share / "hy2-cert-renew-hook.sh",
        "#!/usr/bin/env bash\nexit 0\n",
    )

    paths = {
        "log": nginx_root / "conf.d" / "hysteria-panel-log.conf",
        "panel": (
            nginx_root / "sites-available" / "hysteria-panel.conf"
        ),
        "tls": (
            nginx_root
            / "sites-available"
            / "hysteria-panel-https.conf"
        ),
        "link": (
            nginx_root
            / "sites-enabled"
            / "hysteria-panel-https.conf"
        ),
        "hook": (
            letsencrypt_root
            / "renewal-hooks"
            / "deploy"
            / "hy2-cert-renew-hook.sh"
        ),
    }
    paths["log"].write_text("old log\n", encoding="utf-8")
    paths["log"].chmod(0o640)
    paths["panel"].write_text("old redirect\n", encoding="utf-8")
    paths["tls"].write_text("old tls\n", encoding="utf-8")
    paths["link"].symlink_to("/operator/previous-tls.conf")
    paths["hook"].write_text("#!/bin/sh\n# old hook\n", encoding="utf-8")
    paths["hook"].chmod(0o700)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    command_log = tmp_path / "commands.log"
    reload_count = tmp_path / "reload-count"
    nginx_count = tmp_path / "nginx-count"
    probe_count = tmp_path / "probe-count"
    redirect_probe_count = tmp_path / "redirect-probe-count"

    _write_executable(
        bin_dir / "certbot",
        """#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == --version ]]; then
  printf 'certbot 5.4.0\n'
  exit 0
fi
printf 'certbot %s\n' "$*" >> "$HY2_TEST_COMMAND_LOG"
if [[ -n "${HY2_TEST_MUTATE_ARTIFACT:-}" ]]; then
  printf 'third-party-generation\n' > "$HY2_TEST_MUTATE_ARTIFACT"
fi
exit "${HY2_TEST_CERTBOT_STATUS:-0}"
""",
    )
    _write_executable(
        bin_dir / "openssl",
        """#!/usr/bin/env bash
set -eu
case " $* " in
  *" -checkend "*) exit 0 ;;
  *" -checkhost "*|*" -checkip "*) exit "${HY2_TEST_SAN_STATUS:-0}" ;;
  *" s_client "*)
    count=0
    [[ ! -f "$HY2_TEST_PROBE_COUNT" ]] ||
      count="$(cat "$HY2_TEST_PROBE_COUNT")"
    count=$((count + 1))
    printf '%s\n' "$count" > "$HY2_TEST_PROBE_COUNT"
    printf 'tls probe %s\n' "$count" >> "$HY2_TEST_COMMAND_LOG"
    if [[ "${HY2_TEST_PROBE_FAIL_CALL:-0}" == "$count" ]]; then
      exit 1
    fi
    exit 0
    ;;
  *" -enddate "*)
    if [[ "${HY2_TEST_ENDDATE_STATUS:-0}" != 0 ]]; then
      exit "$HY2_TEST_ENDDATE_STATUS"
    fi
    printf 'notAfter=Jul 18 00:00:00 2036 GMT\n'
    exit 0
    ;;
esac
exit 1
""",
    )
    _write_executable(
        bin_dir / "nginx",
        """#!/usr/bin/env bash
set -eu
count=0
[[ ! -f "$HY2_TEST_NGINX_COUNT" ]] ||
  count="$(cat "$HY2_TEST_NGINX_COUNT")"
count=$((count + 1))
printf '%s\n' "$count" > "$HY2_TEST_NGINX_COUNT"
printf 'nginx validation %s\n' "$count" >> "$HY2_TEST_COMMAND_LOG"
if [[ "${HY2_TEST_NGINX_FAIL_CALL:-0}" == "$count" ]]; then
  exit 1
fi
""",
    )
    _write_executable(
        bin_dir / "systemctl",
        """#!/usr/bin/env bash
set -eu
printf 'systemctl %s\n' "$*" >> "$HY2_TEST_COMMAND_LOG"
if [[ "${1:-}" == enable ]]; then
  exit "${HY2_TEST_TIMER_ENABLE_STATUS:-0}"
fi
if [[ "${1:-}" == is-active ]]; then
  if [[ " $* " == *" nginx.service "* ]]; then
    exit "${HY2_TEST_NGINX_ACTIVE_STATUS:-0}"
  fi
  exit "${HY2_TEST_TIMER_ACTIVE_STATUS:-0}"
fi
if [[ "${1:-}" == reload ]]; then
  count=0
  [[ ! -f "$HY2_TEST_RELOAD_COUNT" ]] ||
    count="$(cat "$HY2_TEST_RELOAD_COUNT")"
  count=$((count + 1))
  printf '%s\n' "$count" > "$HY2_TEST_RELOAD_COUNT"
  if [[ "${HY2_TEST_RELOAD_FAIL_CALL:-0}" == "$count" ]]; then
    exit 1
  fi
fi
""",
    )
    _write_executable(
        bin_dir / "redirect-probe",
        """#!/usr/bin/env bash
set -eu
target="$1"
port="$2"
path="$3"
expected="$4"
[[ "$expected" == "https://$target:$port$path" ]] || exit 2
count=0
[[ ! -f "$HY2_TEST_REDIRECT_PROBE_COUNT" ]] ||
  count="$(cat "$HY2_TEST_REDIRECT_PROBE_COUNT")"
count=$((count + 1))
printf '%s\n' "$count" > "$HY2_TEST_REDIRECT_PROBE_COUNT"
printf 'redirect probe %s\n' "$count" >> "$HY2_TEST_COMMAND_LOG"
if [[ "${HY2_TEST_REDIRECT_PROBE_FAIL_CALL:-0}" == "$count" ]]; then
  exit 1
fi
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "HY2_SHARE_DIR": str(share),
            "HY2_CERTBOT_WEBROOT": str(tmp_path / "webroot"),
            "HY2_NGINX_ROOT": str(nginx_root),
            "HY2_LETSENCRYPT_ROOT": str(letsencrypt_root),
            "HY2_CERTBOT_BIN": str(bin_dir / "certbot"),
            "HY2_OPENSSL_BIN": str(bin_dir / "openssl"),
            "HY2_NGINX_BIN": str(bin_dir / "nginx"),
            "HY2_SYSTEMCTL_BIN": str(bin_dir / "systemctl"),
            "HY2_HTTPS_REDIRECT_PROBE_BIN": str(
                bin_dir / "redirect-probe"
            ),
            "HY2_HTTPS_LOCK_FILE": str(tmp_path / "activation.lock"),
            "HY2_HTTPS_RECOVERY_DIR": str(tmp_path / "https-recovery"),
            "HY2_HTTPS_TEST_MODE": "1",
            "HY2_HTTPS_TEST_ROOT": str(tmp_path),
            "HY2_TEST_COMMAND_LOG": str(command_log),
            "HY2_TEST_RELOAD_COUNT": str(reload_count),
            "HY2_TEST_NGINX_COUNT": str(nginx_count),
            "HY2_TEST_PROBE_COUNT": str(probe_count),
            "HY2_TEST_REDIRECT_PROBE_COUNT": str(
                redirect_probe_count
            ),
        }
    )

    def run(
        *,
        fault=None,
        extra_env=None,
        target="panel.example.com",
        port="9444",
        recover_only=False,
    ):
        run_env = env.copy()
        if fault:
            run_env["HY2_HTTPS_TEST_FAULT"] = fault
        if extra_env:
            run_env.update(extra_env)
        command = [str(HELPER)]
        if recover_only:
            command.append("--recover-only")
        else:
            command.extend((target, "", port))
        return subprocess.run(
            command,
            env=run_env,
            capture_output=True,
            text=True,
            timeout=20,
        )

    return {
        "run": run,
        "paths": paths,
        "private_canary": private_canary,
        "command_log": command_log,
        "cert_dir": cert_dir,
        "probe_count": probe_count,
        "redirect_probe_count": redirect_probe_count,
        "env": env,
        "command": [
            str(HELPER),
            "panel.example.com",
            "",
            "9444",
        ],
        "share": share,
    }


def _states(paths):
    return {name: _artifact_state(path) for name, path in paths.items()}


def _restore_artifact_state(path, state):
    if path.exists() or path.is_symlink():
        path.unlink()
    if state[0] == "absent":
        return
    if state[0] == "symlink":
        path.symlink_to(state[1])
        return
    _, mode, content = state
    path.write_bytes(content)
    path.chmod(mode)


@pytest.mark.parametrize(
    ("fault", "expected_status"),
    (
        ("replace:2", 1),
        ("link", 1),
        ("signal:HUP", 129),
        ("signal:INT", 130),
        ("signal:TERM", 143),
    ),
)
def test_commit_and_signal_failures_restore_every_artifact(
    activation,
    fault,
    expected_status,
):
    before = _states(activation["paths"])

    result = activation["run"](fault=fault)

    assert result.returncode == expected_status
    assert _states(activation["paths"]) == before
    _assert_no_transaction_debris(activation["paths"].values())
    combined_output = result.stdout + result.stderr
    assert activation["private_canary"] not in combined_output


def test_final_reload_failure_restores_then_reloads_old_configuration(
    activation,
):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_TEST_RELOAD_FAIL_CALL": "2"},
    )

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert command_log.count("systemctl reload nginx.service") == 3
    assert activation["private_canary"] not in (
        result.stdout + result.stderr + command_log
    )
    _assert_no_transaction_debris(activation["paths"].values())


def test_initial_acme_reload_failure_restores_the_log_configuration(
    activation,
):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_TEST_RELOAD_FAIL_CALL": "1"},
    )

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert command_log.count("systemctl reload nginx.service") == 2
    _assert_no_transaction_debris(activation["paths"].values())


def test_nginx_validation_failure_restores_every_artifact(activation):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_TEST_NGINX_FAIL_CALL": "2"},
    )

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    _assert_no_transaction_debris(activation["paths"].values())


def test_certbot_failure_also_rolls_back_the_early_log_activation(activation):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_TEST_CERTBOT_STATUS": "42"},
    )

    assert result.returncode == 42
    assert _states(activation["paths"]) == before
    _assert_no_transaction_debris(activation["paths"].values())


def test_failure_restores_original_absence(activation):
    for path in activation["paths"].values():
        path.unlink()
    before = _states(activation["paths"])

    result = activation["run"](fault="reload")

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    _assert_no_transaction_debris(activation["paths"].values())


def test_success_atomically_activates_expected_files_and_link(activation):
    result = activation["run"]()

    assert result.returncode == 0, result.stderr
    recovery_root = Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"])
    assert not (recovery_root / "renewal-pending").exists()
    paths = activation["paths"]
    assert paths["log"].read_text(encoding="utf-8") == (
        "log_format hy2_no_args '$uri';\n"
    )
    assert paths["panel"].read_text(encoding="utf-8") == (
        "redirect panel.example.com 9444;\n"
    )
    assert paths["tls"].read_text(encoding="utf-8") == (
        "tls panel.example.com 9444 "
        f"{activation['cert_dir']}/fullchain.pem "
        f"{activation['cert_dir']}/privkey.pem;\n"
    )
    assert paths["link"].is_symlink()
    assert os.readlink(paths["link"]) == str(paths["tls"])
    assert stat.S_IMODE(paths["hook"].stat().st_mode) == 0o755
    assert activation["probe_count"].read_text(encoding="utf-8").strip() == "3"
    assert (
        activation["redirect_probe_count"]
        .read_text(encoding="utf-8")
        .strip()
        == "3"
    )
    assert "--cert-name panel.example.com" in (
        activation["command_log"].read_text(encoding="utf-8")
    )
    assert activation["private_canary"] not in (
        result.stdout
        + result.stderr
        + activation["command_log"].read_text(encoding="utf-8")
    )
    _assert_no_transaction_debris(paths.values())


def test_production_redirect_probe_waits_for_nginx_worker_handoff():
    helper = HELPER.read_text(encoding="utf-8")
    final_reload = helper.index(
        'reload nginx.service; then\n'
        '  die "nginx reload failed while activating the HTTPS redirect."'
    )
    grace_period = helper.index('/usr/bin/sleep 1', final_reload)
    redirect_probe = helper.index('\nprobe_redirect_once() {', grace_period)

    assert final_reload < grace_period < redirect_probe


@pytest.mark.parametrize("commit_prefix", (1, 2, 3, 4, 5))
def test_sigkill_after_every_commit_prefix_is_recovered_on_restart(
    activation,
    commit_prefix,
):
    paths = activation["paths"]
    for path in paths.values():
        path.unlink()

    killed = activation["run"](
        extra_env={
            "HY2_HTTPS_TEST_KILL_AFTER_COMMIT": str(commit_prefix),
        },
    )

    assert killed.returncode == -9
    pending = (
        Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"]) / "pending"
    )
    manifest = pending / "manifest.json"
    assert stat.S_IMODE(pending.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600

    # A crash may leave no redirect, or a redirect backed by an already
    # committed TLS file and enabled link.  It can never produce a dead
    # plaintext redirect pointing at a missing listener.
    if paths["panel"].exists():
        assert paths["tls"].is_file()
        assert paths["link"].is_symlink()
        assert os.readlink(paths["link"]) == str(paths["tls"])

    restarted = activation["run"]()

    assert restarted.returncode == 0, restarted.stderr
    assert "Recovered an interrupted HTTPS activation" in restarted.stderr
    assert not pending.exists()
    assert paths["tls"].is_file()
    assert paths["link"].is_symlink()
    assert paths["panel"].read_text(encoding="utf-8") == (
        "redirect panel.example.com 9444;\n"
    )
    for parent in {path.parent for path in paths.values()}:
        assert not [
            child
            for child in parent.iterdir()
            if ".hy2-" in child.name and child.name.endswith(".candidate")
        ]


def test_recover_only_refuses_to_clobber_a_third_generation(activation):
    killed = activation["run"](
        extra_env={"HY2_HTTPS_TEST_KILL_AFTER_COMMIT": "2"},
    )
    assert killed.returncode == -9
    tls_path = activation["paths"]["tls"]
    canary = b"operator-owned-third-generation\n"
    tls_path.write_bytes(canary)
    tls_path.chmod(0o644)

    recovered = activation["run"](recover_only=True)

    assert recovered.returncode == 1
    assert tls_path.read_bytes() == canary
    pending = (
        Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"]) / "pending"
    )
    assert pending.is_dir()
    assert stat.S_IMODE((pending / "manifest.json").stat().st_mode) == 0o600
    assert "durable recovery data remains" in recovered.stderr


def test_recover_only_skips_paths_already_restored_to_original_generation(
    activation,
):
    before = _states(activation["paths"])
    killed = activation["run"](
        extra_env={"HY2_HTTPS_TEST_KILL_AFTER_COMMIT": "2"},
    )
    assert killed.returncode == -9
    for name in ("log", "tls"):
        _restore_artifact_state(
            activation["paths"][name],
            before[name],
        )
    restored_inodes = {
        name: activation["paths"][name].stat().st_ino
        for name in ("log", "tls")
    }

    recovered = activation["run"](recover_only=True)

    assert recovered.returncode == 0, recovered.stderr
    assert _states(activation["paths"]) == before
    assert {
        name: activation["paths"][name].stat().st_ino
        for name in ("log", "tls")
    } == restored_inodes
    pending = Path(
        activation["env"]["HY2_HTTPS_RECOVERY_DIR"],
        "pending",
    )
    assert not pending.exists(), recovered.stderr


def test_commit_cas_preserves_a_noncooperating_writer_during_acme(
    activation,
):
    tls_path = activation["paths"]["tls"]

    result = activation["run"](
        extra_env={"HY2_TEST_MUTATE_ARTIFACT": str(tls_path)},
    )

    assert result.returncode == 1
    assert tls_path.read_text(encoding="utf-8") == (
        "third-party-generation\n"
    )
    pending = Path(
        activation["env"]["HY2_HTTPS_RECOVERY_DIR"],
        "pending",
    )
    assert not pending.exists(), result.stderr


def test_boot_recover_only_restores_disk_without_reloading_inactive_nginx(
    activation,
):
    before = _states(activation["paths"])
    killed = activation["run"](
        extra_env={"HY2_HTTPS_TEST_KILL_AFTER_COMMIT": "2"},
    )
    assert killed.returncode == -9

    recovered = activation["run"](
        recover_only=True,
        extra_env={"HY2_TEST_NGINX_ACTIVE_STATUS": "3"},
    )

    assert recovered.returncode == 0, recovered.stderr
    assert _states(activation["paths"]) == before
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert command_log.count("systemctl reload nginx.service") == 1
    assert "systemctl is-active --quiet nginx.service" in command_log


def test_recovery_unit_is_a_required_pre_nginx_fail_closed_gate():
    unit = RECOVERY_UNIT.read_text(encoding="utf-8")

    assert "Before=nginx.service" in unit
    assert "RequiredBy=nginx.service" in unit
    assert "ConditionPathExists=" not in unit
    assert (
        "ExecStart=/usr/local/sbin/hy2-enable-https.sh --recover-only"
    ) in unit
    assert "SuccessExitStatus=2" in unit
    for directive in (
        "User=root",
        "UMask=0077",
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "RestrictNamespaces=true",
    ):
        assert directive in unit
    assert "ReadWritePaths=/etc/nginx /etc/systemd/system" in unit


def test_recover_only_is_a_clean_noop_before_nginx_exists(tmp_path):
    tmp_path.chmod(0o700)
    environment = os.environ.copy()
    environment.update(
        {
            "HY2_HTTPS_TEST_MODE": "1",
            "HY2_HTTPS_TEST_ROOT": str(tmp_path),
            "HY2_HTTPS_LOCK_FILE": str(tmp_path / "activation.lock"),
            "HY2_HTTPS_RECOVERY_DIR": str(tmp_path / "missing-recovery"),
            "HY2_NGINX_ROOT": str(tmp_path / "missing-nginx"),
            "HY2_LETSENCRYPT_ROOT": str(tmp_path / "missing-letsencrypt"),
            "HY2_SHARE_DIR": str(tmp_path / "missing-share"),
        }
    )

    result = subprocess.run(
        [str(HELPER), "--recover-only"],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert "recovery state is clean" in result.stdout
    assert not (tmp_path / "missing-nginx").exists()
    assert not (tmp_path / "missing-recovery").exists()


def test_incomplete_rollback_preserves_private_manifest_and_snapshots(
    activation,
):
    before = _states(activation["paths"])

    failed = activation["run"](
        fault="replace:2",
        extra_env={"HY2_HTTPS_TEST_RESTORE_FAIL_INDEX": "1"},
    )

    assert failed.returncode == 1
    pending = (
        Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"]) / "pending"
    )
    manifest = pending / "manifest.json"
    assert pending.is_dir()
    assert stat.S_IMODE(pending.stat().st_mode) == 0o700
    assert stat.S_IMODE(manifest.stat().st_mode) == 0o600
    assert list(pending.glob("snapshot-*"))
    assert str(pending) in failed.stderr
    assert activation["private_canary"] not in failed.stderr

    recovered = activation["run"]()

    assert recovered.returncode == 0, recovered.stderr
    assert not pending.exists()
    # Recovery first restores the old state, then the retry completes a new
    # activation.  No partially restored state is treated as success.
    assert _states(activation["paths"]) != before


@pytest.mark.parametrize(
    "extra_env",
    (
        {"HY2_TEST_SAN_STATUS": "1"},
        {"HY2_TEST_PROBE_FAIL_CALL": "2"},
    ),
)
def test_certificate_and_stability_failures_roll_back(
    activation,
    extra_env,
):
    before = _states(activation["paths"])

    result = activation["run"](extra_env=extra_env)

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    assert activation["private_canary"] not in result.stdout + result.stderr


def test_successful_final_reload_without_live_redirect_is_rolled_back(
    activation,
):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_TEST_REDIRECT_PROBE_FAIL_CALL": "1"},
    )

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert command_log.count("systemctl reload nginx.service") == 4
    assert "redirect probe 1" in command_log
    assert not activation["probe_count"].exists()


@pytest.mark.parametrize(
    "extra_env",
    (
        {"HY2_TEST_TIMER_ENABLE_STATUS": "1"},
        {"HY2_TEST_TIMER_ACTIVE_STATUS": "1"},
    ),
)
def test_renewal_timer_failure_is_truthful_post_commit_degraded_status(
    activation,
    extra_env,
):
    before = _states(activation["paths"])

    result = activation["run"](extra_env=extra_env)

    assert result.returncode == 2
    assert _states(activation["paths"]) != before
    assert "HTTPS is active" in result.stderr
    assert "renewal is degraded" in result.stderr
    assert activation["paths"]["panel"].read_text(encoding="utf-8") == (
        "redirect panel.example.com 9444;\n"
    )
    assert not Path(
        activation["env"]["HY2_HTTPS_RECOVERY_DIR"],
        "pending",
    ).exists()
    renewal_marker = Path(
        activation["env"]["HY2_HTTPS_RECOVERY_DIR"],
        "renewal-pending",
    )
    assert renewal_marker.is_file()
    assert stat.S_IMODE(renewal_marker.stat().st_mode) == 0o600
    marker_txid = renewal_marker.read_text(encoding="ascii").strip()
    assert len(marker_txid) == 32
    assert all(character in "0123456789abcdef" for character in marker_txid)

    recovered = activation["run"](recover_only=True)

    assert recovered.returncode == 0, recovered.stderr
    assert not renewal_marker.exists()


def test_marker_only_boot_recovery_keeps_degraded_status_until_timer_works(
    activation,
):
    failed = activation["run"](
        extra_env={"HY2_TEST_TIMER_ENABLE_STATUS": "1"},
    )
    assert failed.returncode == 2
    recovery_root = Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"])
    renewal_marker = recovery_root / "renewal-pending"
    assert renewal_marker.is_file()
    assert not (recovery_root / "pending").exists()

    still_degraded = activation["run"](
        recover_only=True,
        extra_env={"HY2_TEST_TIMER_ENABLE_STATUS": "1"},
    )

    assert still_degraded.returncode == 2
    assert "renewal remains degraded" in still_degraded.stderr
    assert renewal_marker.is_file()


def test_recover_only_rejects_an_unsafe_renewal_marker(activation):
    recovery_root = Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"])
    recovery_root.mkdir(mode=0o700)
    canary = recovery_root.parent / "operator-canary"
    canary.write_text("do not unlink\n", encoding="utf-8")
    renewal_marker = recovery_root / "renewal-pending"
    renewal_marker.symlink_to(canary)

    recovered = activation["run"](recover_only=True)

    assert recovered.returncode == 1
    assert "recovery state is unsafe" in recovered.stderr
    assert renewal_marker.is_symlink()
    assert canary.read_text(encoding="utf-8") == "do not unlink\n"


def test_renewal_marker_is_removed_when_precomplete_failure_rolls_back(
    activation,
):
    before = _states(activation["paths"])

    result = activation["run"](fault="renewal-journal")

    assert result.returncode == 1
    assert _states(activation["paths"]) == before
    recovery_root = Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"])
    assert not (recovery_root / "pending").exists()
    assert not (recovery_root / "renewal-pending").exists()


def test_sigkill_before_timer_is_reconciled_from_renewal_marker(activation):
    before = _states(activation["paths"])

    killed = activation["run"](fault="kill-before-timer")

    assert killed.returncode == -9
    assert _states(activation["paths"]) != before
    recovery_root = Path(activation["env"]["HY2_HTTPS_RECOVERY_DIR"])
    assert not (recovery_root / "pending").exists()
    renewal_marker = recovery_root / "renewal-pending"
    assert renewal_marker.is_file()
    assert stat.S_IMODE(renewal_marker.stat().st_mode) == 0o600

    recovered = activation["run"](recover_only=True)

    assert recovered.returncode == 0, recovered.stderr
    assert not renewal_marker.exists()
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert "systemctl enable --now snap.certbot.renew.timer" in command_log
    assert (
        "systemctl is-active --quiet snap.certbot.renew.timer"
        in command_log
    )


def test_expiry_display_failure_is_post_commit_and_does_not_lie(activation):
    result = activation["run"](
        extra_env={"HY2_TEST_ENDDATE_STATUS": "42"},
    )

    assert result.returncode == 0
    assert "HTTPS is active" in result.stderr
    assert activation["paths"]["panel"].read_text(encoding="utf-8") == (
        "redirect panel.example.com 9444;\n"
    )


def test_closed_stdout_after_commit_does_not_change_success_status(activation):
    process = subprocess.Popen(
        activation["command"],
        env=activation["env"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    process.stdout.close()
    stderr = process.stderr.read()
    status = process.wait(timeout=20)

    assert status == 0, stderr
    assert activation["paths"]["panel"].exists()


@pytest.mark.parametrize(
    ("target", "port"),
    (
        ("01.2.3.4", "9444"),
        ("999.2.3.4", "9444"),
        ("singlelabel", "9444"),
        ("-bad.example.com", "9444"),
        ("bad..example.com", "9444"),
        ("panel.example.com", "09444"),
        ("panel.example.com", "443"),
        ("panel.example.com", "8081"),
        ("panel.example.com", "8082"),
        ("panel.example.com", "8443"),
        ("panel.example.com", "9443"),
        ("panel.example.com", "10085"),
        ("panel.example.com", "25413"),
    ),
)
def test_noncanonical_targets_and_reserved_ports_fail_before_mutation(
    activation,
    target,
    port,
):
    before = _states(activation["paths"])

    result = activation["run"](target=target, port=port)

    assert result.returncode != 0
    assert _states(activation["paths"]) == before
    assert not Path(
        activation["env"]["HY2_HTTPS_RECOVERY_DIR"],
        "pending",
    ).exists()


def test_template_with_unknown_placeholder_is_rejected_and_rolled_back(
    activation,
):
    before = _states(activation["paths"])
    template = activation["share"] / "hysteria-panel-https.conf"
    template.write_text(
        "tls __HY_SERVER_HOST__ __HY_HTTPS_PORT__ "
        "__HY_TLS_CERT__ __HY_TLS_KEY__ __UNKNOWN_PLACEHOLDER__;\n",
        encoding="utf-8",
    )

    result = activation["run"]()

    assert result.returncode == 1
    assert _states(activation["paths"]) == before


def test_canonical_ip_uses_explicit_cert_name_ip_profile_and_san_check(
    activation,
):
    result = activation["run"](target="192.0.2.1")

    assert result.returncode == 0, result.stderr
    command_log = activation["command_log"].read_text(encoding="utf-8")
    assert "--cert-name 192.0.2.1" in command_log
    assert "--ip-address 192.0.2.1" in command_log
    assert (
        f"{Path(activation['env']['HY2_LETSENCRYPT_ROOT'])}/live/"
        "192.0.2.1/fullchain.pem"
    ) in activation["paths"]["tls"].read_text(encoding="utf-8")


def test_dns_hostname_is_normalized_to_lowercase_before_certificate_use(
    activation,
):
    result = activation["run"](target="Panel.Example.COM")

    assert result.returncode == 0, result.stderr
    assert "--cert-name panel.example.com" in (
        activation["command_log"].read_text(encoding="utf-8")
    )
    assert "https://panel.example.com:9444/admin" in result.stdout


def test_test_command_override_cannot_escape_root_only_harness(activation):
    before = _states(activation["paths"])

    result = activation["run"](
        extra_env={"HY2_SYSTEMCTL_BIN": "/bin/true"},
    )

    assert result.returncode != 0
    assert _states(activation["paths"]) == before
    assert "escapes the isolated test root" in result.stderr


def test_privileged_shell_entry_ignores_bash_env_and_exported_functions(
    activation,
    tmp_path,
):
    bash_env_canary = tmp_path / "bash-env-canary"
    function_canary = tmp_path / "function-canary"
    bash_env = tmp_path / "bash-env.sh"
    bash_env.write_text(
        f"printf injected > {str(bash_env_canary)!r}\n",
        encoding="utf-8",
    )

    result = activation["run"](
        port="09444",
        extra_env={
            "BASH_ENV": str(bash_env),
            "BASH_FUNC_dirname%%": (
                "() { "
                f"printf injected > {str(function_canary)!r}; "
                '/usr/bin/dirname "$@"; '
                "}"
            ),
        },
    )

    assert result.returncode != 0
    assert not bash_env_canary.exists()
    assert not function_canary.exists()
