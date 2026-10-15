import ast
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from service_rollback_guard import quiesce_services

UNITS = ('shop.service', 'workbench.service')


@pytest.mark.parametrize('failure', ['first-stop', 'second-stop', 'still-running', 'status', None])
def test_shutdown_attempts_both_and_requires_proof(failure):
    calls = []

    def run(*args):
        calls.append(args)
        if args[1] == 'disable' and (
            (failure == 'first-stop' and args[-1] == UNITS[0])
            or (failure == 'second-stop' and args[-1] == UNITS[1])
        ):
            raise RuntimeError('injected stop failure')
        if args[1] == 'show':
            if failure == 'status':
                raise RuntimeError('cannot inspect')
            if failure == 'still-running' and args[2] == UNITS[0]:
                return SimpleNamespace(
                    stdout='ActiveState=inactive\nSubState=dead\nMainPID=27\nControlPID=0'
                )
            return SimpleNamespace(
                stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0'
            )
        return SimpleNamespace(stdout='')

    if failure:
        with pytest.raises(RuntimeError):
            quiesce_services(UNITS, run)
    else:
        quiesce_services(UNITS, run)
    assert [c[-1] for c in calls if c[1] == 'disable'] == list(UNITS)
    assert [c[2] for c in calls if c[1] == 'show'] == list(UNITS)


def test_actual_cutover_never_restores_monolith_when_shutdown_fails(tmp_path):
    tree = ast.parse((ROOT / 'scripts/service_split_cutover.py').read_text())
    restore = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'restore')
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        if args[1] == 'disable' and args[-1] == UNITS[0]:
            raise RuntimeError('stop refused')
        return SimpleNamespace(
            stdout='ActiveState=active\nSubState=running\nMainPID=42\nControlPID=0'
        )

    namespace = {
        'quiesce_services': quiesce_services,
        'UNITS': UNITS,
        'run': run,
        'log': lambda _: None,
        'ENV_FILES': {'missing': tmp_path / 'missing'},
        'Failure': RuntimeError,
    }
    exec(
        compile(ast.Module(body=[restore], type_ignores=[]), '<cutover-restore>', 'exec'), namespace
    )
    with pytest.raises(SystemExit) as error:
        namespace['restore'](tmp_path)
    assert error.value.code == 2
    assert [c[-1] for c in calls if c[1] == 'disable'] == list(UNITS)
    assert not any(c[1] == 'start' for c in calls)


def test_missing_environment_backup_cannot_skip_service_shutdown(tmp_path):
    tree = ast.parse((ROOT / 'scripts/service_split_cutover.py').read_text())
    restore = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'restore')
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(
            stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\nControlPID=0'
        )

    namespace = {
        'quiesce_services': quiesce_services,
        'UNITS': UNITS,
        'run': run,
        'log': lambda _: None,
        'ENV_FILES': {'missing': tmp_path / 'missing'},
        'Failure': RuntimeError,
        'CHANGED': [],
        'DELETED': [],
        'ROOT': tmp_path,
        'STAGE': tmp_path,
        'PANEL': tmp_path,
        'RELEASE_TOOL': ['release-tool'],
        'MONOLITH_RELEASE': 'old',
        'wait_http': lambda *a, **kw: None,
        'sha256': lambda _: 'old',
        'VHOST': tmp_path / 'vhost',
        'VHOST_OLD_SHA': 'old',
        'expect': lambda *a: None,
        'SCRIPTS': {},
        'SHARE_FILES': [],
        'loopback': lambda *a: (200, '', '', b''),
        'public': lambda *a: (200, '', '', b''),
        'document_assets': lambda *a: ['old.js'],
        'install_file': lambda *a: None,
    }
    exec(
        compile(ast.Module(body=[restore], type_ignores=[]), '<cutover-restore>', 'exec'), namespace
    )
    with pytest.raises(SystemExit):
        namespace['restore'](tmp_path)
    assert [c[-1] for c in calls if len(c) > 1 and c[1] == 'disable'] == list(UNITS)
    starts = [i for i, c in enumerate(calls) if c[:2] == ('systemctl', 'start')]
    checks = [i for i, c in enumerate(calls) if c[:2] == ('systemctl', 'show')]
    assert len(starts) == 1 and starts[0] > max(checks)
