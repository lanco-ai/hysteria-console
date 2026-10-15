"""Fault injection at the actual installer transaction boundary (temporary paths only)."""

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import install_service as deploy
from hy2_panel_release import activate_release, install_release


def dist(path, value):
    (path / 'assets').mkdir(parents=True)
    (path / 'assets/app.js').write_text(value)
    (path / 'assets/app.css').write_text('body{}')
    (path / 'index.html').write_text('<html></html>')
    (path / 'manifest.json').write_text(
        json.dumps(
            {'index.html': {'file': 'assets/app.js', 'isEntry': True, 'css': ['assets/app.css']}}
        )
    )


@pytest.fixture
def installation(tmp_path, monkeypatch):
    service = 'shop' if (ROOT / 'server/shop_server.py').exists() else 'workbench'
    checkout, runtime = tmp_path / 'checkout', tmp_path / 'runtime'
    (checkout / 'server').mkdir(parents=True)
    (checkout / 'server/app.py').write_text('VERSION = "new"\n')
    (checkout / 'deploy').mkdir()
    unit = f'lancoai-{service}.service'
    (checkout / 'deploy' / unit).write_text('new unit')
    (checkout / f'deploy/{service}.env.example').write_text(
        'LANCOAI_STANDBY=1\n'
        + ('SHOP_SCHEDULER=0\n' if service == 'shop' else 'WORKBENCH_SCHEDULERS=0\n')
    )
    runtime.mkdir()
    (runtime / 'app.py').write_text('VERSION = "old"\n')
    (runtime / 'removed.py').write_text('old companion')
    dist(tmp_path / 'old-dist', 'old frontend')
    old_id = install_release(tmp_path / 'old-dist', runtime / 'panel').release_id
    activate_release(runtime / 'panel', old_id)
    dist(checkout / 'frontend/dist', 'new frontend')
    config = tmp_path / 'config.env'
    scheduler = 'SHOP_SCHEDULER' if service == 'shop' else 'WORKBENCH_SCHEDULERS'
    config.write_text(f'{scheduler}=0\nLANCOAI_STANDBY=1\nPRIVATE=never-print-this\n')
    config.chmod(0o640)
    units = tmp_path / 'units'
    units.mkdir()
    (units / unit).write_text('old unit')
    token = tmp_path / 'token'
    token.write_text('old-secret')
    monkeypatch.setattr(deploy, '__file__', str(checkout / 'scripts/install_service.py'))
    monkeypatch.setattr(deploy, 'SYSTEMD_DIR', units)
    monkeypatch.setattr(deploy, 'TOKEN_FILE', token)
    monkeypatch.setattr(deploy, 'STATE_ROOT', tmp_path / 'state')
    monkeypatch.setattr(deploy.grp, 'getgrnam', lambda _: SimpleNamespace(gr_gid=os.getgid()))
    monkeypatch.setattr(deploy.os, 'chown', lambda *args, **kwargs: None)
    monkeypatch.setattr(deploy.time, 'sleep', lambda _: None)
    monkeypatch.setenv(f'LANCOAI_{service.upper()}_ROOT', str(runtime))
    monkeypatch.setenv(f'LANCOAI_{service.upper()}_ENV', str(config))
    monkeypatch.setenv('LANCOAI_VENV_PYTHON', sys.executable)
    control = {'active': True, 'enabled': True, 'failure': None, 'calls': []}

    def command(*args, check=True):
        control['calls'].append(args)
        rc, output = 0, ''
        new = (runtime / 'app.py').exists() and 'new' in (runtime / 'app.py').read_text()
        if args[0] == 'curl':
            rc = 22 if new and control['failure'] == 'health' else 0
        elif args[1] == 'is-active':
            rc = 0 if control['active'] else 3
        elif args[1] == 'is-enabled':
            output = 'enabled' if control['enabled'] else 'disabled'
        elif args[1] == 'show':
            output = (
                'ActiveState=active\nSubState=running\nMainPID=42\nControlPID=0\n'
                if control['active']
                else 'ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0\n'
            )
        elif args[1] == 'stop':
            if new and control['failure'] == 'rollback-stop':
                rc = 1
            else:
                control['active'] = False
        elif args[1] == 'start':
            control['active'] = True
            if new and control['failure'] in ('start', 'rollback-stop'):
                rc = 1
        elif args[1] in ('enable', 'disable'):
            control['enabled'] = args[1] == 'enable'
        elif args[1] != 'daemon-reload':
            raise AssertionError(args)
        if check and rc:
            raise deploy.InstallError('injected command failure')
        return SimpleNamespace(returncode=rc, stdout=output)

    monkeypatch.setattr(deploy, 'command', command)
    return SimpleNamespace(
        service=service,
        checkout=checkout,
        runtime=runtime,
        config=config,
        units=units,
        unit=unit,
        token=token,
        control=control,
        old_id=old_id,
    )


def assert_old(i):
    assert (i.runtime / 'app.py').read_text() == 'VERSION = "old"\n'
    assert (i.runtime / 'removed.py').read_text() == 'old companion'
    assert os.readlink(i.runtime / 'panel/current') == 'releases/' + i.old_id
    assert (i.units / i.unit).read_text() == 'old unit'
    assert 'LANCOAI_STANDBY=1' in i.config.read_text()
    assert i.config.stat().st_mode & 0o777 == 0o640
    assert i.token.read_text() == 'old-secret'
    assert i.control['active'] and i.control['enabled']


@pytest.mark.parametrize('failure', ['manifest', 'copy', 'start', 'health'])
def test_failed_install_restores_entire_previous_release(installation, monkeypatch, failure):
    i = installation
    i.control['failure'] = failure
    if failure == 'manifest':
        (i.checkout / 'frontend/dist/manifest.json').write_text('{broken')
    if failure == 'copy':
        original = deploy.shutil.copyfile

        def copy(source, dest, **kwargs):
            if Path(source) == i.checkout / 'server/app.py':
                raise OSError('injected disk error')
            return original(source, dest, **kwargs)

        monkeypatch.setattr(deploy.shutil, 'copyfile', copy)
    with pytest.raises(Exception):
        deploy.install(i.service, enable=True)
    assert_old(i)
    if failure in ('manifest', 'copy'):
        assert not any(call[1] in ('stop', 'start') for call in i.control['calls'])
    assert not list(i.runtime.parent.glob('.*-install-*'))


def test_success_switches_backend_frontend_and_flags_together(installation):
    i = installation
    deploy.install(i.service, enable=True)
    assert 'new' in (i.runtime / 'app.py').read_text()
    assert not (i.runtime / 'removed.py').exists()
    assert os.readlink(i.runtime / 'panel/current') != 'releases/' + i.old_id
    assert 'LANCOAI_STANDBY=0' in i.config.read_text()
    assert i.control['active'] and i.control['enabled']


def test_rollback_stop_failure_does_not_restart_old_process(installation):
    i = installation
    i.control['failure'] = 'rollback-stop'
    with pytest.raises(deploy.InstallError):
        deploy.install(i.service)
    starts = [call for call in i.control['calls'] if call[1] == 'start']
    assert len(starts) == 1
    assert list(i.runtime.parent.glob('.*-install-pending'))
    assert (next(i.runtime.parent.glob('.*-install-*/*previous*')) / 'app.py').exists()
    with pytest.raises(deploy.InstallError, match='unfinished'):
        deploy.install(i.service)


def test_failed_first_install_leaves_no_enabled_service(installation):
    import shutil

    i = installation
    shutil.rmtree(i.runtime)
    i.config.unlink()
    (i.units / i.unit).unlink()
    i.token.unlink()
    i.control.update(active=False, enabled=False, failure='health')
    with pytest.raises(deploy.InstallError):
        deploy.install(i.service)
    assert not i.runtime.exists() and not i.config.exists()
    assert not (i.units / i.unit).exists() and not i.token.exists()
    assert not i.control['active'] and not i.control['enabled']
