"""The managed client uses sing-box's actual non-reset RPC and strict wire data."""

import importlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import grpc
import pytest

METHOD = 'v2ray.core.app.stats.command.StatsService'
REQUEST = b'\x1a\x07user>>>'
ROOT = Path(__file__).resolve().parents[1]


def client():
    if not (ROOT / 'hysteria/tuic_stats_client.py').is_file():
        pytest.fail('the fixed TUIC stats client is missing')
    return importlib.import_module('tuic_stats_client')


def varint(number):
    result = bytearray()
    while number > 127:
        result.append((number & 127) | 128)
        number >>= 7
    result.append(number)
    return bytes(result)


def row(name=b'user>>>alice>>>traffic>>>uplink', value=None):
    stat = b'\x0a' + varint(len(name)) + name
    if value is not None:
        stat += b'\x10' + varint(value)
    return b'\x0a' + varint(len(stat)) + stat


@contextmanager
def server(response, *, requests=None, release=None):
    def query(request, context):
        if requests is not None:
            requests.append(request)
        if request != REQUEST:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'wrong pattern or reset')
        if release is not None:
            release.wait(4)
        return response

    service = grpc.server(ThreadPoolExecutor(max_workers=1))
    service.add_generic_rpc_handlers((grpc.method_handlers_generic_handler(METHOD, {
        'QueryStats': grpc.unary_unary_rpc_method_handler(query),
    }),))
    port = service.add_insecure_port('127.0.0.1:0')
    service.start()
    try:
        yield f'127.0.0.1:{port}'
    finally:
        if release is not None:
            release.set()
        service.stop(0).wait(2)


def test_real_rpc_fixed_namespace_repeated_pattern_and_omitted_zero():
    requests = []
    response = row() + row(b'user>>>bob>>>traffic>>>downlink', 123)
    with server(response, requests=requests) as endpoint:
        assert client().query_stats(endpoint) == {'stat': [
            {'name': 'user>>>alice>>>traffic>>>uplink', 'value': 0},
            {'name': 'user>>>bob>>>traffic>>>downlink', 'value': 123},
        ]}
        assert client().query_stats(endpoint)['stat'][1]['value'] == 123
    # Field3 is repeated patterns; omitted reset defaults to false.
    assert requests == [b'\x1a\x07user>>>', b'\x1a\x07user>>>']


@pytest.mark.parametrize('wire', [
    b'\x0a\x02\x0a\x03',                         # truncated string
    b'\x0a\x03\x0a\x01\xff',                   # invalid UTF8
    b'\x0a\x06\x0a\x01a\x0a\x01b',            # duplicate name
    b'\x0a\x07\x0a\x01a\x10\x00\x10\x01',    # duplicate value
    row(value=(1 << 64) - 1),                      # negative int64
    b'\x0a\x0e\x0a\x01a\x10' + b'\x80' * 9 + b'\x02',  # uint64 overflow
    b'\x0a\x0f\x0a\x01a\x10' + b'\x80' * 10 + b'\x00', # >10 bytes
    b'\x0a\x02\x10\x01',                        # missing name
    b'\x00', b'\x0f', b'\x13',                   # invalid field/wire/group
    b'\x12\x02x', b'\x11\x00', b'\x1d\x00',    # truncated unknown fields
    b'\x0a\x01\x0d',                            # wrong scalar wire type
])
def test_malformed_wire_never_becomes_an_empty_success(wire):
    with pytest.raises(ValueError):
        client().decode_response(wire)


def test_valid_unknown_fields_are_skipped_without_losing_counters():
    wire = b'\x10\x01\x19' + b'\x00' * 8 + b'\x22\x02ok\x2d' + b'\x00' * 4
    assert client().decode_response(wire + row(value=(1 << 63) - 1)) == {
        'stat': [{'name': 'user>>>alice>>>traffic>>>uplink', 'value': (1 << 63) - 1}],
    }


@pytest.mark.parametrize('endpoint', [
    'localhost:10086', 'example.invalid:10086', '0.0.0.0:10086',
    '127.0.0.1:10085', '127.0.0.1:0', '127.0.0.1:65536',
    'dns:///127.0.0.1:10086', '127.0.0.1:1/path', '[::1%lo]:10086',
])
def test_only_a_dedicated_literal_loopback_endpoint_is_accepted(endpoint):
    with pytest.raises(ValueError):
        client().query_stats(endpoint)


def test_real_rpc_has_a_three_second_deadline():
    release = threading.Event()
    with server(b'', release=release) as endpoint:
        with pytest.raises(grpc.RpcError) as failure:
            client().query_stats(endpoint)
    assert failure.value.code() == grpc.StatusCode.DEADLINE_EXCEEDED


def test_real_rpc_rejects_an_oversized_wire_response():
    with server(b'x' * (2 * 1024 * 1024 + 1)) as endpoint:
        with pytest.raises(grpc.RpcError) as failure:
            client().query_stats(endpoint)
    assert failure.value.code() == grpc.StatusCode.RESOURCE_EXHAUSTED


def test_cli_emits_only_bounded_json_and_no_partial_oversized_output():
    command = [sys.executable, '-s', '-E', str(ROOT / 'hysteria/tuic_stats_client.py')]
    with server(row(value=27)) as endpoint:
        result = subprocess.run(command + ['--endpoint', endpoint], capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        'stat': [{'name': 'user>>>alice>>>traffic>>>uplink', 'value': 27}],
    }
    with server(row(b'\x01' * 400000)) as endpoint:
        result = subprocess.run(command + ['--endpoint', endpoint], capture_output=True, timeout=5)
    assert result.returncode == 1
    assert result.stdout == b''
    assert len(result.stderr) < 1024


def test_import_does_not_require_grpc_for_legacy_collectors():
    result = subprocess.run([sys.executable, '-s', '-E', '-c',
        "import sys; sys.path.insert(0, sys.argv[1]); import tuic_stats_client; "
        "assert 'grpc' not in sys.modules", str(ROOT / 'hysteria')],
        capture_output=True, timeout=5)
    assert result.returncode == 0, result.stderr
