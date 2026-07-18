#!/usr/bin/env python3
"""Loopback-only, bounded HTTP bridge for Hysteria authentication."""

from collections import OrderedDict, deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import ipaddress
import json
import math
import re
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import auth_backend


AUTH_HOST = "127.0.0.1"
AUTH_PORT = 8082
AUTH_PATH = "/auth"
HEALTH_PATH = "/healthz"
LIVE_PATH = "/livez"
READY_PATH = "/readyz"
MAX_BODY_BYTES = 2048
MAX_ADDR_BYTES = 512
MAX_AUTH_CHARS = 64 + 1 + auth_backend.AUTH_SECRET_MAX_CHARS
MAX_AUTH_BYTES = 64 + 1 + auth_backend.AUTH_SECRET_MAX_BYTES
MAX_TX_RATE = (1 << 64) - 1
READ_TIMEOUT_SECONDS = 3.0
REQUEST_DEADLINE_SECONDS = 3.0
READY_DEADLINE_SECONDS = 2.5
MAX_WORKERS = 16
MAX_PENDING_REQUESTS = 16
REQUEST_QUEUE_SIZE = 32
PBKDF2_WINDOW_SECONDS = 30.0
PBKDF2_PER_SOURCE_BURST = 3
PBKDF2_GLOBAL_BURST = 20
PBKDF2_MAX_SOURCES = 1024
PBKDF2_MAX_CONCURRENT = 2
_ZONE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


class InvalidRequest(ValueError):
    """The HTTP request is not a valid Hysteria authentication message."""


@dataclass(frozen=True)
class AuthRequest:
    addr: str
    source: str
    auth: str
    tx: int


class _PasswordWorkLease:
    def __init__(self, semaphore):
        self._semaphore = semaphore
        self._released = False
        self._lock = threading.Lock()

    def release(self):
        with self._lock:
            if self._released:
                return
            self._released = True
            self._semaphore.release()


class PasswordWorkLimiter:
    """Bound PBKDF2 rate, source cardinality, and concurrent CPU work."""

    def __init__(
        self,
        *,
        window_seconds=PBKDF2_WINDOW_SECONDS,
        per_source_burst=PBKDF2_PER_SOURCE_BURST,
        global_burst=PBKDF2_GLOBAL_BURST,
        max_sources=PBKDF2_MAX_SOURCES,
        max_concurrent=PBKDF2_MAX_CONCURRENT,
        clock=time.monotonic,
    ):
        integer_bounds = (
            per_source_burst,
            global_burst,
            max_sources,
            max_concurrent,
        )
        if (
            isinstance(window_seconds, bool)
            or not isinstance(window_seconds, (int, float))
            or not math.isfinite(window_seconds)
            or window_seconds <= 0
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or value <= 0
                for value in integer_bounds
            )
            or not callable(clock)
        ):
            raise ValueError("invalid password work limiter bounds")
        self._window_seconds = float(window_seconds)
        self._per_source_burst = per_source_burst
        self._global_burst = global_burst
        self._max_sources = max_sources
        self._clock = clock
        self._lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(max_concurrent)
        self._global_attempts = deque()
        self._sources = OrderedDict()

    @staticmethod
    def _prune(events, cutoff):
        while events and events[0] <= cutoff:
            events.popleft()

    def _cleanup_sources(self, cutoff):
        for source, events in list(self._sources.items()):
            self._prune(events, cutoff)
            if not events:
                self._sources.pop(source, None)

    @property
    def tracked_source_count(self):
        with self._lock:
            return len(self._sources)

    def try_acquire(self, source, *, deadline=None):
        if not isinstance(source, str) or not source or len(source) > 128:
            return None
        try:
            now = float(self._clock())
            if not math.isfinite(now):
                return None
            if deadline is not None and now >= float(deadline):
                return None
        except (TypeError, ValueError, OverflowError):
            return None

        cutoff = now - self._window_seconds
        with self._lock:
            self._prune(self._global_attempts, cutoff)
            source_events = self._sources.pop(source, None)
            if source_events is None:
                if len(self._sources) >= self._max_sources:
                    self._cleanup_sources(cutoff)
                while len(self._sources) >= self._max_sources:
                    self._sources.popitem(last=False)
                source_events = deque()
            else:
                self._prune(source_events, cutoff)
            self._sources[source] = source_events

            if (
                len(self._global_attempts) >= self._global_burst
                or len(source_events) >= self._per_source_burst
            ):
                return None

            # Never queue a PBKDF2 fallback behind another one. This preserves
            # worker capacity for constant-time token authentication.
            if not self._semaphore.acquire(blocking=False):
                return None
            self._global_attempts.append(now)
            source_events.append(now)
            return _PasswordWorkLease(self._semaphore)


class ConnectionDeadlineWatchdog:
    """Bound all accepted sockets with one process-wide watchdog thread."""

    def __init__(self, *, clock=time.monotonic):
        if not callable(clock):
            raise ValueError("invalid deadline clock")
        self._clock = clock
        self._condition = threading.Condition()
        self._deadlines = {}
        self._armed = set()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run,
            name="hy2-auth-deadlines",
            daemon=True,
        )
        self._thread.start()

    def register(self, request, deadline):
        with self._condition:
            if self._closed:
                raise RuntimeError("deadline watchdog is closed")
            self._deadlines[request] = deadline
            self._armed.add(request)
            self._condition.notify()

    def deadline_for(self, request):
        with self._condition:
            return self._deadlines.get(request)

    def unregister(self, request):
        with self._condition:
            self._deadlines.pop(request, None)
            self._armed.discard(request)
            self._condition.notify()

    def close(self):
        with self._condition:
            self._closed = True
            active = list(self._deadlines)
            self._deadlines.clear()
            self._armed.clear()
            self._condition.notify()
        for request in active:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self._thread.join(timeout=2)

    def _run(self):
        while True:
            expired = []
            with self._condition:
                if self._closed:
                    return
                if not self._armed:
                    self._condition.wait()
                    continue
                now = self._clock()
                for request in tuple(self._armed):
                    deadline = self._deadlines.get(request)
                    if deadline is None:
                        self._armed.discard(request)
                    elif deadline <= now:
                        self._armed.discard(request)
                        expired.append(request)
                if not expired:
                    next_deadline = min(
                        self._deadlines[request]
                        for request in self._armed
                    )
                    self._condition.wait(
                        timeout=max(0.0, next_deadline - now)
                    )
                    continue
            for request in expired:
                try:
                    request.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass


def _json_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest("duplicate JSON field")
        result[key] = value
    return result


def _reject_json_constant(_value):
    raise InvalidRequest("non-finite JSON number")


def normalize_client_addr(value):
    """Canonicalize Hysteria's official IP:port addr and source identity."""
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_ADDR_BYTES
        or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value)
    ):
        raise InvalidRequest("invalid addr")

    if value.startswith("["):
        closing = value.find("]")
        if closing <= 1 or value[closing + 1:closing + 2] != ":":
            raise InvalidRequest("invalid addr")
        host_text = value[1:closing]
        port_text = value[closing + 2:]
        if "]" in port_text:
            raise InvalidRequest("invalid addr")
    else:
        host_text, separator, port_text = value.rpartition(":")
        if not separator or not host_text or ":" in host_text:
            raise InvalidRequest("invalid addr")

    if (
        not port_text
        or not port_text.isascii()
        or not port_text.isdigit()
    ):
        raise InvalidRequest("invalid addr")
    port = int(port_text)
    if not 1 <= port <= 65535:
        raise InvalidRequest("invalid addr")

    address_text, zone_separator, zone = host_text.partition("%")
    if zone_separator and not _ZONE_RE.fullmatch(zone):
        raise InvalidRequest("invalid addr")
    try:
        address = ipaddress.ip_address(address_text)
    except ValueError as exc:
        raise InvalidRequest("invalid addr") from exc
    if zone_separator and not isinstance(address, ipaddress.IPv6Address):
        raise InvalidRequest("invalid addr")
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        if zone_separator:
            raise InvalidRequest("invalid addr")
        address = address.ipv4_mapped

    canonical_host = address.compressed
    if zone_separator:
        canonical_host = f"{canonical_host}%{zone}"
    if isinstance(address, ipaddress.IPv6Address):
        canonical = f"[{canonical_host}]:{port}"
    else:
        canonical = f"{canonical_host}:{port}"
    return canonical, canonical_host


def decode_auth_request(body):
    """Decode and strictly validate Hysteria's HTTP-auth request body."""
    if not isinstance(body, bytes) or not body or len(body) > MAX_BODY_BYTES:
        raise InvalidRequest("invalid body size")
    try:
        text = body.decode("utf-8")
        payload = json.loads(
            text,
            object_pairs_hook=_json_no_duplicates,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, InvalidRequest) as exc:
        raise InvalidRequest("invalid JSON") from exc

    if not isinstance(payload, dict) or set(payload) != {"addr", "auth", "tx"}:
        raise InvalidRequest("invalid schema")
    addr = payload["addr"]
    auth_payload = payload["auth"]
    tx_rate = payload["tx"]
    canonical_addr, source = normalize_client_addr(addr)
    if (
        not isinstance(auth_payload, str)
        or len(auth_payload) > MAX_AUTH_CHARS
        or len(auth_payload.encode("utf-8")) > MAX_AUTH_BYTES
        or any(
            ord(char) < 0x20 or ord(char) == 0x7F
            for char in auth_payload
        )
    ):
        raise InvalidRequest("invalid auth payload")
    if (
        isinstance(tx_rate, bool)
        or not isinstance(tx_rate, int)
        or not 0 <= tx_rate <= MAX_TX_RATE
    ):
        raise InvalidRequest("invalid tx rate")
    return AuthRequest(
        addr=canonical_addr,
        source=source,
        auth=auth_payload,
        tx=tx_rate,
    )


class AuthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP surface; request contents and credentials are never logged."""

    protocol_version = "HTTP/1.1"
    server_version = "hy2-auth"
    sys_version = ""

    def setup(self):
        self._request_deadline = self.server.request_deadline(self.request)
        if (
            self._request_deadline is None
            or self._request_deadline <= time.monotonic()
        ):
            raise socket.timeout("authentication request deadline exceeded")
        super().setup()
        self._arm_connection()

    def _arm_connection(self):
        remaining = self._request_deadline - time.monotonic()
        if remaining <= 0:
            raise socket.timeout("authentication request deadline exceeded")
        self.connection.settimeout(min(READ_TIMEOUT_SECONDS, remaining))

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, OSError, socket.timeout):
            # The deadline watchdog intentionally interrupts slow connections.
            # Never turn that expected fail-closed path into a traceback.
            self.close_connection = True

    def log_message(self, _format, *_args):
        return

    def _send_json(self, status, payload, *, allow=None):
        body = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, socket.timeout):
            pass
        self.close_connection = True

    def _content_type_is_json(self):
        raw = self.headers.get("Content-Type", "")
        parts = [part.strip().lower() for part in raw.split(";")]
        if not parts or parts[0] != "application/json":
            return False
        return all(part == "charset=utf-8" for part in parts[1:])

    def do_GET(self):
        if self.path == LIVE_PATH:
            self._send_json(200, {"ok": True})
            return
        if self.path not in (READY_PATH, HEALTH_PATH):
            self._send_json(404, {"ok": False})
            return
        ready = auth_backend.deep_authorization_state_ready(
            deadline=min(
                self._request_deadline,
                time.monotonic() + READY_DEADLINE_SECONDS,
            )
        )
        self._send_json(200 if ready else 503, {"ok": ready})

    def do_POST(self):
        if self.path != AUTH_PATH:
            self._send_json(404, {"ok": False})
            return
        if self.headers.get("Transfer-Encoding") is not None:
            self._send_json(400, {"ok": False})
            return
        content_lengths = self.headers.get_all("Content-Length", [])
        if len(content_lengths) != 1:
            self._send_json(411, {"ok": False})
            return
        raw_length = content_lengths[0].strip()
        if not raw_length.isascii() or not raw_length.isdigit():
            self._send_json(400, {"ok": False})
            return
        content_length = int(raw_length)
        if content_length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False})
            return
        if content_length <= 0:
            self._send_json(400, {"ok": False})
            return
        if not self._content_type_is_json():
            self._send_json(415, {"ok": False})
            return
        try:
            self._arm_connection()
            body = self.rfile.read(content_length)
        except socket.timeout:
            self._send_json(408, {"ok": False})
            return
        if len(body) != content_length:
            self._send_json(400, {"ok": False})
            return
        try:
            request = decode_auth_request(body)
        except (InvalidRequest, UnicodeError):
            self._send_json(400, {"ok": False})
            return

        try:
            user_id = auth_backend.authenticate_payload(
                request.auth,
                addr=request.addr,
                source=request.source,
                deadline=self._request_deadline,
                password_limiter=self.server.password_limiter,
            )
        except Exception:
            # An unavailable dependency or unexpected state must reject the
            # connection without disclosing details to the proxy or journal.
            user_id = None
        response = {"ok": user_id is not None}
        if user_id is not None:
            response["id"] = user_id
        # Hysteria requires status 200 for both accepted and rejected,
        # structurally valid authentication decisions.
        self._send_json(200, response)

    def _method_not_allowed(self):
        self._send_json(405, {"ok": False}, allow="GET, POST")

    do_DELETE = _method_not_allowed
    do_HEAD = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_PUT = _method_not_allowed


class BoundedHTTPServer(HTTPServer):
    """HTTPServer with fixed workers and a bounded application queue."""

    allow_reuse_address = True
    request_queue_size = REQUEST_QUEUE_SIZE

    def __init__(
        self,
        server_address,
        handler_class=AuthHandler,
        *,
        max_workers=MAX_WORKERS,
        max_pending=MAX_PENDING_REQUESTS,
        password_limiter=None,
        request_deadline_seconds=REQUEST_DEADLINE_SECONDS,
    ):
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers <= 0
            or isinstance(max_pending, bool)
            or not isinstance(max_pending, int)
            or max_pending < 0
            or isinstance(request_deadline_seconds, bool)
            or not isinstance(request_deadline_seconds, (int, float))
            or not math.isfinite(request_deadline_seconds)
            or request_deadline_seconds <= 0
        ):
            raise ValueError("invalid worker bounds")
        super().__init__(server_address, handler_class)
        self._request_deadline_seconds = float(request_deadline_seconds)
        self._deadline_watchdog = ConnectionDeadlineWatchdog()
        self.password_limiter = (
            PasswordWorkLimiter()
            if password_limiter is None
            else password_limiter
        )
        if not hasattr(self.password_limiter, "try_acquire"):
            raise ValueError("invalid password limiter")
        self._capacity = threading.BoundedSemaphore(
            max_workers + max_pending
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="hy2-auth",
        )
        self._reject_capacity = threading.BoundedSemaphore(2)
        self._reject_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="hy2-auth-overload",
        )

    def get_request(self):
        request, client_address = super().get_request()
        try:
            self._deadline_watchdog.register(
                request,
                time.monotonic() + self._request_deadline_seconds,
            )
        except Exception:
            request.close()
            raise
        return request, client_address

    def request_deadline(self, request):
        return self._deadline_watchdog.deadline_for(request)

    def process_request(self, request, client_address):
        if not self._capacity.acquire(blocking=False):
            if not self._reject_capacity.acquire(blocking=False):
                self._deadline_watchdog.unregister(request)
                self.close_request(request)
                return
            try:
                self._reject_executor.submit(
                    self._reject_overload_bounded,
                    request,
                )
            except Exception:
                self._reject_capacity.release()
                self._deadline_watchdog.unregister(request)
                self.close_request(request)
            return
        try:
            self._executor.submit(
                self._process_request_bounded, request, client_address
            )
        except Exception:
            self._capacity.release()
            self._deadline_watchdog.unregister(request)
            self.shutdown_request(request)

    def _process_request_bounded(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self._deadline_watchdog.unregister(request)
            self.shutdown_request(request)
            self._capacity.release()

    def _reject_overload(self, request):
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 12\r\n"
            b"Cache-Control: no-store\r\n"
            b"Connection: close\r\n"
            b"\r\n"
            b'{"ok":false}'
        )
        try:
            # This runs in one dedicated, bounded rejection worker rather than
            # the accept loop. Give a normal client a brief chance to finish
            # its tiny request and observe the 503, but use one absolute drain
            # deadline so a byte drip cannot retain even that worker.
            drain_deadline = time.monotonic() + 0.25
            request.settimeout(0.25)
            request.sendall(response)
            request.shutdown(socket.SHUT_WR)
            remaining = MAX_BODY_BYTES + 8192
            while remaining > 0:
                timeout = drain_deadline - time.monotonic()
                if timeout <= 0:
                    break
                request.settimeout(timeout)
                chunk = request.recv(min(4096, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        finally:
            self._deadline_watchdog.unregister(request)
            self.close_request(request)

    def _reject_overload_bounded(self, request):
        try:
            self._reject_overload(request)
        finally:
            self._reject_capacity.release()

    def handle_error(self, _request, _client_address):
        # Request bodies and credentials never belong in worker tracebacks.
        return

    def server_close(self):
        super().server_close()
        try:
            self._executor.shutdown(wait=True)
            self._reject_executor.shutdown(wait=True)
        finally:
            self._deadline_watchdog.close()


def create_server(*, host=AUTH_HOST, port=AUTH_PORT, **kwargs):
    if host != AUTH_HOST:
        raise ValueError("auth service must bind to IPv4 loopback")
    return BoundedHTTPServer((host, port), AuthHandler, **kwargs)


def main():
    try:
        server = create_server()
    except OSError:
        print("auth service could not bind its loopback socket", file=sys.stderr)
        return 1
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
