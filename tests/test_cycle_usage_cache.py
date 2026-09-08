"""Cycle-usage aggregation cache regressions (phase 4).

The cache stores ONLY raw per-user cycle byte sums. Every authorization
decision input — disabled, expires_at, quota, vless_uuid, display multiplier
— must still be re-evaluated on every plan build.
"""

from datetime import datetime
import json
import os

import pytest

import subscription_service as ss
import state_store


NOW = datetime(2026, 7, 18, 12, 0, 0)

UUID_A = "11111111-1111-4111-8111-111111111111"
UUID_B = "22222222-2222-4222-8222-222222222222"


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _meta():
    return {
        "settlement_day": 1,
        "cycle_length_days": 30,
        "cycle_anchor_date": "2026-01-01",
    }


def _metered(quota, *, uuid_value=UUID_A):
    return {
        "sub_token": "tok",
        "vless_uuid": uuid_value,
        "metered": True,
        "monthly_quota_bytes": quota,
        "disabled": False,
    }


def _live_state(tmp_path, monkeypatch, *, daily=None, multiplier=1.0):
    """Point the live state files at tmp and force live-core-state mode."""
    daily_file = tmp_path / "usage_daily.json"
    mult_file = tmp_path / "display_multiplier.json"
    users_file = tmp_path / "users.json"
    _write_json(daily_file, daily if daily is not None else {})
    _write_json(mult_file, {"enabled": True, "multiplier": multiplier})
    _write_json(users_file, {})
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", daily_file)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(ss, "DISPLAY_MULTIPLIER_STATE_FILE", mult_file)
    monkeypatch.setattr(ss, "_using_live_core_state", lambda: True)
    with ss._cycle_usage_cache_lock:
        ss._cycle_usage_cache.clear()
    return daily_file, mult_file


def _counting_strict(monkeypatch):
    from authorization_service import AuthorizationService

    calls = []
    original = AuthorizationService._cycle_usage_sum_strict

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(AuthorizationService, "_cycle_usage_sum_strict", counting)
    return calls


def _bump_file(path, payload):
    """Rewrite the file and force a distinct mtime_ns so the version changes."""
    _write_json(path, payload)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))


# A. Identical inputs: cached and uncached plans are exactly equal.

def test_cached_plan_matches_uncached_plan(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 20, "rx": 40, "total": 60}}},
    )
    users = {"alice": _metered(1000)}
    meta = _meta()

    uncached = ss._build_static_access_plan(users, daily=json.loads(
        daily_file.read_text(encoding="utf-8")), meta=meta, now=NOW)
    version = ss._usage_daily_file_version()
    first = ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=version,
    )
    second = ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=version,
    )
    assert first == uncached == second == {"alice": UUID_A}


# B. Unchanged usage_daily: the second build hits the aggregation cache.

def test_unchanged_usage_daily_hits_cache(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 20, "rx": 40, "total": 60}}},
    )
    calls = _counting_strict(monkeypatch)
    users = {"alice": _metered(1000)}
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    assert len(calls) == 1


# C. usage_daily change: cache miss, quota decision changes immediately.

def test_usage_daily_change_invalidates_cache(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 20, "rx": 40, "total": 60}}},
    )
    calls = _counting_strict(monkeypatch)
    users = {"alice": _metered(100)}
    meta = _meta()

    version = ss._usage_daily_file_version()
    plan = ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=version,
    )
    assert plan["alice"] == UUID_A  # 60 < 100

    # Traffic grows past the quota: the very next build must deny.
    _bump_file(daily_file, {
        "2026-07-18": {"alice": {"tx": 60, "rx": 60, "total": 120}},
    })
    version = ss._usage_daily_file_version()
    plan = ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=version,
    )
    assert plan["alice"] is None
    assert len(calls) == 2  # miss, not a stale hit


# D. Quota change: aggregation cache may hit, but the decision is re-evaluated.

def test_quota_change_applies_immediately_despite_cache_hit(
    tmp_path, monkeypatch,
):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 20, "rx": 40, "total": 60}}},
    )
    calls = _counting_strict(monkeypatch)
    meta = _meta()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))
    version = ss._usage_daily_file_version()

    plan = ss._build_static_access_plan(
        {"alice": _metered(1000)}, daily, meta, now=NOW,
        usage_version=version,
    )
    assert plan["alice"] == UUID_A

    plan = ss._build_static_access_plan(
        {"alice": _metered(50)}, daily, meta, now=NOW,
        usage_version=version,
    )
    assert plan["alice"] is None  # 60 >= 50
    assert len(calls) == 1  # raw aggregation was reused; decision was not


# E. disabled change: immediate, no cache involvement.

def test_disabled_change_applies_immediately(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}}},
    )
    meta = _meta()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))
    version = ss._usage_daily_file_version()

    enabled = {"alice": _metered(1000)}
    plan = ss._build_static_access_plan(
        enabled, daily, meta, now=NOW, usage_version=version,
    )
    assert plan["alice"] == UUID_A

    disabled = {"alice": {**_metered(1000), "disabled": True}}
    plan = ss._build_static_access_plan(
        disabled, daily, meta, now=NOW, usage_version=version,
    )
    assert plan["alice"] is None


# F. Multiplier change: re-read from disk on every build.

def test_multiplier_change_applies_immediately(tmp_path, monkeypatch):
    daily_file, mult_file = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 30, "rx": 30, "total": 60}}},
        multiplier=1.0,
    )
    users = {"alice": _metered(100)}
    meta = _meta()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))
    version = ss._usage_daily_file_version()

    plan = ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    assert plan["alice"] == UUID_A  # 60 * 1.0 < 100

    _write_json(mult_file, {"enabled": True, "multiplier": 2.0})
    plan = ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    assert plan["alice"] is None  # 60 * 2.0 >= 100


# G. New cycle: different cycle days => automatic miss.

def test_cycle_rollover_reaggregates(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}}},
    )
    calls = _counting_strict(monkeypatch)
    users = {"alice": _metered(1000)}
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    later = datetime(2026, 8, 20, 12, 0, 0)  # next 30-day cycle
    ss._build_static_access_plan(
        users, daily, meta, now=later, usage_version=version,
    )
    assert len(calls) == 2


# H. Non-live callers bypass the live cache even if a version is supplied.

def test_non_live_caller_bypasses_cache(tmp_path, monkeypatch):
    daily_file = tmp_path / "usage_daily.json"
    mult_file = tmp_path / "display_multiplier.json"
    _write_json(daily_file, {
        "2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}},
    })
    _write_json(mult_file, {"enabled": True, "multiplier": 1.0})
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", daily_file)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json")
    monkeypatch.setattr(ss, "DISPLAY_MULTIPLIER_STATE_FILE", mult_file)
    monkeypatch.setattr(ss, "_using_live_core_state", lambda: False)
    with ss._cycle_usage_cache_lock:
        ss._cycle_usage_cache.clear()
    calls = _counting_strict(monkeypatch)

    users = {"alice": _metered(1000)}
    meta = _meta()
    fake_version = (123, 456)
    ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=fake_version,
    )
    ss._build_static_access_plan(
        users, json.loads(daily_file.read_text(encoding="utf-8")), meta,
        now=NOW, usage_version=fake_version,
    )
    assert len(calls) == 2  # never cached
    assert not ss._cycle_usage_cache


# I. Invalid usage data keeps the original fail-closed semantics.

def test_invalid_usage_entry_still_fails_closed(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": "not-a-number"}},
    )
    users = {"alice": _metered(1000)}
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    with pytest.raises(state_store.CriticalStateUnavailable):
        ss._build_static_access_plan(
            users, daily, meta, now=NOW, usage_version=version,
        )
    # Errors are never cached: a repeated call raises again.
    with pytest.raises(state_store.CriticalStateUnavailable):
        ss._build_static_access_plan(
            users, daily, meta, now=NOW, usage_version=version,
        )


def test_non_dict_day_bucket_still_fails_closed(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": ["broken"]},
    )
    users = {"alice": _metered(1000)}
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    with pytest.raises(state_store.CriticalStateUnavailable):
        ss._build_static_access_plan(
            users, daily, meta, now=NOW, usage_version=version,
        )


# Bounded: the cache never grows past its configured size.
# Capacity is dynamic: max(_CYCLE_USAGE_CACHE_MIN, 4 * user_count).

def test_cache_is_bounded(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}}},
    )
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    # Build plans one user at a time: capacity = max(32, 4*1) = 32,
    # so the cache must stay at or below 32 even with 36 distinct users.
    for i in range(ss._CYCLE_USAGE_CACHE_MIN + 4):
        users = {
            f"user{i:02d}": _metered(
                1000,
                uuid_value=f"11111111-1111-4111-8111-{i + 1:012d}",
            ),
        }
        ss._build_static_access_plan(
            users, daily, meta, now=NOW, usage_version=version,
        )
    assert len(ss._cycle_usage_cache) <= ss._CYCLE_USAGE_CACHE_MIN


def test_cache_scales_with_user_count(tmp_path, monkeypatch):
    """More than the old fixed cap of 8: every metered user's entry must
    survive a full plan build and hit on the next one."""
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}}},
    )
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    users = {
        f"user{i:02d}": _metered(
            1000,
            uuid_value=f"11111111-1111-4111-8111-{i + 1:012d}",
        )
        for i in range(12)
    }
    calls = _counting_strict(monkeypatch)

    ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    assert len(calls) == 12  # cold: every user aggregated once

    ss._build_static_access_plan(
        users, daily, meta, now=NOW, usage_version=version,
    )
    assert len(calls) == 12  # warm: all 12 hit, zero re-aggregation


# Cache hits must also prune to the current effective bound.

def test_cache_hit_also_prunes_to_current_bound(tmp_path, monkeypatch):
    daily_file, _m = _live_state(
        tmp_path, monkeypatch,
        daily={"2026-07-18": {"alice": {"tx": 1, "rx": 1, "total": 2}}},
    )
    meta = _meta()
    version = ss._usage_daily_file_version()
    daily = json.loads(daily_file.read_text(encoding="utf-8"))

    # Grow the cache with a large user set (bound = 4 * 64 = 256).
    big_users = {
        f"user{i:03d}": _metered(
            1000,
            uuid_value=f"11111111-1111-4111-8111-{i + 1:012d}",
        )
        for i in range(64)
    }
    ss._build_static_access_plan(
        big_users, daily, meta, now=NOW, usage_version=version,
    )
    assert len(ss._cycle_usage_cache) == 64

    # The user base shrank to one: bound collapses to the floor (32).
    # A pure cache hit must still prune the oversized cache.
    small_users = {"user000": big_users["user000"]}
    ss._build_static_access_plan(
        small_users, daily, meta, now=NOW, usage_version=version,
    )
    assert len(ss._cycle_usage_cache) <= ss._CYCLE_USAGE_CACHE_MIN


def test_cache_never_exceeds_hard_cap(tmp_path, monkeypatch):
    assert ss._cycle_usage_cache_bound(0) == ss._CYCLE_USAGE_CACHE_MIN
    assert ss._cycle_usage_cache_bound(8) == ss._CYCLE_USAGE_CACHE_MIN
    assert ss._cycle_usage_cache_bound(100) == 400
    assert (
        ss._cycle_usage_cache_bound(100000)
        == ss._CYCLE_USAGE_CACHE_HARD_MAX
    )
