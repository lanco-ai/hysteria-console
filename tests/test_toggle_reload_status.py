"""Toggle JSON contract and /admin/reload-status.json regressions.

Covers the phase-2 contract: a successful toggle returns the fresh overview
row (same schema as /admin/overview.json) plus the current reload-pending
marker state, so the client never needs an extra overview fetch. Error and
auth paths must keep their original JSON shape, and the reload-status
endpoint must be read-only.
"""

from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import http.client
import json
from urllib.parse import urlencode

import pytest

import subscription_service as ss


EXPECTED_ROW_KEYS = {
    'user', 'tx', 'rx', 'used', 'total',
    'percent', 'online', 'revision', 'disabled',
}


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


def _request(server, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5,
    )
    request_headers = {"Host": "panel.test"}
    if body is not None:
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
        body=urlencode(form),
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
    """Keep the toggle success path free of proxy/network side effects."""
    monkeypatch.setattr(ss, "hy_kick", lambda _users: None)
    monkeypatch.setattr(
        ss,
        "_sync_static_access_from_users",
        lambda _users, **_kwargs: (False, False),
    )


def _toggle_path(username_cfg, desired):
    revision = ss.user_config_revision(username_cfg)
    return (
        "/admin/toggle-user?token=admin-token"
        f"&revision={revision}&desired={desired}"
    )


def test_toggle_success_json_returns_fresh_row_and_reload_state(
    tmp_path, monkeypatch,
):
    users = _seed_users()
    state = _configure_state(tmp_path, monkeypatch, users=users)
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, headers, body = _json_post(
            server,
            _toggle_path(users["alice"], "disabled"),
            {"user": "alice"},
        )

    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload["ok"] is True
    assert payload["username"] == "alice"
    assert payload["reason"] == "disabled"
    assert payload["desired"] == "disabled"

    row = payload["user"]
    assert set(row.keys()) == EXPECTED_ROW_KEYS
    assert row["user"] == "alice"
    assert row["disabled"] is True

    reload_state = payload["reload"]
    assert set(reload_state.keys()) == {"pending", "xray", "tuic"}
    assert reload_state["pending"] is False

    saved = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    assert saved["alice"]["disabled"] is True


def test_toggle_row_schema_matches_overview_payload(tmp_path, monkeypatch):
    users = _seed_users()
    _configure_state(tmp_path, monkeypatch, users=users)
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _headers, body = _json_post(
            server,
            _toggle_path(users["alice"], "disabled"),
            {"user": "alice"},
        )
    assert status == 200
    toggle_row = json.loads(body.decode("utf-8"))["user"]

    overview = ss._build_overview_json_payload(now=ss.local_now())
    overview_row = next(
        u for u in overview["users"] if u["user"] == "alice"
    )
    assert set(toggle_row.keys()) == set(overview_row.keys())
    assert toggle_row["disabled"] == overview_row["disabled"]
    assert toggle_row["revision"] == overview_row["revision"]


def test_toggle_json_error_shapes_unchanged(tmp_path, monkeypatch):
    users = _seed_users()
    _configure_state(tmp_path, monkeypatch, users=users)
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        # 404 — unknown user
        status, _h, body = _json_post(
            server,
            _toggle_path(users["alice"], "disabled"),
            {"user": "ghost"},
        )
        assert status == 404
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["reason"] == "user_not_found"
        assert "user" not in payload
        assert "reload" not in payload

        # 409 — stale revision
        status, _h, body = _json_post(
            server,
            "/admin/toggle-user?token=admin-token"
            "&revision=stale&desired=disabled",
            {"user": "alice"},
        )
        assert status == 409
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["reason"] == "conflict"
        assert "user" not in payload
        assert "reload" not in payload

        # 422 — invalid desired state
        status, _h, body = _json_post(
            server,
            "/admin/toggle-user?token=admin-token&desired=banana",
            {"user": "alice"},
        )
        assert status == 422
        payload = json.loads(body.decode("utf-8"))
        assert payload["ok"] is False
        assert payload["reason"] == "invalid_desired"
        assert "user" not in payload
        assert "reload" not in payload


def test_toggle_json_requires_login(tmp_path, monkeypatch):
    users = _seed_users()
    _configure_state(tmp_path, monkeypatch, users=users)
    _stub_side_effects(monkeypatch)

    with _running_server() as server:
        status, _h, body = _json_post(
            server,
            # No admin token: must be rejected before any mutation.
            "/admin/toggle-user?desired=disabled",
            {"user": "alice"},
        )
    assert status == 401
    payload = json.loads(body.decode("utf-8"))
    assert payload == {"ok": False, "reason": "login_required"}


def test_reload_status_requires_login(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users=_seed_users())

    with _running_server() as server:
        status, _h, body = _request(
            server,
            "GET",
            "/admin/reload-status.json",
            headers={"Accept": "application/json"},
        )
    assert status == 401
    payload = json.loads(body.decode("utf-8"))
    assert payload == {"ok": False, "reason": "login_required"}


def test_reload_status_reports_existing_markers_without_writing(
    tmp_path, monkeypatch,
):
    _configure_state(tmp_path, monkeypatch, users=_seed_users())

    xray_cfg = tmp_path / "xray.json"
    tuic_cfg = tmp_path / "tuic.json"
    xray_cfg.write_text("{}", encoding="utf-8")
    tuic_cfg.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ss.xray_config, "CONFIG_FILE", xray_cfg)
    monkeypatch.setattr(ss.tuic_config, "CONFIG_FILE", tuic_cfg)

    # Seed one durable marker through the existing marker helpers.
    ss.xray_config._mark_reload_pending(xray_cfg)
    xray_marker = ss.xray_config._reload_pending_path(xray_cfg)
    tuic_marker = ss.tuic_config._reload_pending_path(tuic_cfg)
    assert xray_marker.exists()
    assert not tuic_marker.exists()
    before = xray_marker.read_bytes()

    with _running_server() as server:
        status, headers, body = _request(
            server,
            "GET",
            "/admin/reload-status.json?token=admin-token",
            headers={"Accept": "application/json"},
        )

    assert status == 200
    assert headers.get("cache-control") == "no-store"
    payload = json.loads(body.decode("utf-8"))
    assert payload == {
        "ok": True,
        "pending": True,
        "xray": True,
        "tuic": False,
    }

    # Read-only: markers untouched, nothing cleared, nothing new written.
    assert xray_marker.exists()
    assert xray_marker.read_bytes() == before
    assert not tuic_marker.exists()


def test_reload_status_clean_state(tmp_path, monkeypatch):
    _configure_state(tmp_path, monkeypatch, users=_seed_users())
    monkeypatch.setattr(
        ss.xray_config, "CONFIG_FILE", tmp_path / "xray.json",
    )
    monkeypatch.setattr(
        ss.tuic_config, "CONFIG_FILE", tmp_path / "tuic.json",
    )

    with _running_server() as server:
        status, _h, body = _request(
            server,
            "GET",
            "/admin/reload-status.json?token=admin-token",
            headers={"Accept": "application/json"},
        )
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert payload == {
        "ok": True,
        "pending": False,
        "xray": False,
        "tuic": False,
    }
