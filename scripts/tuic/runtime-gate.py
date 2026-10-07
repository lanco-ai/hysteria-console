#!/usr/bin/env python3
"""Isolated real TUICv5 TCP/UDP, identity and managed stats client gate.

Requires an explicitly supplied trusted binary and its SHA256. All sockets,
certificates, credentials and subprocesses belong to this fixture. No systemd,
production config, clear=1 or stats reset calls.
"""
import argparse
from contextlib import ExitStack
import hashlib
import json
import re
from pathlib import Path
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'hysteria'))
import tuic_user_meter as meter


def port(kind=socket.SOCK_STREAM):
    with socket.socket(socket.AF_INET, kind) as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def exact(sock, length):
    data = b''
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise RuntimeError('unexpected connection EOF')
        data += chunk
    return data


def socks_request(socks_port, command, target_port):
    sock = socket.create_connection(('127.0.0.1', socks_port), timeout=3)
    try:
        sock.sendall(b'\x05\x01\x00')
        if exact(sock, 2) != b'\x05\x00':
            raise RuntimeError('SOCKS handshake failed')
        sock.sendall(b'\x05' + bytes([command]) + b'\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', target_port))
        header = exact(sock, 4)
        if header[:2] != b'\x05\x00':
            raise RuntimeError('SOCKS request rejected')
        if header[3] == 1:
            host = socket.inet_ntoa(exact(sock, 4))
        elif header[3] == 4:
            host = socket.inet_ntop(socket.AF_INET6, exact(sock, 16))
        else:
            raise RuntimeError('unexpected SOCKS reply address')
        relay = (host, struct.unpack('!H', exact(sock, 2))[0])
        return sock, relay
    except Exception:
        sock.close()
        raise


class TargetObservation:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = self.bytes = 0
        self.threads = []
        self.error = None

    def add(self, events=0, count=0):
        with self.lock:
            self.events += events
            self.bytes += count

    def snapshot(self):
        self.health()
        with self.lock:
            return self.events, self.bytes

    def health(self):
        if self.error is not None or any(not thread.is_alive() for thread in self.threads):
            raise RuntimeError('target observer unhealthy')


def echo_servers(stack):
    tcp = stack.enter_context(socket.socket())
    tcp.bind(('127.0.0.1', 0))
    tcp.listen()
    tcp.settimeout(.1)
    udp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_DGRAM))
    udp.bind(('127.0.0.1', 0))
    udp.settimeout(.1)
    stop = threading.Event()
    observed = TargetObservation()
    payload = b'tuic-fixture-payload' * 31

    def tcp_echo():
        while not stop.is_set():
            try:
                connection, _ = tcp.accept()
            except TimeoutError:
                continue
            observed.add(events=1)
            with connection:
                connection.settimeout(3)
                value = b''
                try:
                    while len(value) < len(payload):
                        chunk = connection.recv(len(payload) - len(value))
                        if not chunk:
                            break
                        observed.add(count=len(chunk))
                        value += chunk
                    connection.sendall(value * 2)
                except OSError:
                    # Ingress has already been recorded, including partial writes.
                    continue

    def udp_echo():
        while not stop.is_set():
            try:
                value, remote = udp.recvfrom(65536)
            except TimeoutError:
                continue
            observed.add(events=1, count=len(value))
            udp.sendto(value * 2, remote)

    def guarded(function):
        try:
            function()
        except Exception as exc:
            observed.error = exc

    for function in (tcp_echo, udp_echo):
        thread = threading.Thread(target=guarded, args=(function,), daemon=True)
        thread.start()
        observed.threads.append(thread)
        stack.callback(thread.join, 4)
    stack.callback(stop.set)
    return tcp.getsockname()[1], udp.getsockname()[1], payload, observed


def rejection_in_log(path, offset, identity):
    # Pinned sing-quic 6a3a24d65b99 tuic/service.go emits this only on unknown UUID.
    with path.open('rb') as source:
        if source.seek(0, 2) < offset:
            raise RuntimeError('fixture log truncated')
        source.seek(offset)
        data = source.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise RuntimeError('fixture rejection log limit exceeded')
    return re.search(rb'authentication: unknown user ' + re.escape(identity.encode())
                     + rb'(?![0-9a-fA-F-])', data) is not None


def verify_denial(*, attempt, control, snapshot, rejection, health, stats,
                  stop_attempt, timeout=3, quiet=.1):
    control()
    health()
    baseline, counters = snapshot(), stats()
    try:
        returned = attempt()
    except (OSError, RuntimeError):
        returned = b''  # Transport errors alone are never evidence of authentication denial.
    if snapshot() != baseline:
        raise RuntimeError('unauthorized target ingress')
    if returned:
        raise RuntimeError('unauthorized reply received')
    deadline = time.monotonic() + timeout
    while True:
        health()
        if snapshot() != baseline:
            raise RuntimeError('unauthorized target ingress')
        if rejection():
            break
        if time.monotonic() >= deadline:
            raise RuntimeError('unconfirmed credential rejection')
        time.sleep(.01)
    stop_attempt()
    deadline = time.monotonic() + quiet
    while True:
        health()
        if snapshot() != baseline:
            raise RuntimeError('unauthorized target ingress')
        if time.monotonic() >= deadline:
            break
        time.sleep(.01)
    if stats() != counters:
        raise RuntimeError('denied identity changed authorized counters')
    control()
    health()


def transfer(socks_port, protocol, tcp_port, udp_port, payload):
    if protocol == 'tcp':
        connection, _ = socks_request(socks_port, 1, tcp_port)
        with connection:
            connection.sendall(payload)
            return exact(connection, len(payload) * 2)
    association, relay = socks_request(socks_port, 3, 0)
    with association, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(3)
        header = b'\x00\x00\x00\x01' + socket.inet_aton('127.0.0.1') + struct.pack('!H', udp_port)
        udp.sendto(header + payload, relay)
        response, _ = udp.recvfrom(65536)
        if not response.startswith(header):
            raise RuntimeError('unexpected UDP response header')
        return response[len(header):]


def launch(stack, binary, directory, name, config):
    path = directory / (name + '.json')
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    subprocess.run([str(binary), 'check', '-c', str(path)], check=True, timeout=15,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    log = stack.enter_context((directory / (name + '.log')).open('wb'))
    process = subprocess.Popen([str(binary), 'run', '-c', str(path)], stdout=log, stderr=log)

    def close():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    stack.callback(close)
    return process


def wait_port(number, process):
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError('fixture process exited')
        try:
            with socket.create_connection(('127.0.0.1', number), timeout=.1):
                return
        except OSError:
            time.sleep(.05)
    raise RuntimeError('fixture listener did not become ready')


def loopback_inbounds(inbound):
    return [{**inbound, 'tag': f'tuic-fixture-{index}', 'listen': address}
            for index, address in enumerate(('127.0.0.1', '::1'))]


def await_counters(stats, name, before, expected, *, timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        snapshot = stats()
        row = snapshot.get(name, {'rx': 0, 'tx': 0})
        diff = {key: row[key] - before[key] for key in ('rx', 'tx')}
        if diff == expected:
            return snapshot
        if any(diff[key] < 0 or diff[key] > expected[key] for key in diff):
            raise RuntimeError(f'payload accounting mismatch: {name}: {diff}')
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError('counter visibility deadline exceeded')
        time.sleep(min(.02, remaining))


def run(args):
    binary = Path(args.binary).resolve()
    with binary.open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != args.sha256:
        raise RuntimeError('trusted binary SHA256 mismatch')
    version = meter.bounded_command([str(binary), 'version']).decode()
    if '1.14.2' not in version or any(tag not in version for tag in ('with_quic', 'with_v2ray_api')):
        raise RuntimeError('unsupported artifact: require1.14.2 with_quic,with_v2ray_api')
    with tempfile.TemporaryDirectory(prefix='tuic-runtime-gate-') as root, ExitStack() as stack:
        directory = Path(root)
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-keyout', str(directory / 'key.pem'), '-out', str(directory / 'cert.pem'),
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost', '-days', '1'],
                       check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tcp_port, udp_port, payload, observed = echo_servers(stack)
        tuic_port, api_port = port(socket.SOCK_DGRAM), port()
        identities = [
            {'name': 'alice', 'uuid': '11111111-1111-4111-8111-111111111111', 'password': 'alice:fixture'},
            {'name': 'bob', 'uuid': '22222222-2222-4222-8222-222222222222', 'password': 'bob:fixture'},
        ]
        config = {'log': {'level': 'warn'}, 'inbounds': [{
            'type': 'tuic', 'listen_port': tuic_port,
            'users': identities, 'congestion_control': 'bbr', 'zero_rtt_handshake': False,
            'tls': {'enabled': True, 'alpn': ['h3'], 'certificate_path': str(directory / 'cert.pem'),
                    'key_path': str(directory / 'key.pem')},
        }], 'outbounds': [{'type': 'direct'}],
            'experimental': {'v2ray_api': {'listen': f'127.0.0.1:{api_port}',
                                         'stats': {'enabled': True, 'users': ['alice', 'bob']}}}}
        config['inbounds'] = loopback_inbounds(config['inbounds'][0])
        server = launch(stack, binary, directory, 'server', config)
        wait_port(api_port, server)

        def stats():
            output = meter.bounded_command([
                args.stats_python, '-s', '-E',
                str(Path(__file__).resolve().parents[2] / 'hysteria/tuic_stats_client.py'),
                '--endpoint', f'127.0.0.1:{api_port}',
            ], timeout=5, limit=2 * 1024 * 1024)
            return meter.parse_stats(json.loads(output), {'alice', 'bob'})

        initial = stats()
        if any(sum(row.values()) for row in initial.values()):
            raise RuntimeError('fixture counters were not initially zero')
        results = []
        for index, (identity, mode, address) in enumerate([
            (identities[0], 'native', '127.0.0.1'), (identities[1], 'quic', '::1'),
        ]):
            socks_port = port()
            client = {'log': {'level': 'warn'}, 'inbounds': [{'type': 'socks', 'listen': '127.0.0.1', 'listen_port': socks_port}],
                      'outbounds': [{'type': 'tuic', 'server': address, 'server_port': tuic_port,
                                     'uuid': identity['uuid'], 'password': identity['password'],
                                     'congestion_control': 'bbr', 'zero_rtt_handshake': False, 'udp_relay_mode': mode,
                                     'tls': {'enabled': True, 'server_name': 'localhost', 'alpn': ['h3'],
                                             'certificate_path': str(directory / 'cert.pem')}}]}
            process = launch(stack, binary, directory, f'client-{index}', client)
            wait_port(socks_port, process)
            def health():
                observed.health()
                if server.poll() is not None or process.poll() is not None:
                    raise RuntimeError('authorized fixture process exited')
                stats()

            for protocol in ('tcp', 'udp'):
                def control():
                    health()
                    ingress = observed.snapshot()
                    before = stats()
                    if transfer(socks_port, protocol, tcp_port, udp_port, payload) != payload * 2:
                        raise RuntimeError('authorized payload mismatch')
                    if observed.snapshot() != (ingress[0] + 1, ingress[1] + len(payload)):
                        raise RuntimeError('unexpected target ingress during authorized control')
                    name = identity['name']
                    after = await_counters(stats, name, before.get(name, {'rx': 0, 'tx': 0}),
                                           {'rx': len(payload), 'tx': 2 * len(payload)})
                    other = 'bob' if name == 'alice' else 'alice'
                    if before.get(other, {'rx': 0, 'tx': 0}) != after.get(other, {'rx': 0, 'tx': 0}):
                        raise RuntimeError('traffic crossed user identity')
                    if stats() != after:
                        raise RuntimeError('query unexpectedly reset statistics')

                with ExitStack() as denied_stack:
                    denied_port = port()
                    denied_id = str(uuid.uuid4())
                    denied_config = json.loads(json.dumps(client))
                    denied_config['inbounds'][0]['listen_port'] = denied_port
                    denied_config['outbounds'][0].update(uuid=denied_id, password='denied-fixture')
                    denied = launch(denied_stack, binary, directory, f'denied-{index}-{protocol}', denied_config)
                    wait_port(denied_port, denied)
                    log = directory / 'server.log'
                    offset = log.stat().st_size
                    stopped = False

                    def negative_health():
                        health()
                        if not stopped and denied.poll() is not None:
                            raise RuntimeError('denied fixture process exited unexpectedly')

                    def stop_attempt():
                        nonlocal stopped
                        denied_stack.close()
                        stopped = True

                    verify_denial(
                        attempt=lambda: transfer(denied_port, protocol, tcp_port, udp_port, payload),
                        control=control, snapshot=observed.snapshot,
                        rejection=lambda: rejection_in_log(log, offset, denied_id),
                        health=negative_health, stats=stats, stop_attempt=stop_attempt)
                results.append(f'{identity["name"]}: {protocol} {mode} via {address}; exact counters; '
                               'fresh unknown-UUID rejection, no target ingress, authorized controls')
        return {'status': 'PASS', 'binary_sha256': args.sha256, 'version': version, 'checks': results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binary', required=True)
    parser.add_argument('--sha256', required=True)
    parser.add_argument('--stats-python', default='/root/hysteria/.venv-tuic-stats/bin/python')
    args = parser.parse_args()
    try:
        result = run(args)
    except Exception as exc:
        print(json.dumps({'status': 'BLOCKED/FAIL', 'reason': str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
