"""Focused regressions for unlimited device limits on user usage pages."""

from datetime import datetime
from types import SimpleNamespace

import charts
import pytest
import usage_dashboard


NOW = datetime.fromisoformat("2026-07-18T12:00:00+00:00")


def _context(user_config):
    state = {
        "users": {"alice": user_config},
        "online": {"alice": 3},
        "hourly": {},
        "daily": {},
    }
    return SimpleNamespace(
        users_file="users",
        online_file="online",
        usage_hourly_file="hourly",
        usage_daily_file="daily",
        load_json=lambda path, default: state.get(path, default),
        user_expiry_state=lambda _cfg, *, today: {
            "expired": False,
            "expires_at": None,
            "label": "长期有效",
        },
        cycle_raw_for_user=lambda _uid, _daily, *, now: (0, 0, 0),
        user_total_quota=lambda _cfg: 0,
        display_multiplier=1.0,
        hourly_retention_hours=1,
        local_now=lambda: NOW,
        fmt_bytes=lambda value: f"{value} B",
        render_admin_shell=lambda _active, _title, content, **_kwargs: content,
        local_tz_label="UTC",
        asset_version="test",
    )


@pytest.mark.parametrize(
    ("user_config", "expected"),
    (
        ({"max_devices": 0}, 0),
        ({}, 2),
    ),
)
def test_user_payload_preserves_unlimited_zero_and_missing_default(
    user_config,
    expected,
):
    payload = usage_dashboard.build_user_json_payload(
        _context(user_config),
        "alice",
        now=NOW,
        include_charts=False,
    )

    assert payload is not None
    assert payload["max_devices"] == expected


def test_user_page_renders_unlimited_devices_in_clear_chinese(monkeypatch):
    monkeypatch.setattr(charts, "hourly_bars_svg", lambda *_a, **_k: "")
    monkeypatch.setattr(
        charts,
        "weekday_hour_heatmap_svg",
        lambda *_a, **_k: "",
    )

    rendered = usage_dashboard.render_user_detail_page(
        _context({"max_devices": 0}),
        "alice",
        "panel.test",
    )

    assert rendered is not None
    assert "在线 <span data-role=\"detail-online\">3</span>" in rendered
    assert "设备上限：无限制" in rendered
    assert "/ 0 在线" not in rendered
