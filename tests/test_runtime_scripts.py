import os
import subprocess
import tarfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backup_excludes_live_admin_sessions(tmp_path):
    hy_dir = tmp_path / 'hysteria'
    state_dir = hy_dir / 'state'
    state_dir.mkdir(parents=True)
    (hy_dir / 'users.json').write_text('{}')
    (state_dir / 'usage.json').write_text('{}')
    (state_dir / 'panel_sessions.json').write_text('{"sid":{"exp":9999999999}}')

    env = os.environ.copy()
    env['HY2_HY_DIR'] = str(hy_dir)
    env['HY2_BACKUP_DIR'] = str(tmp_path / 'backups')
    env['HY2_XRAY_CONFIG'] = str(tmp_path / 'missing-xray.json')
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-backup.sh')],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    archive = result.stdout.strip()
    with tarfile.open(archive, 'r:gz') as tf:
        names = tf.getnames()

    assert any(name.endswith('/users.json') for name in names)
    assert any(name.endswith('/state/usage.json') for name in names)
    assert not any(name.endswith('/state/panel_sessions.json') for name in names)


def test_restore_check_accepts_plain_backup_archive(tmp_path):
    hy_dir = tmp_path / 'hysteria'
    state_dir = hy_dir / 'state'
    state_dir.mkdir(parents=True)
    (hy_dir / 'users.json').write_text('{}')
    (hy_dir / 'subscription_meta.json').write_text('{}')
    (hy_dir / 'template.yaml').write_text('proxies: []\nrules: []\n')
    (state_dir / 'usage.json').write_text('{}')

    env = os.environ.copy()
    env['HY2_HY_DIR'] = str(hy_dir)
    env['HY2_BACKUP_DIR'] = str(tmp_path / 'backups')
    env['HY2_XRAY_CONFIG'] = str(tmp_path / 'missing-xray.json')
    archive = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-backup.sh')],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()

    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-restore-check.sh'), archive],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert 'OK: hy2 backup dry-run passed' in result.stdout
    assert 'would_overwrite=' in result.stdout


def test_backup_encryption_and_restore_check(tmp_path):
    hy_dir = tmp_path / 'hysteria'
    hy_dir.mkdir(parents=True)
    (hy_dir / 'users.json').write_text('{}')
    (hy_dir / 'subscription_meta.json').write_text('{}')

    env = os.environ.copy()
    env['HY2_HY_DIR'] = str(hy_dir)
    env['HY2_BACKUP_DIR'] = str(tmp_path / 'backups')
    env['HY2_XRAY_CONFIG'] = str(tmp_path / 'missing-xray.json')
    env['HY2_BACKUP_PASSPHRASE'] = 'test-passphrase'
    archive = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-backup.sh')],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout.strip()

    assert archive.endswith('.tar.gz.enc')
    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-restore-check.sh'), archive],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    assert 'OK: hy2 backup dry-run passed' in result.stdout


def test_backup_script_has_retention_guard():
    script = (ROOT / 'scripts/hy2-backup.sh').read_text(encoding='utf-8')

    assert 'BACKUP_KEEP="${HY2_BACKUP_KEEP:-14}"' in script
    assert 'prune_old_backups "$BACKUP_KEEP"' in script
    assert "hy2-backup-*.tar.gz.enc" in script


def test_restore_check_rejects_invalid_json(tmp_path):
    root = tmp_path / 'payload'
    target = root / 'root/hysteria'
    target.mkdir(parents=True)
    (target / 'users.json').write_text('{bad json')
    archive = tmp_path / 'bad.tar.gz'
    with tarfile.open(archive, 'w:gz') as tf:
        tf.add(target / 'users.json', arcname='root/hysteria/users.json')

    result = subprocess.run(
        ['bash', str(ROOT / 'scripts/hy2-restore-check.sh'), str(archive)],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert 'invalid JSON' in result.stderr


def test_nginx_template_and_deploy_render_server_host():
    conf = (ROOT / 'nginx/hysteria-panel.conf').read_text(encoding='utf-8')
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'server_name __HY_SERVER_HOST__ _;' in conf
    assert 'render "$REPO_DIR/nginx/hysteria-panel.conf"' in deploy


def test_deploy_renders_display_multiplier():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')
    env_example = (ROOT / '.env.example').read_text(encoding='utf-8')

    assert 'HY_DISPLAY_MULTIPLIER="${HY_DISPLAY_MULTIPLIER:-2.28}"' in deploy
    assert '__HY_DISPLAY_MULTIPLIER__|${HY_DISPLAY_MULTIPLIER}' in deploy
    assert 'HY_DISPLAY_MULTIPLIER=2.28' in env_example


def test_deploy_installs_cost_calibrator_module():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hysteria/cost_calibrator.py' in deploy
    assert '$HY_DIR/cost_calibrator.py' in deploy


def test_deploy_installs_incident_console_module():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hysteria/incident_console.py' in deploy
    assert '$HY_DIR/incident_console.py' in deploy


def test_deploy_installs_health_widgets_module():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hysteria/health_widgets.py' in deploy
    assert '$HY_DIR/health_widgets.py' in deploy


def test_deploy_installs_usage_dashboard_module():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hysteria/usage_dashboard.py' in deploy
    assert '$HY_DIR/usage_dashboard.py' in deploy


def test_deploy_installs_subscription_profiles_module():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hysteria/subscription_profiles.py' in deploy
    assert '$HY_DIR/subscription_profiles.py' in deploy


def test_deploy_installs_tuic_meter_module_and_nftables():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert ' nftables ' in deploy
    assert 'hysteria/tuic_meter.py' in deploy
    assert '$HY_DIR/tuic_meter.py' in deploy


def test_deploy_installs_and_enables_backup_timer():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'systemd/hy2-backup.service' in deploy
    assert 'systemd/hy2-backup.timer' in deploy
    assert 'systemctl enable --now hy2-backup.timer' in deploy


def test_deploy_installs_xray_logrotate_config():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')
    config = (ROOT / 'logrotate/xray').read_text(encoding='utf-8')

    assert ' logrotate ' in deploy
    assert 'logrotate/xray' in deploy
    assert '/etc/logrotate.d/xray' in deploy
    assert '/var/log/xray/*.log' in config
    assert 'maxsize 20M' in config
    assert 'copytruncate' in config


def test_traffic_limiter_unit_can_access_nftables():
    unit = (ROOT / 'systemd/hysteria-traffic-limiter.service').read_text(encoding='utf-8')

    assert 'AmbientCapabilities=CAP_NET_ADMIN' in unit
    assert 'CapabilityBoundingSet=CAP_NET_ADMIN' in unit
    assert 'AF_NETLINK' in unit


def test_traffic_limiter_timer_runs_every_30_seconds():
    timer = (ROOT / 'systemd/hysteria-traffic-limiter.timer').read_text(encoding='utf-8')

    assert 'OnUnitActiveSec=30s' in timer
    assert 'AccuracySec=5s' in timer
    assert 'OnUnitActiveSec=15s' not in timer
    assert 'OnUnitActiveSec=5s' not in timer


def test_xray_template_uses_ipv4_outbound_strategy():
    cfg = (ROOT / 'xray/config.json.tpl').read_text(encoding='utf-8')

    assert '"domainStrategy": "UseIPv4"' in cfg


def test_deploy_installs_restore_check_script():
    deploy = (ROOT / 'deploy.sh').read_text(encoding='utf-8')

    assert 'hy2-restore-check.sh' in deploy
    assert '/usr/local/sbin/hy2-restore-check.sh' in deploy
