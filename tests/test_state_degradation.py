import json
from datetime import datetime
from pathlib import Path

import pytest

import state_store
import traffic_limiter as tl


def _configure_limiter_state(tmp_path, monkeypatch):
    paths = {
        "USERS_FILE": tmp_path / "users.json",
        "META_FILE": tmp_path / "meta.json",
        "USAGE_FILE": tmp_path / "usage.json",
        "USAGE_DAILY_FILE": tmp_path / "usage_daily.json",
        "USAGE_HOURLY_FILE": tmp_path / "usage_hourly.json",
        "PROTOCOL_USAGE_HOURLY_FILE": tmp_path / "protocol_hourly.json",
        "ONLINE_SNAPSHOT_FILE": tmp_path / "online.json",
        "RESET_STATE_FILE": tmp_path / "reset.json",
        "COST_CALIBRATION_FILE": tmp_path / "cost.json",
        "DISPLAY_MULTIPLIER_STATE_FILE": tmp_path / "display.json",
        "MULTIPLIER_AUTO_POLICY_FILE": tmp_path / "auto.json",
        "USAGE_LOCK_FILE": tmp_path / "usage.lock",
    }
    for name, path in paths.items():
        monkeypatch.setattr(tl, name, str(path), raising=False)
    meter_state = tmp_path / "tuic-meter.json"
    monkeypatch.setattr(tl.tuic_meter, "STATE_FILE", str(meter_state))
    paths["TUIC_METER_STATE_FILE"] = meter_state

    paths["USERS_FILE"].write_text(
        json.dumps({
            "alice": {
                "vless_uuid": "11111111-1111-4111-8111-111111111111",
                "monthly_quota_bytes": 1024 ** 3,
                "metered": True,
            },
        }),
        encoding="utf-8",
    )
    paths["META_FILE"].write_text(
        json.dumps({"settlement_day": 1}),
        encoding="utf-8",
    )
    paths["USAGE_FILE"].write_text("{}", encoding="utf-8")
    paths["USAGE_DAILY_FILE"].write_text("{}", encoding="utf-8")
    return paths


def test_missing_core_state_blocks_all_destructive_collection(
    tmp_path, monkeypatch
):
    paths = _configure_limiter_state(tmp_path, monkeypatch)
    paths["USAGE_FILE"].unlink()
    calls = []
    monkeypatch.setattr(tl, "get", lambda path: calls.append(path) or {})
    monkeypatch.setattr(
        tl, "get_xray_traffic", lambda: calls.append("xray-reset") or {}
    )
    monkeypatch.setattr(
        tl, "get_tuic_traffic", lambda: calls.append("tuic-meter") or {}
    )

    with pytest.raises(state_store.InvalidJsonState, match="missing required"):
        tl.main()

    assert calls == []
    assert not paths["USAGE_FILE"].exists()


def test_corrupt_auxiliary_state_degrades_without_freezing_accounting(
    tmp_path, monkeypatch
):
    paths = _configure_limiter_state(tmp_path, monkeypatch)
    auxiliary = (
        "USAGE_HOURLY_FILE",
        "PROTOCOL_USAGE_HOURLY_FILE",
        "ONLINE_SNAPSHOT_FILE",
        "COST_CALIBRATION_FILE",
        "MULTIPLIER_AUTO_POLICY_FILE",
        "TUIC_METER_STATE_FILE",
    )
    for name in auxiliary:
        paths[name].write_text('{"broken":', encoding="utf-8")

    now = datetime(2026, 7, 3, 12, 0, 0)
    monkeypatch.setattr(tl, "local_now", lambda: now)
    calls = []

    def fake_get(path):
        calls.append(path)
        if path == "/traffic?clear=1":
            return {"alice": {"tx": 1, "rx": 2}}
        if path == "/online":
            return {"alice": 1}
        raise AssertionError(path)

    monkeypatch.setattr(tl, "get", fake_get)
    monkeypatch.setattr(
        tl, "get_xray_traffic", lambda: calls.append("xray-reset") or {}
    )
    monkeypatch.setattr(
        tl,
        "get_tuic_traffic",
        lambda: (_ for _ in ()).throw(
            AssertionError("corrupt TUIC baseline must not be read")
        ),
    )
    monkeypatch.setattr(tl, "check_alerts", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        tl.xray_config, "apply_user_plan", lambda _plan: False
    )
    monkeypatch.setattr(
        tl.tuic_config, "sync_user_plan", lambda _users, _plan: False
    )
    monkeypatch.setattr(
        tl.cost_calibrator,
        "update_sample",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("corrupt calibration state must not be overwritten")
        ),
    )

    tl.main()

    assert calls == ["/traffic?clear=1", "xray-reset", "/online"]
    usage = json.loads(paths["USAGE_FILE"].read_text(encoding="utf-8"))
    assert next(iter(usage.values()))["alice"]["total"] == 3
    daily = json.loads(paths["USAGE_DAILY_FILE"].read_text(encoding="utf-8"))
    assert daily["2026-07-03"]["alice"]["total"] == 3
    for name in auxiliary:
        assert paths[name].read_text(encoding="utf-8") == '{"broken":'
