"""Preview fixtures must not leave renderer state pointing at production."""

import json
import socket
import threading
from pathlib import Path
from urllib.request import urlopen

import pytest

from tests import workspace_preview_server as preview


def test_preview_redirects_runtime_paths_and_restores_them(tmp_path):
    service = preview.ss
    original = service.USERS_FILE
    factory = getattr(preview, 'isolated_preview', None)
    assert callable(factory), 'Preview needs an explicit isolated fixture context'
    with factory(tmp_path):
        for name, value in vars(service).items():
            if isinstance(value, Path) and name.isupper() and name != '_STATIC_DIR':
                assert value.is_relative_to(tmp_path), name
        assert Path(service.HY_API_SECRET_FILE).is_relative_to(tmp_path)
        assert service.render_admin('preview.invalid', 'http://preview.invalid')
    assert service.USERS_FILE == original


def test_preview_server_owns_ephemeral_port_and_closes_socket():
    factory = getattr(preview, 'preview_server', None)
    assert callable(factory), 'Browser runner needs a managed preview server'
    with factory() as server:
        port = server.server_address[1]
        assert port > 0
        with urlopen(f'http://127.0.0.1:{port}/admin', timeout=5) as response:
            assert response.status == 200
            assert 'demo_alex' in response.read().decode()
    assert server.fileno() == -1


def test_base_preview_teardown_closes_partial_header_connections(monkeypatch):
    handler_started = threading.Event()
    handler_threads = []
    base_handler = preview.Preview

    class ObservedPreview(base_handler):
        def setup(self):
            super().setup()
            handler_threads.append(threading.current_thread())
            handler_started.set()

        def do_GET(self):
            self.send_response(204)
            self.end_headers()

    monkeypatch.setattr(preview, 'Preview', ObservedPreview)
    manager = preview.preview_server()
    server = manager.__enter__()
    connection = socket.create_connection(server.server_address, timeout=2)
    try:
        connection.settimeout(0.2)
        connection.sendall(b'GET /admin HTTP/1.1\r\nHost: preview.invalid\r\nX-Stall:')
        assert handler_started.wait(2), 'base preview did not accept the partial request'
        manager.__exit__(None, None, None)
        manager = None
        assert connection.recv(1) == b''
    finally:
        connection.close()
        if manager is not None:
            manager.__exit__(None, None, None)
        for thread in handler_threads:
            thread.join(timeout=2)
            assert not thread.is_alive(), 'controlled base preview request did not stop'


def test_preview_refresh_endpoints_return_fictional_json():
    with preview.preview_server() as server:
        for route in ('/admin/overview.json', '/admin/analytics.json', '/user/panel.json'):
            with urlopen(
                f'http://127.0.0.1:{server.server_address[1]}{route}', timeout=5
            ) as response:
                assert response.headers.get_content_type() == 'application/json'
                assert isinstance(json.load(response), dict)


def test_preview_refuses_accidental_production_file_access(tmp_path, monkeypatch):
    import io

    original_open = io.open

    def forbid_real_read(file, *args, **kwargs):
        if str(file) == '/root/hysteria/users.json':
            raise AssertionError('Production read reached the OS')
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(io, 'open', forbid_real_read)
    with preview.isolated_preview(tmp_path):
        with pytest.raises(PermissionError, match='production path'):
            Path('/root/hysteria/users.json').read_text()


def test_preview_blocks_external_connections_and_service_commands(tmp_path, monkeypatch):
    import socket
    import subprocess

    def reached_external_boundary(*args, **kwargs):
        raise AssertionError('External operation reached the OS')

    monkeypatch.setattr(socket.socket, 'connect', reached_external_boundary)
    monkeypatch.setattr(subprocess, 'Popen', reached_external_boundary)
    with preview.isolated_preview(tmp_path):
        with socket.socket() as connection:
            with pytest.raises(PermissionError, match='connection'):
                connection.connect(('127.0.0.1', 25413))
        with pytest.raises(PermissionError, match='command'):
            subprocess.run(['systemctl', 'is-active', 'hysteria-server.service'])
