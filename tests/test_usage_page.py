"""Integration tests for /admin/usage and /admin/user/<uid> routes."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import subscription_service as ss

SH = ZoneInfo("Asia/Shanghai")


def _seed_state(tmp_path, monkeypatch, *, users=None, hourly=None, daily=None,
                usage=None, online=None):
    """Repoint all state files at tmp_path and pre-fill them."""
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", tmp_path / "usage_daily.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json", raising=False)
    monkeypatch.setattr(ss, "ONLINE_FILE", tmp_path / "online.json", raising=False)
    (tmp_path / "users.json").write_text(json.dumps(users or {}))
    (tmp_path / "usage.json").write_text(json.dumps(usage or {}))
    (tmp_path / "usage_daily.json").write_text(json.dumps(daily or {}))
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly or {}))
    (tmp_path / "online.json").write_text(json.dumps(online or {}))


def test_build_usage_json_payload_schema(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {}
    for i in range(24):
        h = now - timedelta(hours=i)
        hourly[h.strftime("%Y-%m-%dT%H")] = {
            "alice": {"tx": 100, "rx": 100, "total": 200}
        }
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True}}, hourly=hourly, online={"alice": 1})
    payload = ss._build_usage_json_payload(now=now)
    assert "ts" in payload
    assert set(payload["stats"].keys()) >= {
        "current_hour_bytes", "today_bytes", "yesterday_bytes",
        "last_7d_bytes", "cycle_bytes", "cycle_day", "cycle_total_days", "online"
    }
    assert len(payload["hourly_totals"]) == 168
    assert len(payload["heatmap"]) == 7
    assert all(len(r["hours"]) == 24 for r in payload["heatmap"])
    assert isinstance(payload["top_n"], list)
    assert all({"uid", "last_24h_bytes", "spark"} <= set(t.keys()) for t in payload["top_n"])


def test_admin_usage_page_html_contains_three_charts(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True}}, hourly=hourly)
    monkeypatch.setattr(ss, "local_now", lambda: now)
    html_out = ss.render_usage_page("test-host")
    assert 'class="hourly-bars"' in html_out
    assert 'class="heatmap"' in html_out
    assert 'class="spark"' in html_out
    assert 'usage.js' in html_out
    assert "<details" in html_out


def test_admin_daily_redirects_to_usage_with_301(tmp_path, monkeypatch):
    """The legacy /admin/daily route returns 301 → /admin/usage."""
    captured = {}

    class StubHandler:
        def redirect(self, target, status=302):
            captured["target"] = target
            captured["status"] = status

    h = StubHandler()
    ss._handle_legacy_daily_redirect(h)
    assert captured["status"] == 301
    assert captured["target"] == "/admin/usage"


def test_user_detail_json_payload_schema(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 100_000_000_000}},
                hourly=hourly, online={"alice": 1})
    payload = ss._build_user_json_payload("alice", now=now)
    assert payload["uid"] == "alice"
    assert payload["metered"] is True
    assert payload["online"] == 1
    assert isinstance(payload["cycle_quota_bytes"], int)
    assert len(payload["hourly_bars"]) == 168
    assert len(payload["heatmap"]) == 7
    assert "recent_alerts" in payload


def test_user_detail_json_unknown_user_returns_none(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, users={"alice": {}})
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    assert ss._build_user_json_payload("nobody", now=now) is None


def test_user_detail_page_renders_for_known_user(tmp_path, monkeypatch):
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    hourly = {now.strftime("%Y-%m-%dT%H"): {"alice": {"tx": 1, "rx": 1, "total": 2}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 1_000_000_000}},
                hourly=hourly, online={"alice": 1})
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_user_detail_page("alice", "test-host")
    assert out is not None
    assert "alice" in out
    assert 'class="hourly-bars"' in out
    assert 'class="heatmap"' in out
    assert 'href="/admin/usage"' in out


def test_user_detail_page_returns_none_for_unknown_user(tmp_path, monkeypatch):
    _seed_state(tmp_path, monkeypatch, users={"alice": {}})
    now = datetime(2026, 5, 8, 14, tzinfo=SH)
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_user_detail_page("nobody", "test-host")
    assert out is None


def test_cycle_bytes_invariant_cycle_ge_today_ge_current_hour(tmp_path, monkeypatch):
    """When the cycle bucket in usage.json has drifted below the actual daily
    sum (cron stomp / corruption / spec-mandated post-reset state), the display
    must still satisfy `本周期 >= 今日 >= 当小时`. Achieved by deriving cycle
    from usage_daily.json instead of usage.json."""
    now = datetime(2026, 5, 12, 14, tzinfo=SH)  # day == settlement_day default 12
    daily = {
        "2026-05-12": {"alice": {"tx": 0, "rx": 1_000_000_000, "total": 1_000_000_000}},
    }
    hourly = {
        "2026-05-12T13": {"alice": {"tx": 0, "rx": 300_000_000, "total": 300_000_000}},
        "2026-05-12T14": {"alice": {"tx": 0, "rx": 700_000_000, "total": 700_000_000}},
    }
    # The bug pattern: usage.json's cycle bucket is artificially low (e.g. only
    # the current hour, simulating drift). Spec-derived cycle ignores it.
    usage = {"2026-05": {"alice": {"tx": 0, "rx": 700_000_000, "total": 700_000_000}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 5_000_000_000}},
                hourly=hourly, daily=daily, usage=usage)
    payload = ss._build_user_json_payload("alice", now=now)
    assert payload["cycle_used_bytes"] >= payload["today_bytes"], (
        f"cycle ({payload['cycle_used_bytes']}) must be >= today ({payload['today_bytes']})"
    )
    assert payload["today_bytes"] >= payload["current_hour_bytes"]


def test_aggregate_stats_cycle_uses_daily_sum(tmp_path, monkeypatch):
    """Dashboard 本周期 stat derives from usage_daily.json so it stays consistent
    with `today` (which reads the same fine-grained source)."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    daily = {
        "2026-05-12": {"alice": {"tx": 0, "rx": 1_000_000_000, "total": 1_000_000_000}},
        "2026-05-13": {"alice": {"tx": 0, "rx": 2_000_000_000, "total": 2_000_000_000}},
        "2026-05-14": {"alice": {"tx": 0, "rx": 500_000_000, "total": 500_000_000}},
    }
    # Cycle bucket in usage.json is bogus / drifted; the dashboard must ignore it.
    usage = {"2026-05": {"alice": {"tx": 0, "rx": 1, "total": 1}}}
    _seed_state(tmp_path, monkeypatch, users={"alice": {"metered": True}},
                daily=daily, usage=usage)
    stats = ss._aggregate_stats(now=now, online={})
    expected_raw = 1_000_000_000 + 2_000_000_000 + 500_000_000
    assert stats["cycle_bytes"] == int(expected_raw * ss.DISPLAY_MULTIPLIER)


def test_zero_cycle_daily_hourly_clears_user_within_cycle(tmp_path, monkeypatch):
    """Manual reset's daily/hourly clearing must zero the affected user's entries
    inside the current cycle and leave other users / pre-cycle data alone."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    daily = {
        "2026-05-11": {"alice": {"tx": 0, "rx": 9, "total": 9},  # pre-cycle (day 11 < 12)
                       "bob": {"tx": 0, "rx": 9, "total": 9}},
        "2026-05-12": {"alice": {"tx": 0, "rx": 1000, "total": 1000},
                       "bob": {"tx": 0, "rx": 200, "total": 200}},
        "2026-05-13": {"alice": {"tx": 0, "rx": 1500, "total": 1500}},
    }
    hourly = {
        "2026-05-11T23": {"alice": {"tx": 0, "rx": 9, "total": 9}},  # pre-cycle
        "2026-05-12T00": {"alice": {"tx": 0, "rx": 50, "total": 50},
                          "bob": {"tx": 0, "rx": 5, "total": 5}},
        "2026-05-14T09": {"alice": {"tx": 0, "rx": 100, "total": 100}},
    }
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {}, "bob": {}}, daily=daily, hourly=hourly)
    ss._zero_cycle_daily_hourly_for(["alice"], now=now)
    daily_after = json.loads((tmp_path / "usage_daily.json").read_text())
    hourly_after = json.loads((tmp_path / "usage_hourly.json").read_text())
    # alice's in-cycle entries are zeroed
    assert daily_after["2026-05-12"]["alice"] == {"tx": 0, "rx": 0, "total": 0}
    assert daily_after["2026-05-13"]["alice"] == {"tx": 0, "rx": 0, "total": 0}
    assert hourly_after["2026-05-12T00"]["alice"] == {"tx": 0, "rx": 0, "total": 0}
    assert hourly_after["2026-05-14T09"]["alice"] == {"tx": 0, "rx": 0, "total": 0}
    # alice's pre-cycle entries untouched
    assert daily_after["2026-05-11"]["alice"]["total"] == 9
    assert hourly_after["2026-05-11T23"]["alice"]["total"] == 9
    # bob untouched
    assert daily_after["2026-05-12"]["bob"]["total"] == 200
    assert hourly_after["2026-05-12T00"]["bob"]["total"] == 5


def test_save_json_is_atomic_against_crash(tmp_path, monkeypatch):
    """A failing serialize must not leave the target file truncated. Atomic-rename
    means the original survives intact and load_json doesn't fall back to {}."""
    target = tmp_path / "state.json"
    ss.save_json(target, {"good": "value"})
    assert json.loads(target.read_text())["good"] == "value"

    class Bomb:
        def __repr__(self):
            raise RuntimeError("boom")
    try:
        ss.save_json(target, {"bad": Bomb()})
    except (TypeError, RuntimeError):
        pass
    # The previously-written file must still be readable; no truncation.
    assert json.loads(target.read_text())["good"] == "value"


