from pathlib import Path
import os
import subprocess

import yaml


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_hysteria_uses_loopback_http_auth():
    config = yaml.safe_load(_read("hysteria/config.yaml.tpl"))

    assert config["auth"] == {
        "type": "http",
        "http": {"url": "http://127.0.0.1:8082/auth"},
    }
    assert "command:" not in _read("hysteria/config.yaml.tpl")


def test_auth_unit_is_bounded_sandboxed_and_orders_hysteria():
    auth_unit = _read("systemd/hysteria-auth.service")
    server_unit = _read("systemd/hysteria-server.service")

    for directive in (
        "ExecStart=/usr/bin/python3 /root/hysteria/auth_service.py",
        "ExecStartPost=/usr/bin/curl --fail --silent --show-error "
        "--noproxy * --max-time 2 --retry 5 --retry-delay 1 "
        "--retry-connrefused "
        "http://127.0.0.1:8082/livez",
        "ReadOnlyPaths=/root/hysteria",
        "ReadWritePaths=/root/hysteria/state",
        "IPAddressDeny=any",
        "IPAddressAllow=localhost",
        "TasksMax=32",
        "MemoryHigh=128M",
        "MemoryMax=256M",
        "LimitNOFILE=256",
        "NoNewPrivileges=true",
    ):
        assert directive in auth_unit
    assert "Before=hysteria-server.service" in auth_unit
    assert "Requires=hysteria-auth.service" in server_unit
    assert "BindsTo=hysteria-auth.service" in server_unit
    assert "PartOf=hysteria-auth.service" in server_unit
    assert "After=network-online.target hysteria-auth.service" in server_unit
    assert "http://127.0.0.1:8082/readyz" in server_unit
    assert "--noproxy *" in server_unit
    live_probe = auth_unit.index("http://127.0.0.1:8082/livez")
    recover = auth_unit.index(
        "ExecStartPost=/usr/local/sbin/hysteria-auth-recover.sh recover"
    )
    assert live_probe < recover
    deep_probe = server_unit.index("http://127.0.0.1:8082/readyz")
    mark = server_unit.index(
        "ExecStartPost=/usr/local/sbin/hysteria-auth-recover.sh mark"
    )
    clear = server_unit.index(
        "ExecStopPost=/usr/local/sbin/hysteria-auth-recover.sh "
        "clear-if-manual"
    )
    assert deep_probe < mark < clear


def test_deploy_installs_rolls_back_and_probes_auth_before_hysteria():
    deploy = _read("deploy.sh")

    assert '"$HY_DIR/auth_service.py"' in deploy
    assert '"$SYSTEMD_DIR/hysteria-auth.service"' in deploy
    assert (
        'render "$REPO_DIR/hysteria/auth_service.py"'
        '          "$HY_DIR/auth_service.py"'
    ) in deploy
    assert (
        'install_atomic 644 "$REPO_DIR/systemd/hysteria-auth.service"'
    ) in deploy
    assert deploy.count("hysteria-auth.service") >= 7
    assert (
        'install_atomic 755 "$REPO_DIR/scripts/hysteria-auth-recover.sh" '
        "/usr/local/sbin/hysteria-auth-recover.sh"
    ) in deploy
    assert "/usr/local/sbin/hysteria-auth-recover.sh" in deploy

    enable_auth = deploy.index(
        "systemctl enable --now hysteria-auth.service"
    )
    auth_readiness = deploy.index(
        "http://127.0.0.1:8082/livez", enable_auth
    )
    assert "--noproxy '*'" in deploy[enable_auth:auth_readiness]
    enable_hysteria = deploy.index(
        "systemctl enable --now hysteria-server.service", auth_readiness
    )
    assert enable_auth < auth_readiness < enable_hysteria
    stable_gate = deploy.index("wait_for_stable_readiness 3 15 1")
    deep_readiness = deploy.index(
        "http://127.0.0.1:8082/readyz", deploy.index(
            "wait_for_stable_readiness()"
        )
    )
    assert "--noproxy '*'" in deploy[
        deploy.index("wait_for_stable_readiness()"):stable_gate
    ]
    assert deep_readiness < enable_hysteria < stable_gate


def test_fresh_deploy_defers_operational_health_until_lock_is_released():
    deploy = _read("deploy.sh")

    assert "systemctl enable --now hy2-health-check.timer" in deploy
    assert "systemctl start hy2-health-check.service" not in deploy
    assert "Operational health is timer-owned" in deploy


def test_deploy_tracks_https_requirement_as_rollback_safe_runtime_state():
    deploy = _read("deploy.sh")
    marker = '"$HY_DIR/state/https_required"'

    snapshot = deploy.index(marker)
    write = deploy.index(
        'write_atomic_from_stdin 600 "$HY_DIR/state/https_required"',
        snapshot + len(marker),
    )
    remove = deploy.index(
        'durable_remove_artifact "$HY_DIR/state/https_required"',
        write,
    )
    assert snapshot < write < remove
    assert 'HY_ENABLE_HTTPS must be 0 or 1' in deploy


def test_release_downloads_have_retry_and_absolute_timeouts():
    deploy = _read("deploy.sh")

    for option in (
        "--connect-timeout 10",
        "--max-time 300",
        "--retry 4",
        "--retry-delay 2",
        "--retry-connrefused",
    ):
        assert option in deploy
    assert deploy.count('curl "${CURL_DOWNLOAD[@]}"') == 3
    assert "github.com/apernet/hysteria/releases/download/" in deploy
    assert "github.com/XTLS/Xray-core/releases/download/" in deploy
    assert "github.com/tuic-protocol/tuic/releases/download/" in deploy


def test_operator_docs_describe_persistent_and_fallback_paths():
    english = _read("README.md")
    chinese = _read("README.zh-CN.md")
    context = _read("CONTEXT.md")

    assert "persistent http auth" in english.lower()
    assert "token-only" in english
    assert "PBKDF2" in english
    assert "持久 HTTP 鉴权" in chinese
    assert "仅兼容 token" in chinese
    assert "PBKDF2" in chinese
    assert "hysteria-auth.service" in context


def test_auth_recovery_preserves_failure_intent_but_not_manual_stop(
    tmp_path,
):
    helper = ROOT / "scripts/hysteria-auth-recover.sh"
    tmp_path.chmod(0o700)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(mode=0o700)
    fake = bin_dir / "systemctl"
    intent_dir = tmp_path / "intent"
    log = tmp_path / "calls.log"
    fake.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s\n' "$*" >> "$CALL_LOG"
if [[ "$1" == "is-enabled" ]]; then
  [[ "${SERVER_ENABLED:-0}" == "1" ]]
  exit
fi
if [[ "$1" == "is-active" ]]; then
  [[ "${AUTH_ACTIVE:-0}" == "1" ]]
  exit
fi
[[ "$*" == "--no-block start hysteria-server.service" ]]
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    base_env = {
        **os.environ,
        "HY2_AUTH_INTENT_TEST_MODE": "1",
        "HY2_AUTH_INTENT_TEST_ROOT": str(tmp_path),
        "HY2_AUTH_INTENT_DIR": str(intent_dir),
        "HY2_SYSTEMCTL_BIN": str(fake),
        "CALL_LOG": str(log),
        "SERVER_ENABLED": "1",
        "AUTH_ACTIVE": "1",
    }

    marked = subprocess.run(
        ["bash", str(helper), "mark"],
        env=base_env,
        capture_output=True,
        text=True,
    )
    assert marked.returncode == 0, marked.stderr
    marker = intent_dir / "server-wanted"
    assert marker.read_text(encoding="utf-8") == "wanted\n"
    assert marker.stat().st_mode & 0o777 == 0o600

    log.write_text("", encoding="utf-8")
    recovered = subprocess.run(
        ["bash", str(helper), "recover"],
        env=base_env,
        capture_output=True,
        text=True,
    )
    assert recovered.returncode == 0, recovered.stderr
    assert log.read_text(encoding="utf-8").splitlines() == [
        "is-enabled --quiet hysteria-server.service",
        "--no-block start hysteria-server.service",
    ]

    log.write_text("", encoding="utf-8")
    failure_stop = subprocess.run(
        ["bash", str(helper), "clear-if-manual"],
        env={**base_env, "SERVICE_RESULT": "signal"},
        capture_output=True,
        text=True,
    )
    assert failure_stop.returncode == 0, failure_stop.stderr
    assert marker.exists()
    assert log.read_text(encoding="utf-8") == ""

    dependency_stop = subprocess.run(
        ["bash", str(helper), "clear-if-manual"],
        env={
            **base_env,
            "SERVICE_RESULT": "success",
            "AUTH_ACTIVE": "0",
        },
        capture_output=True,
        text=True,
    )
    assert dependency_stop.returncode == 0, dependency_stop.stderr
    assert marker.exists()

    log.write_text("", encoding="utf-8")
    manual_stop = subprocess.run(
        ["bash", str(helper), "clear-if-manual"],
        env={**base_env, "SERVICE_RESULT": "success"},
        capture_output=True,
        text=True,
    )
    assert manual_stop.returncode == 0, manual_stop.stderr
    assert not marker.exists()
    assert log.read_text(encoding="utf-8").splitlines() == [
        "is-active --quiet hysteria-auth.service"
    ]

    log.write_text("", encoding="utf-8")
    no_intent = subprocess.run(
        ["bash", str(helper), "recover"],
        env=base_env,
        capture_output=True,
        text=True,
    )
    assert no_intent.returncode == 0, no_intent.stderr
    assert log.read_text(encoding="utf-8") == ""
