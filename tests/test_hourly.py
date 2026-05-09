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
