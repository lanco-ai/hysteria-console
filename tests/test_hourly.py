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
