"""Transactional installer shared by the shop and workbench service branches.

All validation and copies finish before stopping the live process. A failed
activation restores code, frontend, config, unit and service enablement together.
Recovery material is retained if a rollback itself fails.
"""

import argparse
import grp
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from hy2_panel_release import activate_release, install_release, validate_dist

SYSTEMD_DIR = Path('/etc/systemd/system')
TOKEN_FILE = Path('/etc/lancoai/internal-token')
STATE_ROOT = Path('/root/hysteria/state')


class InstallError(RuntimeError):
    pass


def command(*args, check=True):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    if check and result.returncode:
        # Configuration and subprocess output may contain credentials.
        raise InstallError(f'{args[0]} {args[1]} failed ({result.returncode})')
    return result


def stopped(unit):
    result = command(
        'systemctl',
        'show',
        unit,
        '-p',
        'ActiveState',
        '-p',
        'SubState',
        '-p',
        'MainPID',
        '-p',
        'ControlPID',
        check=False,
    )
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    return (
        result.returncode == 0
        and values.get('ActiveState') == 'inactive'
        and values.get('SubState') == 'dead'
        and values.get('MainPID') == '0'
        and values.get('ControlPID') == '0'
    )


def stop(unit):
    command('systemctl', 'stop', unit, check=False)
    if not stopped(unit):
        raise InstallError(f'{unit} did not fully stop; refusing to replace files')


def healthy(port, unit):
    for _ in range(30):
        if (
            command('systemctl', 'is-active', unit, check=False).returncode == 0
            and command(
                'curl', '-fsS', '--max-time', '2', f'http://127.0.0.1:{port}/healthz', check=False
            ).returncode
            == 0
        ):
            return
        time.sleep(1)
    raise InstallError('service health check failed')


def put_file(source, target):
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    temporary = target.with_name(f'.{target.name}.install-new')
    shutil.copy2(source, temporary)
    os.chown(temporary, source.stat().st_uid, source.stat().st_gid)
    os.replace(temporary, target)


def set_flag(text, name, value):
    lines = [line for line in text.splitlines() if not line.startswith(name + '=')]
    return '\n'.join([*lines, f'{name}={value}', ''])


def install(service, enable=False):
    checkout = Path(__file__).resolve().parents[1]
    key = service.upper()
    runtime = Path(os.environ.get(f'LANCOAI_{key}_ROOT', f'/opt/lancoai/{service}'))
    env_file = Path(os.environ.get(f'LANCOAI_{key}_ENV', f'/etc/lancoai/{service}.env'))
    unit = f'lancoai-{service}.service'
    unit_file = SYSTEMD_DIR / unit
    token_file = TOKEN_FILE if service == 'workbench' else None
    python = os.environ.get('LANCOAI_VENV_PYTHON', '/root/hysteria/.venv-web/bin/python')
    scheduler = 'SHOP_SCHEDULER' if service == 'shop' else 'WORKBENCH_SCHEDULERS'
    port = 8084 if service == 'shop' else 8085
    gid = grp.getgrnam('hy2-xray').gr_gid
    if runtime.is_symlink() or (runtime.exists() and not runtime.is_dir()):
        raise InstallError('runtime must be a regular directory')
    validate_dist(checkout / 'frontend/dist')
    if not Path(python).is_file():
        raise InstallError('install the shared Python environment first')
    old_active = command('systemctl', 'is-active', unit, check=False).returncode == 0
    old_enabled = command('systemctl', 'is-enabled', unit, check=False).stdout.strip()
    if old_enabled not in ('enabled', 'disabled', 'not-found', ''):
        raise InstallError(f'unsupported service enablement: {old_enabled}')
    runtime.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    pending = runtime.parent / f'.{service}-install-pending'
    if pending.exists():
        raise InstallError(f'unfinished installation: inspect {pending} before retrying')
    transaction = Path(tempfile.mkdtemp(prefix=f'.{service}-install-', dir=runtime.parent))
    candidate, previous = transaction / 'candidate', transaction / 'previous'
    snapshots = transaction / 'snapshots'
    snapshots.mkdir()
    tracked = [env_file, unit_file] + ([token_file] if token_file else [])
    saved = {}
    touched = False
    retain = False
    original_runtime = runtime.exists()
    try:
        # Retain previous content-addressed frontends, but stage new Python files
        # separately; never copy into a directory used by a running interpreter.
        candidate.mkdir(mode=0o750)
        if (runtime / 'panel').exists():
            shutil.copytree(runtime / 'panel', candidate / 'panel', symlinks=True)
        for source in (checkout / 'server').rglob('*'):
            if not source.is_file() or '__pycache__' in source.parts:
                continue
            relative = source.relative_to(checkout / 'server')
            if source.suffix != '.py' and relative.parts[0] != 'learning_sandbox':
                continue
            if source.suffix == '.py':
                compile(source.read_bytes(), str(relative), 'exec')
            dest = candidate / relative
            dest.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            shutil.copyfile(source, dest)
            dest.chmod(0o640)
        manifest = install_release(checkout / 'frontend/dist', candidate / 'panel')
        activate_release(candidate / 'panel', manifest.release_id)
        for path in [candidate, *candidate.rglob('*')]:
            os.chown(path, 0, gid, follow_symlinks=False)
        for index, path in enumerate(tracked):
            if path.is_symlink():
                raise InstallError('configuration and unit must not be symlinks')
            backup = snapshots / str(index)
            saved[path] = backup if path.exists() else None
            if path.exists():
                shutil.copy2(path, backup)
                os.chown(backup, path.stat().st_uid, path.stat().st_gid)
        env_text = (
            env_file if env_file.exists() else checkout / f'deploy/{service}.env.example'
        ).read_text()
        if enable:
            env_text = set_flag(set_flag(env_text, scheduler, 1), 'LANCOAI_STANDBY', 0)
        elif 'LANCOAI_STANDBY=' not in env_text:
            # Upgrade an existing active service without turning its API off.
            active = f'{scheduler}=1' in env_text.splitlines()
            env_text = set_flag(env_text, 'LANCOAI_STANDBY', 0 if active else 1)
        staged_env = transaction / 'env'
        staged_env.write_text(env_text)
        staged_env.chmod(0o640)
        os.chown(staged_env, 0, gid)
        staged_unit = transaction / 'unit'
        shutil.copy2(checkout / 'deploy' / unit, staged_unit)
        staged_unit.chmod(0o644)
        state = STATE_ROOT / 'shop' if service == 'shop' else STATE_ROOT
        if not state.exists():
            state.mkdir(parents=True, exist_ok=True, mode=0o750)
            os.chown(state, 0, gid)
        pending.write_text(str(transaction) + '\n')
        touched = True
        if unit_file.exists() or old_active:
            stop(unit)
        if original_runtime:
            os.replace(runtime, previous)
        os.replace(candidate, runtime)
        put_file(staged_env, env_file)
        if token_file and not token_file.exists():
            staged_token = transaction / 'token'
            staged_token.write_text(secrets.token_urlsafe(48) + '\n')
            staged_token.chmod(0o640)
            os.chown(staged_token, 0, gid)
            put_file(staged_token, token_file)
        put_file(staged_unit, unit_file)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'enable', unit)
        command('systemctl', 'start', unit)
        healthy(port, unit)
        flag = next(line for line in env_text.splitlines() if line.startswith(scheduler + '='))
        label = 'scheduler ' if service == 'shop' else ''
        print(f'{service} service healthy; release {manifest.release_id}; {label}{flag}')
    except BaseException:
        if touched:
            try:
                # Never restore files or restart old workers while a candidate
                # process might still be running.
                stop(unit)
                if previous.exists():
                    if runtime.exists():
                        shutil.rmtree(runtime)
                    os.replace(previous, runtime)
                elif not original_runtime and runtime.exists():
                    shutil.rmtree(runtime)
                command('systemctl', 'disable', unit, check=False)
                for path, backup in saved.items():
                    if backup is None:
                        path.unlink(missing_ok=True)
                    else:
                        put_file(backup, path)
                command('systemctl', 'daemon-reload')
                if old_enabled == 'enabled':
                    command('systemctl', 'enable', unit)
                restored_enabled = command(
                    'systemctl', 'is-enabled', unit, check=False
                ).stdout.strip()
                if (restored_enabled == 'enabled') != (old_enabled == 'enabled'):
                    raise InstallError('failed to restore service enablement')
                if old_active:
                    command('systemctl', 'start', unit)
                    healthy(port, unit)
                print(
                    'installation failed; previous release and service state restored',
                    file=sys.stderr,
                )
            except BaseException:
                retain = True
                print(
                    f'ROLLBACK_INCOMPLETE: recovery files retained at {transaction}; '
                    'no old service was started before the candidate stopped',
                    file=sys.stderr,
                )
        raise
    finally:
        if not retain:
            if touched:
                pending.unlink(missing_ok=True)
            shutil.rmtree(transaction)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('service', choices=('shop', 'workbench'))
    parser.add_argument('--enable-scheduler', '--enable-schedulers', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('must run as root')

    def interrupted(_signum, _frame):
        raise InstallError('installation interrupted')

    signal.signal(signal.SIGTERM, interrupted)
    try:
        install(args.service, args.enable_scheduler)
    except Exception as error:
        print(f'INSTALL_FAILED: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
