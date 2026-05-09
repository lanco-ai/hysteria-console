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
    monkeypatch.setattr(ss, "ONLINE_SNAPSHOT_FILE", tmp_path / "online.json", raising=False)
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


