"""Policy-gate regressions for the Hysteria updater (P0).

should_auto_apply is the pure decision function the timer path relies on; if
any gate silently stops firing, a disabled policy would still let the timer
upgrade the live binary. apply_update(force=False) must skip (not fail) and
must never replace the binary. The CLI exit codes drive systemd + journald.
"""

import json
import threading
from datetime import datetime, timedelta

import pytest

import hysteria_update as hu
import online_snapshot
import state_store


TZ = hu.timeutil.LOCAL_TZ  # Asia/Shanghai, the project's local clock.


def _now(h, m=0):
    return datetime(2026, 8, 27, h, m, tzinfo=TZ)


def _policy(**over):
    return hu.normalize_policy(dict(over))


def _ok_backup():
    return {'ok': True, 'label': 'fresh'}


def _bad_backup():
    return {'ok': False, 'label': '无备份'}


# An update is available, published days ago.
INFO = {
    'ok': True,
    'current': 'v2.11.0',
    'latest': 'app/v2.12.2',
    'published_at': '2026-08-23T00:59:00Z',
    'asset_url': 'https://x/bin',
    'asset_sha256': 'a' * 64,
    'checksum_url': '',
    'update_available': True,
}


# ---- should_auto_apply: every documented reason ----

def test_should_auto_apply_policy_disabled():
    allowed, reason = hu.should_auto_apply(
        _policy(), {}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'policy_disabled')


def test_should_auto_apply_outside_maintenance_window():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, maintenance_window={'start': '03:30', 'end': '05:00'}),
        {}, INFO,
        now=_now(6, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'outside_maintenance_window')


def test_should_auto_apply_cross_midnight_window_inside():
    # 23:00 -> 02:00 wraps midnight; 00:30 is inside.
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, maintenance_window={'start': '23:00', 'end': '02:00'}),
        {}, INFO,
        now=_now(0, 30), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (True, 'ok')


def test_should_auto_apply_cross_midnight_window_outside():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, maintenance_window={'start': '23:00', 'end': '02:00'}),
        {}, INFO,
        now=_now(12, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'outside_maintenance_window')


def test_should_auto_apply_malformed_window_is_fail_closed():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, maintenance_window={'start': 'xx', 'end': '05:00'}),
        {}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'outside_maintenance_window')


def test_should_auto_apply_release_too_young():
    published = (_now(4, 0) - timedelta(hours=1)).isoformat()
    info = dict(INFO, published_at=published)
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, min_release_age_hours=24), {}, info,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'release_too_young')


def test_should_auto_apply_published_at_missing_fails_closed():
    info = dict(INFO, published_at='')
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, info,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'release_too_young')


def test_should_auto_apply_users_online():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, INFO,
        now=_now(4, 0), online_count=5, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'users_online')


def test_should_auto_apply_users_online_not_skipped_when_flag_off():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, skip_if_online_users=False), {}, INFO,
        now=_now(4, 0), online_count=5, backup_result=_ok_backup())
    assert (allowed, reason) == (True, 'ok')


def test_should_auto_apply_cooldown_active():
    last = (_now(4, 0) - timedelta(hours=1)).isoformat()
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, cooldown_hours=24), {'cooldown_anchor': last}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'cooldown_active')


def test_should_auto_apply_missing_ts_does_not_lock_out_first_run():
    # No ts -> cooldown skipped so a first run is not permanently blocked.
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (True, 'ok')


@pytest.mark.parametrize(
    ('cooldown_hours', 'first_allowed_day'), ((48, 2), (720, 30)),
)
def test_repeated_daily_policy_skips_do_not_refresh_cooldown_anchor(
    cooldown_hours, first_allowed_day,
):
    anchor = _now(4, 0)
    state = {'cooldown_anchor': anchor.isoformat()}
    outcomes = []
    for day in range(1, first_allowed_day + 1):
        now = anchor + timedelta(days=day)
        allowed, reason = hu.should_auto_apply(
            _policy(enabled=True, cooldown_hours=cooldown_hours), state, INFO,
            now=now, online_count=0, backup_result=_ok_backup(),
        )
        outcomes.append((allowed, reason))
        # The timer records a status timestamp after each daily decision. It
        # must not move the install/recovery cooldown anchor.
        state['ts'] = now.isoformat()

    assert outcomes[:-1] == [
        (False, 'cooldown_active')
    ] * (first_allowed_day - 1)
    assert outcomes[-1] == (True, 'ok')
    assert state['cooldown_anchor'] == anchor.isoformat()


@pytest.mark.parametrize('contents', (None, '{broken', '[]'))
def test_auto_online_snapshot_unknown_fails_closed(
    tmp_path, monkeypatch, contents,
):
    online = tmp_path / 'online.json'
    if contents is not None:
        online.write_text(contents, encoding='utf-8')
    monkeypatch.setattr(hu, 'ONLINE_FILE', str(online))
    assert hu._online_count() is None
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True, skip_if_online_users=True), {}, INFO,
        now=_now(4, 0), online_count=None, backup_result=_ok_backup(),
    )
    assert (allowed, reason) == (False, 'online_state_unknown')


def test_auto_online_snapshot_permission_error_fails_closed(monkeypatch):
    monkeypatch.setattr(
        hu.state_store, 'load_json_strict',
        lambda *_a, **_k: (_ for _ in ()).throw(PermissionError('denied')),
    )
    assert hu._online_count('/root/denied/online.json') is None
    assert hu.should_auto_apply(
        _policy(enabled=True, skip_if_online_users=True), {}, INFO,
        now=_now(4, 0), online_count=None, backup_result=_ok_backup(),
    ) == (False, 'online_state_unknown')


def test_auto_online_snapshot_requires_matching_fresh_metadata(tmp_path):
    snapshot_path = tmp_path / 'online.json'
    snapshot = {'alice': 0}
    snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
    online_snapshot.metadata_path(snapshot_path).write_text(
        json.dumps(online_snapshot.build_metadata(snapshot, captured_at=1000.0)),
        encoding='utf-8',
    )

    assert hu._online_count(snapshot_path, now=1179.0) == 0
    assert hu._online_count(snapshot_path, now=1181.0) is None


def test_online_snapshot_ttl_covers_real_producer_cadence(tmp_path):
    snapshot_path = tmp_path / 'online.json'
    snapshot = {'alice': 0}
    snapshot_path.write_text(json.dumps(snapshot), encoding='utf-8')
    online_snapshot.metadata_path(snapshot_path).write_text(
        json.dumps(online_snapshot.build_metadata(snapshot, captured_at=1000.0)),
        encoding='utf-8',
    )

    # The producer runs every 90s with 10s AccuracySec. A healthy cache must
    # remain usable across that complete interval, while genuinely stale data
    # still fails closed.
    assert hu._online_count(snapshot_path, now=1100.0) == 0
    assert hu._online_count(snapshot_path, now=1181.0) is None


def test_check_and_policy_skip_do_not_refresh_cooldown_anchor(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    anchor = '2026-08-01T00:00:00+00:00'
    hu.save_state({**hu.IDLE_STATE, 'cooldown_anchor': anchor}, state_path)
    monkeypatch.setattr(hu, 'check_latest', lambda **_k: dict(INFO))

    hu.check_and_record(state_path=str(state_path), lock_timeout=0)
    assert hu.load_state(state_path)['cooldown_anchor'] == anchor

    result = hu.apply_update(
        state_path=str(state_path), binary_path=str(tmp_path / 'hysteria'),
        last_good_path=str(tmp_path / 'last-good'),
        policy=hu.normalize_policy({}), force=False, online_count=0,
        backup_result=_ok_backup(), now=_now(4, 0), lock_timeout=0,
    )
    assert result['status'] == 'skipped'
    assert result['cooldown_anchor'] == anchor


def test_should_auto_apply_stale_backup():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_bad_backup())
    assert (allowed, reason) == (False, 'stale_backup')


def test_should_auto_apply_no_update():
    info = dict(INFO, update_available=False)
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, info,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (False, 'no_update')


def test_should_auto_apply_all_pass():
    allowed, reason = hu.should_auto_apply(
        _policy(enabled=True), {}, INFO,
        now=_now(4, 0), online_count=0, backup_result=_ok_backup())
    assert (allowed, reason) == (True, 'ok')


# ---- apply_update(force=False): skip, never replace the binary ----

class _Resp:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else payload.encode()

    def read(self, n=-1):
        if n < 0:
            data, self._payload = self._payload, b''
            return data
        data, self._payload = self._payload[:n], self._payload[n:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _release(tag, *, published_at='2026-08-23T00:59:00Z', digest=None,
             with_checksums=True):
    asset = {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'}
    if digest:
        asset['digest'] = 'sha256:' + digest
    assets = [asset]
    if with_checksums:
        assets.append({'name': hu.CHECKSUMS_NAME,
                       'browser_download_url': 'https://x/sum'})
    return {'tag_name': tag, 'published_at': published_at, 'assets': assets}


def _release_opener(release):
    def opener(req, timeout=0):
        return _Resp(json.dumps(release))
    return opener


def _version_runner(text):
    def runner(cmd, **_k):
        return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
    return runner


@pytest.fixture
def apply_env(tmp_path, monkeypatch):
    """A binary + isolated lock + same-FS staging dir for apply_update tests."""
    import tempfile
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path), raising=False)
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    return binary


def test_auto_disabled_skips_without_replacing_binary(apply_env):
    binary = apply_env
    before = binary.read_bytes()
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        policy=hu.normalize_policy({}),  # enabled=False
        force=False,
        online_count=0,
        backup_result=_ok_backup(),
        now=_now(4, 0),
    )
    assert result['status'] == 'skipped'
    assert result['reason'] == 'policy_disabled'
    assert binary.read_bytes() == before  # binary untouched


def test_apply_default_semantics_are_policy_gated_auto(apply_env):
    """Changing the default back to force/admin semantics must fail this test."""
    binary = apply_env
    before = binary.read_bytes()
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        policy=hu.normalize_policy({}),
        online_count=0,
        backup_result=_ok_backup(),
        now=_now(4, 0),
    )
    assert result['status'] == 'skipped'
    assert result['reason'] == 'policy_disabled'
    assert binary.read_bytes() == before


def test_default_readiness_requires_three_consecutive_service_and_auth_passes(
    monkeypatch,
):
    """A single pass or a pass interrupted by failure must not report ready."""
    systemd_results = iter((True, False, True, True, True))
    systemd_calls = []
    auth_calls = []

    def probe_systemd(unit, *, runner):
        systemd_calls.append(unit)
        return {'ok': next(systemd_results), 'label': 'systemd'}

    def probe_auth_readiness(**kwargs):
        auth_calls.append(kwargs.get('path'))
        return {'ok': True, 'label': 'auth'}

    monkeypatch.setattr(hu.health, 'probe_systemd', probe_systemd)
    monkeypatch.setattr(hu.health, 'probe_auth_readiness', probe_auth_readiness)
    monkeypatch.setattr(hu, 'READINESS_DELAY_SECONDS', 0, raising=False)
    monkeypatch.setattr(hu, 'READINESS_TIMEOUT_SECONDS', 5, raising=False)

    result = hu._ready(lambda *_a, **_k: None)

    assert result['ok'] is True
    assert systemd_calls == [hu.UNIT] * 5
    assert auth_calls == ['/readyz'] * 5


def test_schedule_apply_async_records_pending_and_uses_isolated_worker(
    tmp_path, monkeypatch,
):
    """Scheduling must return before apply and leave a pollable durable state."""
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    calls = []

    class Process:
        def wait(self, timeout):
            assert timeout == hu.SCHEDULE_TIMEOUT_SECONDS
            return 0

    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Process()

    state = hu.schedule_apply_async(
        state_path=str(state_path),
        popen=popen,
        lock_timeout=0,
    )

    assert state['status'] == 'scheduled'
    assert hu.public_status(state)['pending'] is True
    saved = hu.load_state(state_path)
    assert saved['status'] == 'scheduled'
    command = calls[0][0]
    assert command[0] == 'systemd-run'
    assert '--no-block' in command
    assert '-I' in command
    assert '--apply' == command[-2]
    assert command[-1] == state['operation_id']
    assert any('/root/hysteria' in part for part in command)


def test_scheduler_releases_lock_before_start_and_passes_operation_id(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    observed = {}

    class Process:
        def wait(self, timeout):
            return 0

    def popen(command, **_kwargs):
        # A worker may start before systemd-run returns. It must be able to
        # acquire the shared lock and identify exactly the scheduled operation.
        with state_store.file_lock(hu.LOCK_PATH, timeout=0):
            current = hu.load_state(state_path)
            observed['operation_id'] = current['operation_id']
            observed['command'] = command
        return Process()

    state = hu.schedule_apply_async(
        state_path=str(state_path), popen=popen, lock_timeout=0,
    )

    assert state['operation_id'] == observed['operation_id']
    assert observed['operation_id']
    assert observed['command'][-2:] == ['--apply', observed['operation_id']]


def test_systemd_run_timeout_keeps_operation_scheduled_for_redelivery(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'

    class Process:
        def wait(self, timeout):
            raise hu.subprocess.TimeoutExpired('systemd-run', timeout)

        def kill(self):
            raise AssertionError('ambiguous delivery must not kill the client')

    state = hu.schedule_apply_async(
        state_path=str(state_path), popen=lambda *_a, **_k: Process(),
        lock_timeout=0,
    )

    assert state['status'] == 'scheduled'
    assert state['reason'] == 'schedule_delivery_unknown'
    assert hu.load_state(state_path)['operation_id'] == state['operation_id']


def test_scheduler_failure_cannot_overwrite_worker_progress(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'

    class Process:
        def __init__(self, operation_id):
            self.operation_id = operation_id

        def wait(self, timeout):
            with state_store.file_lock(hu.LOCK_PATH, timeout=0):
                state = hu.load_state(state_path)
                assert state['operation_id'] == self.operation_id
                state['status'] = 'downloading'
                hu.save_state(state, state_path)
            return 1

    def popen(command, **_kwargs):
        return Process(command[-1])

    state = hu.schedule_apply_async(
        state_path=str(state_path), popen=popen, lock_timeout=0,
    )

    assert state['status'] == 'downloading'
    assert hu.load_state(state_path)['status'] == 'downloading'


def test_scheduler_success_does_not_fail_when_immediate_worker_holds_lock(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    acquired = threading.Event()
    release = threading.Event()
    holder = []

    def hold_worker_lock(operation_id):
        with state_store.file_lock(hu.LOCK_PATH, timeout=1):
            state = hu.load_state(state_path)
            assert state['operation_id'] == operation_id
            state['status'] = 'downloading'
            hu.save_state(state, state_path)
            acquired.set()
            assert release.wait(2)

    class Process:
        def __init__(self, operation_id):
            self.operation_id = operation_id

        def wait(self, timeout):
            thread = threading.Thread(
                target=hold_worker_lock, args=(self.operation_id,),
            )
            holder.append(thread)
            thread.start()
            assert acquired.wait(2)
            return 0

    try:
        state = hu.schedule_apply_async(
            state_path=str(state_path),
            popen=lambda command, **_k: Process(command[-1]),
            lock_timeout=0,
        )
    finally:
        release.set()
        if holder:
            holder[0].join(timeout=2)

    assert state['status'] == 'scheduled'
    assert state['operation_id']
    assert hu.load_state(state_path)['status'] == 'downloading'


def test_check_cannot_overwrite_durable_scheduled_operation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    hu.save_state({
        'status': 'scheduled', 'operation_id': 'op-one',
        'pending_confirm': False,
    }, state_path)

    with pytest.raises(state_store.LockTimeout):
        hu.check_and_record(
            opener=lambda *_a, **_k: (_ for _ in ()).throw(
                AssertionError('active operation must be checked before network')
            ),
            state_path=str(state_path), lock_timeout=0,
        )

    assert hu.load_state(state_path)['operation_id'] == 'op-one'
    assert hu.load_state(state_path)['status'] == 'scheduled'


def test_stale_worker_cannot_execute_new_scheduled_operation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    hu.save_state({
        'status': 'scheduled', 'operation_id': 'op-new',
        'pending_confirm': False,
    }, state_path)
    monkeypatch.setattr(
        hu, 'check_latest',
        lambda **_k: (_ for _ in ()).throw(
            AssertionError('stale worker reached update logic')
        ),
    )

    with pytest.raises(hu.StaleOperation):
        hu.apply_update(
            operation_id='op-old', state_path=str(state_path),
            binary_path=str(tmp_path / 'hysteria'),
            last_good_path=str(tmp_path / 'last-good'), force=True,
        )

    saved = hu.load_state(state_path)
    assert saved['status'] == 'scheduled'
    assert saved['operation_id'] == 'op-new'


def test_redelivered_auto_operation_cannot_gain_manual_force_semantics(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    state_path = tmp_path / 'update.json'
    hu.save_state({
        **hu.IDLE_STATE,
        'status': 'scheduled',
        'operation_id': 'op-auto',
        'operation_mode': 'auto',
    }, state_path)

    result = hu.apply_update(
        operation_id='op-auto',
        # A stale/compromised caller flag must not override durable provenance.
        force=True,
        policy=hu.normalize_policy({'enabled': False}),
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(state_path),
        online_count=0, backup_result=_ok_backup(), now=_now(4, 0),
    )

    assert result['status'] == 'skipped'
    assert result['reason'] == 'policy_disabled'
    assert binary.read_bytes() == b'old-bin'


def test_redelivered_legacy_operation_without_mode_fails_closed(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    state_path = tmp_path / 'update.json'
    hu.save_state({
        **hu.IDLE_STATE,
        'status': 'scheduled',
        'operation_id': 'op-legacy',
        # A pre-operation_mode state has no durable proof of manual intent.
        'operation_mode': '',
    }, state_path)

    result = hu.apply_update(
        operation_id='op-legacy', force=True,
        policy=hu.normalize_policy({'enabled': False}),
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(state_path), online_count=0,
        backup_result=_ok_backup(), now=_now(4, 0),
    )

    assert result['status'] == 'skipped'
    assert result['reason'] == 'policy_disabled'
    assert result['operation_mode'] == 'auto'
    assert binary.read_bytes() == b'old-bin'


def test_worker_lock_busy_is_real_failure_and_leaves_scheduled(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    hu.save_state({
        'status': 'scheduled', 'operation_id': 'op-one',
        'pending_confirm': False,
    }, state_path)
    monkeypatch.setattr(hu, 'STATE_PATH', str(state_path))

    def busy(**_kwargs):
        raise state_store.LockTimeout('still busy after worker wait')

    monkeypatch.setattr(hu, 'apply_update', busy)
    assert hu._main(['--apply', 'op-one']) == 2
    assert hu.load_state(state_path)['status'] == 'scheduled'


def test_two_concurrent_confirms_create_only_one_operation(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'update.lock'))
    state_path = tmp_path / 'update.json'
    entered = threading.Event()
    release = threading.Event()
    commands = []
    first_result = []

    class Process:
        def wait(self, timeout):
            entered.set()
            assert release.wait(2)
            return 0

    def popen(command, **_kwargs):
        commands.append(command)
        return Process()

    thread = threading.Thread(
        target=lambda: first_result.append(hu.schedule_apply_async(
            state_path=str(state_path), popen=popen, lock_timeout=1,
        )),
    )
    thread.start()
    assert entered.wait(2)
    with pytest.raises(state_store.LockTimeout):
        hu.schedule_apply_async(
            state_path=str(state_path), popen=popen, lock_timeout=0,
        )
    release.set()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert len(commands) == 1
    assert len(first_result) == 1
    assert first_result[0]['operation_id'] == commands[0][-1]


@pytest.mark.parametrize(
    ('status', 'expected_exit'),
    (('done', 0), ('rolled_back', 2), ('rollback_failed', 2)),
)
def test_cli_apply_dispatches_sanitized_terminal_alert(
    monkeypatch, capsys, status, expected_exit,
):
    dispatches = []
    monkeypatch.setattr(
        hu, '_dispatch_pending_alert', lambda **_k: dispatches.append(True),
    )
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(
        hu,
        'apply_update',
        lambda **k: {
            'status': status,
            'version': 'v2.12.2',
            'previous_version': 'v2.11.0',
            'error': 'secret-token-must-not-leak',
            'reason': '',
        },
    )

    assert hu._main(['--apply']) == expected_exit
    output = capsys.readouterr().out
    assert 'secret-token-must-not-leak' not in output
    assert dispatches == [True]


def test_cli_reconcile_dispatches_sanitized_rollback_alert(monkeypatch, capsys):
    dispatches = []
    monkeypatch.setattr(
        hu, '_dispatch_pending_alert', lambda **_k: dispatches.append(True),
    )
    monkeypatch.setattr(
        hu,
        'reconcile',
        lambda **k: {
            'status': 'rollback_failed',
            'version': 'v2.12.2',
            'previous_version': 'v2.11.0',
            'error': 'secret-token-must-not-leak',
        },
    )

    assert hu._main(['--reconcile']) == 2
    output = capsys.readouterr().out
    assert 'secret-token-must-not-leak' not in output
    assert dispatches == [True]


def test_terminal_alert_is_durably_deduplicated_after_success(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    state = hu._terminal_update({
        **hu.IDLE_STATE,
        'operation_id': 'op-123',
        'version': 'v2.12.2',
        'previous_version': 'v2.11.0',
    }, 'done')
    hu.save_state(state, state_path)
    events = []

    def dispatch(event):
        events.append(event)
        return {'attempted': ['webhook'], 'failed': []}

    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=dispatch,
    ) is True
    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=dispatch,
    ) is False

    assert len(events) == 1
    assert events[0]['details']['event_id'] == 'hysteria-update:op-123:done'
    saved = hu.load_state(state_path)
    assert saved['notification_status'] == 'sent'
    assert saved['notification_attempts'] == 1


def test_terminal_alert_failure_retries_same_event_id(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    state = hu._terminal_update({
        **hu.IDLE_STATE,
        'operation_id': 'op-retry',
    }, 'failed', primary_error='download failed')
    hu.save_state(state, state_path)
    events = []

    def dispatch(event):
        events.append(event)
        if len(events) == 1:
            return {'attempted': ['webhook'], 'failed': ['webhook']}
        return {'attempted': ['webhook'], 'failed': []}

    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=dispatch,
    ) is False
    assert hu.load_state(state_path)['notification_status'] == 'retry'
    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=dispatch,
    ) is True

    assert len(events) == 2
    assert events[0]['details']['event_id'] == events[1]['details']['event_id']
    assert hu.load_state(state_path)['notification_attempts'] == 2


def test_terminal_alert_with_zero_attempted_channels_remains_retryable(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    hu.save_state(hu._terminal_update({
        **hu.IDLE_STATE, 'operation_id': 'op-no-channel',
    }, 'done'), state_path)
    results = iter((
        {'attempted': [], 'failed': []},
        {'attempted': ['webhook'], 'failed': []},
    ))

    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=lambda _event: next(results),
    ) is False
    assert hu.load_state(state_path)['notification_status'] == 'retry'
    assert hu._dispatch_pending_alert(
        state_path=str(state_path), dispatch=lambda _event: next(results),
    ) is True
    assert hu.load_state(state_path)['notification_status'] == 'sent'


def test_new_operation_check_failure_clears_previous_version_fields(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    hu.save_state({
        **hu.IDLE_STATE,
        'status': 'done', 'operation_id': 'old-op',
        'version': 'v9.9.9', 'previous_version': 'v8.8.8',
        'sha256': 'f' * 64,
    }, state_path)
    monkeypatch.setattr(
        hu, 'check_latest',
        lambda **_k: (_ for _ in ()).throw(OSError('network down')),
    )

    state = hu.apply_update(
        state_path=str(state_path), binary_path=str(tmp_path / 'hysteria'),
        last_good_path=str(tmp_path / 'last-good'), force=True,
    )

    assert state['status'] == 'failed'
    assert state['version'] == ''
    assert state['previous_version'] == ''
    assert state['sha256'] == ''


def test_terminal_alert_payload_excludes_all_error_and_secret_fields(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    secret = 'https://user:pass@example.test/file?token=secret-token'
    state = hu._terminal_update({
        **hu.IDLE_STATE,
        'operation_id': 'op-secret',
        'authorization': 'Bearer top-secret',
        'traceback': 'Traceback Authorization: Basic hidden',
    }, 'rollback_failed', primary_error=secret,
       rollback_error='token=rollback-secret')
    hu.save_state(state, state_path)
    events = []

    hu._dispatch_pending_alert(
        state_path=str(state_path),
        dispatch=lambda event: (
            events.append(event)
            or {'attempted': ['webhook'], 'failed': []}
        ),
    )

    payload = json.dumps(events, ensure_ascii=False)
    assert 'secret-token' not in payload
    assert 'rollback-secret' not in payload
    assert 'Authorization' not in payload
    assert 'user:pass' not in payload


def test_cli_auto_stops_after_reconcile_rollback(monkeypatch):
    dispatches = []
    monkeypatch.setattr(
        hu, '_dispatch_pending_alert', lambda **_k: dispatches.append(True),
    )
    monkeypatch.setattr(
        hu,
        'reconcile',
        lambda **k: {
            'status': 'rolled_back',
            'previous_version': 'v2.11.0',
            '_reconciled': True,
        },
    )

    def unexpected_apply(**_kwargs):
        raise AssertionError('auto must stop after reconcile rolled back')

    monkeypatch.setattr(hu, 'apply_update', unexpected_apply)

    assert hu._main(['--auto']) == 2
    assert dispatches == [True]


def test_cli_auto_does_not_treat_old_rollback_state_as_new_reconcile(monkeypatch):
    called = {'apply': False}
    monkeypatch.setattr(
        hu,
        'reconcile',
        lambda **k: {'status': 'rolled_back', 'previous_version': 'v2.11.0'},
    )

    def apply(**_kwargs):
        called['apply'] = True
        return {'status': 'done'}

    monkeypatch.setattr(hu, 'apply_update', apply)

    assert hu._main(['--auto']) == 0
    assert called['apply'] is True


def test_auto_skipped_writes_skipped_not_failed(apply_env):
    binary = apply_env
    fresh = (_now(4, 0) - timedelta(hours=1)).isoformat()  # 1h old < 24h gate
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.12.2', published_at=fresh)),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        policy=hu.normalize_policy({'enabled': True, 'min_release_age_hours': 24}),
        force=False,
        online_count=0,
        backup_result=_ok_backup(),
        now=_now(4, 0),
    )
    # A too-young release must be skipped, not failed, so the history card
    # does not turn red on every daily probe.
    assert result['status'] == 'skipped'
    assert result['reason'] == 'release_too_young'
    assert result['error'] == ''


def test_auto_no_update_writes_skipped_no_update(apply_env):
    binary = apply_env
    before = binary.read_bytes()
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.11.0')),  # same as current
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        policy=hu.normalize_policy({'enabled': True}),
        force=False,
        online_count=0,
        backup_result=_ok_backup(),
        now=_now(4, 0),
    )
    assert result['status'] == 'skipped'
    assert result['reason'] == 'no_update'
    assert binary.read_bytes() == before


# ---- force=True still respects the safety (backup) gate ----

def test_force_does_not_bypass_stale_backup(apply_env):
    binary = apply_env
    before = binary.read_bytes()
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result=_bad_backup(),
    )
    assert result['status'] == 'skipped'
    assert result['reason'] == 'stale_backup'
    assert binary.read_bytes() == before


def test_force_no_update_reports_done(apply_env):
    binary = apply_env
    before = binary.read_bytes()
    result = hu.apply_update(
        opener=_release_opener(_release('app/v2.11.0')),  # same as current
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(binary.parent / 'last-good'),
        state_path=str(binary.parent / 'state.json'),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result=_ok_backup(),
    )
    assert result['status'] == 'done'
    assert binary.read_bytes() == before


# ---- CLI exit codes ----

@pytest.fixture(autouse=True)
def _isolate_lock(tmp_path, monkeypatch):
    # Default to a temp lock so a stray real /root lock never affects tests.
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))


def test_cli_check_exits_zero(monkeypatch):
    monkeypatch.setattr(hu, 'check_latest', lambda **k: dict(INFO))
    monkeypatch.setattr(hu, 'record_check', lambda info, **k: info)
    assert hu._main(['--check']) == 0


def test_cli_check_failed_exits_two(monkeypatch):
    def _boom(**_k):
        raise OSError('network down')
    monkeypatch.setattr(hu, 'check_latest', _boom)
    monkeypatch.setattr(hu, 'record_check', lambda info, **k: info)
    assert hu._main(['--check']) == 2


def test_cli_auto_skipped_exits_one(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update',
                        lambda **k: {'status': 'skipped', 'reason': 'policy_disabled'})
    assert hu._main(['--auto']) == 1


def test_cli_auto_no_update_exits_zero(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update',
                        lambda **k: {'status': 'skipped', 'reason': 'no_update'})
    assert hu._main(['--auto']) == 0


def test_cli_auto_done_exits_zero(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update', lambda **k: {'status': 'done'})
    assert hu._main(['--auto']) == 0


def test_cli_apply_done_exits_zero(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update', lambda **k: {'status': 'done'})
    assert hu._main(['--apply']) == 0


def test_cli_apply_failed_exits_two(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update', lambda **k: {'status': 'failed'})
    assert hu._main(['--apply']) == 2


def test_cli_apply_rolled_back_exits_two(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update', lambda **k: {'status': 'rolled_back'})
    assert hu._main(['--apply']) == 2


def test_cli_reconcile_failed_exits_two(monkeypatch):
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'failed'})
    assert hu._main(['--reconcile']) == 2


def test_cli_reconcile_rollback_failed_exits_two(monkeypatch):
    monkeypatch.setattr(
        hu, 'reconcile', lambda **k: {'status': 'rollback_failed'},
    )
    assert hu._main(['--reconcile']) == 2


def test_cli_unknown_mode_exits_two():
    assert hu._main(['--bogus']) == 2


# ---- lock-busy: do not block, do not report failure ----

def test_cli_lock_busy_exits_zero_without_apply(monkeypatch):
    called = {'apply': False}

    def _busy(**_k):
        raise state_store.LockTimeout('update lock busy')

    def _must_not_run(**_k):
        called['apply'] = True
        return {'status': 'done'}

    monkeypatch.setattr(hu, 'reconcile', _busy)
    monkeypatch.setattr(hu, 'apply_update', _must_not_run)
    assert hu._main(['--auto']) == 0
    assert called['apply'] is False


def test_cli_apply_lock_busy_exits_zero(monkeypatch):
    def _busy(**_k):
        raise state_store.LockTimeout('update lock busy')
    monkeypatch.setattr(hu, 'reconcile', lambda **k: {'status': 'idle'})
    monkeypatch.setattr(hu, 'apply_update', _busy)
    assert hu._main(['--apply']) == 0
