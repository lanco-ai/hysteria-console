"""Shared lifecycle primitives for loopback-only preview HTTP servers."""

import socket
import threading
import time
from contextlib import contextmanager
from http.server import ThreadingHTTPServer


class DrainingThreadingHTTPServer(ThreadingHTTPServer):
    """Own accepted sockets and join every request handler before close returns."""

    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self._active_requests = set()
        self._active_requests_lock = threading.Lock()
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        with self._active_requests_lock:
            self._active_requests.add(request)
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self._active_requests_lock:
                self._active_requests.discard(request)
            raise

    def shutdown_request(self, request):
        try:
            super().shutdown_request(request)
        finally:
            with self._active_requests_lock:
                self._active_requests.discard(request)

    def close_active_requests(self):
        with self._active_requests_lock:
            requests = tuple(self._active_requests)
        for request in requests:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


@contextmanager
def managed_preview_http_server(address, handler):
    """Run a preview server and drain accepted handlers before returning."""

    server = DrainingThreadingHTTPServer(address, handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.close_active_requests()
        server.server_close()
        worker.join(timeout=5)
        if worker.is_alive():
            raise RuntimeError('Preview serving thread did not stop')


def read_request_body(
    stream,
    connection,
    content_length,
    *,
    timeout,
    monotonic=time.monotonic,
):
    """Read exactly one framed body within a total receipt deadline."""

    previous_timeout = connection.gettimeout()
    deadline = monotonic() + timeout
    chunks = []
    received = 0
    try:
        while received < content_length:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise socket.timeout
            connection.settimeout(remaining)
            chunk = stream.read1(content_length - received)
            if not chunk:
                break
            chunks.append(chunk)
            received += len(chunk)
    finally:
        connection.settimeout(previous_timeout)
    return b''.join(chunks)
