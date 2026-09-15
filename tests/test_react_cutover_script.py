"""Contracts for the explicitly approved React nginx cutover helper."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/hy2-react-cutover.sh'
DEPLOY = ROOT / 'deploy.sh'
RECOVERY = ROOT / 'scripts/hy2-deploy-recovery.py'


def test_cutover_requires_explicit_approval_and_supports_rollback():
    script = SCRIPT.read_text(encoding='utf-8')

    assert script.startswith('#!/bin/bash -p')
    assert 'HY_REACT_CUTOVER_APPROVED' in script
    assert 'HY_REACT_CUTOVER_APPROVED:-0' in script
    assert 'apply|rollback|status' in script
    assert '"$nginx_bin" -t' in script
    assert 'reload nginx.service' in script
    assert 'rollback_cutover' in script


def test_cutover_is_scoped_to_panel_vhosts_and_preserves_443():
    script = SCRIPT.read_text(encoding='utf-8')

    assert 'hysteria-panel-react.conf' in script
    assert 'hysteria-panel-react-https.conf' in script
    assert 'sites-available/hysteria-panel.conf' in script
    assert 'sites-available/hysteria-panel-https.conf' in script
    assert 'listen 443' not in script
    assert '443.before' in script
    assert '443.after' in script
    assert 'cmp -s' in script


def test_cutover_is_packaged_and_recoverable():
    deploy = DEPLOY.read_text(encoding='utf-8')
    recovery = RECOVERY.read_text(encoding='utf-8')

    assert 'hy2-react-cutover.sh' in deploy
    assert '/usr/local/sbin/hy2-react-cutover.sh' in recovery
