"""Bounded, non-reset generic gRPC client for the pinned sing-box stats API.

The generated source package is misleading: sing-box rewrites its service
descriptor to the v2ray.core namespace. grpcio is imported only when queried,
so legacy collectors and dependency-free source validation remain available.
"""

import argparse
import ipaddress
import json
import re
import sys

METHOD = '/v2ray.core.app.stats.command.StatsService/QueryStats'
# QueryStatsRequest.patterns is field3. Omitted reset(field2) is false.
REQUEST = b'\x1a\x07user>>>'
MAX_BYTES = 2 * 1024 * 1024


def validate_endpoint(endpoint):
    """Accept only a literal loopback address and a dedicated numeric port."""
    if not isinstance(endpoint, str):
        raise ValueError('invalid stats endpoint')
    match = re.fullmatch(r'(\[[0-9a-fA-F:]+\]|[0-9.]+):([0-9]{1,5})', endpoint)
    if match is None:
        raise ValueError('invalid stats endpoint')
    address = ipaddress.ip_address(match[1].strip('[]'))
    port = int(match[2])
    if not address.is_loopback or not 1 <= port <= 65535 or port == 10085:
        raise ValueError('stats require a dedicated loopback port')
    host = f'[{address}]' if address.version == 6 else str(address)
    return f'{host}:{port}'


class _Wire:
    def __init__(self, data):
        self.data = memoryview(data)
        self.offset = 0

    def varint(self):
        number = 0
        for index in range(10):
            if self.offset >= len(self.data):
                raise ValueError('truncated protobuf varint')
            byte = self.data[self.offset]
            self.offset += 1
            if index == 9 and byte > 1:
                raise ValueError('overflowed protobuf varint')
            number |= (byte & 127) << (7 * index)
            if not byte & 128:
                return number
        raise ValueError('oversized protobuf varint')

    def take(self, length):
        end = self.offset + length
        if end > len(self.data):
            raise ValueError('truncated protobuf field')
        value = self.data[self.offset:end]
        self.offset = end
        return value

    def fields(self):
        while self.offset < len(self.data):
            tag = self.varint()
            field, wire = tag >> 3, tag & 7
            if not 1 <= field <= (1 << 29) - 1:
                raise ValueError('invalid protobuf field number')
            if wire == 0:
                value = self.varint()
            elif wire == 1:
                value = self.take(8)
            elif wire == 2:
                value = self.take(self.varint())
            elif wire == 5:
                value = self.take(4)
            else:
                raise ValueError('unsupported protobuf wire type')
            yield field, wire, value


def _stat(data):
    name, value, seen = None, 0, set()
    for field, wire, item in _Wire(data).fields():
        if field not in (1, 2):
            continue
        if field in seen or wire != (2 if field == 1 else 0):
            raise ValueError('duplicate or invalid statistics scalar')
        seen.add(field)
        if field == 1:
            name = bytes(item).decode('utf-8', errors='strict')
        else:
            if item >= 1 << 63:
                raise ValueError('negative statistics counter')
            value = item
    if not name:
        raise ValueError('missing statistics name')
    return {'name': name, 'value': value}


def decode_response(data):
    """Decode the small stats schema; valid unknown fields are safely skipped."""
    if len(data) > MAX_BYTES:
        raise ValueError('statistics response exceeded limit')
    rows, output_bytes = [], len(b'{"stat":[]}')
    for field, wire, value in _Wire(data).fields():
        if field != 1:
            continue
        if wire != 2:
            raise ValueError('invalid statistics row wire type')
        row = _stat(value)
        output_bytes += len(json.dumps(row, ensure_ascii=False, separators=(',', ':')).encode())
        output_bytes += bool(rows)
        if output_bytes > MAX_BYTES:
            raise ValueError('statistics JSON exceeded limit')
        rows.append(row)
    return {'stat': rows}


def query_stats(endpoint):
    endpoint = validate_endpoint(endpoint)
    import grpc

    with grpc.insecure_channel(endpoint, options=(
        ('grpc.enable_http_proxy', 0),
        ('grpc.max_receive_message_length', MAX_BYTES),
        ('grpc.max_send_message_length', 1024),
    )) as channel:
        # No reflection, generated stubs, caller-supplied methods or reset knobs.
        response = channel.unary_unary(METHOD)(REQUEST, timeout=3)
    return decode_response(response)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    args = parser.parse_args(argv)
    try:
        output = json.dumps(query_stats(args.endpoint), ensure_ascii=False, separators=(',', ':'))
        encoded = output.encode('utf-8') + b'\n'
        if len(encoded) > MAX_BYTES:
            raise ValueError('statistics JSON exceeded limit')
    except Exception:
        # Never emit a partial result or remote diagnostics containing identities.
        print('TUIC statistics unavailable', file=sys.stderr)
        return 1
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
