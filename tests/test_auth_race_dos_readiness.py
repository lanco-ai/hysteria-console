from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import errno
import http.client
import json
from pathlib import Path
import socket
import threading
import time

import pytest

import auth_backend as ab
import auth_service


def _write_users(path, user):
    path.write_text(json.dumps({"alice": user}), encoding="utf-8")


def _configure_auth(tmp_path, monkeypatch, user):
    users = tmp_path / "users.json"
    _write_users(users, user)
    monkeypatch.setattr(ab, "USERS_FILE", str(users))
    monkeypatch.setattr(
        ab,
        "DEVICE_ADMISSION_FILE",
        str(tmp_path / "device_admissions.json"),
    )
    return users


@pytest.mark.parametrize(
    "mutation",
    [
        {"password_hash": "new-generation"},
        {"disabled": True},
        {"monthly_quota_bytes": 4096},
    ],
)
def test_inflight_password_auth_rechecks_rotated_or_changed_generation(
    tmp_path, monkeypatch, mutation
):
    original = {
        "sub_token": "TOKEN",
        "password_hash": "old-generation",
    }
    users = _configure_auth(tmp_path, monkeypatch, original)
    started = threading.Event()
    release = threading.Event()

    def delayed_verify(_password, _encoded):
        started.set()
        assert release.wait(timeout=3)
        return True

    monkeypatch.setattr(ab, "verify_password_hash", delayed_verify)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            ab.authenticate,
            "alice",
            "legacy-password",
            addr="198.51.100.9:42000",
            source="198.51.100.9",
        )
        assert started.wait(timeout=2)
        _write_users(users, {**original, **mutation})
        release.set()
        assert future.result(timeout=3) is None


def test_inflight_token_auth_rejects_token_rotated_during_online_check(
    tmp_path, monkeypatch
):
    original = {"sub_token": "OLD", "max_devices": 2}
    users = _configure_auth(tmp_path, monkeypatch, original)
    started = threading.Event()
    release = threading.Event()
    compare_shapes = []
    real_compare = ab.hmac.compare_digest

    def delayed_online(**_kwargs):
        started.set()
        assert release.wait(timeout=3)
        return {"alice": 0}

    def record_compare(left, right):
        compare_shapes.append((len(left), len(right)))
        return real_compare(left, right)

    monkeypatch.setattr(ab, "get_online_counts", delayed_online)
    monkeypatch.setattr(ab.hmac, "compare_digest", record_compare)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            ab.authenticate,
            "alice",
            "OLD",
            addr="198.51.100.9:42000",
            source="198.51.100.9",
        )
        assert started.wait(timeout=2)
        _write_users(users, {"sub_token": "NEW", "max_devices": 2})
        release.set()
        assert future.result(timeout=3) is None

    assert (32, 32) in compare_shapes


@pytest.mark.parametrize(
    "raw,canonical,source",
    [
        (
            "198.51.100.4:00443",
            "198.51.100.4:443",
            "198.51.100.4",
        ),
        (
            "[2001:0DB8:0:0::1]:44321",
            "[2001:db8::1]:44321",
            "2001:db8::1",
        ),
        (
            "[::ffff:192.0.2.8]:44321",
            "192.0.2.8:44321",
            "192.0.2.8",
        ),
    ],
)
def test_official_addr_is_normalized_before_policy(
    raw, canonical, source
):
    decoded = auth_service.decode_auth_request(
        json.dumps(
            {"addr": raw, "auth": "alice:SECRET", "tx": 1}
        ).encode("utf-8")
    )

    assert decoded.addr == canonical
    assert decoded.source == source
    assert decoded.auth == "alice:SECRET"


@pytest.mark.parametrize(
    "addr",
    [
        "example.com:443",
        "2001:db8::1:443",
        "[2001:db8::1]443",
        "192.0.2.1:0",
        "192.0.2.1:65536",
        "[fe80::1%bad zone]:443",
        "192.0.2.1%eth0:443",
    ],
)
def test_non_ip_or_ambiguous_addr_is_rejected(addr):
    with pytest.raises(auth_service.InvalidRequest):
        auth_service.decode_auth_request(
            json.dumps(
                {"addr": addr, "auth": "alice:SECRET", "tx": 1}
            ).encode("utf-8")
        )


def test_password_work_limiter_bounds_rate_sources_and_cleans_up():
    now = [100.0]
    limiter = auth_service.PasswordWorkLimiter(
        window_seconds=10,
        per_source_burst=2,
        global_burst=3,
        max_sources=2,
        max_concurrent=2,
        clock=lambda: now[0],
    )

    for _ in range(2):
        lease = limiter.try_acquire("192.0.2.1")
        assert lease is not None
        lease.release()
    assert limiter.try_acquire("192.0.2.1") is None
    lease = limiter.try_acquire("192.0.2.2")
    assert lease is not None
    lease.release()
    assert limiter.try_acquire("192.0.2.3") is None
    assert limiter.tracked_source_count <= 2

    now[0] += 11
    for source in ("192.0.2.3", "192.0.2.4", "192.0.2.5"):
        lease = limiter.try_acquire(source)
        assert lease is not None
        lease.release()
    assert limiter.tracked_source_count == 2


def test_password_work_limiter_never_queues_cpu_work():
    limiter = auth_service.PasswordWorkLimiter(
        window_seconds=30,
        per_source_burst=10,
        global_burst=10,
        max_sources=10,
        max_concurrent=1,
    )
    first = limiter.try_acquire("192.0.2.1")
    assert first is not None
    started = time.monotonic()
    assert limiter.try_acquire("192.0.2.2") is None
    assert time.monotonic() - started < 0.1
    first.release()
    second = limiter.try_acquire("192.0.2.2")
    assert second is not None
    second.release()


def test_default_password_work_budget_is_small_and_rejects_immediately():
    assert auth_service.PBKDF2_MAX_CONCURRENT == 2
    assert auth_service.PBKDF2_GLOBAL_BURST == 20
    assert auth_service.PBKDF2_PER_SOURCE_BURST == 3
    limiter = auth_service.PasswordWorkLimiter()

    for _ in range(auth_service.PBKDF2_PER_SOURCE_BURST):
        lease = limiter.try_acquire("192.0.2.10")
        assert lease is not None
        lease.release()
    started = time.monotonic()
    assert limiter.try_acquire("192.0.2.10") is None
    assert time.monotonic() - started < 0.1

    global_limiter = auth_service.PasswordWorkLimiter()
    for index in range(auth_service.PBKDF2_GLOBAL_BURST):
        lease = global_limiter.try_acquire(f"198.51.100.{index + 1}")
        assert lease is not None
        lease.release()
    started = time.monotonic()
    assert global_limiter.try_acquire("203.0.113.1") is None
    assert time.monotonic() - started < 0.1


def test_request_deadline_rejects_result_after_bounded_password_work(
    tmp_path, monkeypatch
):
    _configure_auth(
        tmp_path,
        monkeypatch,
        {"sub_token": "TOKEN", "password_hash": "hash"},
    )

    def delayed_success(_password, _encoded):
        time.sleep(0.02)
        return True

    monkeypatch.setattr(ab, "verify_password_hash", delayed_success)
    assert (
        ab.authenticate(
            "alice",
            "legacy-password",
            source="192.0.2.1",
            deadline=time.monotonic() + 0.005,
        )
        is None
    )


def test_token_fast_path_survives_saturated_password_work(
    tmp_path, monkeypatch
):
    _configure_auth(
        tmp_path,
        monkeypatch,
        {
            "sub_token": "FAST-TOKEN",
            "password_hash": "slow-password-hash",
        },
    )
    limiter = auth_service.PasswordWorkLimiter(
        window_seconds=30,
        per_source_burst=20,
        global_burst=20,
        max_sources=20,
        max_concurrent=1,
    )
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_password(_password, _encoded):
        calls.append(True)
        started.set()
        assert release.wait(timeout=3)
        return False

    monkeypatch.setattr(ab, "verify_password_hash", slow_password)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            ab.authenticate,
            "alice",
            "WRONG-1",
            source="192.0.2.1",
            password_limiter=limiter,
        )
        assert started.wait(timeout=2)
        assert (
            ab.authenticate(
                "alice",
                "FAST-TOKEN",
                source="192.0.2.2",
                password_limiter=limiter,
            )
            == "alice"
        )
        before = time.monotonic()
        assert (
            ab.authenticate(
                "alice",
                "WRONG-2",
                source="192.0.2.3",
                password_limiter=limiter,
            )
            is None
        )
        assert time.monotonic() - before < 0.1
        release.set()
        assert first.result(timeout=3) is None
    assert calls == [True]


def test_device_admission_lock_timeout_is_short_and_fails_closed(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    real_flock = ab.fcntl.flock

    def always_busy(fd, operation):
        if operation == ab.fcntl.LOCK_UN:
            return real_flock(fd, operation)
        raise BlockingIOError(errno.EWOULDBLOCK, "busy")

    monkeypatch.setattr(ab.fcntl, "flock", always_busy)
    deadline = time.monotonic() + 0.04
    started = time.monotonic()
    with pytest.raises(ab.StateUnavailable):
        ab.reserve_device_admission(
            "alice",
            max_devices=2,
            online_count=0,
            path=ledger,
            deadline=deadline,
        )
    assert time.monotonic() - started < 0.2
    assert not ledger.exists()


def test_admission_deep_probe_preserves_ledger_and_consumes_no_slot(
    tmp_path,
):
    ledger = tmp_path / "device_admissions.json"
    original = (
        b'{"alice":{"observed":0,"pending":[100.0]}}\n'
    )
    ledger.write_bytes(original)

    assert ab.device_admission_state_ready(path=ledger)
    assert ledger.read_bytes() == original
    assert not list(tmp_path.glob(".device-admission-ready.*.tmp"))


def test_admission_deep_probe_rejects_corrupt_ledger(tmp_path):
    ledger = tmp_path / "device_admissions.json"
    ledger.write_text('{"alice":', encoding="utf-8")

    assert not ab.device_admission_state_ready(path=ledger)
    assert ledger.read_text(encoding="utf-8") == '{"alice":'


def test_online_api_body_is_bounded_before_json_parse(
    tmp_path, monkeypatch
):
    class OversizedResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, maximum):
            return b"x" * (maximum + 1)

    monkeypatch.setattr(
        ab.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: type(
            "OversizedConnection",
            (),
            {
                "request": lambda self, *_a, **_k: None,
                "getresponse": lambda self: OversizedResponse(),
                "close": lambda self: None,
            },
        )(),
    )
    monkeypatch.setattr(
        ab, "ONLINE_SNAPSHOT_FILE", str(tmp_path / "missing.json")
    )

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_deep_readiness_checks_online_and_admission_without_auth(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(ab, "authorization_state_ready", lambda: True)
    monkeypatch.setattr(
        ab,
        "get_online_counts",
        lambda **_kwargs: calls.append("online") or {"alice": 0},
    )
    monkeypatch.setattr(
        ab,
        "device_admission_state_ready",
        lambda **_kwargs: calls.append("admission") or True,
    )

    assert ab.deep_authorization_state_ready()
    assert calls == ["online", "admission"]


@contextmanager
def _running_server(*, password_limiter=None, **server_kwargs):
    server = auth_service.create_server(
        port=0,
        max_workers=4,
        max_pending=4,
        password_limiter=password_limiter,
        **server_kwargs,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def _request(server, method, path, *, payload=None):
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    connection = http.client.HTTPConnection(
        "127.0.0.1", server.server_port, timeout=3
    )
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_http_passes_canonical_addr_source_and_deadline(monkeypatch):
    seen = {}

    def authenticate(payload, **kwargs):
        seen["payload"] = payload
        seen.update(kwargs)
        return "alice"

    monkeypatch.setattr(
        auth_service.auth_backend, "authenticate_payload", authenticate
    )
    with _running_server() as server:
        status, body = _request(
            server,
            "POST",
            "/auth",
            payload={
                "addr": "[2001:0DB8::1]:00443",
                "auth": "alice:SECRET",
                "tx": 1,
            },
        )

    assert status == 200
    assert body == {"ok": True, "id": "alice"}
    assert seen["payload"] == "alice:SECRET"
    assert seen["addr"] == "[2001:db8::1]:443"
    assert seen["source"] == "2001:db8::1"
    assert seen["deadline"] > time.monotonic() - 1
    assert seen["password_limiter"] is not None


def test_throttled_valid_auth_still_uses_http_200_deny(
    tmp_path, monkeypatch
):
    _configure_auth(
        tmp_path,
        monkeypatch,
        {"sub_token": "TOKEN", "password_hash": "hash"},
    )
    calls = []
    monkeypatch.setattr(
        ab,
        "verify_password_hash",
        lambda *_args: calls.append(True) or False,
    )
    limiter = auth_service.PasswordWorkLimiter(
        window_seconds=60,
        per_source_burst=1,
        global_burst=10,
        max_sources=10,
        max_concurrent=1,
    )
    payload = {
        "addr": "198.51.100.4:44321",
        "auth": "alice:WRONG",
        "tx": 1,
    }

    with _running_server(password_limiter=limiter) as server:
        first = _request(server, "POST", "/auth", payload=payload)
        second = _request(server, "POST", "/auth", payload=payload)

    assert first == (200, {"ok": False})
    assert second == (200, {"ok": False})
    assert calls == [True]


def test_livez_is_shallow_and_readyz_is_deep(monkeypatch):
    calls = []

    def deep(**_kwargs):
        calls.append(True)
        return False

    monkeypatch.setattr(
        auth_service.auth_backend,
        "deep_authorization_state_ready",
        deep,
    )
    with _running_server() as server:
        live = _request(server, "GET", "/livez")
        ready = _request(server, "GET", "/readyz")
        compatibility = _request(server, "GET", "/healthz")

    assert live == (200, {"ok": True})
    assert ready == (503, {"ok": False})
    assert compatibility == (503, {"ok": False})
    assert calls == [True, True]


def test_connection_deadline_closes_slow_drip_headers_and_recovers():
    with _running_server(request_deadline_seconds=0.20) as server:
        client = socket.create_connection(
            ("127.0.0.1", server.server_port),
            timeout=1,
        )
        client.settimeout(0.5)
        started = time.monotonic()
        client.sendall(b"POST /auth HTTP/1.1\r\nHost: localhost\r\nX-Slow: ")
        closed = False
        for _ in range(10):
            time.sleep(0.05)
            try:
                client.sendall(b"x")
            except OSError:
                closed = True
                break
        if not closed:
            try:
                closed = client.recv(1) == b""
            except OSError:
                closed = True
        elapsed = time.monotonic() - started
        client.close()

        live = _request(server, "GET", "/livez")

    assert closed
    assert elapsed < 0.55
    assert live == (200, {"ok": True})


def test_online_api_header_drip_cannot_extend_absolute_deadline(
    tmp_path,
    monkeypatch,
):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    monkeypatch.setattr(ab, "API_PORT", listener.getsockname()[1])
    monkeypatch.setattr(
        ab,
        "ONLINE_SNAPSHOT_FILE",
        str(tmp_path / "missing-online.json"),
    )
    finished = threading.Event()

    def drip_response():
        connection = None
        try:
            connection, _address = listener.accept()
            connection.settimeout(1)
            connection.recv(4096)
            response = (
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 2\r\n\r\n{}"
            )
            for byte in response:
                try:
                    connection.sendall(bytes((byte,)))
                except OSError:
                    break
                time.sleep(0.02)
        finally:
            if connection is not None:
                connection.close()
            listener.close()
            finished.set()

    thread = threading.Thread(target=drip_response, daemon=True)
    thread.start()
    started = time.monotonic()
    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts(deadline=started + 0.20)
    elapsed = time.monotonic() - started

    assert elapsed < 0.50
    assert finished.wait(timeout=1)
    thread.join(timeout=1)
