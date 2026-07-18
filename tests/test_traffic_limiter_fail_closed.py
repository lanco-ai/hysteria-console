"""Transaction-order and fail-closed regressions for traffic_limiter."""

from contextlib import contextmanager
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import state_store
import traffic_limiter as tl


def _write(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def _configure_state(tmp_path, monkeypatch, *, users=None, daily=None):
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
    monkeypatch.setattr(
        tl.tuic_meter, "STATE_FILE", str(tmp_path / "tuic-meter.json")
    )
    monkeypatch.setattr(
        tl._alerts, "CONFIG_FILE", tmp_path / "alerts.json"
    )
    monkeypatch.setattr(
        tl._alerts, "STATE_FILE", tmp_path / "alert-state.json"
    )

    _write(
        paths["USERS_FILE"],
        users
        or {
            "alice": {
                "vless_uuid": "11111111-1111-4111-8111-111111111111",
                "metered": True,
                "monthly_quota_bytes": 1024,
            }
        },
    )
    _write(
        paths["META_FILE"],
        {
            "settlement_day": 1,
            "cycle_length_days": 30,
            "cycle_anchor_date": "2026-07-01",
        },
    )
    _write(paths["USAGE_FILE"], {})
    _write(paths["USAGE_DAILY_FILE"], daily or {})
    return paths


@pytest.mark.parametrize(
    ("target", "bad_value"),
    [
        ("USERS_FILE", {"alice": []}),
        (
            "USERS_FILE",
            {"alice": {"monthly_quota_bytes": -1}},
        ),
        ("META_FILE", {"settlement_day": "tomorrow"}),
        (
            "USAGE_FILE",
            {"2026-07": {"alice": {"tx": -1, "rx": 0, "total": 0}}},
        ),
        (
            "USAGE_FILE",
            {"2026-07": {"alice": {"tx": True, "rx": 0, "total": 1}}},
        ),
        (
            "USAGE_DAILY_FILE",
            {"2026-07-03": {"alice": ["not", "usage"]}},
        ),
        ("RESET_STATE_FILE", {"last_reset_month": []}),
    ],
)
def test_deep_core_schema_fails_before_destructive_counters(
    tmp_path, monkeypatch, target, bad_value
):
    paths = _configure_state(tmp_path, monkeypatch)
    _write(paths[target], bad_value)
    original = paths[target].read_bytes()
    calls = []
    monkeypatch.setattr(
        tl, "get", lambda path: calls.append(path) or {}
    )
    monkeypatch.setattr(
        tl,
        "get_xray_traffic",
        lambda: calls.append("xray-reset") or {},
    )
    monkeypatch.setattr(
        tl,
        "get_tuic_traffic",
        lambda: calls.append("tuic-baseline") or {},
    )
    monkeypatch.setattr(
        tl, "local_now", lambda: datetime(2026, 7, 3, 12, 0, 0)
    )

    with pytest.raises(state_store.InvalidJsonState):
        tl.main()

    assert calls == []
    assert paths[target].read_bytes() == original


def test_legacy_integer_strings_are_valid_but_negative_and_bool_are_not():
    tl._validate_usage_ledger(
        {
            "2026-07": {
                "alice": {"tx": "1", "rx": "2", "total": "3"},
                "bob": "4",
            }
        },
        path="usage.json",
    )
    with pytest.raises(state_store.InvalidJsonState):
        tl._validate_usage_ledger(
            {"2026-07": {"alice": True}},
            path="usage.json",
        )


def test_static_removal_precedes_and_survives_every_auxiliary_failure(
    tmp_path, monkeypatch
):
    paths = _configure_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "vless_uuid": "11111111-1111-4111-8111-111111111111",
                "metered": True,
                "monthly_quota_bytes": 1,
            },
            "expired": {
                "vless_uuid": "22222222-2222-4222-8222-222222222222",
                "expires_at": "2026-07-02",
            },
        },
    )
    # Syntactically valid JSON with a bad nested hourly bucket: the hourly
    # feature must leave it byte-for-byte intact when it fails at runtime.
    bad_hourly = '{"2026-07-03T12":[]}'
    paths["USAGE_HOURLY_FILE"].write_text(
        bad_hourly, encoding="utf-8"
    )
    events = []

    def fake_get(path):
        events.append(path)
        if path == "/traffic?clear=1":
            return {"alice": {"tx": 1, "rx": 0}}
        if path == "/online":
            raise RuntimeError("online API down")
        raise AssertionError(path)

    def fake_xray_plan(plan, *, prune_unknown=False, path=None, **_kwargs):
        events.append(("xray-plan", dict(plan), prune_unknown, path))
        return False

    def fake_tuic_plan(users, plan, *, path=None, **_kwargs):
        events.append(("tuic-plan", dict(plan), path))
        return False

    monkeypatch.setattr(tl, "local_now", lambda: datetime(2026, 7, 3, 12))
    monkeypatch.setattr(tl, "get", fake_get)
    monkeypatch.setattr(
        tl, "get_xray_traffic", lambda: events.append("xray-reset") or {}
    )
    monkeypatch.setattr(tl, "get_tuic_traffic", lambda: {})
    monkeypatch.setattr(tl.xray_config, "apply_user_plan", fake_xray_plan)
    monkeypatch.setattr(tl.tuic_config, "sync_user_plan", fake_tuic_plan)
    monkeypatch.setattr(
        tl,
        "post",
        lambda path, body: events.append((path, list(body))) or True,
    )
    monkeypatch.setattr(
        tl,
        "accumulate_protocol_hourly",
        lambda *_args, **_kwargs: events.append("protocol")
        or (_ for _ in ()).throw(RuntimeError("protocol disk error")),
    )
    monkeypatch.setattr(
        tl.cost_calibrator,
        "update_sample",
        lambda *_args, **_kwargs: events.append("calibration")
        or (_ for _ in ()).throw(RuntimeError("netdev error")),
    )
    monkeypatch.setattr(
        tl,
        "check_alerts",
        lambda *_args, **_kwargs: events.append("alerts")
        or (_ for _ in ()).throw(RuntimeError("webhook state error")),
    )

    tl.main()

    xray_event = next(item for item in events if isinstance(item, tuple) and item[0] == "xray-plan")
    assert xray_event[1] == {"alice": None, "expired": None}
    assert xray_event[2] is True
    assert xray_event[3] == tmp_path / "xray.json"
    tuic_event = next(item for item in events if isinstance(item, tuple) and item[0] == "tuic-plan")
    assert tuic_event[1] == {"alice": None, "expired": None}
    assert tuic_event[2] == tmp_path / "tuic.json"
    kick_event = next(item for item in events if isinstance(item, tuple) and item[0] == "/kick")
    assert set(kick_event[1]) == {"alice", "expired"}

    plan_index = events.index(xray_event)
    assert plan_index < events.index(kick_event)
    assert plan_index < events.index("protocol")
    assert plan_index < events.index("calibration")
    assert plan_index < events.index("/online")
    assert plan_index < events.index("alerts")
    assert paths["USAGE_HOURLY_FILE"].read_text(encoding="utf-8") == bad_hourly

    usage = json.loads(paths["USAGE_FILE"].read_text(encoding="utf-8"))
    assert usage["2026-07"]["alice"]["total"] == 1
    daily = json.loads(
        paths["USAGE_DAILY_FILE"].read_text(encoding="utf-8")
    )
    assert daily["2026-07-03"]["alice"]["total"] == 1


def test_critical_state_failure_revokes_static_configs_before_safe_reload(
    monkeypatch
):
    events = []
    monkeypatch.setattr(tl, "_using_live_core_state", lambda: True)
    monkeypatch.setattr(
        tl,
        "_stop_static_service",
        lambda service, *, reason: events.append(("stop", service)) or True,
    )
    monkeypatch.setattr(
        tl.xray_config,
        "apply_user_plan",
        lambda plan, *, prune_unknown=False: events.append(
            ("xray-plan", plan, prune_unknown)
        )
        or True,
    )
    monkeypatch.setattr(
        tl.xray_config,
        "reload_async",
        lambda: events.append(("reload", "xray")),
    )
    monkeypatch.setattr(
        tl.tuic_config,
        "sync_all",
        lambda *, users: events.append(("tuic-plan", users)) or True,
    )
    monkeypatch.setattr(
        tl.tuic_config,
        "reload_async",
        lambda: events.append(("reload", "tuic")),
    )

    tl._fail_closed_static_access(
        state_store.InvalidJsonState("bad quota ledger")
    )

    assert events[:2] == [
        ("stop", tl.xray_config.RELOAD_SERVICE),
        ("stop", tl.tuic_config.RELOAD_SERVICE),
    ]
    assert ("xray-plan", {}, True) in events
    assert ("tuic-plan", {}) in events
    assert ("reload", "xray") in events
    assert ("reload", "tuic") in events


def test_one_static_proxy_failure_does_not_block_the_other(monkeypatch):
    events = []
    monkeypatch.setattr(
        tl.xray_config,
        "apply_user_plan",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            state_store.InvalidJsonState("bad xray config")
        ),
    )
    monkeypatch.setattr(
        tl.tuic_config,
        "sync_user_plan",
        lambda users, plan: events.append(("tuic", users, plan)) or True,
    )
    monkeypatch.setattr(
        tl,
        "_stop_static_service",
        lambda service, *, reason: events.append(("stop", service)) or True,
    )

    xray_changed, tuic_changed = tl._apply_static_access_plan(
        {"alice": {}}, {"alice": None}
    )

    assert xray_changed is False
    assert tuic_changed is True
    assert ("stop", tl.xray_config.RELOAD_SERVICE) in events
    assert ("tuic", {"alice": {}}, {"alice": None}) in events


def test_maximum_cycle_survives_daily_retention_pruning():
    now = datetime(2026, 7, 18, 12)
    cycle_start = now.date() - timedelta(days=tl.CYCLE_LENGTH_MAX - 1)
    meta = {
        "settlement_day": 1,
        "cycle_length_days": tl.CYCLE_LENGTH_MAX,
        "cycle_anchor_date": cycle_start.isoformat(),
    }
    cycle_keys = tl.cycle_days(now, meta=meta)
    assert len(cycle_keys) == tl.CYCLE_LENGTH_MAX

    daily = {
        key: {"alice": {"tx": 0, "rx": 1, "total": 1}}
        for key in cycle_keys
    }
    outside_retention = (
        now.date() - timedelta(days=tl.DAILY_RETENTION_DAYS)
    ).isoformat()
    daily[outside_retention] = {
        "alice": {"tx": 0, "rx": 99, "total": 99}
    }

    tl.prune_daily(daily, now.date())

    assert outside_retention not in daily
    assert set(cycle_keys) <= set(daily)
    assert tl.cycle_used_raw_for(
        "alice", daily, now=now, meta=meta
    ) == tl.CYCLE_LENGTH_MAX


def test_main_preserves_hysteria_protocol_delta_when_merging_xray(
    tmp_path, monkeypatch
):
    paths = _configure_state(tmp_path, monkeypatch)
    captured = {}

    def fake_get(path):
        if path == "/traffic?clear=1":
            return {"alice": {"tx": 10, "rx": 20}}
        if path == "/online":
            return {}
        raise AssertionError(path)

    def capture_protocol(protocol_traffic, _now):
        captured.update(json.loads(json.dumps(protocol_traffic)))

    monkeypatch.setattr(tl, "local_now", lambda: datetime(2026, 7, 3, 12))
    monkeypatch.setattr(tl, "get", fake_get)
    monkeypatch.setattr(
        tl,
        "get_xray_traffic",
        lambda: {"alice": {"tx": 1, "rx": 2}},
    )
    monkeypatch.setattr(tl, "get_tuic_traffic", lambda: {})
    monkeypatch.setattr(
        tl.xray_config, "apply_user_plan", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        tl.tuic_config, "sync_user_plan", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        tl.static_access, "recover_if_pending", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(tl, "accumulate_protocol_hourly", capture_protocol)
    monkeypatch.setattr(
        tl.cost_calibrator, "update_sample", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        tl.cost_calibrator,
        "maybe_auto_adjust",
        lambda *_args, **_kwargs: {"applied": False},
    )
    monkeypatch.setattr(tl, "check_alerts", lambda *_args, **_kwargs: None)

    tl.main()

    assert captured == {
        "hysteria": {"alice": {"tx": 10, "rx": 20}},
        "xray": {"alice": {"tx": 1, "rx": 2}},
    }
    usage = json.loads(paths["USAGE_FILE"].read_text(encoding="utf-8"))
    assert usage["2026-07"]["alice"] == {
        "tx": 11,
        "rx": 22,
        "total": 33,
    }


def test_authoritative_daily_commits_before_legacy_usage_and_survives_failure(
    tmp_path, monkeypatch
):
    paths = _configure_state(tmp_path, monkeypatch)
    real_save_json = tl.save_json
    writes = []

    def fail_legacy_usage(path, data):
        target = Path(path)
        writes.append(target)
        if target == Path(paths["USAGE_FILE"]):
            raise OSError("legacy usage write failed")
        return real_save_json(path, data)

    monkeypatch.setattr(tl, "save_json", fail_legacy_usage)

    with pytest.raises(OSError, match="legacy usage write failed"):
        tl._commit_core_and_enforce(
            {"alice": {"tx": 7, "rx": 5}},
            now=datetime(2026, 7, 3, 12),
        )

    assert writes[:2] == [
        Path(paths["USAGE_DAILY_FILE"]),
        Path(paths["USAGE_FILE"]),
    ]
    daily = json.loads(
        paths["USAGE_DAILY_FILE"].read_text(encoding="utf-8")
    )
    assert daily["2026-07-03"]["alice"] == {
        "tx": 7,
        "rx": 5,
        "total": 12,
    }


def test_destructive_collection_and_core_commit_share_one_usage_lock(
    tmp_path, monkeypatch
):
    """A would-be second lock failure cannot strand return-and-clear bytes."""
    paths = _configure_state(tmp_path, monkeypatch)
    now = datetime(2026, 7, 3, 12)
    events = []
    lock_state = {"held": False, "acquisitions": 0}

    @contextmanager
    def fail_if_reacquired():
        lock_state["acquisitions"] += 1
        if lock_state["acquisitions"] > 1:
            raise state_store.LockTimeout("injected second acquisition failure")
        assert lock_state["held"] is False
        lock_state["held"] = True
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")
            lock_state["held"] = False

    real_preflight = tl.preflight_persistent_state

    def tracked_preflight(current):
        assert lock_state["held"] is True
        events.append("preflight")
        return real_preflight(current)

    def fake_get(path):
        if path == "/traffic?clear=1":
            assert lock_state["held"] is True
            events.append("hysteria-clear")
            return {"alice": {"tx": 7, "rx": 5}}
        if path == "/online":
            assert lock_state["held"] is False
            return {}
        raise AssertionError(path)

    def fake_xray():
        assert lock_state["held"] is True
        events.append("xray-clear")
        return {"alice": {"tx": 3, "rx": 2}}

    real_save_json = tl.save_json

    def tracked_save(path, data):
        target = Path(path)
        if target == Path(paths["USAGE_DAILY_FILE"]):
            assert lock_state["held"] is True
            events.append("daily-fsync")
        elif target == Path(paths["USAGE_FILE"]):
            assert lock_state["held"] is True
            events.append("usage-fsync")
        return real_save_json(path, data)

    def fake_xray_plan(*_args, **_kwargs):
        assert lock_state["held"] is True
        events.append("static-plan")
        return False

    def fake_tuic_plan(*_args, **_kwargs):
        assert lock_state["held"] is True
        return False

    def fake_tuic_meter():
        assert lock_state["held"] is False
        events.append("tuic-meter")
        return {}

    monkeypatch.setattr(tl, "local_now", lambda: now)
    monkeypatch.setattr(tl, "usage_lock", fail_if_reacquired)
    monkeypatch.setattr(tl, "preflight_persistent_state", tracked_preflight)
    monkeypatch.setattr(tl, "get", fake_get)
    monkeypatch.setattr(tl, "get_xray_traffic", fake_xray)
    monkeypatch.setattr(tl, "get_tuic_traffic", fake_tuic_meter)
    monkeypatch.setattr(tl, "save_json", tracked_save)
    monkeypatch.setattr(tl.xray_config, "apply_user_plan", fake_xray_plan)
    monkeypatch.setattr(tl.tuic_config, "sync_user_plan", fake_tuic_plan)
    monkeypatch.setattr(
        tl.static_access, "recover_if_pending", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        tl.cost_calibrator, "update_sample", lambda *_args, **_kwargs: {}
    )
    monkeypatch.setattr(
        tl.cost_calibrator,
        "maybe_auto_adjust",
        lambda *_args, **_kwargs: {"applied": False},
    )
    monkeypatch.setattr(tl, "check_alerts", lambda *_args, **_kwargs: None)

    tl.main()

    assert lock_state["acquisitions"] == 1
    assert events.index("lock-enter") < events.index("preflight")
    assert events.index("preflight") < events.index("hysteria-clear")
    assert events.index("hysteria-clear") < events.index("xray-clear")
    assert events.index("xray-clear") < events.index("daily-fsync")
    assert events.index("daily-fsync") < events.index("usage-fsync")
    assert events.index("usage-fsync") < events.index("static-plan")
    assert events.index("static-plan") < events.index("lock-exit")
    assert events.index("lock-exit") < events.index("tuic-meter")

    usage = json.loads(paths["USAGE_FILE"].read_text(encoding="utf-8"))
    assert usage["2026-07"]["alice"] == {
        "tx": 10,
        "rx": 7,
        "total": 17,
    }
    daily = json.loads(
        paths["USAGE_DAILY_FILE"].read_text(encoding="utf-8")
    )
    assert daily["2026-07-03"]["alice"] == {
        "tx": 10,
        "rx": 7,
        "total": 17,
    }


@pytest.mark.parametrize(
    "users",
    [
        {
            "alice": {
                "vless_uuid": "not-a-uuid",
                "monthly_quota_bytes": 1024,
            }
        },
        {
            "alice": {
                "vless_uuid": "11111111-1111-4111-8111-111111111111",
            },
            "bob": {
                "vless_uuid": "11111111-1111-4111-8111-111111111111",
            },
        },
    ],
    ids=["invalid", "duplicate"],
)
def test_uuid_preflight_fails_closed_before_destructive_counter_reads(
    tmp_path, monkeypatch, users
):
    _configure_state(tmp_path, monkeypatch, users=users)
    destructive_calls = []
    fail_closed_reasons = []
    monkeypatch.setattr(
        tl,
        "get",
        lambda path: destructive_calls.append(path) or {},
    )
    monkeypatch.setattr(
        tl,
        "get_xray_traffic",
        lambda: destructive_calls.append("xray-reset") or {},
    )
    monkeypatch.setattr(
        tl,
        "get_tuic_traffic",
        lambda: destructive_calls.append("tuic-baseline") or {},
    )
    monkeypatch.setattr(
        tl, "_fail_closed_static_access", fail_closed_reasons.append
    )
    monkeypatch.setattr(
        tl, "local_now", lambda: datetime(2026, 7, 3, 12)
    )

    with pytest.raises(state_store.StateStoreError):
        tl.main()

    assert destructive_calls == []
    assert len(fail_closed_reasons) == 1
    assert isinstance(
        fail_closed_reasons[0], state_store.InvalidJsonState
    )


def test_lock_contention_skips_tick_without_revoking_static_access(
    monkeypatch,
):
    fail_closed_reasons = []

    @contextmanager
    def contended_lock():
        raise state_store.LockTimeout("busy")
        yield

    monkeypatch.setattr(tl, "usage_lock", contended_lock)
    monkeypatch.setattr(
        tl,
        "_fail_closed_static_access",
        fail_closed_reasons.append,
    )

    with pytest.raises(state_store.LockTimeout):
        tl.main()

    assert fail_closed_reasons == []
