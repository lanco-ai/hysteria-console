"""Static contracts for the opt-in React deployment wiring.

The regular deployment must remain legacy-only.  Setting the explicit React
flag should install the paired ASGI runtime and verified asset release without
silently changing the production nginx route.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / 'deploy.sh'
RECOVERY = ROOT / 'scripts/hy2-deploy-recovery.py'
RENDERER = ROOT / 'scripts/hy2-render-template.py'
ENV_EXAMPLE = ROOT / '.env.example'
REQUIREMENTS_WEB = ROOT / 'requirements-web.txt'


WEB_API_MODULES = (
    '__init__.py',
    'account_models.py',
    'account_routes.py',
    'app.py',
    'chat_routes.py',
    'chat_service.py',
    'compat_routes.py',
    'config_models.py',
    'document_routes.py',
    'health_models.py',
    'health_routes.py',
    'incident_models.py',
    'landing_models.py',
    'landing_routes.py',
    'models.py',
    'operation_models.py',
    'operation_routes.py',
    'overview_models.py',
    'requests.py',
    'rules_routes.py',
    'services.py',
    'usage_models.py',
    'user_detail_models.py',
    'user_detail_routes.py',
    'user_models.py',
    'video_models.py',
    'video_provider.py',
    'video_routes.py',
    'video_service.py',
)


def test_react_deployment_is_explicitly_opt_in():
    deploy = DEPLOY.read_text(encoding='utf-8')
    renderer = RENDERER.read_text(encoding='utf-8')
    env_example = ENV_EXAMPLE.read_text(encoding='utf-8')

    assert 'HY_ENABLE_REACT_PANEL="${HY_ENABLE_REACT_PANEL:-0}"' in deploy
    assert 'HY_ENABLE_REACT_PANEL' in renderer
    assert 'HY_REACT_DIST_DIR' in renderer
    assert 'HY_ENABLE_REACT_PANEL=0' in env_example
    assert 'HY_REACT_DIST_DIR=frontend/dist' in env_example
    assert 'HY_REACT_DIST_DIR="$REPO_DIR/$HY_REACT_DIST_DIR"' in deploy
    assert '[[ "$HY_ENABLE_REACT_PANEL" == "1" ]]' in deploy


def test_react_runtime_includes_yaml_dependency_for_template_reads():
    requirements = REQUIREMENTS_WEB.read_text(encoding='utf-8')

    assert 'PyYAML==6.0.1' in requirements


def test_react_backend_sources_are_in_every_deploy_inventory():
    deploy = DEPLOY.read_text(encoding='utf-8')
    recovery = RECOVERY.read_text(encoding='utf-8')

    assert 'render "$REPO_DIR/hysteria/react_server.py" "$HY_DIR/react_server.py"' in deploy
    assert 'local dist="$HY_REACT_DIST_DIR"' in deploy
    assert 'install -d -o root -g root -m 755 "$HY_DIR/web_api"' in deploy
    for module in WEB_API_MODULES:
        assert f'hysteria/web_api/{module}' in deploy
        assert f'/root/hysteria/web_api/{module}' in recovery
    assert '/root/hysteria/react_server.py' in recovery
    assert '/etc/systemd/system/hysteria-react.service' in recovery
    assert 'hysteria-react.service' in deploy


def test_react_release_is_staged_and_activated_atomically():
    deploy = DEPLOY.read_text(encoding='utf-8')

    assert '/usr/bin/python3 -I "$REPO_DIR/scripts/hy2_panel_release.py"' in deploy
    assert '      validate "$dist"' in deploy
    assert '    install "$dist" "$panel_root"' in deploy
    assert 'REACT_RELEASE_ID' in deploy
    assert 'symlink_atomic "releases/$REACT_RELEASE_ID" "$HY_DIR/panel/current"' in deploy
    assert 'cleanup_staged_react_release' in deploy
    assert deploy.index('stage_react_release\ninstall_react_runtime') < deploy.index(
        '# Quiesce every critical reader/writer'
    )
    assert 'REACT_VENV_CREATED=1' in deploy
    assert "curl -fsS --noproxy '*' --max-time 3" in deploy
    assert 'http://127.0.0.1:8083/' in deploy
    assert 'HY_DIR/panel/current' in deploy


def test_react_flag_does_not_replace_legacy_nginx_by_default():
    deploy = DEPLOY.read_text(encoding='utf-8')

    legacy_render = 'render "$REPO_DIR/nginx/hysteria-panel.conf" /etc/nginx/sites-available/hysteria-panel.conf'
    assert legacy_render in deploy
    assert (
        'render_react_nginx_template "$REPO_DIR/nginx/hysteria-panel-react.conf" \\\n'
        '    /usr/local/share/hy2/hysteria-panel-react.conf'
    ) in deploy
    assert (
        'render_react_nginx_template "$REPO_DIR/nginx/hysteria-panel-react-https.conf" \\\n'
        '    /usr/local/share/hy2/hysteria-panel-react-https.conf'
    ) in deploy
    assert (
        'hysteria-panel-react-https.conf'
        not in deploy.split(
            '# ---------- 9. nginx reverse proxy for the admin panel ----------', 1
        )[1].split('# ---------- 10. Systemd units ----------', 1)[0]
    )
