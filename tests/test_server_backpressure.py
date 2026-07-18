import http.client
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import threading

import pytest

import subscription_service as ss


ROOT = Path(__file__).resolve().parents[1]


class _BlockingHandler(BaseHTTPRequestHandler):
    started = None
    release = None

    def do_GET(self):
        if self.path == "/hold":
            self.started.set()
            self.release.wait(timeout=5)
        body = b"ok\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return


def _get(port, path):
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        port,
        timeout=3,
    )
    connection.request("GET", path)
    response = connection.getresponse()
    body = response.read()
    result = (
        response.status,
        dict(response.getheaders()),
        body,
    )
    connection.close()
    return result


def test_subscription_server_rejects_work_above_worker_cap():
    started = threading.Event()
    release = threading.Event()
    _BlockingHandler.started = started
    _BlockingHandler.release = release
    server = ss.BoundedThreadingHTTPServer(
        ("127.0.0.1", 0),
        _BlockingHandler,
        max_workers=1,
    )
    serve_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
    )
    serve_thread.start()
    first_result = {}

    def hold_worker():
        first_result["response"] = _get(
            server.server_port,
            "/hold",
        )

    first_thread = threading.Thread(target=hold_worker, daemon=True)
    first_thread.start()
    try:
        assert started.wait(timeout=2)
        status, headers, body = _get(server.server_port, "/busy")
        assert status == 503
        assert headers["Retry-After"] == "5"
        assert headers["Cache-Control"] == "no-store"
        assert headers["Connection"] == "close"
        assert b"retry shortly" in body
    finally:
        release.set()
        first_thread.join(timeout=3)
        server.shutdown()
        server.server_close()
        serve_thread.join(timeout=3)

    assert first_result["response"][0] == 200
    assert server._worker_slots.acquire(blocking=False)
    server._worker_slots.release()


@pytest.mark.parametrize("value", [0, -1, True, 257, 1.5])
def test_subscription_server_rejects_invalid_worker_caps(value):
    with pytest.raises(ValueError):
        ss.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0),
            _BlockingHandler,
            max_workers=value,
        )


def test_subscription_runtime_uses_bounded_server_and_cgroup_caps():
    source = (
        ROOT / "hysteria" / "subscription_service.py"
    ).read_text(encoding="utf-8")
    unit = (
        ROOT / "systemd" / "hysteria-subscription.service"
    ).read_text(encoding="utf-8")

    assert "srv = BoundedThreadingHTTPServer(LISTEN, Handler)" in source
    assert "SERVER_MAX_WORKERS = 32" in source
    assert "SERVER_REQUEST_QUEUE = 64" in source
    for directive in (
        "TimeoutStartSec=30s",
        "TimeoutStopSec=30s",
        "TasksMax=96",
        "MemoryHigh=384M",
        "MemoryMax=512M",
    ):
        assert directive in unit
