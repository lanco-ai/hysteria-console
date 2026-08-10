"""Security regressions for nginx/deploy changes.

These tests inspect repository sources or execute extracted, path-parameterized
shell snippets exclusively under pytest's temporary directory.  They never run
the production deploy scripts or touch the host nginx configuration.
"""

import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy.sh"
HTTPS_HELPER = ROOT / "scripts" / "hy2-enable-https.sh"
NGINX_DIR = ROOT / "nginx"


def _read(path):
    return path.read_text(encoding="utf-8")


def _extract_function(script, name):
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}$",
        script,
    )
    assert match, f"missing shell function: {name}"
    return match.group(0)


def test_access_log_format_omits_query_variables_and_all_vhosts_use_it():
    log_format = _read(NGINX_DIR / "hysteria-panel-log.conf")
    variables = set(re.findall(r"\$[A-Za-z0-9_]+", log_format))

    assert {"$request_method", "$uri", "$server_protocol"} <= variables
    assert {
        "$request",
        "$request_uri",
        "$args",
        "$query_string",
        "$is_args",
        "$http_referer",
    }.isdisjoint(variables)

    access_log = (
        "access_log /var/log/nginx/hysteria-panel.access.log hy2_no_args;"
    )
    for name in (
        "hysteria-panel-bootstrap.conf",
        "hysteria-panel.conf",
        "hysteria-panel-redirect.conf",
        "hysteria-panel-https.conf",
    ):
        assert access_log in _read(NGINX_DIR / name), name


def test_panel_error_logs_suppress_request_lines_below_critical():
    directive = (
        "error_log /var/log/nginx/hysteria-panel.error.log crit;"
    )
    for name in (
        "hysteria-panel-bootstrap.conf",
        "hysteria-panel.conf",
        "hysteria-panel-redirect.conf",
        "hysteria-panel-https.conf",
    ):
        assert directive in _read(NGINX_DIR / name), name


def test_panel_vhosts_apply_restrictive_browser_security_headers():
    required = (
        "add_header X-Content-Type-Options nosniff always;",
        "add_header Referrer-Policy no-referrer always;",
        "add_header X-Frame-Options DENY always;",
        "default-src 'self'",
        "base-uri 'none'",
        "object-src 'none'",
        "frame-ancestors 'none'",
        "form-action 'self'",
        "connect-src 'self'",
    )
    for name in (
        "hysteria-panel.conf",
        "hysteria-panel-https.conf",
    ):
        config = _read(NGINX_DIR / name)
        for directive in required:
            assert directive in config, (name, directive)


def test_panel_proxy_has_request_backpressure_and_bounded_timeouts():
    log_config = _read(NGINX_DIR / "hysteria-panel-log.conf")
    assert (
        "limit_conn_zone $binary_remote_addr "
        "zone=hy2_panel_conn:10m;"
    ) in log_config
    assert (
        "limit_req_zone $binary_remote_addr "
        "zone=hy2_panel_req:10m rate=10r/s;"
    ) in log_config

    for name in (
        "hysteria-panel.conf",
        "hysteria-panel-https.conf",
    ):
        config = _read(NGINX_DIR / name)
        for directive in (
            "client_body_timeout 10s;",
            "client_header_timeout 10s;",
            "keepalive_timeout 30s;",
            "send_timeout 30s;",
            "limit_conn hy2_panel_conn 20;",
            "limit_conn_status 429;",
            "limit_req zone=hy2_panel_req burst=40 nodelay;",
            "limit_req_status 429;",
            "proxy_connect_timeout 2s;",
            "proxy_send_timeout 15s;",
            "proxy_read_timeout 30s;",
        ):
            assert directive in config, (name, directive)


def test_log_format_is_required_and_installed_before_nginx_validation():
    deploy = _read(DEPLOY)
    helper = _read(HTTPS_HELPER)

    share_install = (
        'install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-log.conf" '
        "/usr/local/share/hy2/hysteria-panel-log.conf"
    )
    live_install = (
        'install_atomic 644 "$REPO_DIR/nginx/hysteria-panel-log.conf" '
        "/etc/nginx/conf.d/hysteria-panel-log.conf"
    )
    helper_call = re.search(
        r"/usr/local/sbin/hy2-enable-https\.sh\s+\\?\n?\s*"
        r'"\$HY_SERVER_HOST" "\$\{HY_CERTBOT_EMAIL:-\}" '
        r'"\$HY_HTTPS_PORT"',
        deploy,
    )
    assert helper_call

    assert deploy.index(share_install) < helper_call.start()
    assert deploy.index(live_install) < deploy.index("nginx -t")

    required_assets = re.search(
        r"(?ms)^\s*for template in \\\n(?P<assets>.*?)^\s*"
        r"hy2-cert-renew-hook\.sh; do$",
        helper,
    )
    assert required_assets
    assert "hysteria-panel-log.conf" in required_assets.group("assets")

    transaction_start = "\nmanifest_initialize\n"
    log_stage = (
        "stage_regular log_candidate \\\n"
        '  "$share_dir/hysteria-panel-log.conf" "$log_conf" 644'
    )
    assert helper.index(transaction_start) < helper.index(log_stage)
    log_stage_position = helper.index(log_stage)
    assert log_stage_position < helper.index(
        '"$nginx_bin" -t',
        log_stage_position,
    )


def test_https_recovery_unit_is_transactionally_installed_and_enabled():
    deploy = _read(DEPLOY)

    managed_units = re.search(
        r"(?ms)^declare -a DEPLOY_MANAGED_UNITS=\(\n(?P<body>.*?)^\)$",
        deploy,
    )
    assert managed_units
    assert "hy2-https-recovery.service" in managed_units.group("body")

    snapshot_entry = (
        '"$SYSTEMD_DIR/hy2-https-recovery.service"'
    )
    install_entry = (
        'install_atomic 644 '
        '"$REPO_DIR/systemd/hy2-https-recovery.service" \\\n'
        '  "$SYSTEMD_DIR/hy2-https-recovery.service"'
    )
    capture = deploy.index("\ncapture_service_state\n")
    snapshot = deploy.index("\nbegin_durable_artifact_snapshot\n")
    install = deploy.index(install_entry)
    daemon_reload = deploy.index("systemctl daemon-reload", install)
    enable = deploy.index(
        "systemctl enable hy2-https-recovery.service",
        daemon_reload,
    )

    assert capture < snapshot < install < daemon_reload < enable
    assert "enable --now hy2-https-recovery.service" not in deploy

    required_units = re.search(
        r"(?ms)^required_active_units=\(\n(?P<body>.*?)^\)$",
        deploy,
    )
    assert required_units
    assert "hy2-https-recovery.service" not in required_units.group("body")


def test_deploy_reconciles_https_recovery_under_both_locks_before_mutation():
    deploy = _read(DEPLOY)

    deploy_lock = deploy.index("--marker-env HY2_DEPLOY_LOCK_MARKER")
    https_lock = deploy.index("--marker-env HY2_HTTPS_LOCK_MARKER")
    recover = deploy.index(
        '/usr/bin/bash -p "$REPO_DIR/scripts/hy2-enable-https.sh" '
        "--recover-only"
    )
    validation = deploy.index(
        "# ---------- 1. Validate parsed deployment environment ----------"
    )

    assert deploy_lock < https_lock < recover < validation
    recovery_gate = deploy[recover:validation]
    assert "HTTPS_RECOVERY_PENDING" not in recovery_gate
    assert "https_recovery_status=$?" in recovery_gate
    assert re.search(
        r'(?ms)case "\$https_recovery_status" in\n'
        r"\s*0\) ;;\n"
        r"\s*2\).*?warn .*renewal remains degraded",
        recovery_gate,
    )
    assert "deployment stopped before mutation" in recovery_gate


def test_acme_bootstrap_serves_challenges_but_never_proxies_plaintext_panel():
    bootstrap = _read(NGINX_DIR / "hysteria-panel-bootstrap.conf")

    assert "location ^~ /.well-known/acme-challenge/" in bootstrap
    assert "try_files $uri =404;" in bootstrap
    assert "proxy_pass" not in bootstrap

    root_location = re.search(
        r"(?ms)^\s*location / \{\n(?P<body>.*?)^\s*\}",
        bootstrap,
    )
    assert root_location
    assert "return 503;" in root_location.group("body")
    assert 'add_header Cache-Control "no-store" always;' in root_location.group(
        "body"
    )


def test_deploy_preserves_existing_operator_template_and_creates_missing_one(
    tmp_path,
):
    deploy = _read(DEPLOY)
    guarded_render = re.search(
        r'(?ms)^if \[\[ ! -f "\$HY_DIR/template\.yaml" \]\]; then\n'
        r'.*?^fi$',
        deploy,
    )
    assert guarded_render
    assert deploy.count(
        'render "$REPO_DIR/hysteria/clash-default.yaml.tpl" '
        '"$HY_DIR/template.yaml"'
    ) == 1

    harness = tmp_path / "template-harness.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
HY_DIR="$1"
REPO_DIR="$2"
log() { :; }
render() { printf 'generated-template\\n' > "$2"; }
"""
        + guarded_render.group(0)
        + "\n",
        encoding="utf-8",
    )

    hy_dir = tmp_path / "runtime"
    hy_dir.mkdir()
    template = hy_dir / "template.yaml"
    template.write_text("operator-managed\n", encoding="utf-8")

    subprocess.run(
        ["bash", str(harness), str(hy_dir), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert template.read_text(encoding="utf-8") == "operator-managed\n"

    template.unlink()
    subprocess.run(
        ["bash", str(harness), str(hy_dir), str(ROOT)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert template.read_text(encoding="utf-8") == "generated-template\n"


def test_deploy_preserves_runtime_xray_config_and_exactly_reconciles_clients():
    deploy = _read(DEPLOY)

    assert (
        'render "$REPO_DIR/xray/config.json.tpl" "$XRAY_ETC/config.json"'
        not in deploy
    )
    assert 'render "$REPO_DIR/xray/config.json.tpl" "$XRAY_CANDIDATE"' in deploy
    assert "xray_config.initialize_from_file(" in deploy
    assert "with state_store.file_lock(usage_lock):" in deploy
    assert "tuic_config.sync_user_plan(users, access_plan)" in deploy
    assert "traffic_limiter.build_static_access_plan(" in deploy
    assert "prune_unknown=True" in deploy
    assert "state_store.load_json_strict(path, default, required=True)" in deploy

    stop_subscription = deploy.index(
        'systemctl stop "$unit"',
        deploy.index('# Quiesce every critical reader/writer'),
    )
    render_sources = deploy.index(
        'render "$REPO_DIR/hysteria/subscription_service.py"'
    )
    create_group = deploy.index("groupadd --system hy2-xray")
    initialize = deploy.index("xray_config.initialize_from_file(")
    validate_live = deploy.index(
        'xray run -test -c "$XRAY_ETC/config.json"'
    )
    restart_live = deploy.index("systemctl restart xray.service")

    assert stop_subscription < render_sources
    assert create_group < initialize
    assert initialize < validate_live < restart_live


def test_new_install_has_no_unmanaged_bootstrap_proxy_client():
    deploy = _read(DEPLOY)
    env_example = _read(ROOT / ".env.example")
    template = _read(ROOT / "xray" / "config.json.tpl")

    assert "XRAY_CLIENT_UUID" not in deploy
    assert "XRAY_CLIENT_UUID" not in env_example
    assert "HY_CERTBOT_EMAIL=''" in env_example
    assert "__XRAY_CLIENT_UUID__" not in template
    assert template.count('"clients": []') == 2


def test_fresh_install_stages_xray_without_upstream_installer_or_early_start():
    deploy = _read(DEPLOY)

    archive_download = deploy.index(
        "https://github.com/XTLS/Xray-core/releases/download/"
    )
    strict_extract = deploy.index("scripts/hy2-extract-xray.py", archive_download)
    stage = deploy.index(
        'install -m 755 "$XRAY_EXTRACT_DIR/xray" "$XRAY_CANDIDATE"',
        strict_extract,
    )
    quiesce = deploy.index('for unit in "${CRITICAL_UNITS[@]}"; do', stage)
    commit = deploy.index(
        'durable_replace_candidate "$XRAY_CANDIDATE" /usr/local/bin/xray',
        quiesce,
    )
    initialize = deploy.index("xray_config.initialize_from_file(")

    assert archive_download < strict_extract < stage < quiesce
    assert quiesce < commit < initialize
    assert 'install -d -m 755 "$XRAY_ETC"' not in deploy
    assert "Xray-install" not in deploy
    assert "systemctl start xray" not in deploy[:quiesce]


def test_xray_archive_and_every_runtime_file_are_repository_pinned():
    deploy = _read(DEPLOY)

    download = deploy.index(
        "https://github.com/XTLS/Xray-core/releases/download/"
    )
    archive_verify = deploy.index(
        'printf \'%s  %s\\n\' "$xray_archive_sha256" "$XRAY_ARCHIVE"',
        download,
    )
    strict_extract = deploy.index(
        "/usr/bin/python3 -I \"$REPO_DIR/scripts/hy2-extract-xray.py\"",
        archive_verify,
    )
    binary_verify = deploy.index(
        'printf \'%s  %s\\n\' "$xray_binary_sha256" '
        '"$XRAY_EXTRACT_DIR/xray"',
        strict_extract,
    )
    geoip_verify = deploy.index(
        'printf \'%s  %s\\n\' "$XRAY_GEOIP_SHA256" '
        '"$XRAY_EXTRACT_DIR/geoip.dat"',
        binary_verify,
    )
    geosite_verify = deploy.index(
        'printf \'%s  %s\\n\' "$XRAY_GEOSITE_SHA256" '
        '"$XRAY_EXTRACT_DIR/geosite.dat"',
        geoip_verify,
    )
    commit = deploy.index(
        'durable_replace_candidate "$XRAY_CANDIDATE" /usr/local/bin/xray',
        geosite_verify,
    )
    assert download < archive_verify < strict_extract
    assert strict_extract < binary_verify < geoip_verify < geosite_verify
    assert geosite_verify < commit
    assert "Xray-install" not in deploy
    assert "xray version" not in deploy
    assert "xray -version" not in deploy
    assert (
        "XRAY_AMD64_BINARY_SHA256="
        "8ef87ac07f95617e094b8e9302ea3e0c2d0edaa7045d57b455fdee28b3c9e41e"
        in deploy
    )
    assert (
        "XRAY_GEOIP_SHA256="
        "e551b66e9300a98ecc94a5dc8c86a3973bf7033138b0fa61eb0638419ce50057"
        in deploy
    )
    assert (
        "XRAY_GEOSITE_SHA256="
        "1417d29aa40e07fa3cd92e730e8d81921a78b8e573849ca2a4b8199c7c1d3b2b"
        in deploy
    )
    assert '"$installed_geoip_metadata" == "0:0:644:1"' in deploy
    assert '"$installed_geosite_metadata" == "0:0:644:1"' in deploy
    assert (
        "XRAY_ARM64_BINARY_SHA256="
        "53ad04b1ddcba6f4ff8834b3db2e9a596456441259cea3e4f03f86cd39e22884"
        in deploy
    )


def test_xray_migration_preserves_legacy_logs_and_rejects_instances():
    deploy = _read(DEPLOY)
    template = _read(ROOT / "xray" / "config.json.tpl")
    logrotate = _read(ROOT / "logrotate" / "xray")

    assert '"$uid" == "0" && "$gid" == "0" && "$mode" == "755"' in deploy
    assert "XRAY_LOG_DIR_PREVIOUS_UID" in deploy
    assert "restore_xray_log_directory_state" in deploy
    assert (
        "track_created_artifact_for_rollback "
        "/var/log/xray/hy2-access.log"
    ) in deploy
    assert 'install -d -o root -g hy2-xray -m 750 /var/log/xray' in deploy
    assert "hy2-access.log" in template
    assert "hy2-error.log" in template
    assert "/var/log/xray/*.log" in logrotate
    assert "chown hy2-xray:hy2-xray /var/log/xray" not in deploy

    reject = deploy.index("Unsupported Xray instance units are configured")
    snapshot = deploy.index("capture_service_state", reject)
    assert reject < snapshot
    assert "'xray@*.service'" in deploy
    assert not (ROOT / "systemd" / "xray@.service").exists()
    managed_units = re.search(
        r"(?ms)^declare -a DEPLOY_MANAGED_UNITS=\(\n(?P<body>.*?)^\)$",
        deploy,
    )
    assert managed_units
    assert "xray.service" in managed_units.group("body")
    assert "xray@.service" not in managed_units.group("body")
    assert (
        '"$SYSTEMD_DIR/xray.service.d/10-donot_touch_multi_conf.conf"'
        in deploy
    )
    assert '"$SYSTEMD_DIR/xray@.service"' in deploy


def test_repository_owned_xray_unit_is_single_instance_and_sandboxed():
    unit = _read(ROOT / "systemd" / "xray.service")
    hardening = _read(
        ROOT / "systemd" / "xray.service.d" / "20-hy2-hardening.conf"
    )

    assert "User=hy2-xray" in unit
    assert "Group=hy2-xray" in unit
    assert "CapabilityBoundingSet=CAP_NET_BIND_SERVICE" in unit
    assert (
        "ExecStart=/usr/local/bin/xray run -config "
        "/usr/local/etc/xray/config.json"
    ) in unit
    for directive in (
        "NoNewPrivileges=true",
        "ProtectSystem=strict",
        "ProtectHome=true",
        "PrivateDevices=true",
        "ProtectKernelLogs=true",
        "ProtectProc=invisible",
        "RestrictNamespaces=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "ReadWritePaths=/var/log/xray",
    ):
        assert directive in hardening
    assert not (ROOT / "systemd" / "xray@.service").exists()


def test_exact_access_plan_is_committed_before_first_proxy_start():
    deploy = _read(DEPLOY)

    build_plan = deploy.index("traffic_limiter.build_static_access_plan(")
    prune_xray = deploy.index("prune_unknown=True", build_plan)
    sync_tuic = deploy.index(
        "tuic_config.sync_user_plan(users, access_plan)", prune_xray
    )
    start_xray = deploy.index("systemctl enable --now xray.service")
    start_tuic = deploy.index("systemctl enable --now tuic-server.service")

    assert build_plan < prune_xray < sync_tuic < start_xray
    assert sync_tuic < start_tuic


def test_success_gate_requires_active_services_and_panel_readiness():
    deploy = _read(DEPLOY)

    active_check = deploy.index('systemctl is-active --quiet "$unit"')
    readiness = deploy.index(
        "http://127.0.0.1:8081/healthz", active_check
    )
    final_status_check = deploy.index(
        '[[ "$unit_state" == "active" ]]', readiness
    )
    success = deploy.index("DEPLOY_SUCCEEDED=1", final_status_check)

    assert active_check < readiness < final_status_check < success


def test_deploy_restores_critical_services_after_a_late_failure():
    deploy = _read(DEPLOY)

    assert "trap restore_services_on_failure EXIT" in deploy
    assert 'systemctl disable --now hysteria-server.service' not in deploy
    assert 'for unit in "${CRITICAL_UNITS[@]}"; do' in deploy
    assert 'systemctl stop "$unit"' in deploy
    assert 'unit_state="$(systemctl is-active "$unit"' in deploy
    assert 'PREVIOUSLY_ACTIVE_UNITS+=("$unit")' in deploy
    assert 'systemctl start "$unit"' in deploy
    assert "restore_unit_enable_state" in deploy
    assert "restore_artifacts_on_failure" in deploy
    assert "DEPLOY_SUCCEEDED=1" in deploy

    install_tuic = deploy.index(
        "TUIC server $TUIC_VERSION checksum already matches"
    )
    quiesce = deploy.index(
        'for unit in "${CRITICAL_UNITS[@]}"; do',
        install_tuic,
    )
    render_sources = deploy.index(
        'render "$REPO_DIR/hysteria/subscription_service.py"'
    )
    success = deploy.index("DEPLOY_SUCCEEDED=1", quiesce)
    assert install_tuic < quiesce < render_sources < success


def test_deploy_snapshots_latest_runtime_state_before_first_runtime_write():
    deploy = _read(DEPLOY)

    quiesced = deploy.index(
        'die "Could not authoritatively quiesce $unit '
    )
    late_snapshot = deploy.index(
        "# Snapshot mutable application state only after every critical writer"
    )
    first_runtime_write = deploy.index('log "Writing $HY_DIR/api_secret"')
    restore = deploy.index("restore_artifacts_on_failure")
    daemon_reload = deploy.index(
        "systemctl daemon-reload", restore
    )

    assert quiesced < late_snapshot < first_runtime_write
    assert restore < daemon_reload
    assert "ROLLBACK_MAX_FILE_BYTES=$((64 * 1024 * 1024))" in deploy
    assert "ROLLBACK_MAX_TOTAL_BYTES=$((256 * 1024 * 1024))" in deploy
    assert "mktemp -d /root/.hy2-deploy-rollback.XXXXXX" in deploy
    assert 'chmod 700 "$ROLLBACK_DIR"' in deploy
    assert "HY2_DEPLOY_LOCK_MARKER" in deploy
    assert "--lock-file \"$DEPLOY_LOCK\"" in deploy
    assert "--lock-file \"$HTTPS_ACTIVATION_LOCK\"" in deploy
    assert "flock -n 9" not in deploy
    assert "trap 'exit 130' INT" in deploy
    assert "trap 'exit 143' TERM" in deploy
    assert 'chmod 700 "$HY_DIR"/*.py' not in deploy
    assert "chown -R hy2-xray:hy2-xray /var/log/xray" not in deploy

    for artifact in (
        "$HY_DIR/users.json",
        "$HY_DIR/subscription_meta.json",
        "$HY_DIR/state/usage.json",
        "$HY_DIR/state/usage_daily.json",
        "$HY_DIR/state/auto_reset_state.json",
        "$HY_DIR/admin_initial_password.txt",
        "$HY_DIR/revocation_queue.py",
        "$HY_DIR/rotation_recovery.py",
        "$HY_DIR/static_access.py",
        "$HY_DIR/tuic.json",
        "$XRAY_ETC/config.json",
        "$SYSTEMD_DIR/hysteria-server.service",
        "/etc/nginx/sites-available/hysteria-panel.conf",
    ):
        needle = f'"{artifact}"' if artifact.startswith("$") else artifact
        assert needle in deploy
    for module in ("revocation_queue.py", "rotation_recovery.py"):
        assert (
            f'render "$REPO_DIR/hysteria/{module}"'
            in deploy
        )
        assert f'"$HY_DIR/{module}"' in deploy
    assert "Rendered credential-recovery modules failed Python validation" in deploy


def test_artifact_snapshot_round_trip_restores_files_symlinks_and_absence(
    tmp_path,
):
    deploy = _read(DEPLOY)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(mode=0o700)
    existing = tmp_path / "existing.conf"
    existing.write_text("operator-change\n", encoding="utf-8")
    existing.chmod(0o640)
    missing = tmp_path / "fresh.conf"
    link_target = tmp_path / "operator-target"
    link_target.write_text("target\n", encoding="utf-8")
    link = tmp_path / "managed.link"
    link.symlink_to(link_target)

    harness = tmp_path / "artifact-rollback-harness.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
ROLLBACK_DIR="$1"
existing="$2"
missing="$3"
link="$4"
ROLLBACK_TOTAL_BYTES=0
ROLLBACK_MAX_FILE_BYTES=$((64 * 1024 * 1024))
ROLLBACK_MAX_TOTAL_BYTES=$((256 * 1024 * 1024))
declare -a ROLLBACK_PATHS=()
declare -a ROLLBACK_EXISTED=()
die() { printf '%s\\n' "$*" >&2; exit 1; }
warn() { printf '%s\\n' "$*" >&2; }
"""
        + _extract_function(deploy, "capture_artifact_snapshot")
        + "\n"
        + _extract_function(deploy, "restore_artifacts_on_failure")
        + """
capture_artifact_snapshot "$existing"
capture_artifact_snapshot "$missing"
capture_artifact_snapshot "$link"
printf 'deployed\\n' > "$existing"
chmod 600 "$existing"
printf 'new install\\n' > "$missing"
rm -f -- "$link"
ln -s /tmp/deployed-target "$link"
restore_artifacts_on_failure
""",
        encoding="utf-8",
    )

    subprocess.run(
        [
            "bash",
            str(harness),
            str(snapshot_dir),
            str(existing),
            str(missing),
            str(link),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert existing.read_text(encoding="utf-8") == "operator-change\n"
    assert existing.stat().st_mode & 0o777 == 0o640
    assert not missing.exists()
    assert link.is_symlink()
    assert os.readlink(link) == str(link_target)


def test_artifact_snapshot_rejects_unbounded_file(tmp_path):
    deploy = _read(DEPLOY)
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir(mode=0o700)
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"12345")
    harness = tmp_path / "artifact-bound-harness.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
ROLLBACK_DIR="$1"
ROLLBACK_TOTAL_BYTES=0
ROLLBACK_MAX_FILE_BYTES=4
ROLLBACK_MAX_TOTAL_BYTES=8
declare -a ROLLBACK_PATHS=()
declare -a ROLLBACK_EXISTED=()
die() { printf '%s\\n' "$*" >&2; exit 1; }
"""
        + _extract_function(deploy, "capture_artifact_snapshot")
        + '\ncapture_artifact_snapshot "$2"\n',
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(harness), str(snapshot_dir), str(oversized)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "exceeds the 64 MiB rollback limit" in result.stderr


def test_deploy_failure_trap_restores_prior_active_set(tmp_path):
    deploy = _read(DEPLOY)
    candidate = tmp_path / "candidate.json"
    candidate.write_text("temporary", encoding="utf-8")
    log_file = tmp_path / "systemctl.log"
    harness = tmp_path / "recovery-harness.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -uo pipefail
DEPLOY_SUCCEEDED=0
SERVICE_STATE_CAPTURED=1
ROLLBACK_ACTIVE=1
ROLLBACK_DIR=""
XRAY_CANDIDATE="$1"
HYSTERIA_CANDIDATE=""
TUIC_CANDIDATE=""
TUIC_DOWNLOAD=""
SYSCTL_STATE_CAPTURED=0
XRAY_LOG_DIR_STATE_CAPTURED=0
XRAY_DATA_DIR_CREATED=0
XRAY_CONFIG_DIR_CREATED=0
XRAY_ETC="$1/xray-config"
LOG_FILE="$2"
DEPLOY_MANAGED_UNITS=(
  active.service
  inactive.service
  runtime-enabled.service
  runtime-masked.service
  disabled.service
  static.service
)
PREVIOUSLY_ACTIVE_UNITS=(active.service)
declare -a ROLLBACK_PATHS=()
declare -a ROLLBACK_EXISTED=()
declare -a HY2_SYSCTL_KEYS=()
declare -A PREVIOUS_SYSCTL_VALUES=()
declare -A PREVIOUS_ENABLE_STATE=(
  [active.service]=enabled
  [inactive.service]=masked
  [runtime-enabled.service]=enabled-runtime
  [runtime-masked.service]=masked-runtime
  [disabled.service]=disabled
  [static.service]=static
)
warn() { :; }
systemctl() { printf '%s\\n' "$*" >> "$LOG_FILE"; }
"""
        + _extract_function(deploy, "was_previously_active")
        + "\n"
        + _extract_function(deploy, "is_deferred_rollback_unit")
        + "\n"
        + _extract_function(deploy, "restore_sysctl_state")
        + "\n"
        + _extract_function(deploy, "restore_unit_enable_state")
        + "\n"
        + _extract_function(deploy, "restore_artifacts_on_failure")
        + "\n"
        + _extract_function(deploy, "restore_created_xray_directories")
        + "\n"
        + _extract_function(deploy, "restore_xray_log_directory_state")
        + "\n"
        + _extract_function(deploy, "cleanup_rollback_snapshot")
        + "\n"
        + _extract_function(deploy, "restore_services_on_failure")
        + "\ntrap restore_services_on_failure EXIT\nfalse\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(harness), str(candidate), str(log_file)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert not candidate.exists()
    assert log_file.read_text(encoding="utf-8").splitlines() == [
        "stop active.service",
        "stop inactive.service",
        "stop runtime-enabled.service",
        "stop runtime-masked.service",
        "stop disabled.service",
        "stop static.service",
        "daemon-reload",
        "disable active.service",
        "unmask active.service",
        "enable active.service",
        "disable inactive.service",
        "unmask inactive.service",
        "mask inactive.service",
        "disable runtime-enabled.service",
        "unmask runtime-enabled.service",
        "enable --runtime runtime-enabled.service",
        "disable runtime-masked.service",
        "unmask runtime-masked.service",
        "mask --runtime runtime-masked.service",
        "disable disabled.service",
        "unmask disabled.service",
        "start active.service",
    ]


def test_incomplete_core_rollback_preserves_the_only_recovery_snapshot(
    tmp_path,
):
    deploy = _read(DEPLOY)
    rollback_dir = tmp_path / "rollback"
    rollback_dir.mkdir(mode=0o700)
    cleanup_marker = tmp_path / "cleanup-called"
    harness = tmp_path / "incomplete-rollback.sh"
    harness.write_text(
        """#!/usr/bin/env bash
set -uo pipefail
DEPLOY_SUCCEEDED=0
SERVICE_STATE_CAPTURED=0
ROLLBACK_ACTIVE=1
ROLLBACK_DIR="$1"
CLEANUP_MARKER="$2"
XRAY_CANDIDATE=""
HYSTERIA_CANDIDATE=""
TUIC_CANDIDATE=""
TUIC_DOWNLOAD=""
XRAY_LOG_DIR_STATE_CAPTURED=0
DEPLOY_MANAGED_UNITS=()
PREVIOUSLY_ACTIVE_UNITS=()
warn() { printf '%s\\n' "$*" >&2; }
restore_artifacts_on_failure() { return 1; }
restore_created_xray_directories() { return 0; }
restore_xray_log_directory_state() { return 0; }
restore_sysctl_state() { return 0; }
cleanup_rollback_snapshot() {
  : > "$CLEANUP_MARKER"
  rm -rf -- "$ROLLBACK_DIR"
}
"""
        + _extract_function(deploy, "restore_services_on_failure")
        + "\ntrap restore_services_on_failure EXIT\nfalse\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            "bash",
            str(harness),
            str(rollback_dir),
            str(cleanup_marker),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert rollback_dir.is_dir()
    assert not cleanup_marker.exists()
    assert "preserving the root-only recovery snapshot" in result.stderr


def test_deploy_rolls_back_support_services_and_live_sysctls():
    deploy = _read(DEPLOY)

    managed = re.search(
        r"(?ms)^declare -a DEPLOY_MANAGED_UNITS=\(\n(?P<body>.*?)^\)",
        deploy,
    )
    assert managed
    assert "fail2ban.service" in managed.group("body")
    assert "systemd-journald.service" in managed.group("body")
    assert '[[ "$1" == "systemd-journald.service" ]]' in _extract_function(
        deploy,
        "is_deferred_rollback_unit",
    )

    sysctl_write = deploy.index(
        "write_atomic_from_stdin 644 /etc/sysctl.d/99-hysteria-udp.conf"
    )
    capture_live = deploy.index("capture_sysctl_state", sysctl_write)
    apply_live = deploy.index("sysctl --system", capture_live)
    rollback_artifacts = deploy.index(
        "restore_artifacts_on_failure",
        deploy.index("restore_services_on_failure"),
    )
    rollback_live = deploy.index("restore_sysctl_state", rollback_artifacts)
    assert sysctl_write < capture_live < apply_live
    assert rollback_artifacts < rollback_live

    validate_fail2ban = deploy.index("fail2ban-client -t")
    restart_journal = deploy.index(
        "systemctl restart systemd-journald.service"
    )
    restart_fail2ban = deploy.index("systemctl restart fail2ban.service")
    assert validate_fail2ban < restart_journal < restart_fail2ban


def test_https_deploy_preserves_live_vhosts_and_http_mode_removes_tls_link():
    deploy = _read(DEPLOY)

    preserve_branch = re.search(
        r'(?ms)^if \[\[ "\$HY_ENABLE_HTTPS" == "1" &&\n'
        r'.*?^elif \[\[ "\$HY_ENABLE_HTTPS" == "1" \]\]; then',
        deploy,
    )
    assert preserve_branch
    assert "render " not in preserve_branch.group(0)
    assert "Preserving the active HTTPS and redirect vhosts" in deploy

    nginx_start = deploy.index(
        'log "Installing nginx site for hysteria-panel..."'
    )
    nginx_end = deploy.index("symlink_atomic", nginx_start)
    http_branch = re.search(
        r'(?ms)^else\n(?P<body>.*?)^fi$',
        deploy[nginx_start:nginx_end],
    )
    assert http_branch
    assert (
        "durable_remove_artifact \\\n"
        "    /etc/nginx/sites-enabled/hysteria-panel-https.conf"
        in http_branch.group("body")
    )
    assert (
        'render "$REPO_DIR/nginx/hysteria-panel.conf" '
        "/etc/nginx/sites-available/hysteria-panel.conf"
        in http_branch.group("body")
    )


def test_https_helper_has_one_owned_transaction_with_atomic_restore():
    helper = _read(HTTPS_HELPER)
    transaction = re.search(
        r"(?ms)^declare -a artifact_paths=\(\n"
        r"(?P<body>.*?)^\)$",
        helper,
    )
    assert transaction
    owned = transaction.group("body")
    for artifact in (
        '"$log_conf"',
        '"$panel_conf"',
        '"$tls_conf"',
        '"$tls_link"',
        '"$renew_hook"',
    ):
        assert artifact in owned

    # The ordinary HTTP-site link remains deploy.sh-owned and must not be
    # rewritten by the HTTPS helper.
    assert "sites-enabled/hysteria-panel.conf" not in helper

    commit = _extract_function(helper, "commit_replacement")
    restore = _extract_function(helper, "restore_manifest_artifacts")
    assert 'manifest_action before "$destination"' in commit
    assert "mv -Tf" in commit
    assert 'committed_paths+=("$destination")' in commit
    assert "fsync_parent" in commit
    assert 'manifest_action after "$destination"' in commit
    assert "payload.get(\"committed\")" in restore
    assert "payload.get(\"pending_commit\")" in restore
    assert "os.replace(" in restore
    assert "os.fsync" in restore

    # Expand TLS first and contract plaintext to a redirect only after the
    # enabled TLS link has validated and reloaded.
    tls_commit = helper.index(
        'commit_replacement "$tls_candidate" "$tls_conf" tls'
    )
    hook_commit = helper.index(
        'commit_replacement "$hook_candidate" "$renew_hook" hook'
    )
    link_commit = helper.index(
        'commit_replacement "$link_candidate" "$tls_link" link'
    )
    panel_commit = helper.index(
        'commit_replacement "$panel_candidate" "$panel_conf" panel'
    )
    assert tls_commit < hook_commit < link_commit < panel_commit

    on_exit = _extract_function(helper, "on_exit")
    assert "rollback_nginx_config" in on_exit
    for signal in ("HUP", "INT", "TERM"):
        assert f"trap 'on_signal {signal} " in helper
