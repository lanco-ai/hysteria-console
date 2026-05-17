"""_login_failures must stay bounded under hostile-IP traffic.

Two invariants:
  - When an IP's timestamps decay below the rate-limit window, its dict
    entry is removed (no zero-length lists lingering forever).
  - The dict size never exceeds _LOGIN_FAILURES_MAX_IPS; once full,
    recording a new IP evicts the oldest tracked one (FIFO via dict
    insertion order).
"""
import subscription_service as ss


def _reset():
    ss._login_failures.clear()


def test_empty_entry_is_removed_when_window_decays(monkeypatch):
    _reset()
    fake_now = [1000.0]
    monkeypatch.setattr(ss.time, "time", lambda: fake_now[0])

    ss._record_failure("1.1.1.1")
    assert "1.1.1.1" in ss._login_failures

    # Jump past the window.
    fake_now[0] = 1000.0 + ss._LOGIN_WINDOW + 1
    ss._is_rate_limited("1.1.1.1")
    assert "1.1.1.1" not in ss._login_failures, (
        "empty timestamp lists must not linger in the failures dict"
    )


def test_dict_size_is_capped_with_fifo_eviction(monkeypatch):
    _reset()
    monkeypatch.setattr(ss, "_LOGIN_FAILURES_MAX_IPS", 4, raising=False)
    fake_now = [1000.0]
    monkeypatch.setattr(ss.time, "time", lambda: fake_now[0])

    for i in range(4):
        ss._record_failure(f"10.0.0.{i}")
        fake_now[0] += 1  # keep timestamps within window

    assert list(ss._login_failures.keys()) == [f"10.0.0.{i}" for i in range(4)]
    assert len(ss._login_failures) == 4

    # 5th IP should evict the oldest (10.0.0.0).
    ss._record_failure("10.0.0.99")
    assert "10.0.0.0" not in ss._login_failures
    assert "10.0.0.99" in ss._login_failures
    assert len(ss._login_failures) == 4
