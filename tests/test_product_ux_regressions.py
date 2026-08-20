"""Regression coverage for the panel's highest-risk product UX contracts.

These tests intentionally combine rendered-HTML checks with a few real HTTP
round trips.  They guard behavior that is easy to regress while refactoring
server-rendered templates: form accessibility, inactive-account affordances,
safe destructive actions, cache-busted assets, and unlimited quota editing.
"""

from contextlib import contextmanager
from datetime import datetime
from html.parser import HTMLParser
from http.server import ThreadingHTTPServer
import hashlib
import http.client
import json
from pathlib import Path
import threading
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import codex_dashboard
import subscription_service as ss


ROOT = Path(__file__).resolve().parents[1]
SH = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 7, 18, 12, tzinfo=SH)


@contextmanager
def _running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ss.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _seed_state(tmp_path, monkeypatch, *, users=None):
    """Point renderers at a complete, isolated state directory."""
    payloads = {
        "USERS_FILE": users or {},
        "USAGE_FILE": {},
        "USAGE_DAILY_FILE": {},
        "USAGE_HOURLY_FILE": {},
        "USAGE_PRESERVED_FILE": {},
        "ONLINE_FILE": {},
        "META_FILE": {
            "admin_token": "admin-token",
            "admin_user": "admin",
            "admin_pass_hash": "test-hash",
            "settlement_day": 1,
            "cycle_length_days": 30,
            "cycle_anchor_date": "2026-07-01",
        },
    }
    for attr, payload in payloads.items():
        path = tmp_path / f"{attr.lower()}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(ss, attr, path)

    template = tmp_path / "template.yaml"
    template.write_text(
        "proxies: []\n"
        "proxy-groups: []\n"
        "rules:\n"
        "  - DOMAIN-SUFFIX,example.com,DIRECT\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(ss, "TEMPLATE_FILE", template)
    monkeypatch.setattr(ss, "USAGE_LOCK_FILE", tmp_path / "usage.lock")
    monkeypatch.setattr(ss, "TEMPLATE_LOCK_FILE", tmp_path / "template.lock")
    monkeypatch.setattr(ss, "local_now", lambda: NOW)


class _FormAccessibilityAudit(HTMLParser):
    """Collect label/control relationships without depending on a browser."""

    CONTROL_TAGS = {"input", "select", "textarea"}

    def __init__(self):
        super().__init__()
        self.ids = []
        self.label_targets = set()
        self.controls = []
        self._label_depth = 0

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        element_id = values.get("id")
        if element_id:
            self.ids.append(element_id)

        if tag == "label":
            self._label_depth += 1
            if values.get("for"):
                self.label_targets.add(values["for"])
            return

        if tag not in self.CONTROL_TAGS:
            return
        if tag == "input" and values.get("type", "text").lower() == "hidden":
            return
        self.controls.append(
            {
                "tag": tag,
                "id": element_id,
                "name": values.get("name"),
                "nested_label": self._label_depth > 0,
                "aria_label": values.get("aria-label"),
                "aria_labelledby": values.get("aria-labelledby"),
            }
        )

    def handle_endtag(self, tag):
        if tag == "label":
            self._label_depth = max(0, self._label_depth - 1)

    @property
    def control_ids(self):
        return {control["id"] for control in self.controls if control["id"]}

    @property
    def unlabeled_controls(self):
        return [
            control
            for control in self.controls
            if not (
                control["nested_label"]
                or control["id"] in self.label_targets
                or control["aria_label"]
                or control["aria_labelledby"]
            )
        ]


def test_edit_dialog_clears_sensitive_password_fields_before_each_user():
    js = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    close_block = js.split("function closeEditDialog()", 1)[1].split(
        "function setEditValue", 1
    )[0]
    open_block = js.split("function openEditDialog(btn)", 1)[1].split(
        "function confirmAdminAction", 1
    )[0]

    assert "editForm.reset()" in close_block
    reset_at = open_block.index("editForm.reset()")
    panel_clear_at = open_block.index("setEditValue('panel_password', '')")
    proxy_clear_at = open_block.index("setEditValue('password', '')")
    user_set_at = open_block.index("setEditValue('user', user)")
    assert reset_at < user_set_at < panel_clear_at < proxy_clear_at

    quota_line = next(
        line for line in open_block.splitlines() if "setEditValue('quota_gb'" in line
    )
    assert "btn.dataset.quotaGb" in quota_line
    assert "Number(" not in quota_line


def test_unlimited_quota_survives_an_unrelated_admin_edit(
    tmp_path, monkeypatch
):
    existing = {
        "sub_token": "existing-token",
        "panel_pass_hash": "existing-panel-hash",
        "panel_password_must_change": True,
        "password_hash": "existing-proxy-hash",
        "monthly_quota_bytes": 0,
        "quota_extra_bytes": 0,
        "max_devices": 2,
        "metered": True,
        "guest": True,
        "tuic_enabled": False,
    }
    _seed_state(tmp_path, monkeypatch, users={"alice": existing})
    monkeypatch.setattr(ss, "ensure_meta", lambda: {"admin_token": "admin-token"})
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    monkeypatch.setattr(ss.tuic_config, "sync_all", lambda **_kwargs: False)

    form = urlencode(
        {
            "user": "alice",
            "user_revision": ss.user_config_revision(existing),
            "max_devices": "3",
            "quota_gb": "0",
            "quota_extra_gb": "0",
            "note": "still unlimited",
            "guest": "on",
            "tuic_enabled": "on",
        }
    )
    with _running_server() as server:
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        conn.request(
            "POST",
            "/admin/update",
            body=form,
            headers={
                "Host": "panel.test",
                "Content-Type": "application/x-www-form-urlencoded",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        response = conn.getresponse()
        response.read()
        assert response.status == 302
        assert response.getheader("Location") == "/admin?msg=updated+alice"
        conn.close()

    saved = json.loads(ss.USERS_FILE.read_text(encoding="utf-8"))["alice"]
    assert saved["monthly_quota_bytes"] == 0
    assert saved["quota_extra_bytes"] == 0
    assert saved["note"] == "still unlimited"
    assert saved["max_devices"] == 3
    assert saved["panel_pass_hash"] == "existing-panel-hash"
    assert saved["panel_password_must_change"] is True
    assert saved["password_hash"] == "existing-proxy-hash"

    monkeypatch.setattr(
        ss, "scaled_usage_for_user", lambda *_args, **_kwargs: (0, 0, 0)
    )
    monkeypatch.setattr(ss, "daily_window_for_user", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(ss, "sparkline_svg", lambda *_args, **_kwargs: "")
    row = ss.row_form(
        "alice", saved, {}, "panel.test", "https://panel.test", daily={}, now=NOW
    )
    assert 'data-quota-gb="0"' in row
    assert '<span class="summary-preview">不限 · 3 设备 · 按量</span>' in row

    admin_page = ss.render_admin("panel.test", "https://panel.test")
    assert 'id="edit-quota-gb" name="quota_gb" type="number" min="0"' in admin_page
    assert 'data-quota-gb="0"' in admin_page


def test_login_and_admin_forms_have_programmatic_labels_and_unique_ids(
    tmp_path, monkeypatch
):
    _seed_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "token",
                "monthly_quota_bytes": 0,
                "max_devices": 2,
                "metered": True,
            }
        },
    )
    pages = {
        "admin login": ss.render_login("panel.test", msg="登录失败"),
        "user login": ss.render_user_login(
            "panel.test", msg="登录失败", username="alice"
        ),
        "password change": ss.render_user_change_password(
            "panel.test", "alice", msg="current password wrong"
        ),
        "user admin": ss.render_admin("panel.test", "https://panel.test"),
        "settings": ss.render_settings("panel.test"),
        "template editor": ss.render_config_editor("panel.test"),
        "rules": ss.render_rules("panel.test"),
    }

    for name, page in pages.items():
        audit = _FormAccessibilityAudit()
        audit.feed(page)
        assert len(audit.ids) == len(set(audit.ids)), f"{name}: duplicate id"
        assert audit.label_targets <= audit.control_ids, (
            f"{name}: label targets missing controls: "
            f"{audit.label_targets - audit.control_ids}"
        )
        assert not audit.unlabeled_controls, (
            f"{name}: controls without labels: {audit.unlabeled_controls}"
        )


def test_pages_expose_skip_target_main_landmark_and_current_navigation():
    public_page = ss.render_login("panel.test")
    assert '<a class="skip-link" href="#main-content">' in public_page
    assert public_page.count('id="main-content"') == 1
    assert '<main id="main-content" tabindex="-1">' in public_page

    admin_page = ss.render_admin_shell("health", "健康状态", "<p>ok</p>")
    assert '<a class="skip-link" href="#main-content">' in admin_page
    assert admin_page.count('id="main-content"') == 1
    assert '<main class="content" id="main-content" tabindex="-1">' in admin_page
    assert admin_page.count('aria-current="page"') == 1
    assert (
        'href="/admin/health" class="sidebar-link active" aria-current="page"'
        in admin_page
    )
    assert 'aria-controls="sidebar"' in admin_page
    assert 'aria-expanded="false"' in admin_page
    assert (
        'id="sidebar-close" type="button" aria-label="关闭导航"'
        in admin_page
    )
    assert "cb.addEventListener('click'" in admin_page
    assert "sb.querySelector('#sidebar-close, a, button')" in admin_page

    icon = ss.icon("lock")
    assert 'aria-hidden="true"' in icon
    assert 'focusable="false"' in icon


def test_feedback_alerts_are_persistent_and_have_live_region_semantics():
    error = ss.render_alert("<bad>", "err")
    success = ss.render_alert("saved", "flash")
    assert 'role="alert"' in error
    assert 'aria-live="assertive"' in error
    assert 'aria-atomic="true"' in error
    assert "&lt;bad&gt;" in error
    assert 'role="status"' in success
    assert 'aria-live="polite"' in success

    css = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")
    alert_rules = css.split("/* Alerts and disclosure", 1)[1].split(
        "details > summary", 1
    )[0]
    assert "animation" not in alert_rules
    assert "flash-fade" not in css


def test_health_status_uses_an_authenticated_local_fragment_refresh(
    monkeypatch
):
    for probe_name in (
        "probe_cron_heartbeat",
        "probe_systemd",
        "probe_auth_readiness",
        "probe_disk",
        "probe_cert",
        "probe_panel_tls",
        "probe_certbot_renewal",
        "probe_online",
        "probe_xray_config_permissions",
        "probe_hysteria_update",
        "probe_recent_backup",
    ):
        monkeypatch.setattr(
            ss, probe_name, lambda *_args, **_kwargs: {"status": "ok"}
        )
    monkeypatch.setattr(
        ss,
        "_health_card",
        lambda title, _result: f'<article data-health="{title}">{title}</article>',
    )
    monkeypatch.setattr(ss, "render_line_radar", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(ss, "render_cost_calibrator", lambda *_args, **_kwargs: "")

    page = ss.render_health("panel.test")
    fragment = ss.render_health_fragment()
    assert 'http-equiv="refresh"' not in page.lower()
    assert 'id="health-live-grid"' in page
    assert 'id="health-refresh-now"' in page
    assert "fetch('/admin/health.fragment'" in page
    assert "function retryDelay()" in page
    assert "function scheduleNext()" in page
    assert "setInterval(refresh" not in page
    assert "activeController" in page
    assert "visibilitychange" in page
    # Fragment payload is bare rows for tbody.innerHTML, no outer <tbody>.
    assert "<tbody" not in fragment.lower()
    assert "</tbody" not in fragment.lower()
    # Each probe is rendered as one row with a stable data-health identity.
    assert fragment.count("<tr") == 15
    assert fragment.count("data-health=") == 15
    for probe_title in (
        "CRON 心跳",
        "鉴权服务",
        "鉴权依赖",
        "Hysteria",
        "Xray",
        "TUIC",
        "限流 Timer",
        "TLS 证书",
        "面板 HTTPS",
        "证书自动续期",
        "在线用户",
        "Xray 配置权限",
        "Hysteria 更新",
        "最近备份",
        "磁盘",
    ):
        assert f'data-health="{probe_title}"' in fragment
    # Healthy probes use neutral badge, the disk probe uses danger.
    assert fragment.count('<span class="badge">') == 14
    assert fragment.count('<span class="badge badge-danger">') == 1
    assert "<!doctype" not in fragment.lower()
    assert "<main" not in fragment.lower()
    # The page's SSR tbody wraps the same row markup.
    assert "<tbody>" in page
    assert fragment in page

    auth = {"ok": False}
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: auth["ok"])
    with _running_server() as server:
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        conn.request("GET", "/admin/health.fragment", headers={"Host": "panel.test"})
        denied = conn.getresponse()
        denied.read()
        assert denied.status == 401

        auth["ok"] = True
        conn.request("GET", "/admin/health.fragment", headers={"Host": "panel.test"})
        allowed = conn.getresponse()
        body = allowed.read().decode("utf-8")
        assert allowed.status == 200
        # Live fragment payload: bare rows for tbody.innerHTML.
        assert "<tbody" not in body.lower()
        assert body.count("<tr") == 15
        assert body.count("data-health=") == 15
        conn.close()


def test_mobile_user_cards_keep_filtered_rows_hidden():
    css = (ROOT / "hysteria" / "admin.css").read_text(encoding="utf-8")

    mobile_cards = css.index(
        ".users-table tbody, .users-table tr { display: block;"
    )
    mobile_hidden = css.index(
        ".users-table tr.hidden { display: none; }",
        mobile_cards,
    )
    assert mobile_cards < mobile_hidden


def test_corrupt_template_is_preserved_and_all_overwrite_controls_lock(
    tmp_path,
    monkeypatch,
):
    _seed_state(tmp_path, monkeypatch)
    raw = "rules: [unterminated\noperator-note: keep-me\n"
    ss.TEMPLATE_FILE.write_text(raw, encoding="utf-8")

    editor = ss.render_config_editor("panel.test")
    assert "为避免覆盖原配置，编辑与保存已锁定" in editor
    assert raw in editor
    assert 'data-load-failed="true"' in editor
    assert 'readonly aria-readonly="true"' in editor
    assert '<button class="btn danger-btn" type="submit" disabled' in editor
    assert 'href="/admin/config">重新加载模板</a>' in editor
    assert ">{}</textarea>" not in editor

    rules = ss.render_rules("panel.test")
    assert "所有规则修改已锁定" in rules
    assert 'badge">不可用' in rules
    assert 'action="/admin/rules/add"' not in rules
    assert 'action="/admin/rules/delete"' not in rules
    assert 'action="/admin/rules/raw"' not in rules
    assert 'action="/admin/rule-pack/apply"' not in rules
    assert ss.TEMPLATE_FILE.read_text(encoding="utf-8") == raw


def test_deployed_public_host_cannot_be_replaced_by_request_host(monkeypatch):
    monkeypatch.setattr(ss, "CONFIGURED_PUBLIC_HOST", "panel.example.test")

    host = ss.configured_public_host("attacker.example")
    assert host == "panel.example.test"
    assert ss.safe_base_url(host, "https", "9444") == (
        "https://panel.example.test:9444"
    )


def test_disabled_user_panel_hides_subscription_mutations_and_polling(
    tmp_path, monkeypatch
):
    _seed_state(tmp_path, monkeypatch)
    cfg = {
        "sub_token": "secret-token",
        "monthly_quota_bytes": 0,
        "max_devices": 2,
        "disabled": True,
    }
    page = ss.render_user_panel(
        "panel.test",
        "https://panel.test",
        "alice",
        "secret-token",
        cfg,
        session_auth=True,
    )

    assert "账号已停用，请联系管理员" in page
    assert 'role="alert"' in page
    assert "订阅操作已暂停" in page
    assert "已暂停更新" in page
    assert 'id="profile-show-qr"' not in page
    assert 'data-action="rotate-token"' not in page
    assert 'href="/sub/alice' not in page
    assert "var pollUrl" not in page
    assert "secret-token" not in page


def test_destructive_admin_actions_have_consequence_aware_confirmations(
    tmp_path, monkeypatch
):
    _seed_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "token",
                "monthly_quota_bytes": 10 << 30,
                "max_devices": 2,
            }
        },
    )
    page = ss.render_admin("panel.test", "https://panel.test")
    for action in (
        "reset-user-usage",
        "refresh-user-usage",
        "rotate-user-token",
        "disable-user",
        "delete-user",
        "reset-all",
    ):
        assert f'data-action="{action}"' in page
    assert (
        'action="/admin/reset-usage-all" data-action="reset-all"' in page
    )
    assert (
        '<button class="btn danger-btn btn-sm" type="submit">'
        "一键清空本周期用量</button>" in page
    )

    js = (ROOT / "hysteria" / "admin_poll.js").read_text(encoding="utf-8")
    for action in (
        "delete-user",
        "rotate-user-token",
        "disable-user",
        "reset-user-usage",
        "refresh-user-usage",
        "reset-all",
    ):
        assert f"if (action === '{action}') return confirm(" in js
    for consequence in (
        "此操作不可撤销",
        "旧订阅/面板链接将立即失效",
        "拒绝新连接并断开其现有会话",
        "从服务器本周期总计中扣除",
        "服务器本周期总计会保留",
        "清空全部用户本周期已用流量",
    ):
        assert consequence in js

    rules_page = ss.render_rules("panel.test")
    assert 'data-action="delete-rule"' in rules_page
    assert "confirm('确认删除此规则？')" in rules_page


def test_static_assets_are_versioned_and_honor_etag_revalidation(
    tmp_path, monkeypatch
):
    _seed_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "token",
                "monthly_quota_bytes": 10 << 30,
                "max_devices": 2,
            }
        },
    )
    css_version = ss.BASE_CSS_ETAG.strip('"')
    admin_version = ss.ADMIN_POLL_JS_ETAG.strip('"')
    usage_version = ss.USAGE_JS_ETAG.strip('"')
    codex_version = ss.CODEX_QUOTA_JS_ETAG.strip('"')

    assert (
        f'/static/style.css?v={css_version}'
        in ss.html_page("test", "<p>content</p>")
    )
    assert (
        f'/static/admin-poll.js?v={admin_version}'
        in ss.render_admin("panel.test", "https://panel.test")
    )
    assert (
        f'/static/usage.js?v={usage_version}'
        in ss.render_usage_page("panel.test")
    )
    codex_page = codex_dashboard.render_page(
        {},
        render_admin_shell=lambda _active, _title, content, **_kwargs: content,
        asset_version=codex_version,
    )
    assert f'/static/codex-quota.js?v={codex_version}' in codex_page

    expected_css_etag = (
        '"' + hashlib.sha1(ss.BASE_CSS_BYTES).hexdigest()[:16] + '"'
    )
    assert ss.BASE_CSS_ETAG == expected_css_etag

    with _running_server() as server:
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=3
        )
        conn.request(
            "GET",
            f"/static/style.css?v={css_version}",
            headers={"Host": "panel.test"},
        )
        fresh = conn.getresponse()
        body = fresh.read()
        assert fresh.status == 200
        assert body == ss.BASE_CSS_BYTES
        assert fresh.getheader("ETag") == ss.BASE_CSS_ETAG
        assert fresh.getheader("Cache-Control") == "public, max-age=86400"

        conn.request(
            "GET",
            f"/static/style.css?v={css_version}",
            headers={
                "Host": "panel.test",
                "If-None-Match": f"W/{ss.BASE_CSS_ETAG}",
            },
        )
        cached = conn.getresponse()
        cached_body = cached.read()
        assert cached.status == 304
        assert cached_body == b""
        assert cached.getheader("ETag") == ss.BASE_CSS_ETAG
        assert cached.getheader("Cache-Control") == "public, max-age=86400"
        conn.close()
