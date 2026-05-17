"""Regression tests for traffic_limiter's online-snapshot handling.

Background: a hysteria `/online` API hiccup (timeout, refused, parse error)
caused the previous code path `online = get("/online") or {}` followed by
`save_json(...)` to wipe online.json to `{}`. The cron tick runs every 5 s,
and admin_poll.js polls /admin/usage.json every 5 s, so even brief hiccups
flashed every device as offline across all UI surfaces. These tests pin the
fix: failure keeps the previous snapshot; success overwrites it.
"""
import json

import traffic_limiter as tl


def _patch_paths(tmp_path, monkeypatch):
    snap = tmp_path / "online.json"
    usage = tmp_path / "usage.json"
    usage_daily = tmp_path / "usage_daily.json"
    usage_hourly = tmp_path / "usage_hourly.json"
    users = tmp_path / "users.json"
    reset_state = tmp_path / "auto_reset_state.json"
    lock = tmp_path / "usage.lock"
    monkeypatch.setattr(tl, "ONLINE_SNAPSHOT_FILE", str(snap), raising=False)
    monkeypatch.setattr(tl, "USAGE_FILE", str(usage), raising=False)
    monkeypatch.setattr(tl, "USAGE_DAILY_FILE", str(usage_daily), raising=False)
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(usage_hourly), raising=False)
    monkeypatch.setattr(tl, "USERS_FILE", str(users), raising=False)
    monkeypatch.setattr(tl, "RESET_STATE_FILE", str(reset_state), raising=False)
    monkeypatch.setattr(tl, "USAGE_LOCK_FILE", str(lock), raising=False)
    users.write_text("{}")
    return snap


def _run_main_with_get(monkeypatch, get_results):
    """Drive tl.main() with a scripted sequence of get() return values.

    main() calls get() twice per tick: first /traffic, then /online. We pop
    from a list so each call gets the next scripted value.
    """
    results = list(get_results)

    def fake_get(path):
        return results.pop(0)

    monkeypatch.setattr(tl, "get", fake_get)
    monkeypatch.setattr(tl, "post", lambda *_a, **_k: True)
    monkeypatch.setattr(tl, "get_xray_traffic", lambda: {})
    monkeypatch.setattr(tl, "check_alerts", lambda *a, **k: None)

    import xray_config
    monkeypatch.setattr(xray_config, "sync_user", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "remove_user", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "apply_user_plan", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "reload_async", lambda: None, raising=False)

    tl.main()


def test_online_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    snap = _patch_paths(tmp_path, monkeypatch)
    # A previous successful tick wrote a real snapshot.
    snap.write_text(json.dumps({"alice": 2, "bob": 1}))

    # /traffic ok (empty), /online fails (None).
    _run_main_with_get(monkeypatch, [{}, None])

    assert json.loads(snap.read_text()) == {"alice": 2, "bob": 1}, (
        "an API hiccup must NOT wipe the previous online snapshot"
    )


def test_online_success_overwrites_snapshot(tmp_path, monkeypatch):
    snap = _patch_paths(tmp_path, monkeypatch)
    snap.write_text(json.dumps({"alice": 2}))

    _run_main_with_get(monkeypatch, [{}, {"alice": 1, "carol": 1}])

    assert json.loads(snap.read_text()) == {"alice": 1, "carol": 1}


def test_online_success_empty_is_persisted(tmp_path, monkeypatch):
    """A real `{}` response (no online users) must overwrite the snapshot —
    otherwise stale entries would linger after every user disconnects."""
    snap = _patch_paths(tmp_path, monkeypatch)
    snap.write_text(json.dumps({"alice": 2}))

    _run_main_with_get(monkeypatch, [{}, {}])

    assert json.loads(snap.read_text()) == {}
