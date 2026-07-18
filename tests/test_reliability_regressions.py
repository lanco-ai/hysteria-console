"""Behavioral regressions for panel reliability and HTTP hardening.

These tests intentionally exercise the real ``Handler`` over a loopback HTTP
server where request/response behavior matters.  Smaller helpers are tested
directly only when the trust boundary or file transaction is the behavior
under test.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from urllib.parse import urlencode

import pytest

import http_utils
import subscription_service as ss


_SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "x-frame-options": "DENY",
    "cross-origin-opener-policy": "same-origin",
}


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


def _request(server, method, path, *, body=None, headers=None):
    conn = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=5
    )
    request_headers = {"Host": "panel.test"}
    request_headers.update(headers or {})
    if body is not None:
        request_headers.setdefault(
            "Content-Type", "application/x-www-form-urlencoded"
        )
        request_headers.setdefault("Content-Length", str(len(body.encode())))
    conn.request(method, path, body=body, headers=request_headers)
    response = conn.getresponse()
    payload = response.read()
    result = SimpleNamespace(
        status=response.status,
        reason=response.reason,
        headers={key.lower(): value for key, value in response.getheaders()},
        body=payload,
    )
    conn.close()
    return result


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding="utf-8")


def _configure_state(tmp_path, monkeypatch, *, users=None):
    paths = {
        "USERS_FILE": tmp_path / "users.json",
        "META_FILE": tmp_path / "meta.json",
        "SESSIONS_FILE": tmp_path / "sessions.json",
        "USER_SESSIONS_FILE": tmp_path / "user_sessions.json",
        "USAGE_FILE": tmp_path / "usage.json",
        "USAGE_DAILY_FILE": tmp_path / "usage_daily.json",
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
    _write_json(paths["USERS_FILE"], users or {})
    _write_json(paths["USAGE_FILE"], {})
    _write_json(paths["USAGE_DAILY_FILE"], {})
    _write_json(paths["ONLINE_FILE"], {})
    return paths


def test_healthz_reports_core_state_readiness(tmp_path, monkeypatch):
    state = _configure_state(tmp_path, monkeypatch)

    with _running_server() as server:
        ready = _request(server, "GET", "/healthz")
        assert ready.status == 204
        assert ready.body == b""

        state["USAGE_FILE"].write_text('{"broken":', encoding="utf-8")
        unavailable = _request(server, "GET", "/healthz")
        assert unavailable.status == 503


def _assert_security_headers(response, *, cache_control="no-store"):
    for name, expected in _SECURITY_HEADERS.items():
        assert response.headers.get(name) == expected
    assert response.headers.get("cache-control") == cache_control
    assert "camera=()" in response.headers.get("permissions-policy", "")
    csp = response.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp


def test_corrupt_users_state_fails_closed_without_overwrite(
    tmp_path, monkeypatch
):
    state = _configure_state(tmp_path, monkeypatch)
    corrupt_payload = b'{"alice":'
    state["USERS_FILE"].write_bytes(corrupt_payload)

    with _running_server() as server:
        response = _request(
            server,
            "POST",
            "/admin/add?token=admin-token",
            body=urlencode({"user": "bob", "quota_gb": "10"}),
        )

    assert response.status == 503
    assert state["USERS_FILE"].read_bytes() == corrupt_payload
    assert "为避免数据覆盖".encode("utf-8") in response.body
    _assert_security_headers(response)


def test_missing_users_state_fails_closed_without_recreating_it(
    tmp_path, monkeypatch
):
    state = _configure_state(tmp_path, monkeypatch)
    state["USERS_FILE"].unlink()

    with _running_server() as server:
        response = _request(
            server,
            "POST",
            "/admin/add?token=admin-token",
            body=urlencode({"user": "bob", "quota_gb": "10"}),
        )

    assert response.status == 503
    assert not state["USERS_FILE"].exists()
    assert "为避免数据覆盖".encode("utf-8") in response.body
    _assert_security_headers(response)


@pytest.mark.parametrize(
    ("path", "cookie_name", "create_session", "get_sessions", "login_path"),
    [
        ("/logout", "sid", ss.create_session, ss.get_sessions, "/login"),
        (
            "/user/logout",
            "usid",
            lambda: ss.create_user_session("alice"),
            ss.get_user_sessions,
            "/user/login",
        ),
    ],
)
def test_logout_get_is_read_only_and_same_origin_post_revokes_session(
    tmp_path, monkeypatch, path, cookie_name, create_session, get_sessions,
    login_path,
):
    _configure_state(
        tmp_path, monkeypatch,
        users={"alice": {"sub_token": "token", "panel_pass_hash": "hash"}},
    )
    sid = create_session()
    cookie = {"Cookie": f"{cookie_name}={sid}"}

    with _running_server() as server:
        confirmation = _request(server, "GET", path, headers=cookie)
        present_after_get = sid in get_sessions()
        cross_site = _request(
            server, "POST", path, body="",
            headers={**cookie, "Origin": "https://attacker.invalid"},
        )
        present_after_cross_site = sid in get_sessions()
        revoked = _request(
            server, "POST", path, body="",
            headers={**cookie, "Origin": "http://panel.test"},
        )

    assert confirmation.status == 200
    assert f'action="{path}"'.encode() in confirmation.body
    assert present_after_get is True
    assert cross_site.status == 403
    assert present_after_cross_site is True
    assert revoked.status == 303
    assert revoked.headers["location"] == login_path
    assert "max-age=0" in revoked.headers["set-cookie"].lower()
    assert sid not in get_sessions()


@pytest.fixture(autouse=True)
def _clean_login_failures():
    with ss._login_failures_lock:
        ss._login_failures.clear()
        ss._user_login_failures.clear()
        ss._login_attempts_inflight.clear()
    yield
    with ss._login_failures_lock:
        ss._login_failures.clear()
        ss._user_login_failures.clear()
        ss._login_attempts_inflight.clear()


@pytest.mark.parametrize(
    ("peer", "headers", "expected"),
    [
        (
            "127.0.0.1",
            {"X-Real-IP": "198.51.100.9", "X-Forwarded-For": "203.0.113.8"},
            "198.51.100.9",
        ),
        (
            "::ffff:127.0.0.1",
            {"X-Real-IP": "[2001:db8::42]"},
            "2001:db8::42",
        ),
        (
            "::1",
            {"X-Real-IP": "not-an-ip", "X-Forwarded-For": "203.0.113.7, 10.0.0.2"},
            "203.0.113.7",
        ),
        (
            "192.0.2.44",
            {"X-Real-IP": "198.51.100.9", "X-Forwarded-For": "203.0.113.8"},
            "192.0.2.44",
        ),
        (
            "127.0.0.1",
            {"X-Real-IP": "not-an-ip", "X-Forwarded-For": "also-invalid"},
            "127.0.0.1",
        ),
    ],
)
def test_request_client_ip_only_trusts_forwarding_headers_from_loopback(
    peer, headers, expected
):
    handler = SimpleNamespace(client_address=(peer, 12345), headers=headers)
    assert http_utils.request_client_ip(handler) == expected


def test_login_rate_limit_is_partitioned_by_real_ip_and_login_realm(
    tmp_path, monkeypatch
):
    _configure_state(tmp_path, monkeypatch)
    invalid_admin = urlencode({"username": "intruder", "password": "wrong"})
    invalid_user = urlencode({"username": "nobody", "password": "wrong"})

    with _running_server() as server:
        for _ in range(ss._LOGIN_MAX):
            response = _request(
                server,
                "POST",
                "/login",
                body=invalid_admin,
                headers={"X-Real-IP": "198.51.100.10"},
            )
            assert response.status == 200

        # A different proxied client must not inherit the first client's block.
        other_ip = _request(
            server,
            "POST",
            "/login",
            body=invalid_admin,
            headers={"X-Real-IP": "198.51.100.11"},
        )
        assert other_ip.status == 200

        # Admin and end-user authentication deliberately use separate buckets.
        other_realm = _request(
            server,
            "POST",
            "/user/login",
            body=invalid_user,
            headers={"X-Real-IP": "198.51.100.10"},
        )
        assert other_realm.status == 200

        blocked = _request(
            server,
            "POST",
            "/login",
            body=invalid_admin,
            headers={"X-Real-IP": "198.51.100.10"},
        )

    assert blocked.status == 429
    assert blocked.headers["retry-after"] == str(ss._LOGIN_WINDOW)
    _assert_security_headers(blocked)
    assert "198.51.100.10" in ss._login_failures
    assert "198.51.100.11" in ss._login_failures
    assert "127.0.0.1" not in ss._login_failures
    assert "198.51.100.10" in ss._user_login_failures


def test_concurrent_login_burst_cannot_overrun_password_verification_limit(
    tmp_path, monkeypatch
):
    _configure_state(tmp_path, monkeypatch)
    entered_verifier = threading.Barrier(ss._LOGIN_MAX)

    def slow_failed_verification(_plain, _stored_hash):
        entered_verifier.wait(timeout=5)
        return False

    monkeypatch.setattr(ss, "verify_secret", slow_failed_verification)
    invalid_admin = urlencode({"username": "admin", "password": "wrong"})

    def attempt(server):
        return _request(
            server,
            "POST",
            "/login",
            body=invalid_admin,
            headers={"X-Real-IP": "198.51.100.20"},
        )

    attempts = ss._LOGIN_MAX + 7
    with _running_server() as server:
        with ThreadPoolExecutor(max_workers=attempts) as pool:
            responses = list(pool.map(lambda _index: attempt(server), range(attempts)))

    statuses = [response.status for response in responses]
    assert statuses.count(200) == ss._LOGIN_MAX
    assert statuses.count(429) == attempts - ss._LOGIN_MAX
    assert all(
        response.headers.get("retry-after") == str(ss._LOGIN_WINDOW)
        for response in responses
        if response.status == 429
    )
    assert len(ss._login_failures["198.51.100.20"]) == ss._LOGIN_MAX
    assert ss._login_attempts_inflight == {}


@pytest.mark.parametrize(
    ("account_state", "message"),
    [
        ({"disabled": True}, "账号已停用"),
        ({"expires_at": "2020-01-01"}, "账号已到期"),
    ],
)
def test_rejected_correct_user_password_releases_reservation_without_clearing_failures(
    tmp_path, monkeypatch, account_state, message
):
    user = {
        "sub_token": "token",
        "panel_pass_hash": "known-hash",
        "monthly_quota_bytes": 1024,
        **account_state,
    }
    _configure_state(tmp_path, monkeypatch, users={"alice": user})
    monkeypatch.setattr(
        ss,
        "verify_secret",
        lambda plain, stored: plain == "correct-password" and stored == "known-hash",
    )
    client_ip = "198.51.100.30"
    prior_failures = [time.time() - 10, time.time() - 5]
    with ss._login_failures_lock:
        ss._user_login_failures[client_ip] = list(prior_failures)

    with _running_server() as server:
        rejected = _request(
            server,
            "POST",
            "/user/login",
            body=urlencode(
                {"username": "alice", "password": "correct-password"}
            ),
            headers={"X-Real-IP": client_ip},
        )
        final_allowed_failure = _request(
            server,
            "POST",
            "/user/login",
            body=urlencode({"username": "alice", "password": "wrong"}),
            headers={"X-Real-IP": client_ip},
        )
        now_blocked = _request(
            server,
            "POST",
            "/user/login",
            body=urlencode({"username": "alice", "password": "wrong"}),
            headers={"X-Real-IP": client_ip},
        )

    assert rejected.status == 200
    assert message.encode("utf-8") in rejected.body
    assert "set-cookie" not in rejected.headers
    assert final_allowed_failure.status == 200
    assert now_blocked.status == 429
    assert len(ss._user_login_failures[client_ip]) == ss._LOGIN_MAX
    assert ss._user_login_failures[client_ip][:2] == prior_failures
    assert ss._login_attempts_inflight == {}


def test_concurrent_session_creates_preserve_every_committed_session(
    tmp_path, monkeypatch
):
    session_file = tmp_path / "user_sessions.json"
    monkeypatch.setattr(ss, "USER_SESSIONS_FILE", session_file)
    workers = 24
    start = threading.Barrier(workers)

    def create(index):
        start.wait(timeout=5)
        return ss.create_user_session(f"user-{index}")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        session_ids = list(pool.map(create, range(workers)))

    sessions = ss.get_user_sessions()
    assert len(session_ids) == len(set(session_ids)) == workers
    assert set(sessions) == set(session_ids)
    assert {item["user"] for item in sessions.values()} == {
        f"user-{index}" for index in range(workers)
    }


def test_session_replacement_revokes_target_atomically_and_preserves_others(
    tmp_path
):
    session_file = tmp_path / "sessions.json"
    future = int(time.time()) + 600
    _write_json(
        session_file,
        {
            "alice-old-1": {"user": "alice", "exp": future},
            "alice-old-2": {"user": "alice", "exp": future},
            "bob-live": {"user": "bob", "exp": future},
            "expired": {"user": "carol", "exp": 1},
            "malformed": {"user": "carol", "exp": "not-a-number"},
        },
    )

    replacement = ss._replace_sessions_with_new(
        session_file, "alice", credential_generation="alice-generation"
    )
    sessions = json.loads(session_file.read_text(encoding="utf-8"))

    assert set(sessions) == {"bob-live", replacement}
    assert sessions["bob-live"]["user"] == "bob"
    assert sessions[replacement]["user"] == "alice"
    assert (
        sessions[replacement]["credential_generation"]
        == "alice-generation"
    )
    assert sessions[replacement]["exp"] > int(time.time())

    admin_replacement = ss._replace_sessions_with_new(
        session_file,
        "admin",
        revoke_all=True,
        credential_generation="admin-generation",
    )
    sessions = json.loads(session_file.read_text(encoding="utf-8"))
    assert set(sessions) == {admin_replacement}
    assert sessions[admin_replacement]["user"] == "admin"
    assert (
        sessions[admin_replacement]["credential_generation"]
        == "admin-generation"
    )


def test_stale_credential_generation_sessions_are_rejected_and_deleted(
    tmp_path, monkeypatch
):
    state = _configure_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "alice-token",
                "panel_pass_hash": "current-user-hash",
                "monthly_quota_bytes": 1024,
                "max_devices": 2,
            }
        },
    )
    meta = json.loads(state["META_FILE"].read_text(encoding="utf-8"))
    stale_generation = ss._credential_generation("retired-password-hash")
    current_user_generation = ss._credential_generation("current-user-hash")

    stale_user_sid = ss.create_user_session("alice", stale_generation)
    current_user_sid = ss.create_user_session(
        "alice", current_user_generation
    )
    stale_admin_sid = ss.create_session("admin", stale_generation)

    with _running_server() as server:
        stale_user = _request(
            server,
            "GET",
            "/user/panel",
            headers={"Cookie": f"usid={stale_user_sid}"},
        )
        current_user = _request(
            server,
            "GET",
            "/user/panel",
            headers={"Cookie": f"usid={current_user_sid}"},
        )
        stale_admin = _request(
            server,
            "GET",
            "/admin",
            headers={"Cookie": f"sid={stale_admin_sid}"},
        )

    assert stale_user.status == 302
    assert stale_user.headers["location"] == "/user/login"
    assert current_user.status == 200
    assert b"alice" in current_user.body
    assert stale_admin.status == 302
    assert stale_admin.headers["location"] == "/login"
    _assert_security_headers(stale_user)
    _assert_security_headers(stale_admin)

    assert stale_user_sid not in ss.get_user_sessions()
    assert current_user_sid in ss.get_user_sessions()
    assert stale_admin_sid not in ss.get_sessions()
    assert (
        ss._credential_generation(meta["admin_pass_hash"])
        != stale_generation
    )


def test_static_assets_accept_weak_if_none_match_and_keep_hardening_headers():
    with _running_server() as server:
        initial = _request(server, "GET", "/static/style.css")
        etag = initial.headers["etag"]
        weak = _request(
            server,
            "GET",
            "/static/style.css",
            headers={"If-None-Match": f'W/{etag}'},
        )
        listed = _request(
            server,
            "GET",
            "/static/style.css",
            headers={"If-None-Match": f'"old", W/{etag}'},
        )
        stale = _request(
            server,
            "GET",
            "/static/style.css",
            headers={"If-None-Match": '"old"'},
        )

    assert initial.status == 200
    assert initial.body
    _assert_security_headers(initial, cache_control="public, max-age=86400")
    for response in (weak, listed):
        assert response.status == 304
        assert response.body == b""
        assert response.headers["etag"] == etag
        _assert_security_headers(
            response, cache_control="public, max-age=86400"
        )
    assert stale.status == 200
    assert stale.body == initial.body
    assert ss._etag_matches(etag, f"W/{etag}")
    assert ss._etag_matches("*", etag)


def test_sensitive_subscription_json_and_error_responses_are_not_cacheable(
    tmp_path, monkeypatch
):
    _configure_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "secret-token",
                "monthly_quota_bytes": 1024,
                "max_devices": 2,
            }
        },
    )
    monkeypatch.setattr(ss, "build_yaml", lambda *_args, **_kwargs: "mode: rule\n")
    monkeypatch.setattr(ss, "scaled_usage_for_user", lambda *_args, **_kwargs: (1, 2, 3))
    monkeypatch.setattr(ss, "subscription_template_mtime", lambda: "123")
    monkeypatch.setattr(
        ss,
        "_build_panel_json_payload",
        lambda *_args, **_kwargs: {"user": "alice", "used": 3},
    )

    with _running_server() as server:
        subscription = _request(
            server, "GET", "/sub/alice?token=secret-token"
        )
        panel_json = _request(
            server, "GET", "/panel/alice.json?token=secret-token"
        )
        denied = _request(server, "GET", "/panel/alice.json?token=wrong")

    assert subscription.status == 200
    assert subscription.headers["content-disposition"].startswith(
        "attachment; filename*=UTF-8''alice.yaml"
    )
    assert panel_json.status == 200
    assert json.loads(panel_json.body) == {"user": "alice", "used": 3}
    assert denied.status == 403
    for response in (subscription, panel_json, denied):
        _assert_security_headers(response)
        assert "secret-token" not in "\n".join(response.headers.values())


def test_usage_csv_window_is_an_allowlist_and_cannot_inject_headers(
    tmp_path, monkeypatch
):
    _configure_state(tmp_path, monkeypatch)

    with _running_server() as server:
        default = _request(
            server, "GET", "/admin/usage.csv?token=admin-token"
        )
        trailing_30d = _request(
            server,
            "GET",
            "/admin/usage.csv?token=admin-token&window=30d",
        )
        invalid = _request(
            server,
            "GET",
            (
                "/admin/usage.csv?token=admin-token"
                "&window=cycle%0D%0AX-Injected%3Ayes"
            ),
        )

    assert default.status == 200
    assert 'filename="usage-cycle-' in default.headers["content-disposition"]
    assert trailing_30d.status == 200
    assert 'filename="usage-30d-' in trailing_30d.headers["content-disposition"]
    for response in (default, trailing_30d):
        assert response.body.startswith(
            b"date,user,tx_bytes,rx_bytes,total_bytes,displayed_bytes\n"
        )
        _assert_security_headers(response)

    assert invalid.status == 400
    assert "content-disposition" not in invalid.headers
    assert "x-injected" not in invalid.headers
    _assert_security_headers(invalid)


@pytest.mark.parametrize(
    ("path", "form", "expected_state"),
    [
        (
            "/admin/delete",
            {"user": "alice"},
            "deleted",
        ),
        (
            "/admin/pause-user",
            {"user": "alice", "minutes": "30"},
            "disabled",
        ),
        (
            "/admin/toggle-user",
            {"user": "alice"},
            "disabled",
        ),
    ],
)
def test_delete_and_disable_routes_revoke_existing_user_panel_sessions(
    tmp_path, monkeypatch, path, form, expected_state
):
    state = _configure_state(
        tmp_path,
        monkeypatch,
        users={
            "alice": {
                "sub_token": "alice-token",
                "monthly_quota_bytes": 1024,
                "disabled": False,
            },
            "bob": {
                "sub_token": "bob-token",
                "monthly_quota_bytes": 1024,
                "disabled": False,
            },
        },
    )
    alice_sessions = {
        ss.create_user_session("alice"),
        ss.create_user_session("alice"),
    }
    bob_session = ss.create_user_session("bob")
    kicked = []
    synced_users = []
    reloads = []

    monkeypatch.setattr(ss, "hy_kick", lambda users: kicked.append(list(users)))
    monkeypatch.setattr(
        ss.xray_config, "reload_async", lambda: reloads.append("xray")
    )

    def sync_static(users):
        synced_users.append(json.loads(json.dumps(users)))
        return True, True

    monkeypatch.setattr(
        ss, "_sync_static_access_from_users", sync_static,
    )
    monkeypatch.setattr(
        ss.tuic_config, "reload_async", lambda: reloads.append("tuic")
    )
    request_form = dict(form)
    request_form["user_revision"] = ss.user_config_revision(
        json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))[
            "alice"
        ]
    )
    request_path = f"{path}?token=admin-token"
    if path == "/admin/toggle-user":
        request_path += "&desired=disabled"

    with _running_server() as server:
        changed = _request(
            server,
            "POST",
            request_path,
            body=urlencode(request_form),
        )
        stale_cookie = _request(
            server,
            "GET",
            "/user/panel",
            headers={"Cookie": f"usid={next(iter(alice_sessions))}"},
        )

    assert changed.status == 302
    sessions = ss.get_user_sessions()
    assert alice_sessions.isdisjoint(sessions)
    assert sessions[bob_session]["user"] == "bob"
    assert stale_cookie.status == 302
    assert stale_cookie.headers["location"] == "/user/login"
    _assert_security_headers(stale_cookie)

    users = json.loads(state["USERS_FILE"].read_text(encoding="utf-8"))
    if expected_state == "deleted":
        assert "alice" not in users
        assert "alice" not in synced_users[-1]
    else:
        assert users["alice"]["disabled"] is True
        assert synced_users[-1]["alice"]["disabled"] is True
    assert kicked == [["alice"]]
    if path == "/admin/delete":
        # Deletion uses the durable revocation handoff; alternate state roots
        # are explicitly non-live and therefore never touch process reloads.
        assert reloads == []
    else:
        assert set(reloads) == {"xray", "tuic"}
