"""Concurrency and retry guarantees for the alert dedup state store."""
import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import alerts
import pytest
import state_store
import subscription_service as ss


def _mark_in_process(path, user):
    """Multiprocessing target: hold the transaction briefly to force overlap."""
    def mark(state):
        time.sleep(0.02)
        state['anomaly'][user] = '2026-07-18'

    alerts.mutate_state(mark, path)


class _SuccessOpener:
    def __init__(self):
        self.calls = 0

    def urlopen(self, _req, timeout=None):
        del timeout
        self.calls += 1

        class _Response:
            def read(self):
                return b''

        return _Response()


class _FailingOpener:
    def __init__(self):
        self.calls = 0

    def urlopen(self, _req, timeout=None):
        del timeout
        self.calls += 1
        raise OSError('transport unavailable')


def _quota_event():
    return {
        'kind': 'quota_80',
        'user': 'alice',
        'details': {
            'used_human': '8 GB',
            'total_human': '10 GB',
            'cycle': '2026-07',
        },
    }


def test_mutate_state_serializes_threads_without_lost_updates(tmp_path):
    path = tmp_path / 'alert_state.json'

    def add(user):
        def mutate(state):
            time.sleep(0.005)
            state['anomaly'][user] = '2026-07-18'

        alerts.mutate_state(mutate, path)

    users = [f'user-{i}' for i in range(12)]
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(add, users))

    assert set(alerts.load_state(path)['anomaly']) == set(users)


def test_mutate_state_serializes_processes_without_lost_updates(tmp_path):
    path = tmp_path / 'alert_state.json'
    ctx = multiprocessing.get_context('fork')
    users = [f'user-{i}' for i in range(6)]
    processes = [
        ctx.Process(target=_mark_in_process, args=(str(path), user))
        for user in users
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=5)
        assert process.exitcode == 0

    assert set(alerts.load_state(path)['anomaly']) == set(users)


def test_corrupt_state_is_never_overwritten_by_mutation(tmp_path):
    path = tmp_path / 'alert_state.json'
    original = b'{"quota_80":'
    path.write_bytes(original)
    called = False

    def should_not_run(_state):
        nonlocal called
        called = True

    with pytest.raises(state_store.InvalidJsonState):
        alerts.mutate_state(should_not_run, path)

    assert not called
    assert path.read_bytes() == original


def test_active_claim_suppresses_duplicate_and_expired_claim_retries(tmp_path):
    path = tmp_path / 'alert_state.json'
    first = alerts.claim_alert(
        'quota_80', 'alice', '2026-07', path=path, now=100, ttl=60
    )
    assert first
    assert alerts.claim_alert(
        'quota_80', 'alice', '2026-07', path=path, now=159, ttl=60
    ) is None

    replacement = alerts.claim_alert(
        'quota_80', 'alice', '2026-07', path=path, now=161, ttl=60
    )
    assert replacement and replacement != first
    assert not alerts.finish_alert_claim(
        'quota_80', 'alice', '2026-07', first,
        delivered=True, path=path,
    )
    assert alerts.finish_alert_claim(
        'quota_80', 'alice', '2026-07', replacement,
        delivered=True, path=path,
    )
    assert alerts.load_state(path)['quota_80']['alice'] == '2026-07'


def test_transport_failure_releases_claim_for_immediate_retry(tmp_path):
    path = tmp_path / 'alert_state.json'
    config = {'webhook': {'url': 'https://example.invalid/hook'}}
    failing = _FailingOpener()

    result = alerts.dispatch_once(
        _quota_event(), '2026-07',
        config=config, opener=failing, path=path,
    )

    assert result == {'attempted': ['webhook'], 'failed': ['webhook']}
    assert 'alice' not in alerts.load_state(path)['quota_80']

    success = _SuccessOpener()
    result = alerts.dispatch_once(
        _quota_event(), '2026-07',
        config=config, opener=success, path=path,
    )
    assert result == {'attempted': ['webhook'], 'failed': []}
    assert success.calls == 1
    assert alerts.load_state(path)['quota_80']['alice'] == '2026-07'


def test_unexpected_transport_exception_cannot_commit_false_success(
    tmp_path, caplog,
):
    path = tmp_path / 'alert_state.json'
    config = {'webhook': {'url': 'https://example.invalid/hook'}}

    class ExplodingOpener:
        def urlopen(self, _req, timeout=None):
            del timeout
            raise RuntimeError(
                'https://secret.example/hook?token=do-not-log'
            )

    result = alerts.dispatch_once(
        _quota_event(), '2026-07',
        config=config, opener=ExplodingOpener(), path=path,
    )

    assert result == {
        'attempted': ['webhook'],
        'failed': ['webhook'],
    }
    assert 'alice' not in alerts.load_state(path)['quota_80']
    assert 'do-not-log' not in caplog.text
    assert 'secret.example' not in caplog.text


def test_concurrent_dispatch_claims_once_and_network_runs_outside_lock(tmp_path):
    path = tmp_path / 'alert_state.json'
    config = {'webhook': {'url': 'https://example.invalid/hook'}}
    entered = threading.Event()
    release = threading.Event()
    calls = []

    class BlockingOpener:
        def urlopen(self, _req, timeout=None):
            del timeout
            calls.append('sent')
            entered.set()
            assert release.wait(timeout=3)

            class _Response:
                def read(self):
                    return b''

            return _Response()

    def dispatch():
        return alerts.dispatch_once(
            _quota_event(), '2026-07',
            config=config, opener=BlockingOpener(), path=path,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(dispatch)
        assert entered.wait(timeout=2)

        duplicate = pool.submit(dispatch)
        assert duplicate.result(timeout=2) == {
            'attempted': [], 'failed': [],
        }

        # This independent state transaction can commit while transport I/O is
        # blocked, proving no alert-state lock is held across the network call.
        other = pool.submit(
            alerts.mutate_state,
            lambda state: state.setdefault(
                'future_kind', {}
            ).__setitem__('bob', 'v1'),
            path,
        )
        other.result(timeout=2)

        release.set()
        assert first.result(timeout=2) == {
            'attempted': ['webhook'], 'failed': [],
        }

    assert calls == ['sent']
    state = alerts.load_state(path)
    assert state['quota_80']['alice'] == '2026-07'
    assert state['future_kind']['bob'] == 'v1'


def test_concurrent_clear_wins_over_stale_claim_completion(tmp_path):
    path = tmp_path / 'alert_state.json'
    token = alerts.claim_alert(
        'quota_80', 'alice', '2026-07', path=path, now=100
    )
    alerts.clear_quota_dedup_transaction(['alice'], path)

    assert not alerts.finish_alert_claim(
        'quota_80', 'alice', '2026-07', token,
        delivered=True, path=path,
    )
    assert 'alice' not in alerts.load_state(path)['quota_80']


def test_user_delete_transaction_preserves_concurrent_other_user(tmp_path):
    path = tmp_path / 'alert_state.json'
    alerts.save_state(
        {
            'quota_80': {'alice': '2026-07', 'bob': '2026-07'},
            'future_kind': {'alice': 'x', 'bob': 'y'},
        },
        path,
    )

    alerts.clear_user_dedup_transaction(['alice'], path)
    state = json.loads(path.read_text())
    assert all('alice' not in bucket for bucket in state.values())
    assert state['quota_80']['bob'] == '2026-07'
    assert state['future_kind']['bob'] == 'y'


def test_subscription_optional_update_preserves_corrupt_state(
    tmp_path, monkeypatch
):
    path = tmp_path / 'alert_state.json'
    original = b'not-json'
    path.write_bytes(original)
    monkeypatch.setattr(ss, 'USAGE_FILE', tmp_path / 'usage.json')
    monkeypatch.setattr(ss.alerts, 'STATE_FILE', path)

    assert not ss._clear_alert_dedup_for_users(
        ['alice'], quota_only=True
    )
    assert path.read_bytes() == original
