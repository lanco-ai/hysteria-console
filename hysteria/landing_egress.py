"""Validated, secret-safe registry for real residential SOCKS5 exits."""
from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import struct
import time
from pathlib import Path
from urllib.parse import urlparse

import state_store


REGISTRY_FILE = Path('/root/hysteria/landing_egresses.json')
REGISTRY_VERSION = 1
PROBE_URL = 'https://api.ipify.org/'
PROBE_TIMEOUT_SECONDS = 5
NODE_ID_RE = __import__('re').compile(r'^[a-z0-9][a-z0-9-]{0,62}$')
TEXT_MAX_LENGTH = 120
CREDENTIAL_MAX_LENGTH = 255


class LandingEgressValidationError(ValueError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


class LandingEgressProbeError(RuntimeError):
    def __init__(self, code):
        self.code = str(code)
        super().__init__(self.code)


def _has_control(value):
    return any(ord(ch) < 32 or ord(ch) == 127 for ch in value)


def _text(value, *, required=True):
    if not isinstance(value, str):
        raise LandingEgressValidationError('node_text_invalid')
    text = value.strip()
    if required and not text:
        raise LandingEgressValidationError('node_text_invalid')
    if (
        len(text) > TEXT_MAX_LENGTH
        or _has_control(value)
        or '<' in value
        or '>' in value
    ):
        raise LandingEgressValidationError('node_text_invalid')
    return text


def _global_ip(value, invalid_code, nonglobal_code=None):
    if not isinstance(value, str) or _has_control(value):
        raise LandingEgressValidationError(invalid_code)
    try:
        address = ipaddress.ip_address(value.strip())
    except ValueError as exc:
        raise LandingEgressValidationError(invalid_code) from exc
    if nonglobal_code and not address.is_global:
        raise LandingEgressValidationError(nonglobal_code)
    return str(address)


def _credential(value):
    if value is None:
        return ''
    if (
        not isinstance(value, str)
        or len(value.encode('utf-8')) > CREDENTIAL_MAX_LENGTH
        or _has_control(value)
    ):
        raise LandingEgressValidationError('credential_invalid')
    return value


def validate_node(raw):
    if not isinstance(raw, dict):
        raise LandingEgressValidationError('node_invalid')
    node_id = raw.get('id')
    if not isinstance(node_id, str) or not NODE_ID_RE.fullmatch(node_id):
        raise LandingEgressValidationError('node_id_invalid')
    port = raw.get('socks_port')
    if isinstance(port, bool):
        raise LandingEgressValidationError('socks_port_invalid')
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise LandingEgressValidationError('socks_port_invalid') from exc
    if not 1 <= port <= 65535 or (
        isinstance(raw.get('socks_port'), str)
        and str(port) != raw['socks_port'].strip()
    ):
        raise LandingEgressValidationError('socks_port_invalid')
    username = _credential(raw.get('socks_username', ''))
    password = _credential(raw.get('socks_password', ''))
    if bool(username) != bool(password):
        raise LandingEgressValidationError('credential_invalid')
    enabled = raw.get('enabled', True)
    if not isinstance(enabled, bool):
        raise LandingEgressValidationError('node_enabled_invalid')
    health = raw.get('health')
    if health is not None and not isinstance(health, dict):
        raise LandingEgressValidationError('node_health_invalid')
    return {
        'id': node_id,
        'name': _text(raw.get('name')),
        'socks_ip': _global_ip(
            raw.get('socks_ip'),
            'socks_ip_invalid',
            'socks_ip_not_global',
        ),
        'socks_port': port,
        'socks_username': username,
        'socks_password': password,
        'expected_exit_ip': _global_ip(
            raw.get('expected_exit_ip'),
            'exit_ip_invalid',
            'exit_ip_not_global',
        ),
        'isp': _text(raw.get('isp', ''), required=False),
        'region': _text(raw.get('region', ''), required=False),
        'enabled': enabled,
        'health': health,
    }


def empty_registry():
    return {'version': REGISTRY_VERSION, 'nodes': {}}


def validate_registry(raw):
    if (
        not isinstance(raw, dict)
        or raw.get('version') != REGISTRY_VERSION
        or not isinstance(raw.get('nodes'), dict)
    ):
        raise state_store.InvalidJsonState('invalid landing egress registry')
    nodes = {}
    try:
        for node_id, raw_node in raw['nodes'].items():
            node = validate_node(raw_node)
            if node_id != node['id']:
                raise LandingEgressValidationError('node_id_mismatch')
            nodes[node_id] = node
    except LandingEgressValidationError as exc:
        raise state_store.InvalidJsonState(
            f'invalid landing egress registry: {exc.code}',
        ) from exc
    return {'version': REGISTRY_VERSION, 'nodes': nodes}


def load_registry(path=None):
    target = Path(path) if path is not None else REGISTRY_FILE
    raw = state_store.load_json_strict(target, empty_registry())
    return validate_registry(raw)


def save_registry(registry, path=None):
    target = Path(path) if path is not None else REGISTRY_FILE
    validated = validate_registry(registry)
    state_store.save_text_atomic(
        target,
        json.dumps(validated, ensure_ascii=True, indent=2) + '\n',
    )
    target.chmod(0o600)


def _public_health(raw):
    if not isinstance(raw, dict):
        return None
    return {
        key: raw[key]
        for key in ('status', 'observed_ip', 'checked_at', 'error_code')
        if key in raw
    }


def public_node(node):
    validated = validate_node(node)
    return {
        'id': validated['id'],
        'name': validated['name'],
        'exit_ip': validated['expected_exit_ip'],
        'isp': validated['isp'],
        'region': validated['region'],
        'enabled': validated['enabled'],
        'health': _public_health(validated.get('health')),
    }


def _probe_deadline(timeout):
    return time.monotonic() + float(timeout)


def _remaining_probe_time(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError('probe deadline exceeded')
    return remaining


def _send_with_deadline(sock, payload, deadline):
    sock.settimeout(_remaining_probe_time(deadline))
    sock.sendall(payload)


def _recv_exact(sock, size, deadline):
    chunks = bytearray()
    while len(chunks) < size:
        sock.settimeout(_remaining_probe_time(deadline))
        part = sock.recv(size - len(chunks))
        if not part:
            raise OSError('unexpected EOF')
        chunks.extend(part)
    return bytes(chunks)


def _socks_connect(node, target_host, target_port, deadline):
    sock = socket.create_connection(
        (node['socks_ip'], node['socks_port']),
        timeout=_remaining_probe_time(deadline),
    )
    sock.settimeout(_remaining_probe_time(deadline))
    try:
        if node['socks_username']:
            _send_with_deadline(sock, b'\x05\x01\x02', deadline)
            if _recv_exact(sock, 2, deadline) != b'\x05\x02':
                raise OSError('SOCKS authentication method rejected')
            username = node['socks_username'].encode('utf-8')
            password = node['socks_password'].encode('utf-8')
            _send_with_deadline(sock,
                b'\x01' + bytes([len(username)]) + username
                + bytes([len(password)]) + password,
                deadline,
            )
            if _recv_exact(sock, 2, deadline) != b'\x01\x00':
                raise OSError('SOCKS authentication rejected')
        else:
            _send_with_deadline(sock, b'\x05\x01\x00', deadline)
            if _recv_exact(sock, 2, deadline) != b'\x05\x00':
                raise OSError('SOCKS no-auth method rejected')
        host = target_host.encode('idna')
        if not 1 <= len(host) <= 255:
            raise OSError('probe host is invalid')
        _send_with_deadline(sock,
            b'\x05\x01\x00\x03' + bytes([len(host)]) + host
            + struct.pack('!H', target_port),
            deadline,
        )
        head = _recv_exact(sock, 4, deadline)
        if head[:3] != b'\x05\x00\x00':
            raise OSError('SOCKS CONNECT rejected')
        atyp = head[3]
        if atyp == 1:
            _recv_exact(sock, 4, deadline)
        elif atyp == 4:
            _recv_exact(sock, 16, deadline)
        elif atyp == 3:
            _recv_exact(sock, _recv_exact(sock, 1, deadline)[0], deadline)
        else:
            raise OSError('SOCKS response address is invalid')
        _recv_exact(sock, 2, deadline)
        return sock
    except Exception:
        sock.close()
        raise


def _request_exit_ip(node, url, timeout):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or not parsed.hostname:
        raise OSError('probe URL is invalid')
    deadline = _probe_deadline(timeout)
    raw = _socks_connect(node, parsed.hostname, parsed.port or 443, deadline)
    try:
        context = ssl.create_default_context()
        raw.settimeout(_remaining_probe_time(deadline))
        with context.wrap_socket(raw, server_hostname=parsed.hostname) as tls:
            target = parsed.path or '/'
            if parsed.query:
                target += '?' + parsed.query
            request = (
                f'GET {target} HTTP/1.1\r\n'
                f'Host: {parsed.hostname}\r\n'
                'Accept: text/plain\r\n'
                'Connection: close\r\n\r\n'
            ).encode('ascii')
            tls.settimeout(_remaining_probe_time(deadline))
            tls.sendall(request)
            response = bytearray()
            while len(response) <= 8192:
                tls.settimeout(_remaining_probe_time(deadline))
                chunk = tls.recv(min(4096, 8193 - len(response)))
                if not chunk:
                    break
                response.extend(chunk)
    finally:
        raw.close()
    head, separator, body = bytes(response).partition(b'\r\n\r\n')
    if not separator or not head.startswith(b'HTTP/1.1 200 '):
        raise OSError('probe HTTP response is invalid')
    if len(body) > 64:
        raise OSError('probe body is too large')
    return body.decode('ascii', errors='strict')


def probe_exit(node, *, requester=None, url=PROBE_URL, timeout=PROBE_TIMEOUT_SECONDS):
    validated = validate_node(node)
    request = requester or _request_exit_ip
    try:
        raw_observed = request(validated, url, timeout)
        observed = str(ipaddress.ip_address(str(raw_observed).strip()))
    except LandingEgressProbeError:
        raise
    except Exception as exc:
        raise LandingEgressProbeError('probe_failed') from exc
    if observed != validated['expected_exit_ip']:
        raise LandingEgressProbeError('exit_ip_mismatch')
    return observed
