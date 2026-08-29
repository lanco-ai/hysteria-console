"""P0: share-panel hygiene, disabled/expired /panel/ gates, landing display fields."""

from contextlib import contextmanager
from datetime import datetime
from http.server import ThreadingHTTPServer
import http.client
import json
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest

import subscription_service as ss
import user_compat


NOW = datetime(2026, 7, 18, 12, tzinfo=ZoneInfo("Asia/Shanghai"))


@contextmanager
def _running_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), ss.Handler)
    thread = __import__("threading").Thread(
        target=server.serve_forever,
        daemon=True,
    )
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def _configure_state(tmp_path, monkeypatch, *, users=None):
    paths = {
        "USERS_FILE": tmp_path / "users.json",
        "META_FILE": tmp_path / "meta.json",
        "SESSIONS_FILE": tmp_path / "sessions.json",
        "USER_SESSIONS_FILE": tmp_path / "user_sessions.json",
        "USAGE_FILE": tmp_path / "usage.json",
        "USAGE_DAILY_FILE": tmp_path / "usage_daily.json",
        "USAGE_HOURLY_FILE": tmp_path / "usage_hourly.json",
        "ONLINE_FILE": tmp_path / "online.json",
        "RESET_LOG_FILE": tmp_path / "usage_reset.log",
        "USAGE_LOCK_FILE": tmp_path / "usage.lock",
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    _write_json(
        paths["META_FILE"],
        {
            "admin_user": "admin",
            "admin_pass_hash": "unused-but-present",
            "admin_token": "admin-token",
            "settlement_day": 1,
            "cycle_length_days": 30,
            "cycle_anchor_date": "2026-01-01",
        },
    )
    _write_json(paths["USERS_FILE"], users if users is not None else {})
    for name in (
        "SESSIONS_FILE",
        "USER_SESSIONS_FILE",
        "USAGE_FILE",
        "USAGE_DAILY_FILE",
        "USAGE_HOURLY_FILE",
        "ONLINE_FILE",
    ):
        _write_json(paths[name], {})
    monkeypatch.setattr(ss, "local_now", lambda: NOW)
    return paths


def _request(server, method, path, *, form=None, headers=None):
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5,
    )
    request_headers = {"Host": "panel.test"}
    body = None
    if form is not None:
        body = urlencode(form)
        request_headers["Content-Type"] = (
            "application/x-www-form-urlencoded"
        )
    if headers:
        request_headers.update(headers)
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    payload = response.read()
    result = (
        response.status,
        {key.lower(): value for key, value in response.getheaders()},
        payload,
    )
    conn.close()
    return result


def _alice(**overrides):
    cfg = {
        "sub_token": "alice-token",
        "monthly_quota_bytes": 1 << 30,
        "max_devices": 2,
        "disabled": False,
        "vless_uuid": "11111111-1111-4111-8111-111111111111",
    }
    cfg.update(overrides)
    return cfg


def test_user_panel_without_session_shows_share_link_prompt(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    with _running_server() as server:
        status, _h, body = _request(server, "GET", "/user/panel")
    html = body.decode("utf-8")
    assert status == 403
    assert "请使用管理员提供的专属链接" in html
    assert "<input" not in html.lower()
    assert "alice" not in html
    assert 'name="token"' not in html
    assert 'href="/login"' in html


def test_panel_token_exchange_still_creates_session(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    with _running_server() as server:
        status, headers, _body = _request(
            server, "GET", "/panel/alice?token=alice-token",
        )
    assert status == 303
    assert headers["location"] == "/user/panel"
    assert "usid=" in headers.get("set-cookie", "")
    assert ss.get_user_sessions()


def test_disabled_panel_link_is_forbidden_without_session(tmp_path, monkeypatch):
    _configure_state(
        tmp_path, monkeypatch,
        users={"alice": _alice(disabled=True)},
    )
    with _running_server() as server:
        status, _h, body = _request(
            server, "GET", "/panel/alice?token=alice-token",
        )
    assert status == 403
    assert "停用" in body.decode("utf-8")
    assert ss.get_user_sessions() == {}


def test_expired_panel_link_is_forbidden_without_session(tmp_path, monkeypatch):
    _configure_state(
        tmp_path, monkeypatch,
        users={"alice": _alice(expires_at="2020-01-01")},
    )
    with _running_server() as server:
        status, _h, body = _request(
            server, "GET", "/panel/alice?token=alice-token",
        )
    assert status == 403
    assert "到期" in body.decode("utf-8")
    assert ss.get_user_sessions() == {}


def test_unknown_panel_token_does_not_leak_existence(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    with _running_server() as server:
        missing_user = _request(server, "GET", "/panel/ghost?token=alice-token")
        bad_token = _request(server, "GET", "/panel/alice?token=wrong")
    assert missing_user[0] == 403
    assert bad_token[0] == 403
    assert missing_user[2] == bad_token[2]


def test_admin_copy_button_exposes_unique_share_link(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})

    row = ss.row_form(
        "alice", _alice(), {}, "panel.test", "https://panel.test",
        daily={}, now=NOW,
    )
    bob = _alice(
        sub_token="bob-token",
        vless_uuid="22222222-2222-4222-8222-222222222222",
    )
    bob_row = ss.row_form(
        "bob", bob, {}, "panel.test", "https://panel.test",
        daily={}, now=NOW,
    )

    assert '<span class="copy-label">复制专属面板</span>' in row
    assert (
        'data-copy="https://panel.test/panel/alice?token=alice-token"'
        in row
    )
    assert 'data-copy="https://panel.test/user/panel"' not in row
    assert 'data-copy="https://panel.test/panel/bob?token=bob-token"' in bob_row
    assert "alice-token" not in bob_row

    poll_js = (Path(ss.__file__).parent / "admin_poll.js").read_text(
        encoding="utf-8",
    )
    assert "anchor.setAttribute('href', url);" in poll_js
    assert "copyBtn.dataset.copy = url;" in poll_js


def test_landing_write_rejects_overlong_and_control_chars():
    value, err = user_compat.parse_landing_write("x" * 121)
    assert value is None and err == "landing_too_long"
    value, err = user_compat.parse_landing_write("ok\nline")
    assert value is None and err == "landing_invalid"
    value, err = user_compat.parse_landing_write("  电信  ")
    assert value == "电信" and err is None
    value, err = user_compat.parse_landing_write("   ")
    assert value is None and err is None
    value, err = user_compat.parse_landing_write("<b>test</b>")
    assert value is None and err == "landing_invalid"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (" 203.0.113.9 ", "203.0.113.9"),
        ("2001:0db8:0:0:0:0:0:1", "2001:db8::1"),
    ],
)
def test_landing_ip_write_accepts_and_normalizes_ip_addresses(raw, expected):
    assert user_compat.parse_landing_ip_write(raw) == (expected, None)


@pytest.mark.parametrize(
    "raw",
    [
        "home.example.com",
        "203.0.113.9:443",
        "2001:db8::1/64",
        "fe80::1%eth0",
        "<203.0.113.9>",
        "203.0.113.9\n",
    ],
)
def test_landing_ip_write_rejects_non_ip_endpoints(raw):
    assert user_compat.parse_landing_ip_write(raw) == (
        None, "landing_ip_invalid",
    )


def test_admin_update_rejects_invalid_landing_ip_without_mutation(
    tmp_path, monkeypatch,
):
    state = _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    before = state["USERS_FILE"].read_text(encoding="utf-8")
    revision = ss.user_config_revision(json.loads(before)["alice"])
    monkeypatch.setattr(ss, "is_logged_in", lambda _h: True)

    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/admin/update?token=admin-token",
            form={
                "user": "alice",
                "user_revision": revision,
                "max_devices": "2",
                "quota_gb": "1",
                "quota_extra_gb": "0",
                "landing_ip": "home.example.com",
            },
        )

    assert status == 422
    assert "请输入合法的 IPv4 或 IPv6 地址" in body.decode("utf-8")
    assert state["USERS_FILE"].read_text(encoding="utf-8") == before


def test_landing_reader_treats_garbage_as_empty():
    cfg = {
        "landing_isp": 123,
        "landing_region": "x" * 200,
        "landing_note": "ok\x00no",
    }
    assert user_compat.landing_fields(cfg) == {}
    assert user_compat.landing_field(cfg, "landing_isp") == ""
    assert user_compat.landing_field(
        {"landing_note": "<b>x</b>"}, "landing_note",
    ) == ""
    assert user_compat.landing_field(
        {"landing_ip": "home.example.com"}, "landing_ip",
    ) == ""
    assert user_compat.landing_field(
        {"landing_ip": "2001:0db8::1"}, "landing_ip",
    ) == "2001:db8::1"


def test_landing_does_not_affect_static_access_plan(tmp_path, monkeypatch):
    users = {
        "alice": _alice(metered=True, monthly_quota_bytes=10_000),
        "bob": _alice(
            metered=True,
            monthly_quota_bytes=10_000,
            vless_uuid="22222222-2222-4222-8222-222222222222",
            landing_isp="<script>x</script>",
            landing_note="x" * 500,
            landing_ip="203.0.113.9",
        ),
    }
    users["bob"]["sub_token"] = "bob-token"
    daily = {}
    meta = {
        "settlement_day": 1,
        "cycle_length_days": 30,
        "cycle_anchor_date": "2026-01-01",
    }
    monkeypatch.setattr(ss, "_using_live_core_state", lambda: False)
    monkeypatch.setattr(
        ss, "DISPLAY_MULTIPLIER_STATE_FILE", tmp_path / "display_multiplier.json",
    )
    (tmp_path / "display_multiplier.json").write_text(
        json.dumps({"enabled": False, "multiplier": 1.0}), encoding="utf-8",
    )
    monkeypatch.setattr(ss, "USAGE_FILE", tmp_path / "usage.json")
    plan_a = ss._build_static_access_plan(users, daily, meta, now=NOW)
    users["bob"].pop("landing_isp")
    users["bob"].pop("landing_note")
    users["bob"].pop("landing_ip")
    plan_b = ss._build_static_access_plan(users, daily, meta, now=NOW)
    assert plan_a == plan_b
    assert user_compat.authorization_config_error(users["alice"]) is None
    assert user_compat.authorization_config_error({
        **users["alice"], "landing_ip": "not-an-ip",
    }) is None


def test_landing_saved_via_admin_update_and_shown_on_panel(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    revision = ss.user_config_revision(
        json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    )
    monkeypatch.setattr(ss, "is_logged_in", lambda _h: True)
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users",
        lambda _users, **_k: (False, False),
    )
    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/update?token=admin-token",
            form={
                "user": "alice",
                "user_revision": revision,
                "max_devices": "2",
                "quota_gb": "1",
                "quota_extra_gb": "0",
                "landing_isp": "电信",
                "landing_region": "上海",
                "landing_note": "家宽备注",
                "landing_ip": "2001:0db8:0:0:0:0:0:1",
            },
        )
    assert status == 302
    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    assert saved["landing_isp"] == "电信"
    assert saved["landing_region"] == "上海"
    assert saved["landing_note"] == "家宽备注"
    assert saved["landing_ip"] == "2001:db8::1"
    html = ss.render_user_panel(
        "panel.test", "https://panel.test", "alice", "alice-token", saved,
        session_auth=True,
    )
    assert "落地家宽" in html
    assert "电信" in html
    assert "上海" in html
    assert "家宽备注" in html
    assert "家宽 IP" in html
    assert "2001:db8::1" in html


def test_landing_ip_can_be_cleared_via_admin_update(tmp_path, monkeypatch):
    state = _configure_state(
        tmp_path, monkeypatch,
        users={"alice": _alice(landing_ip="203.0.113.9")},
    )
    revision = ss.user_config_revision(
        json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    )
    monkeypatch.setattr(ss, "is_logged_in", lambda _h: True)
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users",
        lambda _users, **_k: (False, False),
    )

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/update?token=admin-token",
            form={
                "user": "alice",
                "user_revision": revision,
                "max_devices": "2",
                "quota_gb": "1",
                "quota_extra_gb": "0",
                "landing_ip": "",
            },
        )

    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    assert status == 302
    assert "landing_ip" not in saved


def test_empty_landing_omits_panel_section():
    html = ss.render_user_panel(
        "panel.test", "https://panel.test", "alice", "alice-token", _alice(),
        session_auth=True,
    )
    assert "落地家宽" not in html


def test_landing_change_updates_revision(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users={"alice": _alice()})
    before = ss.user_config_revision(
        json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    )
    monkeypatch.setattr(ss, "is_logged_in", lambda _h: True)
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users",
        lambda _users, **_k: (False, False),
    )
    with _running_server() as server:
        _request(
            server,
            "POST",
            "/admin/update?token=admin-token",
            form={
                "user": "alice",
                "user_revision": before,
                "max_devices": "2",
                "quota_gb": "1",
                "quota_extra_gb": "0",
                "landing_isp": "联通",
            },
        )
        status, _h, body = _request(
            server,
            "POST",
            "/admin/update?token=admin-token",
            form={
                "user": "alice",
                "user_revision": before,
                "max_devices": "2",
                "quota_gb": "1",
                "quota_extra_gb": "0",
                "landing_isp": "移动",
            },
        )
    assert status == 409
    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))["alice"]
    assert saved["landing_isp"] == "联通"
