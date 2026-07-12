from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_admin_copy_link_has_manual_fallback():
    text = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    assert "function manualCopy()" in text
    assert "window.prompt" in text
    assert ".catch(manualCopy)" in text


def test_usage_polling_pauses_when_tab_is_hidden():
    text = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("visibilitychange"' in text
    assert 'window.addEventListener("pagehide", stop)' in text
    assert "if (inflight) return" in text


def test_usage_page_has_manual_refresh_status_controls():
    text = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert 'getElementById("usage-refresh-now")' in text
    assert '[data-role="poll-status"]' in text
    assert 'refreshBtn.addEventListener("click", function () { tick(true); })' in text
    assert 'setPollStatus("更新 " + stamp(), "is-live")' in text


def test_usage_poll_refreshes_charts_and_user_detail_cards():
    text = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert "function updateHourlyChart" in text
    assert "function updateHeatmap" in text
    assert "function updateTopN" in text
    assert '[data-stat=user_cycle] .v' in text


def test_admin_poll_reports_errors_and_handles_user_list_changes():
    text = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    assert "刷新失败" in text
    assert "用户列表已变更" in text
    assert "data-user-count" in (ROOT / "hysteria" / "subscription_service.py").read_text(encoding="utf-8")


def test_polling_uses_tiered_lightweight_endpoints():
    admin = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    usage = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert "fetch('/admin/overview.json'" in admin
    assert "spark_html" not in admin
    assert 'return "/admin/analytics.json"' in usage
    assert 'CHART_REFRESH_MS = 30000' in usage
    assert '"?summary=1"' in usage
    assert "function chartSignature" in usage


def test_admin_uses_one_reusable_edit_dialog():
    js = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    service = (ROOT / "hysteria" / "subscription_service.py").read_text(encoding="utf-8")
    assert "user-edit-dialog" in js
    assert "function openEditDialog" in js
    assert 'id="user-edit-dialog"' in service


def test_usage_history_is_loaded_on_first_expand():
    text = (ROOT / "hysteria" / "usage.js").read_text(encoding="utf-8")
    assert 'getElementById("usage-history")' in text
    assert 'historyDetails.addEventListener("toggle"' in text
    assert 'fetch(historyHost.dataset.url || "/admin/usage-history"' in text
