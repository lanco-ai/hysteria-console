import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hy2-health-check.sh"


def _write_executable(path, body):
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _health_env(tmp_path, *, require_https=False):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    call_log = tmp_path / "curl.calls"
    _write_executable(
        fake_bin / "systemctl",
        """if [[ "$*" == "is-active --quiet snap.certbot.renew.timer" &&
              "${FAIL_RENEW_TIMER:-0}" == 1 ]]; then
  exit 3
fi
exit 0
""",
    )
    _write_executable(
        fake_bin / "curl",
        'printf "%s\\n" "$*" >>"$CALL_LOG"\nexit 0\n',
    )

    hy_dir = tmp_path / "runtime"
    backups = hy_dir / "backups"
    state = hy_dir / "state"
    backups.mkdir(parents=True)
    state.mkdir()
    (backups / "hy2-backup-test.tar.gz").write_bytes(b"backup")
    marker = state / "https_required"
    if require_https:
        marker.write_text("required\n", encoding="utf-8")

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "CALL_LOG": str(call_log),
            "HY2_HY_DIR": str(hy_dir),
            "HY2_BACKUP_DIR": str(backups),
            "HY2_TLS_SITE_CONF": str(tmp_path / "missing-tls-site.conf"),
            "HY2_HTTPS_REQUIRED_FILE": str(marker),
            "HY2_HTTPS_RENEWAL_PENDING": str(
                tmp_path / "renewal-pending"
            ),
        }
    )
    return env, call_log


def test_operational_health_check_requires_auth_service_and_deep_readiness():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "hysteria-auth.service" in script
    assert "http://127.0.0.1:8082/readyz" in script
    assert "--noproxy '*'" in script
    assert "--connect-timeout 1" in script
    assert "--max-time 3" in script
    assert "authentication dependencies are not ready" in script


def test_operational_health_check_uses_the_configured_tls_identity():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "HY2_TLS_SITE_CONF" in script
    assert "HY2_HTTPS_REQUIRED_FILE" in script
    assert '-f "$HTTPS_REQUIRED_FILE"' in script
    assert '-e "$TLS_SITE_CONF"' in script
    assert '$1 == "server_name"' in script
    assert '$1 == "ssl_certificate"' in script
    assert "find /etc/letsencrypt/live" not in script
    assert '--resolve "${panel_target}:${HTTPS_PORT}:127.0.0.1"' in script
    assert "panel HTTPS intentionally not required" in script


def test_http_only_runtime_does_not_report_optional_tls_as_broken(tmp_path):
    env, call_log = _health_env(tmp_path)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "panel HTTPS intentionally not required" in result.stdout
    calls = call_log.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8082/readyz" in calls
    assert "https://" not in calls


def test_required_tls_missing_fails_operational_health(tmp_path):
    env, _call_log = _health_env(tmp_path, require_https=True)

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 1
    assert "configured panel certificate missing" in result.stderr
    assert "local HTTPS endpoint unavailable" in result.stderr


def test_required_tls_reports_inactive_renewal_timer(tmp_path):
    env, _call_log = _health_env(tmp_path, require_https=True)
    env["FAIL_RENEW_TIMER"] = "1"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 1
    assert "certificate renewal timer is not active" in result.stderr


def test_required_tls_reports_pending_renewal_recovery(tmp_path):
    env, _call_log = _health_env(tmp_path, require_https=True)
    Path(env["HY2_HTTPS_RENEWAL_PENDING"]).write_text(
        "transaction\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )

    assert result.returncode == 1
    assert "certificate renewal activation is pending recovery" in result.stderr
