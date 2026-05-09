from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import traffic_limiter as tl

SH = ZoneInfo("Asia/Shanghai")


def _make_hourly(now, n_hours):
    """Build a dict with n_hours hour keys ending at `now`."""
    out = {}
    for i in range(n_hours):
        h = now - timedelta(hours=i)
        out[h.strftime("%Y-%m-%dT%H")] = {"alice": {"tx": 1, "rx": 1, "total": 2}}
    return out


def test_prune_hourly_drops_keys_older_than_168h():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 200)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168


def test_prune_hourly_keeps_exact_boundary():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 168)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168, "no key should be dropped at exact 168 boundary"


def test_prune_hourly_drops_just_one_too_old():
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = _make_hourly(now, 169)
    tl.prune_hourly(hourly, now)
    assert len(hourly) == 168


import json


def test_accumulate_hourly_creates_bucket_for_first_tick(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now = datetime(2026, 5, 8, 14, 5, tzinfo=SH)
    traffic = {"alice": {"tx": 100, "rx": 200}}
    tl.accumulate_hourly(traffic, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data == {
        "2026-05-08T14": {"alice": {"tx": 100, "rx": 200, "total": 300}}
    }


def test_accumulate_hourly_appends_within_same_hour(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now1 = datetime(2026, 5, 8, 14, 5, tzinfo=SH)
    now2 = datetime(2026, 5, 8, 14, 55, tzinfo=SH)
    tl.accumulate_hourly({"alice": {"tx": 100, "rx": 200}}, now1)
    tl.accumulate_hourly({"alice": {"tx": 50,  "rx": 25}},  now2)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data["2026-05-08T14"]["alice"] == {"tx": 150, "rx": 225, "total": 375}


def test_accumulate_hourly_rolls_to_new_bucket_at_hour_boundary(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    tl.accumulate_hourly(
        {"alice": {"tx": 1, "rx": 1}},
        datetime(2026, 5, 8, 14, 59, tzinfo=SH),
    )
    tl.accumulate_hourly(
        {"alice": {"tx": 5, "rx": 5}},
        datetime(2026, 5, 8, 15, 0, tzinfo=SH),
    )
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert "2026-05-08T14" in data
    assert "2026-05-08T15" in data
    assert data["2026-05-08T14"]["alice"]["total"] == 2
    assert data["2026-05-08T15"]["alice"]["total"] == 10


def test_accumulate_hourly_creates_bucket_for_new_user_mid_cycle(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    tl.accumulate_hourly({"alice": {"tx": 1, "rx": 1}}, now)
    tl.accumulate_hourly({"alice": {"tx": 1, "rx": 1}, "bob": {"tx": 9, "rx": 9}}, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert data["2026-05-08T14"]["bob"] == {"tx": 9, "rx": 9, "total": 18}
    assert data["2026-05-08T14"]["alice"]["total"] == 4


def test_accumulate_hourly_prunes_at_each_call(tmp_path, monkeypatch):
    monkeypatch.setattr(tl, "USAGE_HOURLY_FILE", str(tmp_path / "usage_hourly.json"))
    old = datetime(2026, 5, 1, 0, tzinfo=SH).strftime("%Y-%m-%dT%H")
    (tmp_path / "usage_hourly.json").write_text(json.dumps(
        {old: {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    ))
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    tl.accumulate_hourly({"alice": {"tx": 5, "rx": 5}}, now)
    data = json.loads((tmp_path / "usage_hourly.json").read_text())
    assert old not in data, "old hour should be pruned"
    assert "2026-05-08T14" in data


import subscription_service as ss


def _seed_hourly(hours_back, per_hour_bytes_per_user):
    """Build a hourly dict with `hours_back` hours up to a fixed `now`.

    Each hour holds the same {uid: bytes} payload (raw, pre-display).
    Returns (hourly_dict, fixed_now).
    """
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    out = {}
    for i in range(hours_back):
        h = now - timedelta(hours=i)
        out[h.strftime("%Y-%m-%dT%H")] = {
            uid: {"tx": v // 2, "rx": v - v // 2, "total": v}
            for uid, v in per_hour_bytes_per_user.items()
        }
    return out, now


def test_load_hourly_totals_returns_168_entries_padded_with_zeros(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    hourly, now = _seed_hourly(50, {"alice": 1_000_000, "bob": 500_000})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    series = ss._load_hourly_totals(now=now)
    assert len(series) == 168
    nonzero = [s for s in series if s["bytes"] > 0]
    assert len(nonzero) == 50
    from display import DISPLAY_MULTIPLIER
    expected_per_hour = int((1_000_000 + 500_000) * DISPLAY_MULTIPLIER)
    assert nonzero[0]["bytes"] == expected_per_hour


def test_top_n_users_orders_by_last_24h_total_descending(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({
        "alice": {"metered": True}, "bob": {"metered": True},
        "carol": {"metered": False}, "dave": {"metered": True},
    }))
    hourly, now = _seed_hourly(
        24,
        {"alice": 100, "bob": 50, "carol": 75, "dave": 10},
    )
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert [u["uid"] for u in top] == ["alice", "carol", "bob", "dave"]


def test_top_n_users_includes_unmetered(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({
        "alice": {"metered": False}, "bob": {"metered": True},
    }))
    hourly, now = _seed_hourly(24, {"alice": 1_000_000_000, "bob": 1})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert top[0]["uid"] == "alice", "unmetered user with high traffic should rank first"


def test_top_n_users_caps_at_5(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    users = {f"u{i}": {"metered": True} for i in range(10)}
    (tmp_path / "users.json").write_text(json.dumps(users))
    hourly, now = _seed_hourly(24, {f"u{i}": 100 - i for i in range(10)})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    top = ss._top_n_users(n=5, window_hours=24, now=now)
    assert len(top) == 5
    assert [u["uid"] for u in top] == ["u0", "u1", "u2", "u3", "u4"]


def test_load_heatmap_grid_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    hourly, now = _seed_hourly(168, {"alice": 1_000_000})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    grid = ss._load_heatmap_grid(now=now)
    assert len(grid) == 7
    assert all(len(row["hours"]) == 24 for row in grid)


def test_aggregate_stats_keys(monkeypatch, tmp_path):
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json")
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json")
    (tmp_path / "users.json").write_text(json.dumps({"alice": {"metered": True}}))
    (tmp_path / "usage.json").write_text(json.dumps({}))
    (tmp_path / "usage_daily.json").write_text(json.dumps({}))
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", tmp_path / "usage_daily.json")
    hourly, now = _seed_hourly(48, {"alice": 1_000_000})
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly))
    stats = ss._aggregate_stats(now=now, online={})
    assert {"current_hour_bytes", "today_bytes", "yesterday_bytes",
            "last_7d_bytes", "cycle_bytes", "cycle_day", "cycle_total_days",
            "online"} <= set(stats.keys())
