"""Focused guards for the final static frontend P1 fixes.

Browser and screen-reader behavior still needs real-device verification. These
tests protect the server-rendered semantics and the recovery logic that can be
checked deterministically without claiming visual validation.
"""

from pathlib import Path
from types import SimpleNamespace

import charts
import codex_dashboard
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
    assert "var skip = document.querySelector('.skip-link')" in page
    assert "if (skip) skip.setAttribute('inert', '')" in page
    assert "if (skip) skip.removeAttribute('inert')" in page
    assert "if (ev.key === 'Tab' && sb.classList.contains('open'))" in page
    assert "var items = focusableItems()" in page
    assert "active === first || !sb.contains(active)" in page
    assert "active === last || !sb.contains(active)" in page
    assert "last.focus()" in page
    assert "first.focus()" in page


def test_codex_refresh_has_timeout_login_recovery_and_live_error_semantics():
    page = codex_dashboard.render_page(
        {},
        render_admin_shell=lambda _active, _title, content, **_kwargs: content,
    )
    js = (ROOT / "hysteria" / "codex_quota.js").read_text(encoding="utf-8")

    assert 'data-role="collector-status"' in page
    assert 'role="status" aria-live="polite" aria-atomic="true"' in page
    assert 'id="codex-collector-error"' in page
    assert 'role="alert" aria-live="assertive" aria-atomic="true"' in page
    assert "var REQUEST_TIMEOUT_MS = 10000" in js
    assert "Promise.race([request, timeout])" in js
    assert "response.status === 401" in js
    assert 'authError.code = "login_required"' in js
    assert 'auth: ["登录已失效", "is-error"]' in js
    assert 'setCollectorStatus("auth")' in js
    assert 'loginRequired ? "登录状态已失效"' in js
    assert 'loginLink.href = "/login"' in js
    assert "if (timeoutId) clearTimeout(timeoutId)" in js
    assert "refreshBtn.disabled = false" in js
    assert "function startCountdowns()" in js
    assert "function stopCountdowns()" in js
    assert "if (document.hidden) {" in js
    assert "stopCountdowns()" in js
    assert "if (document.hidden) return;" in js


def test_admin_and_usage_requests_timeout_back_off_and_remain_retryable():
    admin = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    usage = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")

    for script in (admin, usage):
        assert "REQUEST_TIMEOUT_MS = 10000" in script
        assert "POLL_BASE_MS = 30000" in script
        assert "POLL_MAX_MS = 240000" in script
        assert "RETRY_JITTER_MS = 4000" in script
        assert "typeof AbortController" in script
        assert "Promise.race([fetch(url, requestOptions), timeoutPromise])" in script
        assert "function retryDelay" in script
        assert "Math.pow(2, exponent)" in script
        assert "function scheduleNext" in script
        assert "setTimeout(function" in script
        assert "setInterval(tick" not in script

    assert "请求超时' : '刷新失败') + ' · 点此重试" in admin
    assert '"请求超时" : "刷新失败") + " · 可点立即刷新"' in usage
    assert 'fetchWithTimeout(historyHost.dataset.url || "/admin/usage-history"' in usage
    assert 'id="usage-history-retry"' in usage


def test_primary_controls_and_codex_light_surfaces_use_aa_text_contrast():
    styles = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")

    assert "background: linear-gradient(135deg, #6556d8, #493bb8);" in styles
    assert (
        ".filter-chips .chip.active { color: white; background: #5a4fd5;"
        in styles
    )
    assert (
        ".codex-records-table th { padding: 12px 18px; color: #53647a;"
        in styles
    )
    assert (
        ".codex-records-table .empty { color: #59697f; background: #fff; }"
        in styles
    )

    for foreground, background in (
        ("#ffffff", "#6556d8"),
        ("#ffffff", "#493bb8"),
        ("#ffffff", "#5a4fd5"),
        ("#53647a", "#f6f8fb"),
        ("#59697f", "#ffffff"),
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


def test_logout_confirmation_has_complete_auth_layout_and_codex_marks_are_hidden():
    styles = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")
    logout = ss.render_logout_confirmation("panel.test")
    codex = codex_dashboard.render_page(
        {},
        render_admin_shell=lambda _active, _title, content, **_kwargs: content,
    )

    assert 'class="auth-page"' in logout
    assert 'class="auth-wrap"' in logout
    assert 'class="auth-card"' in logout
    assert 'class="auth-brand"' in logout
    assert ".auth-page {" in styles
    assert ".auth-wrap { width: min(430px, 100%); }" in styles
    assert ".auth-brand {" in styles
    assert "border-radius: var(--radius-lg);" in styles
    assert codex.count(
        'class="codex-context-icon" aria-hidden="true"'
    ) == 3


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
        "成本校准数据，可横向滚动",
    ):
        assert 'tabindex="0"' in health_source
        assert f'aria-label="{label}"' in health_source
