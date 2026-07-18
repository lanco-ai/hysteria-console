"""Crash-safe proxy config reload scheduling.

All process creation is mocked: these tests must never contact the host
systemd instance or restart a real proxy service.
"""
import json
from pathlib import Path

import pytest

import tuic_config as tc
import xray_config as xc

VALID_GENERATION = '123-0123456789abcdef0123456789abcdef'


class FakeProcess:
    def __init__(self, returncode=0, on_wait=None):
        self.returncode = returncode
        self.on_wait = on_wait
        self.wait_timeouts = []
        self.killed = False

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.on_wait is not None:
            callback, self.on_wait = self.on_wait, None
            callback()
        return self.returncode

    def kill(self):
        self.killed = True


class FakeCompletedProcess:
    def __init__(self, returncode):
        self.returncode = returncode


def _xray_config(path):
    path.write_text(json.dumps({
        'inbounds': [
            {
                'protocol': 'vless',
                'port': 443,
                'settings': {'clients': []},
            },
            {
                'protocol': 'vless',
                'port': 8443,
                'settings': {'clients': []},
            },
        ],
    }))
    return path


def _pending_path(config_path):
    return Path(str(config_path) + '.reload.pending')


def _mock_popen(monkeypatch, module, returncodes, *, on_wait=None):
    calls = []
    processes = []
    outcomes = iter(returncodes)

    def fake_popen(command, **kwargs):
        process = FakeProcess(next(outcomes), on_wait=on_wait)
        calls.append((command, kwargs))
        processes.append(process)
        return process

    monkeypatch.setattr(module.subprocess, 'Popen', fake_popen)
    return calls, processes


def _mock_run(monkeypatch, module, outcomes):
    calls = []
    results = iter(outcomes)

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        outcome = next(results)
        if isinstance(outcome, BaseException):
            raise outcome
        return FakeCompletedProcess(outcome)

    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    return calls


def _mark_live(monkeypatch, module, config_file):
    monkeypatch.setattr(module, 'CONFIG_FILE', Path(config_file))


def _mock_fail_closed(monkeypatch, module):
    calls = []

    def fake_stop(service, **kwargs):
        calls.append((service, kwargs))
        return True

    monkeypatch.setattr(
        module.static_access,
        'stop_fail_closed',
        fake_stop,
    )
    return calls


def _unit_name(command):
    return command[command.index('--unit') + 1]


def test_xray_replace_then_crash_leaves_pending_reload_for_next_sync(
        tmp_path, monkeypatch):
    config_file = _xray_config(tmp_path / 'xray.json')
    marker = _pending_path(config_file)
    original_save = xc._save_config

    def crash_after_replace(path, cfg):
        assert marker.exists(), 'reload intent must be durable before replace'
        original_save(path, cfg)
        raise RuntimeError('simulated crash after replace')

    monkeypatch.setattr(xc, '_save_config', crash_after_replace)
    with pytest.raises(RuntimeError, match='simulated crash'):
        xc.sync_user('alice', 'uuid-A', path=config_file)

    assert marker.exists()
    monkeypatch.setattr(xc, '_save_config', original_save)
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is True


def test_tuic_replace_then_crash_leaves_pending_reload_for_next_sync(
        tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    config_file.write_text('{}')
    marker = _pending_path(config_file)
    users = {
        'alice': {
            'vless_uuid': 'uuid-A',
            'sub_token': 'token-A',
        },
    }
    original_save = tc._save_config

    def crash_after_replace(path, cfg):
        assert marker.exists(), 'reload intent must be durable before replace'
        original_save(path, cfg)
        raise RuntimeError('simulated crash after replace')

    monkeypatch.setattr(tc, '_save_config', crash_after_replace)
    with pytest.raises(RuntimeError, match='simulated crash'):
        tc.sync_all(users=users, path=config_file)

    assert marker.exists()
    monkeypatch.setattr(tc, '_save_config', original_save)
    assert tc.sync_all(users=users, path=config_file) is True


def test_xray_schedule_never_acks_before_restart_worker_completes(
        tmp_path, monkeypatch):
    config_file = _xray_config(tmp_path / 'xray.json')
    _mark_live(monkeypatch, xc, config_file)
    fail_closed_calls = _mock_fail_closed(monkeypatch, xc)
    marker = _pending_path(config_file)
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is True
    pending_token = xc._read_reload_pending(config_file)

    calls, processes = _mock_popen(monkeypatch, xc, [1, 0])
    assert xc.reload_async(path=config_file) is False
    assert marker.exists(), 'non-zero systemd-run status must retain intent'
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is True
    assert len(fail_closed_calls) == 1

    assert xc.reload_async(path=config_file) is True
    assert marker.exists(), 'accepted scheduling is not a completed restart'
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is True
    assert all(
        process.wait_timeouts == [xc.RELOAD_SCHEDULE_TIMEOUT_SECONDS]
        for process in processes
    )
    assert calls[1][0][-5:] == [
        xc.sys.executable,
        str(Path(xc.__file__).resolve()),
        xc.RELOAD_WORKER_FLAG,
        str(config_file),
        pending_token,
    ]

    sleep_calls = []
    monkeypatch.setattr(
        xc.time, 'sleep', lambda seconds: sleep_calls.append(seconds),
    )
    restart_calls = _mock_run(
        monkeypatch,
        xc,
        [0] + [0] * xc.RELOAD_READINESS_STABILITY_PROBES,
    )
    assert xc._run_reload_worker(config_file, pending_token) is True
    assert not marker.exists()
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is False
    assert restart_calls[0] == (
        ['systemctl', 'restart', xc.RELOAD_SERVICE],
        {
            'stdout': xc.subprocess.DEVNULL,
            'stderr': xc.subprocess.DEVNULL,
            'timeout': xc.RELOAD_RESTART_TIMEOUT_SECONDS,
            'check': False,
        },
    )
    assert restart_calls[1] == (
        ['systemctl', 'is-active', '--quiet', xc.RELOAD_SERVICE],
        {
            'stdout': xc.subprocess.DEVNULL,
            'stderr': xc.subprocess.DEVNULL,
            'timeout': xc.RELOAD_READINESS_TIMEOUT_SECONDS,
            'check': False,
        },
    )
    assert sleep_calls == [
        xc.RELOAD_READINESS_DELAY_SECONDS
    ] * xc.RELOAD_READINESS_STABILITY_PROBES


def test_tuic_schedule_never_acks_before_restart_worker_completes(
        tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    _mark_live(monkeypatch, tc, config_file)
    fail_closed_calls = _mock_fail_closed(monkeypatch, tc)
    users = {
        'alice': {
            'vless_uuid': 'uuid-A',
            'sub_token': 'token-A',
        },
    }
    marker = _pending_path(config_file)
    assert tc.sync_all(users=users, path=config_file) is True
    pending_token = tc._read_reload_pending(config_file)

    calls, processes = _mock_popen(monkeypatch, tc, [1, 0])
    assert tc.reload_async(path=config_file) is False
    assert marker.exists(), 'non-zero systemd-run status must retain intent'
    assert tc.sync_all(users=users, path=config_file) is True
    assert len(fail_closed_calls) == 1

    assert tc.reload_async(path=config_file) is True
    assert marker.exists(), 'accepted scheduling is not a completed restart'
    assert tc.sync_all(users=users, path=config_file) is True
    assert all(
        process.wait_timeouts == [tc.RELOAD_SCHEDULE_TIMEOUT_SECONDS]
        for process in processes
    )
    assert calls[1][0][-5:] == [
        tc.sys.executable,
        str(Path(tc.__file__).resolve()),
        tc.RELOAD_WORKER_FLAG,
        str(config_file),
        pending_token,
    ]

    sleep_calls = []
    monkeypatch.setattr(
        tc.time, 'sleep', lambda seconds: sleep_calls.append(seconds),
    )
    restart_calls = _mock_run(
        monkeypatch,
        tc,
        [0] + [0] * tc.RELOAD_READINESS_STABILITY_PROBES,
    )
    assert tc._run_reload_worker(config_file, pending_token) is True
    assert not marker.exists()
    assert tc.sync_all(users=users, path=config_file) is False
    assert restart_calls[0][0] == [
        'systemctl', 'restart', tc.RELOAD_SERVICE,
    ]
    assert restart_calls[0][1]['timeout'] == (
        tc.RELOAD_RESTART_TIMEOUT_SECONDS
    )
    assert restart_calls[1][0] == [
        'systemctl', 'is-active', '--quiet', tc.RELOAD_SERVICE,
    ]
    assert restart_calls[1][1]['timeout'] == (
        tc.RELOAD_READINESS_TIMEOUT_SECONDS
    )
    assert sleep_calls == [
        tc.RELOAD_READINESS_DELAY_SECONDS
    ] * tc.RELOAD_READINESS_STABILITY_PROBES


@pytest.mark.parametrize(
    ('module', 'prefix'),
    [
        (xc, 'xray-reload-'),
        (tc, 'tuic-reload-'),
    ],
)
def test_reload_unit_names_remain_unique_with_frozen_clock(
        tmp_path, monkeypatch, module, prefix):
    tokens = iter(['firstentropy', 'secondentropy'])
    monkeypatch.setattr(module.time, 'time_ns', lambda: 123456789)
    monkeypatch.setattr(module.secrets, 'token_hex', lambda _size: next(tokens))
    calls, _processes = _mock_popen(monkeypatch, module, [0, 0])

    config_file = tmp_path / f'{prefix}.json'
    _mark_live(monkeypatch, module, config_file)
    _pending_path(config_file).write_text(VALID_GENERATION + '\n')
    assert module.reload_async(path=config_file) is True
    assert module.reload_async(path=config_file) is True

    units = [_unit_name(command) for command, _kwargs in calls]
    assert units == [
        f'{prefix}123456789-firstentropy',
        f'{prefix}123456789-secondentropy',
    ]
    assert len(set(units)) == 2


@pytest.mark.parametrize('module', [xc, tc])
@pytest.mark.parametrize(
    'outcome',
    ['nonzero', 'timeout', 'spawn-error'],
)
def test_live_schedule_failure_fails_closed_and_returns_false(
        tmp_path, monkeypatch, module, outcome):
    config_file = tmp_path / f'{module.__name__}.json'
    _mark_live(monkeypatch, module, config_file)
    marker = _pending_path(config_file)
    marker.write_text(VALID_GENERATION + '\n')
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)

    if outcome == 'nonzero':
        process = FakeProcess(returncode=1)
        monkeypatch.setattr(
            module.subprocess,
            'Popen',
            lambda _command, **_kwargs: process,
        )
    elif outcome == 'timeout':
        class TimeoutProcess:
            def __init__(self):
                self.wait_count = 0
                self.killed = False

            def wait(self, timeout=None):
                self.wait_count += 1
                if self.wait_count == 1:
                    raise module.subprocess.TimeoutExpired(
                        ['systemd-run'],
                        timeout,
                    )
                return -9

            def kill(self):
                self.killed = True

        process = TimeoutProcess()
        monkeypatch.setattr(
            module.subprocess,
            'Popen',
            lambda _command, **_kwargs: process,
        )
    else:
        process = None

        def fail_spawn(_command, **_kwargs):
            raise OSError('simulated spawn failure')

        monkeypatch.setattr(module.subprocess, 'Popen', fail_spawn)

    assert module.reload_async(path=config_file) is False
    assert marker.read_text() == VALID_GENERATION + '\n'
    assert len(fail_closed_calls) == 1
    service, kwargs = fail_closed_calls[0]
    assert service == module.RELOAD_SERVICE
    assert kwargs['live'] is True
    assert kwargs['runner'] is module.subprocess.run
    if outcome == 'timeout':
        assert process.killed is True


@pytest.mark.parametrize('module', [xc, tc])
def test_alternate_paths_never_contact_systemd(
        tmp_path, monkeypatch, module):
    alternate = tmp_path / f'alternate-{module.__name__}.json'
    monkeypatch.setattr(
        module.subprocess,
        'Popen',
        lambda *_args, **_kwargs: pytest.fail(
            'alternate reload scheduling contacted systemd'
        ),
    )
    monkeypatch.setattr(
        module.subprocess,
        'run',
        lambda *_args, **_kwargs: pytest.fail(
            'alternate reload worker contacted systemd'
        ),
    )
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)

    assert module.reload_async(path=alternate) is False
    assert module._run_reload_worker(alternate, 'generation-A') is False
    assert fail_closed_calls == []


@pytest.mark.parametrize('module', [xc, tc])
def test_reload_without_pending_generation_is_a_noop(
        tmp_path, monkeypatch, module):
    config_file = tmp_path / 'runtime.json'
    _mark_live(monkeypatch, module, config_file)
    monkeypatch.setattr(
        module.subprocess,
        'Popen',
        lambda *_args, **_kwargs: pytest.fail(
            'reload without a generation contacted systemd'
        ),
    )
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)

    assert module.reload_async(path=config_file) is False
    assert fail_closed_calls == []


@pytest.mark.parametrize('module', [xc, tc])
@pytest.mark.parametrize(
    'marker_payload',
    ['\n', 'generation-A\n', '123-zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz\n'],
)
def test_invalid_reload_generation_fails_closed_without_scheduling(
        tmp_path, monkeypatch, module, marker_payload):
    config_file = tmp_path / 'runtime.json'
    _mark_live(monkeypatch, module, config_file)
    _pending_path(config_file).write_text(marker_payload)
    monkeypatch.setattr(
        module.subprocess,
        'Popen',
        lambda *_args, **_kwargs: pytest.fail(
            'invalid reload generation was scheduled'
        ),
    )
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)

    assert module.reload_async(path=config_file) is False
    assert len(fail_closed_calls) == 1


def test_xray_successful_old_reload_does_not_clear_newer_marker(
        tmp_path, monkeypatch):
    config_file = _xray_config(tmp_path / 'xray.json')
    _mark_live(monkeypatch, xc, config_file)
    marker = _pending_path(config_file)
    assert xc.sync_user('alice', 'uuid-A', path=config_file) is True
    old_token = xc._read_reload_pending(config_file)
    assert xc.sync_user('bob', 'uuid-B', path=config_file) is True
    new_token = xc._read_reload_pending(config_file)
    monkeypatch.setattr(xc.time, 'sleep', lambda _seconds: None)
    _mock_run(
        monkeypatch,
        xc,
        [0] + [0] * xc.RELOAD_READINESS_STABILITY_PROBES,
    )

    assert xc._run_reload_worker(config_file, old_token) is True

    assert marker.exists()
    assert xc._read_reload_pending(config_file) == new_token
    assert new_token != old_token
    assert xc.sync_user('bob', 'uuid-B', path=config_file) is True


def test_tuic_successful_old_reload_does_not_clear_newer_marker(
        tmp_path, monkeypatch):
    config_file = tmp_path / 'tuic.json'
    _mark_live(monkeypatch, tc, config_file)
    users_a = {
        'alice': {
            'vless_uuid': 'uuid-A',
            'sub_token': 'token-A',
        },
    }
    users_b = {
        **users_a,
        'bob': {
            'vless_uuid': 'uuid-B',
            'sub_token': 'token-B',
        },
    }
    marker = _pending_path(config_file)
    assert tc.sync_all(users=users_a, path=config_file) is True
    old_token = tc._read_reload_pending(config_file)
    assert tc.sync_all(users=users_b, path=config_file) is True
    new_token = tc._read_reload_pending(config_file)
    monkeypatch.setattr(tc.time, 'sleep', lambda _seconds: None)
    _mock_run(
        monkeypatch,
        tc,
        [0] + [0] * tc.RELOAD_READINESS_STABILITY_PROBES,
    )

    assert tc._run_reload_worker(config_file, old_token) is True

    assert marker.exists()
    assert tc._read_reload_pending(config_file) == new_token
    assert new_token != old_token
    assert tc.sync_all(users=users_b, path=config_file) is True


@pytest.mark.parametrize('module', [xc, tc])
@pytest.mark.parametrize(
    'outcome',
    [
        pytest.param(1, id='nonzero'),
        pytest.param(
            TimeoutError('simulated timeout'),
            id='timeout',
        ),
        pytest.param(OSError('simulated exec failure'), id='exec-error'),
    ],
)
def test_failed_or_timed_out_restart_worker_retains_marker(
        tmp_path, monkeypatch, module, outcome):
    config_file = tmp_path / f'{module.__name__}.json'
    _mark_live(monkeypatch, module, config_file)
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)
    marker = _pending_path(config_file)
    marker.write_text(VALID_GENERATION + '\n')
    expected_token = module._read_reload_pending(config_file)

    if isinstance(outcome, TimeoutError):
        outcome = module.subprocess.TimeoutExpired(
            ['systemctl', 'restart', module.RELOAD_SERVICE],
            module.RELOAD_RESTART_TIMEOUT_SECONDS,
        )
    _mock_run(monkeypatch, module, [outcome])

    assert module._run_reload_worker(config_file, expected_token) is False
    assert marker.exists()
    assert module._read_reload_pending(config_file) == expected_token
    assert len(fail_closed_calls) == 1
    service, kwargs = fail_closed_calls[0]
    assert service == module.RELOAD_SERVICE
    assert kwargs['live'] is True
    assert kwargs['runner'] is module.subprocess.run


@pytest.mark.parametrize('module', [xc, tc])
@pytest.mark.parametrize(
    'outcome',
    [
        pytest.param(1, id='inactive'),
        pytest.param(
            TimeoutError('simulated timeout'),
            id='timeout',
        ),
        pytest.param(OSError('simulated exec failure'), id='exec-error'),
    ],
)
def test_failed_readiness_retains_marker_and_fails_closed(
        tmp_path, monkeypatch, module, outcome):
    config_file = tmp_path / f'{module.__name__}.json'
    _mark_live(monkeypatch, module, config_file)
    marker = _pending_path(config_file)
    marker.write_text(VALID_GENERATION + '\n')
    expected_token = module._read_reload_pending(config_file)
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)
    sleep_calls = []
    monkeypatch.setattr(
        module.time,
        'sleep',
        lambda seconds: sleep_calls.append(seconds),
    )

    if isinstance(outcome, TimeoutError):
        outcome = module.subprocess.TimeoutExpired(
            ['systemctl', 'is-active', '--quiet', module.RELOAD_SERVICE],
            module.RELOAD_READINESS_TIMEOUT_SECONDS,
        )
    run_calls = _mock_run(monkeypatch, module, [0, outcome])

    assert module._run_reload_worker(config_file, expected_token) is False
    assert marker.exists()
    assert module._read_reload_pending(config_file) == expected_token
    assert len(fail_closed_calls) == 1
    assert sleep_calls == [module.RELOAD_READINESS_DELAY_SECONDS]
    assert run_calls[1][0] == [
        'systemctl', 'is-active', '--quiet', module.RELOAD_SERVICE,
    ]


@pytest.mark.parametrize('module', [xc, tc])
def test_late_readiness_failure_is_not_acknowledged(
        tmp_path, monkeypatch, module):
    config_file = tmp_path / f'{module.__name__}.json'
    _mark_live(monkeypatch, module, config_file)
    marker = _pending_path(config_file)
    marker.write_text(VALID_GENERATION + '\n')
    expected_token = module._read_reload_pending(config_file)
    fail_closed_calls = _mock_fail_closed(monkeypatch, module)
    sleep_calls = []
    monkeypatch.setattr(
        module.time,
        'sleep',
        lambda seconds: sleep_calls.append(seconds),
    )
    _mock_run(monkeypatch, module, [0, 0, 1])

    assert module._run_reload_worker(config_file, expected_token) is False
    assert marker.exists()
    assert len(fail_closed_calls) == 1
    assert sleep_calls == [
        module.RELOAD_READINESS_DELAY_SECONDS,
        module.RELOAD_READINESS_DELAY_SECONDS,
    ]


def test_xray_missing_runtime_group_fails_before_replace(
        tmp_path, monkeypatch):
    config_file = _xray_config(tmp_path / 'xray.json')
    original_bytes = config_file.read_bytes()
    replace_calls = []
    original_replace = xc.os.replace

    monkeypatch.setattr(xc.os, 'geteuid', lambda: 0)

    def missing_group(_name):
        raise KeyError(xc.CONFIG_GROUP)

    def tracking_replace(source, destination):
        replace_calls.append((source, destination))
        return original_replace(source, destination)

    monkeypatch.setattr(xc.grp, 'getgrnam', missing_group)
    monkeypatch.setattr(xc.os, 'replace', tracking_replace)

    with pytest.raises(RuntimeError, match='required xray config group'):
        xc._save_config(config_file, {'inbounds': []})

    assert replace_calls == []
    assert config_file.read_bytes() == original_bytes
    assert not list(tmp_path.glob('xray.json.*.tmp'))
