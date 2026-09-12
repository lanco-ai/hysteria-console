"""Bounded request adapters for the FastAPI HTTP boundary."""

from collections.abc import Mapping
from types import MappingProxyType

import anyio
import http_utils

FORM_READ_TIMEOUT = 10.0


class FormReadTimeout(Exception):
    """The transport did not finish delivering a form within its receipt bound."""


class RequestHeaders(Mapping):
    """Read-only, case-insensitive headers retaining every raw ASGI value."""

    def __init__(self, headers):
        if isinstance(headers, RequestHeaders):
            pairs = headers._pairs
        elif isinstance(headers, Mapping):
            pairs = tuple((str(name).lower(), str(value)) for name, value in headers.items())
        else:
            pairs = tuple(
                (
                    bytes(name).decode('latin-1').lower(),
                    bytes(value).decode('latin-1'),
                )
                for name, value in headers
            )
        self._pairs = pairs
        first = {}
        repeated = {}
        for name, value in pairs:
            first.setdefault(name, value)
            repeated.setdefault(name, []).append(value)
        self._first = MappingProxyType(first)
        self._repeated = MappingProxyType(
            {name: tuple(values) for name, values in repeated.items()}
        )

    def __getitem__(self, name):
        return self._first[str(name).lower()]

    def __iter__(self):
        return iter(self._first)

    def __len__(self):
        return len(self._first)

    def get(self, name, default=None):
        return self._first.get(str(name).lower(), default)

    def get_all(self, name, default=None):
        values = self._repeated.get(str(name).lower())
        return list(values) if values is not None else default


async def read_form(request, headers):
    """Receive and decode one exactly framed URL-encoded form."""

    claimed_length = http_utils.form_content_length(headers)
    chunks = []
    received = 0
    with anyio.move_on_after(FORM_READ_TIMEOUT) as receipt_timeout:
        while True:
            message = await request.receive()
            if message.get('type') != 'http.request':
                raise http_utils.BadRequest
            chunk = message.get('body', b'')
            if not isinstance(chunk, bytes):
                raise http_utils.BadRequest
            received += len(chunk)
            if received > http_utils.MAX_FORM_BYTES:
                raise http_utils.RequestTooLarge
            if received > claimed_length:
                raise http_utils.BadRequest
            chunks.append(chunk)
            if not message.get('more_body', False):
                break
    if receipt_timeout.cancel_called:
        raise FormReadTimeout
    if received != claimed_length:
        raise http_utils.BadRequest
    return http_utils.decode_form_body(b''.join(chunks))
