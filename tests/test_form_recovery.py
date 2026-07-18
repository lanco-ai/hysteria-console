"""Regression coverage for safe recovery of the add-user form."""

from contextlib import contextmanager
from html.parser import HTMLParser
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from urllib.parse import urlencode

import pytest

import subscription_service as ss


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


def _post_add(server, fields):
    body = urlencode(fields)
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5
    )
    conn.request(
        "POST",
        "/admin/add?token=admin-token",
        body=body,
        headers={
            "Host": "panel.test",
            "Origin": "http://panel.test",
            "Content-Type": "application/x-www-form-urlencoded",
            "Content-Length": str(len(body.encode("utf-8"))),
        },
    )
    response = conn.getresponse()
    payload = response.read()
    result = SimpleNamespace(
        status=response.status,
        headers={key.lower(): value for key, value in response.getheaders()},
        body=payload.decode("utf-8"),
    )
    conn.close()
    return result


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def _configure_state(tmp_path, monkeypatch, *, users=None):
    paths = {
        "USERS_FILE": tmp_path / "users.json",
        "META_FILE": tmp_path / "meta.json",
        "ONLINE_FILE": tmp_path / "online.json",
        "USAGE_DAILY_FILE": tmp_path / "usage_daily.json",
        "USAGE_PRESERVED_FILE": tmp_path / "usage_preserved.json",
        "USAGE_LOCK_FILE": tmp_path / "usage.lock",
        "USER_SESSIONS_FILE": tmp_path / "user_sessions.json",
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
    _write_json(paths["USERS_FILE"], users or {})
    _write_json(paths["ONLINE_FILE"], {})
    _write_json(paths["USAGE_DAILY_FILE"], {})
    _write_json(paths["USAGE_PRESERVED_FILE"], {})
    return paths


class _ElementCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)


def _elements(body):
    parser = _ElementCollector()
    parser.feed(body)
    return parser.elements


def _element_by_id(body, element_id):
    for tag, attrs in _elements(body):
        if attrs.get("id") == element_id:
            return tag, attrs
    raise AssertionError(f"missing element #{element_id}")


def _input_by_name(body, name):
    match = None
    for tag, attrs in _elements(body):
        if tag == "input" and attrs.get("name") == name:
            match = attrs
    if match is not None:
        return match
    raise AssertionError(f"missing input named {name}")


def test_invalid_username_rerenders_422_with_safe_allowlisted_draft(
    tmp_path, monkeypatch
):
    state = _configure_state(tmp_path, monkeypatch)
    fields = {
        "user": "bad <user>",
        "panel_password": "PANEL-SUPER-SECRET",
        "password": "PROXY-SUPER-SECRET",
        "token": "POSTED-FORM-TOKEN",
        "quota_gb": "23",
        "quota_extra_gb": "7",
        "expires_at": "2026-12-31",
        "note": '续费 "VIP" & <朋友>',
        "tuic_enabled": "on",
        "reset_token": "on",
    }

    with _running_server() as server:
        response = _post_add(server, fields)

    assert response.status == 422
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "location" not in response.headers
    assert '<details class="summary-muted" open>' in response.body

    tag, error = _element_by_id(response.body, "create-add-error")
    assert tag == "div"
    assert error["class"] == "err"
    assert error["role"] == "alert"
    assert error["aria-live"] == "assertive"

    _, username = _element_by_id(response.body, "create-user")
    assert username["value"] == fields["user"]
    assert username["aria-invalid"] == "true"
    assert username["aria-describedby"] == "create-add-error"
    assert "autofocus" in username
    assert _element_by_id(response.body, "create-quota-gb")[1]["value"] == "23"
    assert (
        _element_by_id(response.body, "create-quota-extra-gb")[1]["value"]
        == "7"
    )
    assert (
        _element_by_id(response.body, "create-expires-at")[1]["value"]
        == "2026-12-31"
    )
    assert _element_by_id(response.body, "create-note")[1]["value"] == fields["note"]
    assert "checked" not in _input_by_name(response.body, "guest")
    assert "checked" in _input_by_name(response.body, "tuic_enabled")
    assert 'name="reset_token"' not in response.body

    panel_password = _input_by_name(response.body, "panel_password")
    proxy_password = _input_by_name(response.body, "password")
    assert "value" not in panel_password
    assert "value" not in proxy_password
    for secret in (
        fields["panel_password"],
        fields["password"],
        fields["token"],
        "admin-token",
    ):
        assert secret not in response.body
    assert json.loads(state["USERS_FILE"].read_text(encoding="utf-8")) == {}


@pytest.mark.parametrize(
    ("fields", "error_field", "message"),
    [
        (
            {
                "user": "alice",
                "panel_password": "p4N3L!",
                "password": "proxy-ok",
            },
            "create-panel-password",
            "用户面板登录密码至少需要 8 位",
        ),
        (
            {
                "user": "alice",
                "panel_password": "",
                "password": "PROXY-SECRET-" + ("x" * 260),
            },
            "create-proxy-password",
            "代理连接密码不能超过",
        ),
    ],
)
def test_password_validation_focuses_error_without_echoing_secret(
    tmp_path, monkeypatch, fields, error_field, message
):
    _configure_state(tmp_path, monkeypatch)
    fields.update(
        {
            "quota_gb": "41",
            "quota_extra_gb": "9",
            "expires_at": "2027-01-02",
            "note": "需要跟进",
            "guest": "on",
        }
    )

    with _running_server() as server:
        response = _post_add(server, fields)

    assert response.status == 422
    assert message in response.body
    _, error_input = _element_by_id(response.body, error_field)
    assert error_input["aria-invalid"] == "true"
    assert error_input["aria-describedby"] == "create-add-error"
    assert "autofocus" in error_input
    assert _element_by_id(response.body, "create-user")[1]["value"] == "alice"
    assert _element_by_id(response.body, "create-quota-gb")[1]["value"] == "41"
    assert _element_by_id(response.body, "create-note")[1]["value"] == "需要跟进"
    assert "checked" in _input_by_name(response.body, "guest")
    for name in ("panel_password", "password"):
        secret = fields[name]
        assert "value" not in _input_by_name(response.body, name)
        if secret:
            assert secret not in response.body


def test_existing_user_conflict_renders_after_unlock_without_sync(
    tmp_path, monkeypatch
):
    existing = {
        "alice": {
            "sub_token": "existing-sub-token",
            "vless_uuid": "existing-vless-uuid",
            "monthly_quota_bytes": 10 * 1024**3,
            "quota_extra_bytes": 0,
            "max_devices": 2,
            "metered": True,
            "guest": True,
        }
    }
    state = _configure_state(tmp_path, monkeypatch, users=existing)
    lock_state = {"held": False}

    @contextmanager
    def tracked_lock():
        assert lock_state["held"] is False
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    def forbidden_sync(*args, **kwargs):
        raise AssertionError("conflict must not write runtime configs")

    real_render_admin = ss.render_admin

    def render_after_unlock(*args, **kwargs):
        assert lock_state["held"] is False
        return real_render_admin(*args, **kwargs)

    monkeypatch.setattr(ss, "usage_lock", tracked_lock)
    monkeypatch.setattr(ss, "render_admin", render_after_unlock)
    monkeypatch.setattr(ss.xray_config, "sync_user", forbidden_sync)
    monkeypatch.setattr(ss.tuic_config, "sync_all", forbidden_sync)

    with _running_server() as server:
        response = _post_add(
            server,
            {
                "user": "alice",
                "quota_gb": "88",
                "quota_extra_gb": "5",
                "expires_at": "2026-10-11",
                "note": "保留草稿",
                "tuic_enabled": "on",
            },
        )

    assert response.status == 422
    assert "用户已存在" in response.body
    _, username = _element_by_id(response.body, "create-user")
    assert username["value"] == "alice"
    assert username["aria-invalid"] == "true"
    assert "autofocus" in username
    assert _element_by_id(response.body, "create-quota-gb")[1]["value"] == "88"
    assert 'name="reset_token"' not in response.body
    assert (
        json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
        == existing
    )


def test_renderer_ignores_sensitive_and_unknown_draft_keys(
    tmp_path, monkeypatch
):
    _configure_state(tmp_path, monkeypatch)
    secrets = {
        "panel_password": "DIRECT-PANEL-SECRET",
        "password": "DIRECT-PROXY-SECRET",
        "token": "DIRECT-TOKEN-SECRET",
        "sub_token": "DIRECT-SUB-TOKEN-SECRET",
        "unknown": "DIRECT-UNKNOWN-VALUE",
    }

    page = ss.render_admin(
        "panel.test",
        "http://panel.test",
        flash="err:username_invalid",
        create_draft={
            "user": "safe-user",
            "quota_gb": 12,
            "quota_extra_gb": 3,
            "expires_at": "2026-09-01",
            "note": "safe note",
            "guest": True,
            **secrets,
        },
        create_error_field="create-user",
    )

    assert _element_by_id(page, "create-user")[1]["value"] == "safe-user"
    assert _element_by_id(page, "create-note")[1]["value"] == "safe note"
    for value in secrets.values():
        assert value not in page


def test_success_keeps_redirect_and_runtime_config_writes_inside_lock(
    tmp_path, monkeypatch
):
    state = _configure_state(tmp_path, monkeypatch)
    lock_state = {"held": False}
    calls = []

    @contextmanager
    def tracked_lock():
        assert lock_state["held"] is False
        lock_state["held"] = True
        try:
            yield
        finally:
            lock_state["held"] = False

    def sync_static(users):
        assert lock_state["held"] is True
        calls.append(("exact-sync", sorted(users)))
        return True, True

    def xray_reload():
        assert lock_state["held"] is False
        calls.append(("xray-reload",))

    def tuic_reload():
        assert lock_state["held"] is False
        calls.append(("tuic-reload",))

    monkeypatch.setattr(ss, "usage_lock", tracked_lock)
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users", sync_static,
    )
    monkeypatch.setattr(ss.xray_config, "reload_async", xray_reload)
    monkeypatch.setattr(ss.tuic_config, "reload_async", tuic_reload)

    with _running_server() as server:
        response = _post_add(
            server,
            {
                "user": "alice",
                "quota_gb": "25",
                "quota_extra_gb": "4",
                "expires_at": "2026-11-30",
                "note": "created",
                "guest": "on",
                "tuic_enabled": "on",
            },
        )

    assert response.status == 302
    assert response.headers["location"] == "/admin?msg=created+alice"
    users = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    assert users["alice"]["monthly_quota_bytes"] == 25 * 1024**3
    assert users["alice"]["quota_extra_bytes"] == 4 * 1024**3
    assert users["alice"]["expires_at"] == "2026-11-30"
    assert users["alice"]["note"] == "created"
    assert calls == [
        ("exact-sync", ["alice"]),
        ("xray-reload",),
        ("tuic-reload",),
    ]
