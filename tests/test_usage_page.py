"""Integration tests for /admin/usage and /admin/user/<uid> routes."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import subscription_service as ss

SH = ZoneInfo("Asia/Shanghai")


def test_usage_dashboard_logic_lives_in_dedicated_module():
    import usage_dashboard

    assert usage_dashboard.build_usage_json_payload.__module__ == 'usage_dashboard'
    assert usage_dashboard.render_usage_page.__module__ == 'usage_dashboard'
    assert usage_dashboard.render_user_detail_page.__module__ == 'usage_dashboard'


def _seed_state(tmp_path, monkeypatch, *, users=None, hourly=None, daily=None,
                usage=None, online=None, preserved=None):
    """Repoint all state files at tmp_path and pre-fill them."""
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", tmp_path / "usage_daily.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_PRESERVED_FILE", tmp_path / "usage_preserved.json", raising=False)
    monkeypatch.setattr(ss, "ONLINE_FILE", tmp_path / "online.json", raising=False)
    (tmp_path / "users.json").write_text(json.dumps(users or {}))
    (tmp_path / "usage.json").write_text(json.dumps(usage or {}))
    (tmp_path / "usage_daily.json").write_text(json.dumps(daily or {}))
    (tmp_path / "usage_hourly.json").write_text(json.dumps(hourly or {}))
    (tmp_path / "usage_preserved.json").write_text(json.dumps(preserved or {}))
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
    assert "total_used" in payload
    assert payload["users"][0]["user"] == "alice"
    assert {"tx", "rx", "used", "total", "percent", "online", "spark_html"} <= set(payload["users"][0])


def test_usage_json_route_is_not_shadowed_by_legacy_handler():
    src = Path(ss.__file__).read_text(encoding="utf-8")
    assert src.count("if path == '/admin/usage.json':") == 1


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
    assert 'id="usage-refresh-now"' in html_out
    assert 'data-role="poll-status"' in html_out


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
    assert 'id="usage-refresh-now"' in out
    assert 'data-role="poll-status"' in out


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


def _seed_meta(tmp_path, monkeypatch, **meta):
    """Repoint META_FILE at tmp_path and write the given fields."""
    path = tmp_path / "subscription_meta.json"
    monkeypatch.setattr(ss, "META_FILE", path, raising=False)
    path.write_text(json.dumps(meta))


def test_fixed_15_day_cycle_rolls_independently_of_calendar(tmp_path, monkeypatch):
    """With cycle_length_days=15 and anchor=2026-05-12, the cycle rolls in
    pure 15-day blocks: 5/12-5/26, 5/27-6/10, 6/11-6/25, ..."""
    _seed_meta(tmp_path, monkeypatch,
               settlement_day=12, cycle_length_days=15, cycle_anchor_date="2026-05-12")

    def cycle_for(day):
        now = datetime(2026, 5, day, 10, tzinfo=SH) if day <= 31 else datetime(2026, 6, day - 31, 10, tzinfo=SH)
        return ss.cycle_start_for(now).date(), ss._cycle_days(now)

    s, days = cycle_for(14)  # mid first block
    assert s.isoformat() == "2026-05-12"
    assert days[0] == "2026-05-12" and days[-1] == "2026-05-14"

    s, _ = cycle_for(27)  # day 1 of second block
    assert s.isoformat() == "2026-05-27"

    s, _ = cycle_for(32)  # = 2026-06-01, still in second block
    assert s.isoformat() == "2026-05-27"

    s, _ = cycle_for(41)  # = 2026-06-10, last day of second block
    assert s.isoformat() == "2026-05-27"

    s, _ = cycle_for(42)  # = 2026-06-11, day 1 of third block
    assert s.isoformat() == "2026-06-11"


def test_default_30_day_cycle_unchanged_when_meta_empty(tmp_path, monkeypatch):
    """No META override: default cycle_length=30, anchor derived from
    settlement_day=12 (most recent). Today 2026-05-14 -> anchor 2026-05-12,
    cycle 2026-05-12 .. 2026-06-10."""
    _seed_meta(tmp_path, monkeypatch)  # empty meta
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    assert ss.cycle_start_for(now).date().isoformat() == "2026-05-12"
    days = ss._cycle_days(now)
    assert days[0] == "2026-05-12"
    assert days[-1] == "2026-05-14"  # capped at today
    assert ss.get_cycle_length_days() == 30


def test_cycle_length_clamped_to_supported_range(tmp_path, monkeypatch):
    _seed_meta(tmp_path, monkeypatch, cycle_length_days=999)
    assert ss.get_cycle_length_days() == ss.CYCLE_LENGTH_MAX
    _seed_meta(tmp_path, monkeypatch, cycle_length_days=0)
    assert ss.get_cycle_length_days() == ss.CYCLE_LENGTH_MIN
    _seed_meta(tmp_path, monkeypatch, cycle_length_days="abc")
    assert ss.get_cycle_length_days() == ss.CYCLE_LENGTH_DAYS_DEFAULT


def test_aggregate_stats_reports_configured_cycle_length(tmp_path, monkeypatch):
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_state(tmp_path, monkeypatch, users={"alice": {}}, daily={})
    _seed_meta(tmp_path, monkeypatch,
               settlement_day=12, cycle_length_days=7, cycle_anchor_date="2026-05-12")
    stats = ss._aggregate_stats(now=now, online={})
    assert stats["cycle_total_days"] == 7


def test_admin_form_includes_cycle_length(tmp_path, monkeypatch):
    """The /admin topbar form must expose both 结算日 and 周期 inputs and POST
    to /admin/cycle-config so a single submit updates the cycle calendar."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_state(tmp_path, monkeypatch, users={})
    _seed_meta(tmp_path, monkeypatch,
               settlement_day=12, cycle_length_days=21, cycle_anchor_date="2026-05-12")
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_admin("test-host", "http://test-host")
    assert "/admin/cycle-config" in out, "form must target the new endpoint"
    assert 'name="day"' in out
    assert 'name="length"' in out
    assert 'value="21"' in out, "current cycle_length should be pre-filled"


def test_refresh_zeroes_user_but_keeps_server_total(tmp_path, monkeypatch):
    """`add_preserved_for_user` + `_zero_cycle_daily_hourly_for` together model the
    refresh-traffic flow: the per-user cycle counter drops to 0 (so the user
    regains quota) while the dashboard's '本周期总流量' stays put."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)  # within cycle anchored 2026-05-12
    daily = {
        "2026-05-12": {"alice": {"tx": 0, "rx": 1_000_000_000, "total": 1_000_000_000},
                       "bob": {"tx": 0, "rx": 500_000_000, "total": 500_000_000}},
        "2026-05-13": {"alice": {"tx": 0, "rx": 2_000_000_000, "total": 2_000_000_000}},
    }
    hourly = {"2026-05-12T00": {"alice": {"tx": 0, "rx": 50, "total": 50}}}
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 5_000_000_000},
                       "bob": {"metered": True, "monthly_quota_bytes": 5_000_000_000}},
                daily=daily, hourly=hourly)
    monkeypatch.setattr(ss, "local_now", lambda: now)

    before_total = ss._build_usage_json_payload(now=now)["total_used"]
    before_cycle = ss._aggregate_stats(now=now, online={})["cycle_bytes"]
    assert before_total > 0 and before_cycle > 0

    # Refresh alice: bank her cycle bytes then zero her per-user counters.
    tx, rx, total = ss.usage_for_user("alice", now=now)
    assert total == 3_000_000_000
    ss.add_preserved_for_user("alice", tx, rx, total, now=now)
    ss._zero_cycle_daily_hourly_for(["alice"], now=now)

    payload_after = ss._build_usage_json_payload(now=now)
    stats_after = ss._aggregate_stats(now=now, online={})

    # Alice's per-user counter is now 0 (quota restored).
    alice_after = next(u for u in payload_after["users"] if u["user"] == "alice")
    assert alice_after["used"] == 0
    # Server total/cycle stays at the pre-refresh value (within the multiplier).
    assert payload_after["total_used"] == before_total
    assert stats_after["cycle_bytes"] == before_cycle


def test_refresh_then_new_usage_accumulates_on_top_of_preserved(tmp_path, monkeypatch):
    """Bytes earned after a refresh stack on top of the preserved bucket, so a
    user who keeps using the service continues to grow the server total."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 5_000_000_000}},
                preserved={"2026-05-12": {"alice": {"tx": 0, "rx": 1_000_000_000, "total": 1_000_000_000}}},
                daily={"2026-05-14": {"alice": {"tx": 0, "rx": 200_000_000, "total": 200_000_000}}})
    monkeypatch.setattr(ss, "local_now", lambda: now)
    stats = ss._aggregate_stats(now=now, online={})
    expected_raw = 1_000_000_000 + 200_000_000
    assert stats["cycle_bytes"] == int(expected_raw * ss.DISPLAY_MULTIPLIER)


def test_preserved_bucket_is_cycle_scoped_and_gcs_old_keys(tmp_path, monkeypatch):
    """Preserved bytes are a display adjustment scoped to one cycle. Old cycle
    keys must be dropped on write so the file doesn't grow without bound."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_state(tmp_path, monkeypatch, users={"alice": {}},
                preserved={"2026-04-12": {"alice": {"tx": 0, "rx": 999, "total": 999}}})
    monkeypatch.setattr(ss, "local_now", lambda: now)
    # Old cycle's preserved bytes do not bleed into the current cycle's total.
    assert ss.preserved_raw_for_cycle(now=now) == 0
    # Writing to the current cycle GCs the older key.
    ss.add_preserved_for_user("alice", 0, 100, 100, now=now)
    data = json.loads((tmp_path / "usage_preserved.json").read_text())
    assert "2026-04-12" not in data
    assert data["2026-05-12"]["alice"]["total"] == 100


def test_refresh_usage_button_renders_in_admin(tmp_path, monkeypatch):
    """The user row exposes both '清流量' (subtracts from server total) and
    '刷新流量' (preserves server total) so operators can pick the right semantics."""
    now = datetime(2026, 5, 14, 10, tzinfo=SH)
    _seed_state(tmp_path, monkeypatch,
                users={"alice": {"metered": True, "monthly_quota_bytes": 1_000_000_000}})
    monkeypatch.setattr(ss, "local_now", lambda: now)
    out = ss.render_admin("test-host", "http://test-host")
    assert 'action="/admin/reset-usage"' in out
    assert 'action="/admin/refresh-usage"' in out
    assert '刷新流量' in out


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

