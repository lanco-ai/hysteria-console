"""Cumulative TUIC accounting: strict observations and replay-safe credits."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import state_store


def meter():
    import tuic_user_meter

    return tuic_user_meter


def sample(alice=10, bob=20):
    return {
        'stat': [
            {'name': 'user>>>alice>>>traffic>>>uplink', 'value': str(alice)},
            {'name': 'user>>>bob>>>traffic>>>downlink', 'value': str(bob)},
        ]
    }


def test_authenticated_direction_mapping_and_valid_empty():
    m = meter()
    assert m.parse_stats(sample(), {'alice', 'bob'}) == {
        'alice': {'rx': 10, 'tx': 0},
        'bob': {'rx': 0, 'tx': 20},
    }
    assert m.parse_stats({}, {'alice'}) == {}


@pytest.mark.parametrize(
    'data',
    [
        {'error': 'unavailable'},
        {'stat': None},
        {'stat': {}},
        {'stat': [{'name': 'user>>>mallory>>>traffic>>>uplink', 'value': 1}]},
        {'stat': [{'name': 'user>>>alice>>>traffic>>>uplink', 'value': -1}]},
        {'stat': [{'name': 'user>>>alice>>>traffic>>>uplink', 'value': True}]},
        {'stat': [{'name': 'user>>>alice>>>traffic>>>uplink', 'value': '1.5'}]},
        {'stat': sample()['stat'] * 2},
    ],
)
def test_invalid_observation_is_never_empty_success(data):
    with pytest.raises(state_store.StateStoreError):
        meter().parse_stats(data, {'alice', 'bob'})


def setup_store(tmp_path):
    m = meter()
    daily = tmp_path / 'usage_daily.json'
    usage = tmp_path / 'usage.json'
    state_store.save_json(daily, {})
    state_store.save_json(usage, {})
    m.initialize(tmp_path, activated_at='2026-10-02T12:00:00+08:00')
    return m, daily, usage


def credit(m, daily, usage, generation='a', counters=None, users=None):
    return m.credit_locked(
        daily_path=daily,
        usage_path=usage,
        now=datetime(2026, 10, 2, 12, tzinfo=ZoneInfo('Asia/Shanghai')),
        month_key='2026-09',
        generation=generation,
        counters=counters or {'alice': {'rx': 10, 'tx': 20}},
        users={'alice': {}} if users is None else users,
        identities={'alice': 'a' * 64},
    )


def test_repeated_sampling_and_restart_credit_once(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    assert credit(m, daily, usage)['alice'] == {'rx': 10, 'tx': 20}
    assert credit(m, daily, usage) == {}
    credit(m, daily, usage, generation='b')
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 60
    assert json.loads(usage.read_text())['2026-09']['alice']['total'] == 60


def test_decrease_within_generation_fails_without_mutation(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    credit(m, daily, usage)
    before = daily.read_bytes()
    with pytest.raises(state_store.StateStoreError):
        credit(m, daily, usage, counters={'alice': {'rx': 9, 'tx': 20}})
    assert daily.read_bytes() == before


@pytest.mark.parametrize('boundary', [1, 2, 3, 4])
@pytest.mark.parametrize('after_replace', [False, True])
def test_interrupted_transaction_recovers_exactly_once(
    tmp_path, monkeypatch, boundary, after_replace
):
    m, daily, usage = setup_store(tmp_path)
    real_save = state_store.save_json
    calls = 0

    def fail(path, data):
        nonlocal calls
        calls += 1
        if calls == boundary:
            if after_replace:
                real_save(path, data)
                raise state_store.AtomicReplaceDurabilityUncertain(path)
            raise OSError('fixture write failure')
        return real_save(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, 'save_json', fail)
        with pytest.raises((OSError, state_store.StateStoreError)):
            credit(m, daily, usage)
    m.recover_locked(daily, usage)
    credit(m, daily, usage)
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 30
    assert json.loads(usage.read_text())['2026-09']['alice']['total'] == 30


def test_manual_zero_does_not_reset_source_watermark(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    credit(m, daily, usage)
    m.recover_locked(daily, usage)
    state_store.save_json(daily, {})
    state_store.save_json(usage, {})
    assert credit(m, daily, usage) == {}
    assert json.loads(daily.read_text()) == {}
    credit(m, daily, usage, counters={'alice': {'rx': 11, 'tx': 23}})
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 4


def test_deleted_identity_drained_without_resurrection(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    credit(m, daily, usage)
    state_store.save_json(daily, {})
    credit(m, daily, usage, users={}, counters={'alice': {'rx': 11, 'tx': 23}})
    assert json.loads(daily.read_text()) == {}


def test_missing_checkpoint_is_not_silent_reinitialization(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    (tmp_path / 'tuic_user_state.json').unlink()
    with pytest.raises(state_store.StateStoreError):
        credit(m, daily, usage)


@pytest.mark.parametrize(
    'endpoint',
    [
        '0.0.0.0:10086',
        '[::]:10086',
        'localhost:10086',
        '127.0.0.1:10085',
        '127.0.0.1:0',
        '127.0.0.1:99999',
    ],
)
def test_statistics_endpoint_must_be_dedicated_literal_loopback(endpoint):
    with pytest.raises(state_store.StateStoreError):
        meter().validate_endpoint(endpoint)


def test_query_is_nonreset_and_checks_generation_on_both_sides(monkeypatch):
    m = meter()
    commands = []
    generations = iter(['boot:a:1', 'boot:a:1'])
    monkeypatch.setattr(m, 'runtime_generation', lambda: next(generations))

    def run(command, **kwargs):
        commands.append(command)
        return json.dumps(sample()).encode()

    monkeypatch.setattr(m, 'bounded_command', run)
    generation, counters = m.observe('127.0.0.1:10086', {'alice', 'bob'})
    assert generation == 'boot:a:1'
    assert counters['alice']['rx'] == 10
    assert commands == [
        [
            '/usr/local/bin/xray',
            'api',
            'statsquery',
            '--server=127.0.0.1:10086',
            '-pattern',
            'user>>>',
        ]
    ]


def test_generation_change_during_query_is_rejected(monkeypatch):
    m = meter()
    generations = iter(['a', 'b'])
    monkeypatch.setattr(m, 'runtime_generation', lambda: next(generations))
    monkeypatch.setattr(m, 'bounded_command', lambda *a, **k: b'{}')
    with pytest.raises(state_store.StateStoreError):
        m.observe('127.0.0.1:10086', {'alice'})


def test_command_output_and_runtime_are_bounded():
    import sys

    m = meter()
    with pytest.raises(state_store.StateStoreError):
        m.bounded_command([sys.executable, '-c', 'print("x" * 50000)'], limit=1024)
    with pytest.raises(state_store.StateStoreError):
        m.bounded_command([sys.executable, '-c', 'import time; time.sleep(5)'], timeout=0.05)
    with pytest.raises(state_store.StateStoreError):
        m.bounded_command([sys.executable, '-c', 'raise SystemExit(1)'])


def test_recovery_precedes_panel_mutation(tmp_path, monkeypatch):
    import subscription_service as panel

    m, daily, usage = setup_store(tmp_path)
    original = state_store.save_json

    def fail(path, data):
        if str(path) == str(usage):
            raise OSError('fixture failure')
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, 'save_json', fail)
        with pytest.raises(OSError):
            credit(m, daily, usage)
    monkeypatch.setattr(panel, 'USAGE_LOCK_FILE', tmp_path / 'usage.lock')
    monkeypatch.setattr(panel, 'USAGE_FILE', usage)
    monkeypatch.setattr(panel, 'USAGE_DAILY_FILE', daily)
    with panel.usage_lock():
        assert json.loads(usage.read_text())['2026-09']['alice']['total'] == 30
        state_store.save_json(daily, {})
        state_store.save_json(usage, {})
    m.recover_locked(daily, usage)
    assert json.loads(daily.read_text()) == {}


def test_concurrent_collectors_share_canonical_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    m, daily, usage = setup_store(tmp_path)

    def collect(_):
        with state_store.file_lock(tmp_path / 'usage.lock'):
            return credit(m, daily, usage)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(collect, range(12)))
    assert sum(bool(result) for result in results) == 1
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 30


@pytest.mark.parametrize('value', [None, [], 1, 'invalid'])
def test_corrupt_checkpoint_schema_raises_state_error(tmp_path, value):
    m, daily, usage = setup_store(tmp_path)
    state_store.save_json(tmp_path / 'tuic_user_state.json', value)
    with pytest.raises(state_store.StateStoreError):
        credit(m, daily, usage)


def test_next_shanghai_day_only_credits_new_bytes(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    credit(m, daily, usage)
    m.credit_locked(
        daily_path=daily,
        usage_path=usage,
        now=datetime(2026, 10, 3, 0, tzinfo=ZoneInfo('Asia/Shanghai')),
        month_key='2026-10',
        generation='a',
        counters={'alice': {'rx': 11, 'tx': 23}},
        users={'alice': {}},
        identities={'alice': 'a' * 64},
    )
    actual = json.loads(daily.read_text())
    assert actual['2026-10-02']['alice']['total'] == 30
    assert actual['2026-10-03']['alice']['total'] == 4


def test_pending_replay_rejects_intervening_ledger_mutation(tmp_path, monkeypatch):
    m, daily, usage = setup_store(tmp_path)
    original = state_store.save_json

    def fail(path, data):
        if str(path) == str(usage):
            raise OSError('fixture failure')
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, 'save_json', fail)
        with pytest.raises(OSError):
            credit(m, daily, usage)
    state_store.save_json(daily, {'2026-10-02': {'alice': {'tx': 999, 'rx': 0, 'total': 999}}})
    with pytest.raises(state_store.StateStoreError, match='conflicts'):
        m.recover_locked(daily, usage)
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 999


def test_no_pending_journal_leaves_legacy_scoped_paths_untouched(tmp_path):
    m = meter()
    m.recover_locked(tmp_path / 'daily' / 'usage_daily.json', tmp_path / 'cycle' / 'usage.json')
    assert list(tmp_path.iterdir()) == []


def test_actual_pending_journal_still_requires_same_state_directory(tmp_path):
    m = meter()
    (tmp_path / m.PENDING_NAME).write_text('{}')
    with pytest.raises(state_store.StateStoreError, match='share a state directory'):
        m.recover_locked(tmp_path / 'daily.json', tmp_path / 'other' / 'usage.json')


def test_activation_and_explicit_rollback_leave_checkpoint_inert(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    assert m.accounting_mode(tmp_path) == 'active'
    m.deactivate_locked(daily, usage)
    assert m.accounting_mode(tmp_path) == 'legacy'
    assert (tmp_path / m.STATE_NAME).exists()
    (tmp_path / m.MODE_NAME).unlink()
    with pytest.raises(state_store.StateStoreError):
        m.accounting_mode(tmp_path)


def test_generation_identity_binding_drains_deleted_recreated_name(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    old = m.credential_id('alice', 'uuid-old', 'alice:old')
    new = m.credential_id('alice', 'uuid-new', 'alice:new')
    kwargs = dict(
        daily_path=daily,
        usage_path=usage,
        now=datetime(2026, 10, 2, 12, tzinfo=ZoneInfo('Asia/Shanghai')),
        month_key='2026-09',
        generation='old-G',
    )
    m.credit_locked(
        **kwargs,
        counters={'alice': {'rx': 10, 'tx': 20}},
        users={'alice': {}},
        identities={'alice': old},
    )
    state_store.save_json(daily, {})  # delete removes the old owner's ledger
    state_store.save_json(usage, {})
    checkpoint = m.load_state(tmp_path)
    counters = {'alice': {'rx': 30, 'tx': 40}}
    # Config is already new Alice; the asynchronous worker has not restarted G.
    owners, binding = m.bind_observation(
        checkpoint,
        'old-G',
        counters,
        runtime_identities={'alice': new},
        canonical_identities={'alice': new},
        pending=True,
    )
    assert owners == set()
    assert binding == {'alice': old}
    m.credit_locked(**kwargs, counters=counters, users={}, identities=binding)
    assert json.loads(daily.read_text()) == {}
    assert m.load_state(tmp_path)['counters'] == counters
    # A changed generation with an unacknowledged config is not yet attributable.
    assert (
        m.bind_observation(
            m.load_state(tmp_path),
            'new-G',
            {},
            runtime_identities={'alice': new},
            canonical_identities={'alice': new},
            pending=True,
        )
        is None
    )
    owners, binding = m.bind_observation(
        m.load_state(tmp_path),
        'new-G',
        {'alice': {'rx': 2, 'tx': 3}},
        runtime_identities={'alice': new},
        canonical_identities={'alice': new},
        pending=False,
    )
    kwargs['generation'] = 'new-G'
    m.credit_locked(
        **kwargs,
        counters={'alice': {'rx': 2, 'tx': 3}},
        users={name: {} for name in owners},
        identities=binding,
    )
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 5


def test_changed_identity_without_reload_ack_cannot_rebind_same_generation(tmp_path):
    m, daily, usage = setup_store(tmp_path)
    credit(m, daily, usage)
    checkpoint = m.load_state(tmp_path)
    with pytest.raises(state_store.StateStoreError):
        m.bind_observation(
            checkpoint,
            'a',
            {'alice': {'rx': 11, 'tx': 21}},
            runtime_identities={'alice': 'b' * 64},
            canonical_identities={'alice': 'b' * 64},
            pending=False,
        )


def test_wait_survives_process_restart_and_boot_change(tmp_path):
    import os
    import subprocess
    import sys

    m, daily, usage = setup_store(tmp_path)
    first = {'boot': 'boot-a', 'wall': 100.0, 'monotonic': 30.0}
    m.begin_reload_wait(tmp_path, m.load_state(tmp_path), first, clock=first)
    code = """import sys,tuic_user_meter as m
try:
 m.check_reload_wait(m.load_state(sys.argv[1]), clock={'boot':'boot-b','wall':220.,'monotonic':1.})
except m.state_store.StateStoreError:
 raise SystemExit(7)
"""
    result = subprocess.run(
        [sys.executable, '-c', code, str(tmp_path)],
        env={**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path(m.__file__).parent)},
    )
    assert result.returncode == 7


@pytest.mark.parametrize(
    'clock',
    [
        {'boot': 'boot-a', 'wall': 99.0, 'monotonic': 40.0},
        {'boot': 'boot-a', 'wall': 110.0, 'monotonic': 29.0},
        {'boot': 'boot-b', 'wall': 99.0, 'monotonic': 1.0},
        {'boot': 'boot-b', 'wall': float('nan'), 'monotonic': 1.0},
    ],
)
def test_wait_clock_anomalies_fail_closed(tmp_path, clock):
    m, daily, usage = setup_store(tmp_path)
    first = {'boot': 'boot-a', 'wall': 100.0, 'monotonic': 30.0}
    state = m.begin_reload_wait(tmp_path, m.load_state(tmp_path), first, clock=first)
    with pytest.raises(state_store.StateStoreError):
        m.check_reload_wait(state, clock=clock)


def test_wait_clear_recovers_in_same_transaction_as_acked_credit(tmp_path, monkeypatch):
    m, daily, usage = setup_store(tmp_path)
    first = {'boot': 'fixture', 'wall': 100.0, 'monotonic': 100.0}
    m.begin_reload_wait(tmp_path, m.load_state(tmp_path), first, clock=first)
    original = state_store.save_json

    def fail(path, data):
        if str(path) == str(usage):
            raise OSError('interrupted accounting commit')
        original(path, data)

    with monkeypatch.context() as patch:
        patch.setattr(state_store, 'save_json', fail)
        with pytest.raises(OSError):
            m.credit_locked(
                daily_path=daily,
                usage_path=usage,
                now=datetime(2026, 10, 2, 12, tzinfo=ZoneInfo('Asia/Shanghai')),
                month_key='2026-09',
                generation='new-G',
                counters={'alice': {'rx': 10, 'tx': 20}},
                users={'alice': {}},
                identities={'alice': 'a' * 64},
                clear_reload_wait=True,
            )
    assert m.load_state(tmp_path)['reload_wait'] == first
    m.recover_locked(daily, usage)
    assert m.load_state(tmp_path)['reload_wait'] is None
    assert json.loads(daily.read_text())['2026-10-02']['alice']['total'] == 30
    assert credit(m, daily, usage, generation='new-G') == {}
