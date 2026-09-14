"""Shared overview traffic and user-status operation services."""

import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import alerts
import pytest
import subscription_service as ss
from overview_mutation_result import OverviewMutationResult

FIXED_NOW = datetime(2026, 9, 14, 10, 30, 45, tzinfo=ZoneInfo('Asia/Shanghai'))


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def operation_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'USAGE_FILE': tmp_path / 'usage.json',
        'USAGE_DAILY_FILE': tmp_path / 'usage_daily.json',
        'USAGE_HOURLY_FILE': tmp_path / 'usage_hourly.json',
        'USAGE_PRESERVED_FILE': tmp_path / 'usage_preserved.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    alert_file = tmp_path / 'alert_state.json'
    monkeypatch.setattr(alerts, 'STATE_FILE', alert_file)
    monkeypatch.setattr(ss, 'local_now', lambda: FIXED_NOW)

    users = {
        'alice': {'disabled': False, 'monthly_quota_bytes': 1000, 'marker': 'keep'},
        'bob': {'disabled': False, 'monthly_quota_bytes': 2000},
    }
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': 'hash-before',
            'admin_token': 'legacy-token',
            'settlement_day': 1,
            'cycle_length_days': 30,
            'cycle_anchor_date': '2026-09-01',
        },
    )
    _write_json(paths['USERS_FILE'], users)
    _write_json(
        paths['USAGE_FILE'],
        {'2026-09': {'alice': {'tx': 7, 'rx': 8, 'total': 15}, 'bob': {'total': 9}}},
    )
    _write_json(
        paths['USAGE_DAILY_FILE'],
        {
            '2026-08-31': {'alice': {'tx': 50, 'rx': 50, 'total': 100}},
            '2026-09-13': {
                'alice': {'tx': 10, 'rx': 20, 'total': 30},
                'bob': {'tx': 1, 'rx': 2, 'total': 3},
            },
            '2026-09-14': {
                'alice': {'tx': 4, 'rx': 6, 'total': 10},
                'bob': {'tx': 3, 'rx': 4, 'total': 7},
            },
        },
    )
    _write_json(
        paths['USAGE_HOURLY_FILE'],
        {
            '2026-08-31T23': {'alice': {'tx': 5, 'rx': 5, 'total': 10}},
            '2026-09-14T10': {
                'alice': {'tx': 4, 'rx': 6, 'total': 10},
                'bob': {'tx': 3, 'rx': 4, 'total': 7},
            },
        },
    )
    _write_json(paths['USAGE_PRESERVED_FILE'], {'2026-09-01': {}})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    alerts.save_state(
        {
            'quota_80': {'alice': '2026-09', 'bob': '2026-09'},
            'quota_100': {'alice': '2026-09', 'bob': '2026-09'},
            'anomaly': {'alice': '2026-09-14', 'bob': '2026-09-14'},
            'expiry_soon': {},
            'expiry_expired': {},
        },
        alert_file,
    )

    effects = []

    def sync(saved_users, **_kwargs):
        effects.append(('sync', json.loads(json.dumps(saved_users))))
        return True, True

    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: effects.append('xray'))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: effects.append('tuic'))
    monkeypatch.setattr(ss, 'hy_kick', lambda usernames: effects.append(('kick', list(usernames))))
    audits = []
    audit = lambda action, target, before, after: audits.append((action, target, before, after))
    return SimpleNamespace(paths=paths, users=users, effects=effects, audits=audits, audit=audit)


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _snapshot_existing(paths):
    return {
        name: path.read_bytes()
        for name, path in paths.items()
        if not name.endswith('LOCK_FILE') and path.exists()
    }


def test_result_is_frozen_slotted_and_contains_only_minimal_metadata():
    result = OverviewMutationResult(outcome='success', username='alice')

    assert result == OverviewMutationResult(outcome='success', username='alice')
    assert not hasattr(result, '__dict__')
    with pytest.raises(FrozenInstanceError):
        result.username = 'bob'


@pytest.mark.parametrize(
    ('form', 'code'),
    [
        ({'day': ['']}, 'err:settlement_invalid'),
        ({'day': ['0']}, 'err:settlement_invalid'),
        ({'day': ['29']}, 'err:settlement_invalid'),
        ({'day': ['1'], 'length': ['bad']}, 'err:cycle_length_invalid'),
        ({'day': ['1'], 'length': ['0']}, 'err:cycle_length_invalid'),
        ({'day': ['1'], 'length': ['91']}, 'err:cycle_length_invalid'),
    ],
)
def test_cycle_validation_happens_before_storage_or_lock(operation_state, monkeypatch, form, code):
    state = operation_state
    before = _snapshot_existing(state.paths)
    monkeypatch.setattr(ss, 'usage_lock', lambda: pytest.fail('invalid cycle must not lock'))

    result = ss._traffic_mutation_service(state.audit).configure_cycle(form=form)

    assert result == OverviewMutationResult(outcome='invalid', code=code)
    assert _snapshot_existing(state.paths) == before
    assert state.effects == [] and state.audits == []


def test_cycle_updates_locked_metadata_merge_then_syncs_users_and_reloads(
    operation_state, monkeypatch
):
    state = operation_state
    real_load_meta = ss.load_meta
    calls = []

    def concurrent_load_meta():
        meta = real_load_meta()
        if calls == []:
            meta['admin_pass_hash'] = 'concurrent-hash'
            _write_json(state.paths['META_FILE'], meta)
        calls.append('metadata-read')
        return real_load_meta()

    monkeypatch.setattr(ss, 'load_meta', concurrent_load_meta)

    result = ss._traffic_mutation_service(state.audit).configure_cycle(
        form={'day': ['12', '1'], 'length': ['15', '30']}
    )

    assert result == OverviewMutationResult(outcome='success', day=12)
    meta = _read(state.paths['META_FILE'])
    assert meta['admin_pass_hash'] == 'concurrent-hash'
    assert meta['settlement_day'] == 12
    assert meta['cycle_length_days'] == 15
    assert meta['cycle_anchor_date'] == '2026-09-12'
    assert state.effects == [('sync', state.users), 'xray', 'tuic']
    assert state.audits == []


@pytest.mark.parametrize(
    'form',
    [
        {'day': ['12']},
        {'day': ['12'], 'length': ['   ']},
    ],
    ids=['omitted-length', 'blank-length'],
)
def test_cycle_blank_or_omitted_length_preserves_locked_existing_length_and_merge(
    operation_state,
    monkeypatch,
    form,
):
    state = operation_state
    real_load_meta = ss.load_meta
    first_read = True

    def concurrent_load_meta():
        nonlocal first_read
        meta = real_load_meta()
        if first_read:
            first_read = False
            meta['admin_pass_hash'] = 'concurrent-hash'
            _write_json(state.paths['META_FILE'], meta)
        return real_load_meta()

    monkeypatch.setattr(ss, 'load_meta', concurrent_load_meta)

    result = ss._traffic_mutation_service(state.audit).configure_cycle(form=form)

    assert result == OverviewMutationResult(outcome='success', day=12)
    meta = _read(state.paths['META_FILE'])
    assert meta['admin_pass_hash'] == 'concurrent-hash'
    assert meta['settlement_day'] == 12
    assert meta['cycle_length_days'] == 30
    assert meta['cycle_anchor_date'] == '2026-09-12'
    assert state.effects == [('sync', state.users), 'xray', 'tuic']
    assert state.audits == []


@pytest.mark.parametrize(
    ('method_name', 'preserved_total'), [('reset_user', 0), ('refresh_user', 40)]
)
def test_reset_and_refresh_share_accounting_but_only_refresh_banks_raw_bytes(
    operation_state,
    method_name,
    preserved_total,
):
    state = operation_state
    revision = ss.user_config_revision(state.users['alice'])

    result = getattr(ss._traffic_mutation_service(state.audit), method_name)(
        form={'user': [' alice ', 'bob']},
        expected_revision=revision,
    )

    action = 'reset_usage_user' if method_name == 'reset_user' else 'refresh_usage_user'
    assert result == OverviewMutationResult(outcome='success', username='alice')
    assert _read(state.paths['USAGE_FILE'])['2026-09']['alice'] == {
        'tx': 0,
        'rx': 0,
        'total': 0,
    }
    daily = _read(state.paths['USAGE_DAILY_FILE'])
    assert daily['2026-08-31']['alice']['total'] == 100
    assert daily['2026-09-13']['alice']['total'] == 0
    assert daily['2026-09-14']['alice']['total'] == 0
    hourly = _read(state.paths['USAGE_HOURLY_FILE'])
    assert hourly['2026-08-31T23']['alice']['total'] == 10
    assert hourly['2026-09-14T10']['alice']['total'] == 0
    preserved = _read(state.paths['USAGE_PRESERVED_FILE'])['2026-09-01']
    assert sum(item['total'] for item in preserved.values()) == preserved_total
    dedup = alerts.load_state(state.paths['USAGE_FILE'].parent / 'alert_state.json')
    assert 'alice' not in dedup['quota_80'] and 'alice' not in dedup['quota_100']
    assert dedup['anomaly']['alice'] == '2026-09-14'
    assert dedup['quota_80']['bob'] == '2026-09'
    assert state.effects == [('sync', state.users), 'xray', 'tuic']
    assert state.audits == [
        (
            action,
            'alice',
            {'tx': 14, 'rx': 26, 'total': 40},
            {'tx': 0, 'rx': 0, 'total': 0},
        )
    ]


def test_reset_all_preserves_user_order_and_audits_exact_maps(operation_state):
    state = operation_state

    result = ss._traffic_mutation_service(state.audit).reset_all()

    assert result == OverviewMutationResult(outcome='success')
    assert list(_read(state.paths['USAGE_FILE'])['2026-09']) == ['alice', 'bob']
    assert _read(state.paths['USAGE_DAILY_FILE'])['2026-09-14'] == {
        'alice': {'tx': 0, 'rx': 0, 'total': 0},
        'bob': {'tx': 0, 'rx': 0, 'total': 0},
    }
    assert state.audits == [
        (
            'reset_usage_all',
            'all_users',
            {
                'alice': {'tx': 14, 'rx': 26, 'total': 40},
                'bob': {'tx': 4, 'rx': 6, 'total': 10},
            },
            {
                'alice': {'tx': 0, 'rx': 0, 'total': 0},
                'bob': {'tx': 0, 'rx': 0, 'total': 0},
            },
        )
    ]


@pytest.mark.parametrize(
    ('username', 'revision', 'outcome'),
    [('missing', 'ignored', 'not_found'), ('alice', 'stale', 'conflict')],
)
def test_rejected_per_user_traffic_operations_do_not_write_or_emit_effects(
    operation_state,
    username,
    revision,
    outcome,
):
    state = operation_state
    before = _snapshot_existing(state.paths)

    result = ss._traffic_mutation_service(state.audit).reset_user(
        form={'user': [username]}, expected_revision=revision
    )

    assert result == OverviewMutationResult(outcome=outcome, username=username)
    assert _snapshot_existing(state.paths) == before
    assert state.effects == [] and state.audits == []


@pytest.mark.parametrize(
    ('operation', 'outcome'),
    [
        ('reset_user', 'conflict'),
        ('refresh_user', 'conflict'),
        ('pause', 'not_found'),
        ('toggle', 'not_found'),
    ],
)
def test_non_dict_targets_preserve_rejected_outcomes_without_protected_writes(
    operation_state,
    operation,
    outcome,
):
    state = operation_state
    users = _read(state.paths['USERS_FILE'])
    users['alice'] = []
    _write_json(state.paths['USERS_FILE'], users)
    before = _snapshot_existing(state.paths)

    if operation in ('reset_user', 'refresh_user'):
        result = getattr(ss._traffic_mutation_service(state.audit), operation)(
            form={'user': ['alice']},
            expected_revision='',
        )
    elif operation == 'pause':
        result = ss._user_status_service(state.audit).pause(
            form={'user': ['alice']},
            expected_revision='',
        )
    else:
        result = ss._user_status_service(state.audit).toggle(
            form={'user': ['alice']},
            desired='disabled',
            expected_revision='',
        )

    assert result == OverviewMutationResult(outcome=outcome, username='alice')
    assert _snapshot_existing(state.paths) == before
    assert state.effects == [] and state.audits == []


def test_traffic_writes_sync_inside_lock_and_reloads_and_audit_outside(
    operation_state,
    monkeypatch,
):
    state = operation_state
    trace = []
    held = False
    real_lock = ss.usage_lock
    real_save = ss.save_json

    @contextmanager
    def tracked_lock():
        nonlocal held
        with real_lock():
            held = True
            trace.append('lock-enter')
            try:
                yield
            finally:
                held = False
                trace.append('lock-exit')

    def tracked_save(path, value):
        assert held
        trace.append(('save', Path(path).name))
        real_save(path, value)

    def tracked_sync(users, **_kwargs):
        assert held and users['alice']['marker'] == 'keep'
        trace.append('sync')
        return True, True

    monkeypatch.setattr(ss, 'usage_lock', tracked_lock)
    monkeypatch.setattr(ss, 'save_json', tracked_save)
    monkeypatch.setattr(ss, '_sync_static_access_from_users', tracked_sync)
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: trace.append(('xray', held)))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: trace.append(('tuic', held)))
    service = ss._traffic_mutation_service(lambda *_args: trace.append(('audit', held)))

    result = service.reset_user(
        form={'user': ['alice']},
        expected_revision=ss.user_config_revision(state.users['alice']),
    )

    assert result.outcome == 'success'
    assert trace[0] == 'lock-enter' and trace[-4:] == [
        'lock-exit',
        ('xray', False),
        ('tuic', False),
        ('audit', False),
    ]
    assert 'sync' in trace


@pytest.mark.parametrize(
    ('raw', 'minutes'),
    [('', 60), ('bad', 60), ('0', 1), ('1441', 1440), ('30', 30)],
)
def test_pause_uses_existing_default_and_clamped_minute_parser(operation_state, raw, minutes):
    state = operation_state
    revision = ss.user_config_revision(state.users['alice'])

    result = ss._user_status_service(state.audit).pause(
        form={'user': ['alice'], 'minutes': [raw]},
        expected_revision=revision,
    )

    expected = FIXED_NOW.replace(microsecond=0) + __import__('datetime').timedelta(minutes=minutes)
    assert result == OverviewMutationResult(
        outcome='success',
        username='alice',
        disabled_until=expected.isoformat(timespec='seconds'),
    )
    saved = _read(state.paths['USERS_FILE'])['alice']
    assert saved['disabled'] is True
    assert saved['disabled_until'] == result.disabled_until
    assert state.effects[-3:] == ['xray', 'tuic', ('kick', ['alice'])]


def test_pause_order_is_commit_sync_sessions_reloads_kick_then_audit(operation_state, monkeypatch):
    state = operation_state
    trace = []
    held = False
    real_lock = ss.usage_lock
    real_save = ss.save_json

    @contextmanager
    def tracked_lock():
        nonlocal held
        with real_lock():
            held = True
            trace.append('lock-enter')
            try:
                yield
            finally:
                held = False
                trace.append('lock-exit')

    def save(path, value):
        assert held
        trace.append('save')
        real_save(path, value)

    def sync(_users):
        assert held
        trace.append('sync')
        return True, True

    monkeypatch.setattr(ss, 'usage_lock', tracked_lock)
    monkeypatch.setattr(ss, 'save_json', save)
    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss, 'delete_user_sessions_for', lambda _u: trace.append(('sessions', held)))
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: trace.append(('xray', held)))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: trace.append(('tuic', held)))
    monkeypatch.setattr(ss, 'hy_kick', lambda _u: trace.append(('kick', held)))

    result = ss._user_status_service(lambda *_args: trace.append(('audit', held))).pause(
        form={'user': ['alice'], 'minutes': ['30']},
        expected_revision=ss.user_config_revision(state.users['alice']),
    )

    assert result.outcome == 'success'
    assert trace == [
        'lock-enter',
        'save',
        'sync',
        'lock-exit',
        ('sessions', False),
        ('xray', False),
        ('tuic', False),
        ('kick', False),
        ('audit', False),
    ]


@pytest.mark.parametrize(('desired', 'disabled'), [('disabled', True), ('enabled', False)])
def test_toggle_preserves_disable_enable_side_effect_difference(
    operation_state,
    desired,
    disabled,
):
    state = operation_state
    users = _read(state.paths['USERS_FILE'])
    users['alice']['disabled_until'] = '2026-09-20T00:00:00+08:00'
    _write_json(state.paths['USERS_FILE'], users)
    revision = ss.user_config_revision(users['alice'])

    result = ss._user_status_service(state.audit).toggle(
        form={'user': ['alice', 'bob']},
        desired=desired,
        expected_revision=revision,
    )

    assert result == OverviewMutationResult(outcome='success', username='alice')
    saved = _read(state.paths['USERS_FILE'])['alice']
    assert saved['disabled'] is disabled and 'disabled_until' not in saved
    expected_tail = (
        [('sync', _read(state.paths['USERS_FILE'])), 'xray', 'tuic', ('kick', ['alice'])]
        if disabled
        else [('sync', _read(state.paths['USERS_FILE'])), 'xray', 'tuic']
    )
    assert state.effects == expected_tail
    assert state.audits == [('disable_user' if disabled else 'enable_user', 'alice', {}, {})]


def test_invalid_desired_is_rejected_before_lock_or_write(operation_state, monkeypatch):
    state = operation_state
    before = state.paths['USERS_FILE'].read_bytes()
    monkeypatch.setattr(ss, 'usage_lock', lambda: pytest.fail('invalid desired must not lock'))

    result = ss._user_status_service(state.audit).toggle(
        form={'user': ['alice']},
        desired='banana',
        expected_revision='ignored',
    )

    assert result == OverviewMutationResult(outcome='invalid', code='invalid_desired')
    assert state.paths['USERS_FILE'].read_bytes() == before
    assert state.effects == [] and state.audits == []


@pytest.mark.parametrize(
    ('operation', 'username', 'revision', 'outcome'),
    [('pause', 'missing', 'ignored', 'not_found'), ('toggle', 'alice', 'stale', 'conflict')],
)
def test_rejected_status_operations_do_not_write_or_revoke(
    operation_state,
    operation,
    username,
    revision,
    outcome,
):
    state = operation_state
    before = state.paths['USERS_FILE'].read_bytes()
    service = ss._user_status_service(state.audit)
    if operation == 'pause':
        result = service.pause(form={'user': [username]}, expected_revision=revision)
    else:
        result = service.toggle(
            form={'user': [username]}, desired='disabled', expected_revision=revision
        )

    assert result == OverviewMutationResult(outcome=outcome, username=username)
    assert state.paths['USERS_FILE'].read_bytes() == before
    assert state.effects == [] and state.audits == []


@pytest.mark.parametrize('failure', ['sync', 'sessions', 'reload', 'kick', 'audit'])
def test_pause_failures_propagate_once_without_rollback(operation_state, monkeypatch, failure):
    state = operation_state
    revision = ss.user_config_revision(state.users['alice'])
    calls = []

    def maybe(name, result=None):
        calls.append(name)
        if name == failure:
            raise RuntimeError(name)
        return result

    monkeypatch.setattr(
        ss, '_sync_static_access_from_users', lambda _users: maybe('sync', (True, False))
    )
    monkeypatch.setattr(ss, 'delete_user_sessions_for', lambda _u: maybe('sessions'))
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: maybe('reload'))
    monkeypatch.setattr(ss, 'hy_kick', lambda _u: maybe('kick'))
    service = ss._user_status_service(lambda *_args: maybe('audit'))

    with pytest.raises(RuntimeError, match=failure):
        service.pause(
            form={'user': ['alice'], 'minutes': ['30']},
            expected_revision=revision,
        )

    saved = _read(state.paths['USERS_FILE'])['alice']
    if failure == 'sync':
        assert saved['disabled'] is True
    else:
        assert saved['disabled'] is True and saved['disabled_until']
    assert calls.count(failure) == 1


def test_admin_actor_and_log_helpers_preserve_legacy_query_token_and_cookie_semantics(
    operation_state,
    monkeypatch,
):
    session = {'sid': {'user': 'operator'}}
    monkeypatch.setattr(ss, 'get_sessions', lambda: session)
    logged = []
    monkeypatch.setattr(
        ss.audit_log, 'append_reset_log', lambda *args, **kwargs: logged.append((args, kwargs))
    )
    token_request = SimpleNamespace(
        path='/admin/reset-usage?token=legacy-token',
        headers={},
        client_address=('198.51.100.8', 1234),
    )
    cookie_request = SimpleNamespace(
        path='/api/v1/admin/operations/reset-usage',
        headers={'Cookie': 'sid=sid'},
        client_address=('198.51.100.9', 4321),
    )

    assert ss._admin_actor(token_request) == 'token-admin'
    assert ss._admin_actor(cookie_request) == 'operator'
    ss._write_reset_log(cookie_request, 'operator', 'reset_usage_user', 'alice', {}, {})

    assert logged[0][0][:6] == (
        ss.RESET_LOG_FILE,
        'operator',
        'reset_usage_user',
        'alice',
        {},
        {},
    )
    assert logged[0][1]['client_ip'] == '198.51.100.9'
