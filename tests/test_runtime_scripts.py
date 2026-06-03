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
