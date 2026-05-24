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
    assert 'refreshBtn.addEventListener("click", tick)' in text
    assert 'setPollStatus("更新 " + stamp(), "is-live")' in text
