"""AJAX JSON contracts for the remaining admin dashboard mutations.

Every mutation endpoint keeps its original form-POST redirect behaviour for
non-JS clients; when the client asks for JSON (Accept: application/json or
_json=1) it must instead receive a JSON envelope so admin_poll.js can patch
the table without a full page load.
"""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import http.client
import json
from urllib.parse import urlencode

import pytest

import subscription_service as ss


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


def _json_post(server, path, form):
    return _request(
        server,
        "POST",
        path,
        form=form,
        headers={"Accept": "application/json"},
    )


def _seed_users():
    return {
        "alice": {
            "sub_token": "alice-token",
            "monthly_quota_bytes": 1 << 30,
            "max_devices": 2,
            "disabled": False,
        },
    }


def _stub_side_effects(monkeypatch):
    monkeypatch.setattr(ss, "hy_kick", lambda _users: None)
    monkeypatch.setattr(
        ss,
        "_sync_static_access_from_users",
        lambda _users, **_kwargs: (False, False),
    )


def _revision(state, username="alice"):
    users = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    return ss.user_config_revision(users[username])


# ---------------------------------------------------------------------------
# /admin/reset-usage
# ---------------------------------------------------------------------------

def test_reset_usage_json_returns_fresh_row(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/reset-usage?token=admin-token"
            f"&revision={_revision(state)}",
            {"user": "alice"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["username"] == "alice"
    row = payload["user"]
    assert row["user"] == "alice"
    assert row["used"] == 0
    assert set(payload["reload"].keys()) == {"pending", "xray", "tuic"}


def test_reset_usage_json_error_shapes(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        # 401 — no token
        status, _h, body = _json_post(
            server, "/admin/reset-usage", {"user": "alice"},
        )
        assert status == 401
        assert json.loads(body.decode("utf-8")) == {
            "ok": False, "reason": "login_required",
        }

        # 404 — unknown user
        status, _h, body = _json_post(
            server,
            f"/admin/reset-usage?token=admin-token&revision={_revision(state)}",
            {"user": "ghost"},
        )
        assert status == 404
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["reason"] == "user_not_found"

        # 409 — stale revision
        status, _h, body = _json_post(
            server,
            "/admin/reset-usage?token=admin-token&revision=stale",
            {"user": "alice"},
        )
        assert status == 409
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["reason"] == "conflict"


def test_reset_usage_form_post_still_redirects(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/reset-usage?token=admin-token"
            f"&revision={_revision(state)}",
            form={"user": "alice"},
        )
    assert status == 302
    assert headers["location"].startswith("/admin?msg=")


# ---------------------------------------------------------------------------
# /admin/refresh-usage
# ---------------------------------------------------------------------------

def test_refresh_usage_json_returns_fresh_row(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/refresh-usage?token=admin-token"
            f"&revision={_revision(state)}",
            {"user": "alice"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["user"]["user"] == "alice"
    assert payload["user"]["used"] == 0


# ---------------------------------------------------------------------------
# /admin/rotate-token
# ---------------------------------------------------------------------------

def test_rotate_token_json_returns_links_and_row(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/rotate-token?token=admin-token"
            f"&revision={_revision(state)}",
            {"user": "alice"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["username"] == "alice"
    assert payload["flash"] == "rotated"

    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    new_token = saved["alice"]["sub_token"]
    assert new_token != "alice-token"

    links = payload["links"]
    assert links["panel"].endswith(f"/panel/alice?token={new_token}")
    assert links["sub"].endswith(f"/sub/alice?token={new_token}")

    row = payload["user"]
    assert row["user"] == "alice"
    assert row["revision"] == ss.user_config_revision(saved["alice"])


def test_rotate_token_json_conflicts_and_missing(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        # 404
        status, _h, body = _json_post(
            server,
            f"/admin/rotate-token?token=admin-token&revision={_revision(state)}",
            {"user": "ghost"},
        )
        assert status == 404
        assert json.loads(body.decode("utf-8"))["reason"] == "user_not_found"

        # 409
        status, _h, body = _json_post(
            server,
            "/admin/rotate-token?token=admin-token&revision=stale",
            {"user": "alice"},
        )
        assert status == 409
        assert json.loads(body.decode("utf-8"))["reason"] == "conflict"


def test_rotate_token_form_post_still_redirects(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/rotate-token?token=admin-token"
            f"&revision={_revision(state)}",
            form={"user": "alice"},
        )
    assert status == 302
    assert "rotated" in headers["location"]


# ---------------------------------------------------------------------------
# /admin/delete
# ---------------------------------------------------------------------------

def test_delete_user_json_returns_deleted_flag(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/delete?token=admin-token"
            f"&revision={_revision(state)}",
            {"user": "alice"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["deleted"] is True
    assert payload["username"] == "alice"
    assert payload["flash"] == "deleted"

    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    assert "alice" not in saved


def test_delete_user_json_conflicts_and_missing(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        # 404
        status, _h, body = _json_post(
            server,
            f"/admin/delete?token=admin-token&revision={_revision(state)}",
            {"user": "ghost"},
        )
        assert status == 404
        assert json.loads(body.decode("utf-8"))["reason"] == "user_not_found"

        # 409
        status, _h, body = _json_post(
            server,
            "/admin/delete?token=admin-token&revision=stale",
            {"user": "alice"},
        )
        assert status == 409
        assert json.loads(body.decode("utf-8"))["reason"] == "conflict"


def test_delete_user_form_post_still_redirects(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/delete?token=admin-token"
            f"&revision={_revision(state)}",
            form={"user": "alice"},
        )
    assert status == 302
    assert "deleted" in headers["location"]


# ---------------------------------------------------------------------------
# /admin/reset-usage-all
# ---------------------------------------------------------------------------

def test_reset_all_json_returns_full_user_list(tmp_path, monkeypatch):
    users = _seed_users()
    users["bob"] = {
        "sub_token": "bob-token",
        "monthly_quota_bytes": 1 << 30,
        "max_devices": 2,
        "disabled": False,
    }
    _configure_state(tmp_path, monkeypatch, users=users)
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/reset-usage-all?token=admin-token",
            {},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    names = {u["user"] for u in payload["users"]}
    assert names == {"alice", "bob"}
    assert all(u["used"] == 0 for u in payload["users"])
    assert "total_used" in payload
    assert set(payload["reload"].keys()) == {"pending", "xray", "tuic"}


def test_reset_all_requires_login(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server, "/admin/reset-usage-all", {},
        )
    assert status == 401
    assert json.loads(body.decode("utf-8")) == {
        "ok": False, "reason": "login_required",
    }


def test_reset_all_form_post_still_redirects(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/reset-usage-all?token=admin-token",
            form={},
        )
    assert status == 302
    assert headers["location"].startswith("/admin?msg=")


# ---------------------------------------------------------------------------
# /admin/pause-user (JSON parity with the other mutation endpoints)
# ---------------------------------------------------------------------------

def test_pause_user_json_returns_fresh_row(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            "/admin/pause-user?token=admin-token"
            f"&revision={_revision(state)}",
            {"user": "alice", "minutes": "60"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["username"] == "alice"
    assert "disabled_until" in payload
    assert payload["user"]["user"] == "alice"
    assert payload["user"]["disabled"] is True
    assert set(payload["reload"].keys()) == {"pending", "xray", "tuic"}

    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    assert saved["alice"]["disabled"] is True
    assert saved["alice"]["disabled_until"]


def test_pause_user_json_error_shapes(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        # 401
        status, _h, body = _json_post(
            server, "/admin/pause-user", {"user": "alice"},
        )
        assert status == 401
        assert json.loads(body.decode("utf-8")) == {
            "ok": False, "reason": "login_required",
        }

        # 404
        status, _h, body = _json_post(
            server,
            f"/admin/pause-user?token=admin-token&revision={_revision(state)}",
            {"user": "ghost"},
        )
        assert status == 404
        assert json.loads(body.decode("utf-8"))["reason"] == "user_not_found"

        # 409
        status, _h, body = _json_post(
            server,
            "/admin/pause-user?token=admin-token&revision=stale",
            {"user": "alice"},
        )
        assert status == 409
        assert json.loads(body.decode("utf-8"))["reason"] == "conflict"


def test_pause_user_form_post_still_redirects(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch, users=_seed_users())
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/admin/pause-user?token=admin-token"
            f"&revision={_revision(state)}",
            form={"user": "alice", "minutes": "60"},
        )
    assert status == 302
    assert "paused" in headers["location"]
