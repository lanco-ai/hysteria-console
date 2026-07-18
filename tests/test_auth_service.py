from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import http.client
import json
import socket
from types import SimpleNamespace
import threading
import time

import pytest

import auth_backend
import auth_service


@contextmanager
def _running_server(*, max_workers=4, max_pending=4):
    server = auth_service.create_server(
        port=0,
        max_workers=max_workers,
        max_pending=max_pending,
    )
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
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    result = SimpleNamespace(
        status=response.status,
        headers={
            key.lower(): value for key, value in response.getheaders()
        },
        body=response.read(),
    )
    conn.close()
    return result


def _auth_body(auth_payload="alice:SECRET", **overrides):
    payload = {
        "addr": "198.51.100.4:44321",
        "auth": auth_payload,
        "tx": 12500000,
    }
    payload.update(overrides)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _post_auth(server, body, *, content_type="application/json"):
    return _request(
        server,
        "POST",
        "/auth",
        body=body,
        headers={"Content-Type": content_type},
    )


def test_protocol_returns_200_for_accept_and_reject(monkeypatch):
    seen = []

    def authenticate(payload, **_kwargs):
        seen.append(payload)
        return "alice" if payload == "alice:SECRET" else None

    monkeypatch.setattr(
        auth_service.auth_backend, "authenticate_payload", authenticate
    )
    with _running_server() as server:
        accepted = _post_auth(server, _auth_body())
        rejected = _post_auth(server, _auth_body("alice:WRONG"))

    assert accepted.status == 200
    assert json.loads(accepted.body) == {"ok": True, "id": "alice"}
    assert rejected.status == 200
    assert json.loads(rejected.body) == {"ok": False}
    assert seen == ["alice:SECRET", "alice:WRONG"]
    for response in (accepted, rejected):
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"


def test_http_round_trip_uses_shared_authorization_policy(
    tmp_path, monkeypatch
):
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps({"alice": {"sub_token": "SECRET"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(auth_backend, "USERS_FILE", str(users))

    with _running_server() as server:
        accepted = _post_auth(server, _auth_body("alice:SECRET"))
        rejected = _post_auth(server, _auth_body("alice:WRONG"))

    assert json.loads(accepted.body) == {"ok": True, "id": "alice"}
    assert json.loads(rejected.body) == {"ok": False}


def test_authentication_exception_fails_closed_without_protocol_error(
    monkeypatch,
):
    def unavailable(_payload, **_kwargs):
        raise RuntimeError("sensitive internal detail")

    monkeypatch.setattr(
        auth_service.auth_backend, "authenticate_payload", unavailable
    )
    with _running_server() as server:
        response = _post_auth(server, _auth_body())

    assert response.status == 200
    assert json.loads(response.body) == {"ok": False}
    assert b"sensitive" not in response.body


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b'{"addr":"x","auth":"alice:SECRET"}',
        b'{"addr":"x","auth":"alice:SECRET","tx":1,"extra":true}',
        b'{"addr":"x","auth":"alice:SECRET","tx":true}',
        b'{"addr":"x","auth":"alice:SECRET","tx":-1}',
        b'{"addr":"x","auth":"alice:SECRET","tx":NaN}',
        b'{"addr":"x","addr":"y","auth":"alice:SECRET","tx":1}',
        b'{"addr":"x\\n","auth":"alice:SECRET","tx":1}',
        b'{"addr":"x","auth":"alice:\\u0000SECRET","tx":1}',
    ],
)
def test_malformed_or_noncanonical_requests_are_400(body, monkeypatch):
    called = False

    def authenticate(_payload):
        nonlocal called
        called = True
        return "alice"

    monkeypatch.setattr(
        auth_service.auth_backend, "authenticate_payload", authenticate
    )
    with _running_server() as server:
        response = _post_auth(server, body)

    assert response.status == 400
    assert json.loads(response.body) == {"ok": False}
    assert called is False


def test_body_limit_and_content_type_are_enforced():
    with _running_server() as server:
        too_large = _request(
            server,
            "POST",
            "/auth",
            body=b"",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(auth_service.MAX_BODY_BYTES + 1),
            },
        )
        wrong_type = _post_auth(
            server, _auth_body(), content_type="text/plain"
        )

    assert too_large.status == 413
    assert wrong_type.status == 415


def test_missing_content_length_is_411():
    with _running_server() as server:
        conn = http.client.HTTPConnection(
            "127.0.0.1", server.server_port, timeout=5
        )
        conn.putrequest("POST", "/auth")
        conn.putheader("Content-Type", "application/json")
        conn.endheaders()
        response = conn.getresponse()
        body = response.read()
        conn.close()

    assert response.status == 411
    assert json.loads(body) == {"ok": False}


def test_healthz_reflects_authorization_state(monkeypatch):
    monkeypatch.setattr(
        auth_service.auth_backend,
        "deep_authorization_state_ready",
        lambda **_kwargs: True,
    )
    with _running_server() as server:
        ready = _request(server, "GET", "/healthz")
        monkeypatch.setattr(
            auth_service.auth_backend,
            "deep_authorization_state_ready",
            lambda **_kwargs: False,
        )
        unavailable = _request(server, "GET", "/healthz")
        missing = _request(server, "GET", "/missing")
        unsupported = _request(server, "PUT", "/auth", body=b"")

    assert ready.status == 200
    assert json.loads(ready.body) == {"ok": True}
    assert unavailable.status == 503
    assert missing.status == 404
    assert unsupported.status == 405
    assert unsupported.headers["allow"] == "GET, POST"


def test_fixed_worker_capacity_rejects_overload(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def slow_auth(_payload, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        return "alice"

    monkeypatch.setattr(
        auth_service.auth_backend, "authenticate_payload", slow_auth
    )
    with _running_server(max_workers=1, max_pending=0) as server:
        with ThreadPoolExecutor(max_workers=1) as clients:
            first_future = clients.submit(
                _post_auth, server, _auth_body()
            )
            assert started.wait(timeout=3)
            overloaded = _post_auth(server, _auth_body())
            release.set()
            first = first_future.result(timeout=5)

    assert overloaded.status == 503
    assert json.loads(overloaded.body) == {"ok": False}
    assert first.status == 200
    assert json.loads(first.body) == {"ok": True, "id": "alice"}


def test_overload_rejection_never_blocks_the_accept_loop():
    server = auth_service.create_server(
        port=0,
        max_workers=1,
        max_pending=0,
    )
    request, peer = socket.socketpair()
    try:
        assert server._capacity.acquire(blocking=False)
        server._deadline_watchdog.register(
            request,
            time.monotonic() + 1,
        )
        started = time.monotonic()
        server.process_request(request, ("local", 0))
        elapsed = time.monotonic() - started
        peer.settimeout(0.5)
        response = peer.recv(4096)
        server._capacity.release()
    finally:
        peer.close()
        server.server_close()

    assert elapsed < 0.10
    assert b"503 Service Unavailable" in response


def test_service_can_only_bind_to_ipv4_loopback():
    with pytest.raises(ValueError):
        auth_service.create_server(host="0.0.0.0", port=0)

    with _running_server() as server:
        assert server.server_address[0] == auth_service.AUTH_HOST


def test_callable_payload_api_has_no_process_exit(monkeypatch):
    monkeypatch.setattr(
        auth_backend,
        "authenticate",
        lambda user, secret, **_kwargs: user,
    )

    assert auth_backend.authenticate_payload("alice:secret") == "alice"
    assert auth_backend.authenticate_payload("missing-delimiter") is None


def test_readiness_validates_metered_policy_state(tmp_path, monkeypatch):
    users = tmp_path / "users.json"
    meta = tmp_path / "subscription_meta.json"
    daily = tmp_path / "usage_daily.json"
    display = tmp_path / "display_multiplier.json"
    monkeypatch.setattr(auth_backend, "USERS_FILE", str(users))
    monkeypatch.setattr(auth_backend, "META_FILE", str(meta))
    monkeypatch.setattr(auth_backend, "USAGE_DAILY_FILE", str(daily))
    monkeypatch.setattr(
        auth_backend, "DISPLAY_MULTIPLIER_STATE_FILE", str(display)
    )

    users.write_text(
        json.dumps(
            {
                "alice": {
                    "sub_token": "SECRET",
                    "metered": True,
                    "monthly_quota_bytes": 1024,
                }
            }
        ),
        encoding="utf-8",
    )
    assert auth_backend.authorization_state_ready() is False

    meta.write_text(
        json.dumps({"settlement_day": 1, "cycle_length_days": 30}),
        encoding="utf-8",
    )
    daily.write_text("{}", encoding="utf-8")
    assert auth_backend.authorization_state_ready() is True

    users.write_text(
        json.dumps(
            {
                "alice": {
                    "sub_token": "SECRET",
                    "metered": True,
                    "monthly_quota_bytes": 1.5,
                }
            }
        ),
        encoding="utf-8",
    )
    assert auth_backend.authorization_state_ready() is False
