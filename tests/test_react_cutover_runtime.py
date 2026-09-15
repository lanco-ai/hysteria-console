"""Exercise the React nginx cutover helper entirely inside a temp root."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/hy2-react-cutover.sh'


def _executable(path: Path, body: str) -> None:
    path.write_text(f'#!/bin/bash\nset -eu\n{body}', encoding='utf-8')
    path.chmod(0o755)


def _cutover_env(root: Path) -> dict[str, str]:
    nginx_root = root / 'etc/nginx'
    share_dir = root / 'share/hy2'
    backup_root = root / 'var/lib/hysteria/react-cutover'
    bin_dir = root / 'bin'
    for directory in (
        nginx_root / 'sites-available',
        nginx_root / 'sites-enabled',
        share_dir,
        bin_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    (nginx_root / 'sites-enabled/hysteria-panel.conf').symlink_to(
        nginx_root / 'sites-available/hysteria-panel.conf'
    )
    (nginx_root / 'sites-enabled/hysteria-panel-https.conf').symlink_to(
        nginx_root / 'sites-available/hysteria-panel-https.conf'
    )
    legacy_http = 'server {\n    listen 80;\n    server_name test.example;\n}\n'
    legacy_https = 'server {\n    listen 9444 ssl;\n    server_name test.example;\n}\n'
    (nginx_root / 'sites-available/hysteria-panel.conf').write_text(legacy_http, encoding='utf-8')
    (nginx_root / 'sites-available/hysteria-panel-https.conf').write_text(
        legacy_https, encoding='utf-8'
    )
    react_http = (
        'server {\n    listen 80;\n    server_name test.example;\n'
        '    location = / { proxy_pass http://127.0.0.1:8083; }\n}\n'
    )
    react_https = (
        'server {\n    listen 9444 ssl;\n    server_name test.example;\n'
        '    location = /admin { proxy_pass http://127.0.0.1:8083; }\n}\n'
    )
    (share_dir / 'hysteria-panel-react.conf').write_text(react_http, encoding='utf-8')
    (share_dir / 'hysteria-panel-react-https.conf').write_text(react_https, encoding='utf-8')
    _executable(
        bin_dir / 'nginx',
        "if [[ \"$1\" == '-T' ]]; then printf 'listen 443 ssl;\\nlisten 9444 ssl;\\n'; fi\n",
    )
    _executable(
        bin_dir / 'systemctl',
        'if [[ "$1" == \'is-active\' ]]; then exit 0; fi\n',
    )
    _executable(
        bin_dir / 'curl',
        "if [[ \"$*\" == *'-w'* ]]; then printf '200'; fi\n",
    )
    return {
        **os.environ,
        'HY2_REACT_CUTOVER_TEST_MODE': '1',
        'HY2_REACT_TEST_ROOT': str(root),
        'HY2_REACT_NGINX_ROOT': str(nginx_root),
        'HY2_REACT_SHARE_DIR': str(share_dir),
        'HY2_REACT_BACKUP_ROOT': str(backup_root),
        'HY2_REACT_NGINX_BIN': str(bin_dir / 'nginx'),
        'HY2_REACT_SYSTEMCTL_BIN': str(bin_dir / 'systemctl'),
        'HY2_REACT_CURL_BIN': str(bin_dir / 'curl'),
        'HY_REACT_CUTOVER_APPROVED': '1',
        'HY_HTTPS_PORT': '9444',
    }


def test_apply_and_rollback_are_atomic_in_an_isolated_root(tmp_path):
    environment = _cutover_env(tmp_path)
    nginx_root = Path(environment['HY2_REACT_NGINX_ROOT'])
    backup_root = Path(environment['HY2_REACT_BACKUP_ROOT'])
    http_target = nginx_root / 'sites-available/hysteria-panel.conf'
    https_target = nginx_root / 'sites-available/hysteria-panel-https.conf'
    legacy_http = http_target.read_text(encoding='utf-8')
    legacy_https = https_target.read_text(encoding='utf-8')

    applied = subprocess.run(
        [str(SCRIPT), 'apply'], env=environment, capture_output=True, text=True, check=False
    )
    assert applied.returncode == 0, applied.stderr
    assert 'proxy_pass http://127.0.0.1:8083' in http_target.read_text(encoding='utf-8')
    assert 'proxy_pass http://127.0.0.1:8083' in https_target.read_text(encoding='utf-8')
    assert (backup_root / 'current').is_file()

    rolled_back = subprocess.run(
        [str(SCRIPT), 'rollback'], env=environment, capture_output=True, text=True, check=False
    )
    assert rolled_back.returncode == 0, rolled_back.stderr
    assert http_target.read_text(encoding='utf-8') == legacy_http
    assert https_target.read_text(encoding='utf-8') == legacy_https
    assert not (backup_root / 'current').exists()
