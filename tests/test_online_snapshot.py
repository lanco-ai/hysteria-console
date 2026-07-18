"""Regression tests for traffic_limiter's online-snapshot handling.

Background: a hysteria `/online` API hiccup (timeout, refused, parse error)
caused the previous code path `online = get("/online") or {}` followed by
`save_json(...)` to wipe online.json to `{}`. The collector tick runs every
90 seconds, so even brief hiccups
flashed every device as offline across all UI surfaces. These tests pin the
fix: failure keeps the previous snapshot; success overwrites it.
"""
import json

import online_snapshot
import traffic_limiter as tl


def _patch_paths(tmp_path, monkeypatch):
    paths = {
        "ONLINE_SNAPSHOT_FILE": tmp_path / "online.json",
        "USAGE_FILE": tmp_path / "usage.json",
        "USAGE_DAILY_FILE": tmp_path / "usage_daily.json",
        "USAGE_HOURLY_FILE": tmp_path / "usage_hourly.json",
        "PROTOCOL_USAGE_HOURLY_FILE": tmp_path / "protocol_hourly.json",
        "USERS_FILE": tmp_path / "users.json",
        "META_FILE": tmp_path / "meta.json",
        "RESET_STATE_FILE": tmp_path / "auto_reset_state.json",
        "COST_CALIBRATION_FILE": tmp_path / "cost.json",
        "DISPLAY_MULTIPLIER_STATE_FILE": tmp_path / "display.json",
        "MULTIPLIER_AUTO_POLICY_FILE": tmp_path / "auto.json",
        "USAGE_LOCK_FILE": tmp_path / "usage.lock",
    }
    for name, path in paths.items():
        monkeypatch.setattr(tl, name, str(path), raising=False)
    monkeypatch.setattr(
        tl.tuic_meter, "STATE_FILE", str(tmp_path / "tuic-meter.json")
    )
    paths["USERS_FILE"].write_text("{}")
    paths["META_FILE"].write_text('{"settlement_day":1}')
    paths["USAGE_FILE"].write_text("{}")
    paths["USAGE_DAILY_FILE"].write_text("{}")
    return paths["ONLINE_SNAPSHOT_FILE"]


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
    monkeypatch.setattr(tl, "get_tuic_traffic", lambda: {})
    monkeypatch.setattr(tl, "check_alerts", lambda *a, **k: None)

    import xray_config
    monkeypatch.setattr(xray_config, "sync_user", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "remove_user", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "apply_user_plan", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(xray_config, "reload_async", lambda: None, raising=False)
    monkeypatch.setattr(
        tl.tuic_config, "sync_user_plan", lambda *a, **k: False, raising=False
    )
    monkeypatch.setattr(
        tl.tuic_config, "reload_async", lambda: None, raising=False
    )

    tl.main()


def test_online_failure_preserves_previous_snapshot(tmp_path, monkeypatch):
    snap = _patch_paths(tmp_path, monkeypatch)
    # A previous successful tick wrote a real snapshot.
    previous = {"alice": 2, "bob": 1}
    snap.write_text(json.dumps(previous))
    meta = tmp_path / "online.meta.json"
    previous_meta = online_snapshot.build_metadata(
        previous, captured_at=100.0
    )
    meta.write_text(json.dumps(previous_meta))

    # /traffic ok (empty), /online fails (None).
    _run_main_with_get(monkeypatch, [{}, None])

    assert json.loads(snap.read_text()) == {"alice": 2, "bob": 1}, (
        "an API hiccup must NOT wipe the previous online snapshot"
    )
    assert json.loads(meta.read_text()) == previous_meta


def test_online_success_overwrites_snapshot(tmp_path, monkeypatch):
    snap = _patch_paths(tmp_path, monkeypatch)
    snap.write_text(json.dumps({"alice": 2}))
    monkeypatch.setattr(tl.time, "time", lambda: 1_234.5)

    _run_main_with_get(monkeypatch, [{}, {"alice": 1, "carol": 1}])

    current = {"alice": 1, "carol": 1}
    assert json.loads(snap.read_text()) == current
    metadata = json.loads((tmp_path / "online.meta.json").read_text())
    assert metadata == online_snapshot.build_metadata(
        current, captured_at=1_234.5
    )


def test_online_success_empty_is_persisted(tmp_path, monkeypatch):
    """A real `{}` response (no online users) must overwrite the snapshot —
    otherwise stale entries would linger after every user disconnects."""
    snap = _patch_paths(tmp_path, monkeypatch)
    snap.write_text(json.dumps({"alice": 2}))

    _run_main_with_get(monkeypatch, [{}, {}])

    assert json.loads(snap.read_text()) == {}
    metadata = json.loads((tmp_path / "online.meta.json").read_text())
    online_snapshot.validate_fresh_snapshot(
        {},
        metadata,
        now=metadata["captured_at_unix"],
        ttl_seconds=20.0,
    )
