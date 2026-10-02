"""Negative probes must prove denial, not infer it from missing replies."""
import socket
import threading
from contextlib import ExitStack

import pytest

from tests.test_tuic_migration_tools import runtime_gate_module


def test_target_receives_unauthorized_bytes_even_when_reply_times_out():
    gate = runtime_gate_module()
    with ExitStack() as stack:
        client, target = socket.socketpair()
        stack.enter_context(client)
        stack.enter_context(target)
        client.settimeout(.02)
        received = bytearray()
        event = threading.Event()
        def read_target():
            received.extend(target.recv(1024))
            event.set()
        thread = threading.Thread(target=read_target)
        thread.start()
        stack.callback(thread.join, 1)
        def attempt():
            client.sendall(b'unauthorized-payload')
            assert event.wait(1)
            return client.recv(1)
        with pytest.raises(RuntimeError, match='target ingress'):
            gate.verify_denial(attempt=attempt, control=lambda: None,
                snapshot=lambda: (0, len(received)), rejection=lambda: True,
                health=lambda: None, stats=lambda: {}, stop_attempt=lambda: None,
                timeout=.05, quiet=.01)
        assert received == b'unauthorized-payload'


@pytest.mark.parametrize('error', [TimeoutError('timeout'), OSError('network failure'), RuntimeError('SOCKS protocol failure')])
def test_generic_errors_without_explicit_rejection_are_not_denial(error):
    gate = runtime_gate_module()
    def attempt():
        raise error
    with pytest.raises(RuntimeError, match='unconfirmed credential rejection'):
        gate.verify_denial(attempt=attempt, control=lambda: None,
            snapshot=lambda: (0, 0), rejection=lambda: False,
            health=lambda: None, stats=lambda: {}, stop_attempt=lambda: None,
            timeout=.02, quiet=.001)


def test_stale_or_other_identity_log_cannot_certify_rejection(tmp_path):
    gate = runtime_gate_module()
    log = tmp_path / 'server.log'
    uid = '33333333-3333-4333-8333-333333333333'
    log.write_text('authentication: unknown user ' + uid + '\n')
    start = log.stat().st_size
    with log.open('a') as out:
        out.write('authentication: unknown user 44444444-4444-4444-8444-444444444444\n')
        out.write('authentication timeout\n')
    assert gate.rejection_in_log(log, start, uid) is False
    with log.open('a') as out:
        out.write('connection failed: handle uni stream: authentication: unknown user ' + uid + '\n')
    assert gate.rejection_in_log(log, start, uid) is True


@pytest.mark.parametrize('protocol', ['tcp', 'udp'])
def test_loopback_target_records_ingress_before_reply(protocol):
    gate = runtime_gate_module()
    with ExitStack() as stack:
        tcp, udp, payload, observed = gate.echo_servers(stack)
        before = observed.snapshot()
        if protocol == 'tcp':
            with socket.create_connection(('127.0.0.1', tcp), timeout=1) as sock:
                sock.sendall(payload)
                assert gate.exact(sock, len(payload) * 2) == payload * 2
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(1)
                sock.sendto(payload, ('127.0.0.1', udp))
                assert sock.recv(65536) == payload * 2
        assert observed.snapshot() == (before[0] + 1, before[1] + len(payload))
        observed.health()


@pytest.mark.parametrize('mode', ['native', 'quic'])
def test_udp_target_ingress_without_reply_is_failure(mode):
    gate = runtime_gate_module()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as target, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
        target.bind(('127.0.0.1', 0))
        target.settimeout(1)
        client.settimeout(.01)
        received = []
        def attempt():
            client.sendto(mode.encode(), target.getsockname())
            received.append(target.recv(1024))
            return client.recv(1024)
        with pytest.raises(RuntimeError, match='target ingress'):
            gate.verify_denial(attempt=attempt, control=lambda: None,
                snapshot=lambda: (len(received), sum(map(len, received))), rejection=lambda: True,
                health=lambda: None, stats=lambda: {}, stop_attempt=lambda: None,
                timeout=.02, quiet=.001)
        assert received == [mode.encode()]


@pytest.mark.parametrize('fault', ['control', 'health', 'stats', 'reply'])
def test_explicit_rejection_does_not_mask_failed_controls_or_health(fault):
    gate = runtime_gate_module()
    calls = []
    def control():
        calls.append('control')
        if fault == 'control':
            raise RuntimeError('control failed')
    def health():
        if fault == 'health':
            raise RuntimeError('health failed')
    def attempt():
        calls.append('attempt')
        return b'unexpected' if fault == 'reply' else b''
    def stats():
        return {'changed': 1} if fault == 'stats' and 'attempt' in calls else {}
    with pytest.raises(RuntimeError):
        gate.verify_denial(attempt=attempt, control=control,
            snapshot=lambda: (0, 0), rejection=lambda: True,
            health=health, stats=stats, stop_attempt=lambda: None, quiet=.001)


def test_confirmed_denial_requires_both_controls_and_stops_probe():
    gate = runtime_gate_module()
    calls = []
    gate.verify_denial(attempt=lambda: b'', control=lambda: calls.append('control'),
        snapshot=lambda: (0, 0), rejection=lambda: True,
        health=lambda: None, stats=lambda: {}, stop_attempt=lambda: calls.append('stop'), quiet=.001)
    assert calls == ['control', 'stop', 'control']


def test_observer_thread_failure_and_oversized_logs_fail(tmp_path):
    gate = runtime_gate_module()
    observer = gate.TargetObservation()
    observer.error = OSError('observer failed')
    with pytest.raises(RuntimeError, match='observer unhealthy'):
        observer.snapshot()
    log = tmp_path / 'server.log'
    log.write_bytes(b'x' * (1024 * 1024 + 1))
    with pytest.raises(RuntimeError, match='log limit'):
        gate.rejection_in_log(log, 0, 'denied')


def test_partial_tcp_ingress_is_recorded_without_complete_payload():
    gate = runtime_gate_module()
    with ExitStack() as stack:
        tcp, _, _, observed = gate.echo_servers(stack)
        with socket.create_connection(('127.0.0.1', tcp), timeout=1) as sock:
            sock.sendall(b'x')
            sock.shutdown(socket.SHUT_WR)
            assert sock.recv(16) == b'xx'
        assert observed.snapshot() == (1, 1)


def test_ingress_during_probe_shutdown_fails():
    gate = runtime_gate_module()
    ingress = []
    with pytest.raises(RuntimeError, match='target ingress'):
        gate.verify_denial(attempt=lambda: b'', control=lambda: None,
            snapshot=lambda: (len(ingress), 0), rejection=lambda: True,
            health=lambda: None, stats=lambda: {}, stop_attempt=lambda: ingress.append('late'), quiet=.001)


def test_failed_post_control_cannot_pass():
    gate = runtime_gate_module()
    calls = []
    def control():
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError('post control failed')
    with pytest.raises(RuntimeError, match='post control failed'):
        gate.verify_denial(attempt=lambda: b'', control=control,
            snapshot=lambda: (0, 0), rejection=lambda: True,
            health=lambda: None, stats=lambda: {}, stop_attempt=lambda: None, quiet=.001)
