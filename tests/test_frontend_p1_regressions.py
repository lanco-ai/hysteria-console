"""Focused guards for the final static frontend P1 fixes.

Browser and screen-reader behavior still needs real-device verification. These
tests protect the server-rendered semantics and the recovery logic that can be
checked deterministically without claiming visual validation.
"""

from pathlib import Path
from types import SimpleNamespace

import charts
import health_widgets
import incident_console
import subscription_service as ss


ROOT = Path(__file__).resolve().parents[1]


def _relative_luminance(hex_color):
    channels = [
        int(hex_color[index : index + 2], 16) / 255
        for index in (1, 3, 5)
    ]
    linear = [
        value / 12.92
        if value <= 0.04045
        else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first, second):
    bright, dark = sorted(
        (_relative_luminance(first), _relative_luminance(second)),
        reverse=True,
    )
    return (bright + 0.05) / (dark + 0.05)


def _blend(foreground, background, opacity):
    foreground_channels = [
        int(foreground[index : index + 2], 16)
        for index in (1, 3, 5)
    ]
    background_channels = [
        int(background[index : index + 2], 16)
        for index in (1, 3, 5)
    ]
    mixed = [
        round(opacity * front + (1 - opacity) * back)
        for front, back in zip(
            foreground_channels,
            background_channels,
        )
    ]
    return "#" + "".join(f"{channel:02x}" for channel in mixed)


def test_mobile_sidebar_contains_focus_and_removes_background_skip_target():
    page = ss.render_admin_shell("dashboard", "总览", "<p>content</p>")

    assert '<a class="skip-link" href="#main-content">' in page
    assert '/static/shell.js?v=' in page
    page = ss.web_assets.ASSETS['/static/shell.js'][0].decode()
    assert "var skip = document.querySelector('.skip-link')" in page
    assert "if (skip) skip.setAttribute('inert', '')" in page
    assert "if (skip) skip.removeAttribute('inert')" in page
    assert "if (ev.key === 'Tab' && sb.classList.contains('open'))" in page
    assert "var items = focusableItems()" in page
    assert "active === first || !sb.contains(active)" in page
    assert "active === last || !sb.contains(active)" in page
    assert "last.focus()" in page
    assert "first.focus()" in page




def test_admin_and_usage_requests_timeout_back_off_and_remain_retryable():
    admin = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    usage = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")

    for script in (admin, usage):
        assert "REQUEST_TIMEOUT_MS = 10000" in script
        assert "POLL_BASE_MS = 30000" in script
        assert "POLL_MAX_MS = 240000" in script
        assert "RETRY_JITTER_MS = 4000" in script
        assert "window.Hy2UI.fetchWithTimeout(url, options, REQUEST_TIMEOUT_MS" in script
        assert "function retryDelay" in script
        assert "Math.pow(2, exponent)" in script
        assert "function scheduleNext" in script
        assert "setTimeout(function" in script
        assert "setInterval(tick" not in script

    assert "请求超时' : '刷新失败') + ' · 点此重试" in admin
    assert '"请求超时" : "刷新失败") + " · 可点立即刷新"' in usage
    assert 'fetchWithTimeout(historyHost.dataset.url || "/admin/usage-history"' in usage
    assert 'id="usage-history-retry"' in usage


def test_primary_controls_and_light_surfaces_use_aa_text_contrast():
    styles = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")

    import re
    def token(name):
        return re.search(r'--' + name + r':\s*(#[0-9a-fA-F]{6})', styles).group(1)
    primary = styles.split('body.has-shell .btn-primary {', 1)[1].split('}', 1)[0]
    background = re.search(r'background:\s*(#[0-9a-fA-F]{6})', primary).group(1)
    assert 'color: #fff;' in primary
    for foreground, background in (
        ('#ffffff', background),
        (token('text-primary'), token('bg-subtle')),
        (token('text-secondary'), token('bg-surface')),
    ):
        assert _contrast(foreground, background) >= 4.5


def test_nonzero_heatmap_cells_keep_three_to_one_graphical_contrast():
    charts_source = (ROOT / "hysteria" / "charts.py").read_text(
        encoding="utf-8"
    )
    usage_js = (ROOT / "hysteria" / "usage.js").read_text(
        encoding="utf-8"
    )
    minimum_cell = _blend("#9d8cff", "#101c31", 0.60)

    assert _contrast(minimum_cell, "#101c31") >= 3.0
    assert "0.60 + 0.40 * (v / max_v)" in charts_source
    assert "0.60 + 0.40 * value / maxValue" in usage_js


def test_logout_confirmation_has_complete_auth_layout():
    styles = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")
    logout = ss.render_logout_confirmation("panel.test")

    assert 'class="auth-page"' in logout
    assert 'class="auth-wrap"' in logout
    assert 'class="auth-card"' in logout
    assert 'class="auth-brand"' in logout
    assert ".auth-page {" in styles
    assert ".auth-wrap { width: min(430px, 100%); }" in styles
    assert ".auth-brand {" in styles
    assert "border-radius: var(--radius-lg);" in styles


def test_heatmap_has_an_expandable_semantic_hourly_data_table():
    grid = [
        {
            "date": f"2026-07-{day:02d}",
            "hours": [day * 1000 + hour for hour in range(24)],
        }
        for day in range(12, 19)
    ]
    markup = charts.weekday_hour_heatmap_svg(
        grid,
        current_hour_iso="2026-07-18T12",
    )

    assert 'role="img"' in markup
    assert "详细数值见下方可展开数据表" in markup
    assert '<details class="heatmap-data-details mt-sm">' in markup
    assert "<summary>查看每小时数据表</summary>" in markup
    assert 'tabindex="0"' in markup
    assert 'aria-label="7 天每小时流量数据，可横向滚动"' in markup
    assert '<caption class="sr-only">' in markup
    assert markup.count('scope="col"') == 25
    assert markup.count('scope="row"') == 7
    assert 'data-role="heatmap-data-body"' in markup
    assert "2026-07-18 13:00 · 尚未发生" in markup

    js = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert "function updateHeatmapTable(grid, timestamp)" in js
    assert "updateHeatmapTable(grid, timestamp)" in js
    assert "详细数值见下方可展开数据表" in js


def test_incident_and_health_wide_tables_are_keyboard_scrollable(monkeypatch):
    incident_payload = {
        "stats": {"current_hour_bytes": 0, "online": 0},
        "peak_hour": {"bytes": 0, "hour": "", "users": []},
        "users": [],
        "line_radar": {
            "recommendation": "default",
            "reason": "保持默认模板",
        },
        "cost_calibration": {},
        "alerts": [],
    }
    monkeypatch.setattr(
        incident_console,
        "build_incident_payload",
        lambda _ctx, now=None: incident_payload,
    )
    ctx = SimpleNamespace(
        local_now=lambda: SimpleNamespace(),
        render_alert=lambda value: value,
        flash_text=lambda value: value,
        fmt_bytes=lambda value: f"{value} B",
        subscription_profiles={"default": {"label": "默认"}},
        render_line_radar=lambda **_kwargs: "",
        render_line_radar_summary=lambda **_kwargs: "",
        render_cost_calibrator=lambda **_kwargs: "",
        render_admin_shell=lambda _active, _title, content, **_kwargs: content,
    )
    incident_page = incident_console.render_incidents(ctx, "panel.test")

    for label in (
        "峰值小时相关用户，可横向滚动",
        "近期告警状态，可横向滚动",
        "处置候选用户，可横向滚动",
    ):
        assert f'tabindex="0"' in incident_page
        assert f'aria-label="{label}"' in incident_page

    health_source = (
        ROOT / "hysteria" / "health_widgets.py"
    ).read_text(encoding="utf-8")
    for label in (
        "线路质量雷达，可横向滚动",
        "多窗口倍率对比，可横向滚动",
    ):
        assert 'tabindex="0"' in health_source
        assert f'aria-label="{label}"' in health_source
