"""Password verification reservations remain bounded before hashing."""

import threading


def test_inflight_attempts_reserve_slots_and_release():
    from login_throttle import LoginThrottle

    failures = {}
    inflight = {}
    throttle = LoginThrottle(failures, inflight, threading.Lock(), lambda: 100, 3, 3600, 2)
    assert all(throttle._begin_login_attempt('client') for _ in range(3))
    assert not throttle._begin_login_attempt('client')
    throttle._finish_login_attempt('client', None)
    assert throttle._begin_login_attempt('client')
    for _ in range(3):
        throttle._finish_login_attempt('client', False)
    assert inflight == {}
    assert throttle._is_rate_limited('client')


def test_failure_tracking_evicts_oldest_source():
    from login_throttle import LoginThrottle

    failures = {}
    throttle = LoginThrottle(failures, {}, threading.Lock(), lambda: 100, 3, 3600, 2)
    for client in ('old', 'second', 'new'):
        throttle._record_failure(client)
    assert set(failures) == {'second', 'new'}
