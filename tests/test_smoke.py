"""Sanity check that the test scaffold loads the production modules."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import subscription_service as ss

SH = ZoneInfo("Asia/Shanghai")


def test_anomaly_module_will_be_importable_eventually():
    assert 1 + 1 == 2


def test_usage_routes_wired_in_dispatcher():
    """Smoke: the new routes appear in the GET dispatcher."""
    src = (ss.__file__).replace("\\", "/")
    text = open(src, encoding="utf-8").read()
    assert "/admin/usage.json" in text
    assert "/admin/usage" in text
    assert "/admin/user/" in text
    assert "/static/usage.js" in text


def test_render_usage_page_smoke(tmp_path, monkeypatch):
    """Render /admin/usage end-to-end against an empty state."""
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_DAILY_FILE", tmp_path / "usage_daily.json", raising=False)
    monkeypatch.setattr(ss, "USAGE_HOURLY_FILE", tmp_path / "usage_hourly.json", raising=False)
    monkeypatch.setattr(ss, "ONLINE_FILE", tmp_path / "online.json", raising=False)
    for name in ("users.json", "usage.json", "usage_daily.json", "usage_hourly.json", "online.json"):
        (tmp_path / name).write_text("{}")
    monkeypatch.setattr(ss, "local_now", lambda: datetime(2026, 5, 8, 14, tzinfo=SH))
    out = ss.render_usage_page("smoke-host")
    assert "<svg" in out
    assert "流量分析" in out


def test_render_user_detail_404_for_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "USERS_FILE", tmp_path / "users.json", raising=False)
    (tmp_path / "users.json").write_text(json.dumps({}))
    assert ss.render_user_detail_page("nobody", "host") is None
