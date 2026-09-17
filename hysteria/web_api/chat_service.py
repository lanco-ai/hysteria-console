"""Server-side settings and transport helpers for the self-use chat page.

The module deliberately keeps credentials on the server.  It does not log
request bodies, upstream headers, or upstream response bodies.
"""

import asyncio
import codecs
import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import state_store


DEFAULT_SETTINGS_PATH = Path('/root/hysteria/state/chat/settings.json')
MAX_BODY_BYTES = 128 * 1024
MAX_MESSAGES = 128
MAX_MESSAGE_CHARS = 32 * 1024
REASONING_EFFORTS = ('auto', 'low', 'medium', 'high')
STREAM_CONNECT_TIMEOUT = 5.0
STREAM_READ_TIMEOUT = 45.0
STREAM_WRITE_TIMEOUT = 10.0
STREAM_POOL_TIMEOUT = 5.0
STREAM_TOTAL_TIMEOUT = 10 * 60
_MISSING = object()


class ChatSettingsError(ValueError):
    """A settings value is missing or outside the supported safe shape."""


class ChatUpstreamError(RuntimeError):
    """A third-party API failed without retaining its response body."""

    def __init__(
        self,
        status: int | None = None,
        *,
        retry_after: str | None = None,
        reasoning_unsupported: bool = False,
        stream_options_unsupported: bool = False,
    ):
        self.status = status
        self.retry_after = retry_after
        self.reasoning_unsupported = reasoning_unsupported
        self.stream_options_unsupported = stream_options_unsupported
        super().__init__('chat upstream request failed')


@dataclass(frozen=True, slots=True)
class ChatSettings:
    base_url: str = ''
    api_key: str = ''
    temperature: float = 0.7


def mask_api_key(value: str) -> str:
    """Return a non-sensitive display value for an API key."""
    if not value:
        return ''
    if len(value) <= 8:
        return '•' * 8
    return f'{value[:3]}…{value[-4:]}'


def _coerce_settings(raw: object) -> ChatSettings:
    if raw is None:
        return ChatSettings()
    if not isinstance(raw, dict):
        raise ChatSettingsError('invalid settings shape')
    base_url = str(raw.get('base_url') or '').strip()
    api_key = str(raw.get('api_key') or '')
    temperature = raw.get('temperature', 0.7)
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise ChatSettingsError('temperature must be a number') from None
    _validate_values(base_url=base_url, api_key=api_key, temperature=temperature)
    return ChatSettings(
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
    )


def _validate_values(*, base_url: str, api_key: str, temperature: float) -> None:
    if len(base_url) > 2048:
        raise ChatSettingsError('base_url is too long')
    if base_url:
        parsed = urlsplit(base_url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc:
            raise ChatSettingsError('base_url must be an http(s) URL')
        if (
            parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.query
        ):
            raise ChatSettingsError('base_url contains an unsupported authority')
    if len(api_key) > 1024:
        raise ChatSettingsError('api_key is too long')
    if isinstance(temperature, bool) or not math.isfinite(float(temperature)):
        raise ChatSettingsError('temperature must be finite')
    if not 0 <= float(temperature) <= 2:
        raise ChatSettingsError('temperature must be between 0 and 2')


class ChatSettingsStore:
    def __init__(self, path: str | Path = DEFAULT_SETTINGS_PATH):
        self.path = Path(path)

    def read(self) -> ChatSettings:
        try:
            raw = state_store.load_json_strict(self.path, {})
        except state_store.StateStoreError as exc:
            raise ChatSettingsError('settings unavailable') from exc
        return _coerce_settings(raw)

    def public(self) -> dict[str, object]:
        settings = self.read()
        return {
            'base_url': settings.base_url,
            'temperature': settings.temperature,
            'api_key_configured': bool(settings.api_key),
            'api_key_masked': mask_api_key(settings.api_key),
        }

    def update(
        self,
        *,
        base_url: object = _MISSING,
        temperature: object = _MISSING,
        api_key: object = _MISSING,
    ) -> dict[str, object]:
        lock_path = self.path.with_name(self.path.name + '.lock')
        try:
            with state_store.file_lock(lock_path):
                current = self.read()
                next_base_url = (
                    current.base_url
                    if base_url is _MISSING
                    else str(base_url or '').strip()
                )
                next_temperature = current.temperature if temperature is _MISSING else temperature
                next_api_key = current.api_key if api_key is _MISSING else str(api_key or '')
                try:
                    next_temperature = float(next_temperature)
                except (TypeError, ValueError):
                    raise ChatSettingsError('temperature must be a number') from None
                _validate_values(
                    base_url=next_base_url,
                    api_key=next_api_key,
                    temperature=next_temperature,
                )
                payload = {
                    'base_url': next_base_url,
                    'api_key': next_api_key,
                    'temperature': next_temperature,
                }
                state_store.save_json(self.path, payload)
                self.path.chmod(0o600)
        except (state_store.StateStoreError, OSError) as exc:
            raise ChatSettingsError('settings unavailable') from exc
        return self.public()


def validate_messages(messages: object) -> list[dict[str, str]]:
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise ChatSettingsError('messages must be a non-empty list')
    normalized: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise ChatSettingsError('invalid message')
        role = message.get('role')
        content = message.get('content')
        if role not in ('system', 'user', 'assistant') or not isinstance(content, str):
            raise ChatSettingsError('invalid message')
        if not content or len(content) > MAX_MESSAGE_CHARS:
            raise ChatSettingsError('message content is invalid')
        normalized.append({'role': role, 'content': content})
    return normalized


def chat_completions_url(base_url: str) -> str:
    """Build one OpenAI-compatible chat completions URL from a base value.

    Users commonly paste either an API root (``.../v1``) or the complete
    ``.../chat/completions`` endpoint.  Keep both forms valid without adding a
    second endpoint suffix; also collapse a duplicated trailing ``/v1`` that
    can result from copying a provider URL into a field that already contains
    the version path.
    """
    normalized = _normalise_version_path(base_url)
    suffix = '/chat/completions'
    if normalized.endswith(suffix):
        return normalized
    return normalized + suffix


def _normalise_version_path(base_url: str) -> str:
    normalized = base_url.rstrip('/')
    while '/v1/v1/' in normalized:
        normalized = normalized.replace('/v1/v1/', '/v1/')
    while normalized.endswith('/v1/v1'):
        normalized = normalized[:-3]
    return normalized


def chat_models_url(base_url: str) -> str:
    """Build a standard OpenAI ``/models`` URL from a provider base value."""
    normalized = _normalise_version_path(base_url)
    if normalized.endswith('/chat/completions'):
        normalized = normalized[:-len('/chat/completions')]
    if normalized.endswith('/models'):
        return normalized
    return normalized + '/models'


class SSEDecoder:
    """Decode OpenAI-compatible SSE records across arbitrary network chunks."""

    def __init__(self):
        self._buffer = ''
        self._event_lines: list[str] = []
        self._utf8_decoder = codecs.getincrementaldecoder('utf-8')('replace')

    def feed(self, chunk: bytes | str) -> list[dict[str, object]]:
        if isinstance(chunk, bytes):
            chunk = self._utf8_decoder.decode(chunk, final=False)
        self._buffer += chunk
        return self._consume_lines(final=False)

    def finish(self) -> list[dict[str, object]]:
        self._buffer += self._utf8_decoder.decode(b'', final=True)
        events = self._consume_lines(final=True)
        if self._buffer:
            self._event_lines.append(self._buffer)
            self._buffer = ''
        if self._event_lines:
            event = _decode_sse_event('\n'.join(self._event_lines))
            self._event_lines = []
            if event is not None:
                events.append(event)
        return events

    def _consume_lines(self, *, final: bool) -> list[dict[str, object]]:
        events: list[dict[str, object]] = []
        while self._buffer:
            newline_positions = [position for position in (
                self._buffer.find('\n'),
                self._buffer.find('\r'),
            ) if position >= 0]
            if not newline_positions:
                break
            position = min(newline_positions)
            newline_length = 1
            if self._buffer[position] == '\r':
                if position + 1 >= len(self._buffer) and not final:
                    break
                if self._buffer[position:position + 2] == '\r\n':
                    newline_length = 2
            line = self._buffer[:position]
            self._buffer = self._buffer[position + newline_length:]
            if line:
                self._event_lines.append(line)
                continue
            event = _decode_sse_event('\n'.join(self._event_lines))
            self._event_lines = []
            if event is not None:
                events.append(event)
        return events


def _decode_sse_event(raw_event: str) -> dict[str, object] | None:
    data_lines = []
    for line in raw_event.split('\n'):
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip(' '))
    if not data_lines:
        return None
    data = '\n'.join(data_lines).strip()
    if data == '[DONE]':
        return {'type': 'done'}
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    usage = payload.get('usage')
    if isinstance(usage, dict):
        safe_usage = {
            key: int(value)
            for key, value in usage.items()
            if key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and int(value) >= 0
        }
        if safe_usage:
            return {'type': 'usage', 'usage': safe_usage}
    choices = payload.get('choices')
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        delta = choices[0].get('delta')
        if isinstance(delta, dict):
            content = delta.get('content')
            if isinstance(content, str):
                return {'type': 'delta', 'text': content}
            if isinstance(content, list):
                text = ''.join(
                    item.get('text', '')
                    for item in content
                    if isinstance(item, dict) and isinstance(item.get('text'), str)
                )
                if text:
                    return {'type': 'delta', 'text': text}
    error = payload.get('error')
    if isinstance(error, dict):
        upstream_status = error.get('upstream_status')
        safe_status = (
            upstream_status
            if isinstance(upstream_status, int) and not isinstance(upstream_status, bool)
            and 100 <= upstream_status <= 599
            else None
        )
        if safe_status in (401, 403):
            code = 'authentication_failed'
        elif safe_status == 404:
            code = 'model_not_found'
        elif safe_status == 429:
            code = 'rate_limited'
        elif safe_status is not None and safe_status >= 500:
            code = 'upstream_unavailable'
        else:
            code = 'upstream_error'
        result: dict[str, object] = {'type': 'error', 'error': code}
        if safe_status is not None:
            result['upstream_status'] = safe_status
        return result
    if error is not None:
        return {'type': 'error', 'error': 'upstream_error'}
    return None


def build_stream_payload(
    settings: ChatSettings,
    messages: list[dict[str, str]],
    *,
    model: str,
    reasoning_effort: str = 'auto',
    include_usage: bool = True,
) -> dict[str, object]:
    if not settings.api_key:
        raise ChatSettingsError('api key is not configured')
    model = model.strip() if isinstance(model, str) else ''
    if not settings.base_url or not model or len(model) > 256:
        raise ChatSettingsError('chat settings are incomplete')
    if reasoning_effort not in REASONING_EFFORTS:
        raise ChatSettingsError('reasoning_effort is invalid')
    payload: dict[str, object] = {
        'model': model,
        'messages': messages,
        'temperature': settings.temperature,
        'stream': True,
    }
    if include_usage:
        payload['stream_options'] = {'include_usage': True}
    if reasoning_effort != 'auto':
        payload['reasoning_effort'] = reasoning_effort
    return payload


def _stream_sse(event: dict[str, object]) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n".encode('utf-8')


async def _read_error_preview(response: httpx.Response) -> str:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(4096):
        remaining = 16 * 1024 - total
        if remaining <= 0:
            break
        piece = chunk[:remaining]
        chunks.append(piece)
        total += len(piece)
        if total >= 16 * 1024:
            break
    return b''.join(chunks).decode('utf-8', 'ignore').lower()


async def _read_response_bytes(response: httpx.Response, limit: int = 256 * 1024) -> bytes | None:
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes(8192):
        remaining = limit - total
        if remaining <= 0:
            return None
        piece = chunk[:remaining]
        chunks.append(piece)
        total += len(piece)
        if len(chunk) > remaining:
            return None
    return b''.join(chunks)


def _normal_response_events(raw: bytes | None) -> list[dict[str, object]]:
    if raw is None:
        return []
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    events: list[dict[str, object]] = []
    choices = payload.get('choices')
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get('message')
        content = message.get('content') if isinstance(message, dict) else None
        if isinstance(content, str) and content:
            events.append({'type': 'delta', 'text': content})
        elif isinstance(content, list):
            text = ''.join(
                item.get('text', '')
                for item in content
                if isinstance(item, dict) and isinstance(item.get('text'), str)
            )
            if text:
                events.append({'type': 'delta', 'text': text})
    usage = payload.get('usage')
    if isinstance(usage, dict):
        safe_usage = {
            key: int(value)
            for key, value in usage.items()
            if key in ('prompt_tokens', 'completion_tokens', 'total_tokens')
            and isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and int(value) >= 0
        }
        if safe_usage:
            events.append({'type': 'usage', 'usage': safe_usage})
    if events:
        events.append({'type': 'done'})
    return events


def _stream_error_event(error: ChatUpstreamError) -> dict[str, object]:
    if error.status in (401, 403):
        code = 'authentication_failed'
    elif error.status == 404:
        code = 'model_not_found'
    elif error.status == 429:
        code = 'rate_limited'
    elif isinstance(error.status, int) and error.status >= 500:
        code = 'upstream_unavailable'
    elif error.status is None:
        code = 'timeout'
    else:
        code = 'upstream_error'
    event: dict[str, object] = {'type': 'error', 'error': code}
    if isinstance(error.status, int):
        event['upstream_status'] = error.status
    if isinstance(error.retry_after, str):
        retry_after = error.retry_after.strip()
        if retry_after.isdigit() and len(retry_after) <= 6:
            event['retry_after'] = retry_after
    return event


def forward_chat_stream(
    settings: ChatSettings,
    messages: list[dict[str, str]],
    *,
    model: str,
    reasoning_effort: str = 'auto',
):
    """Return an async iterator of sanitized SSE events from the upstream."""
    # Validate synchronously so route errors are returned before headers start.
    build_stream_payload(settings, messages, model=model, reasoning_effort=reasoning_effort)

    async def generate():
        timeout = httpx.Timeout(
            connect=STREAM_CONNECT_TIMEOUT,
            read=STREAM_READ_TIMEOUT,
            write=STREAM_WRITE_TIMEOUT,
            pool=STREAM_POOL_TIMEOUT,
        )
        current_reasoning = reasoning_effort
        include_usage = True
        async with httpx.AsyncClient(timeout=timeout) as client:
            try:
                async with asyncio.timeout(STREAM_TOTAL_TIMEOUT):
                    while True:
                        payload = build_stream_payload(
                            settings,
                            messages,
                            model=model,
                            reasoning_effort=current_reasoning,
                            include_usage=include_usage,
                        )
                        try:
                            async with client.stream(
                                'POST',
                                chat_completions_url(settings.base_url),
                                headers={
                                    'Authorization': f'Bearer {settings.api_key}',
                                    'Accept': 'text/event-stream',
                                },
                                json=payload,
                            ) as response:
                                if response.status_code < 200 or response.status_code >= 300:
                                    preview = await _read_error_preview(response)
                                    error = ChatUpstreamError(
                                        response.status_code,
                                        retry_after=response.headers.get('Retry-After'),
                                        reasoning_unsupported=(
                                            response.status_code == 400
                                            and any(marker in preview for marker in (
                                                'reasoning_effort', 'unsupported parameter',
                                                'unknown parameter', 'unknown field',
                                                'unrecognized parameter',
                                            ))
                                        ),
                                        stream_options_unsupported=(
                                            response.status_code == 400
                                            and any(marker in preview for marker in (
                                                'stream_options', 'include_usage',
                                            ))
                                        ),
                                    )
                                    if (
                                        error.reasoning_unsupported
                                        and current_reasoning != 'auto'
                                    ):
                                        current_reasoning = 'auto'
                                        yield _stream_sse({'type': 'notice', 'notice': 'reasoning_unsupported'})
                                        continue
                                    if error.stream_options_unsupported and include_usage:
                                        include_usage = False
                                        continue
                                    yield _stream_sse(_stream_error_event(error))
                                    return

                                content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                                if content_type and content_type != 'text/event-stream':
                                    normal_events = _normal_response_events(await _read_response_bytes(response))
                                    if not normal_events:
                                        yield _stream_sse({'type': 'error', 'error': 'streaming_unavailable'})
                                        return
                                    for event in normal_events:
                                        yield _stream_sse(event)
                                    return

                                decoder = SSEDecoder()
                                saw_done = False
                                try:
                                    async for chunk in response.aiter_bytes():
                                        for event in decoder.feed(chunk):
                                            if event.get('type') == 'done':
                                                saw_done = True
                                            yield _stream_sse(event)
                                            if saw_done:
                                                return
                                    for event in decoder.finish():
                                        if event.get('type') == 'done':
                                            saw_done = True
                                        yield _stream_sse(event)
                                except asyncio.CancelledError:
                                    raise
                                except httpx.TimeoutException:
                                    yield _stream_sse(_stream_error_event(ChatUpstreamError()))
                                    return
                                except httpx.HTTPError:
                                    yield _stream_sse(_stream_error_event(ChatUpstreamError()))
                                    return
                                if not saw_done:
                                    yield _stream_sse({'type': 'done'})
                                return
                        except asyncio.CancelledError:
                            raise
                        except httpx.TimeoutException:
                            yield _stream_sse(_stream_error_event(ChatUpstreamError()))
                            return
                        except httpx.HTTPError:
                            yield _stream_sse(_stream_error_event(ChatUpstreamError()))
                            return
            except asyncio.TimeoutError:
                yield _stream_sse(_stream_error_event(ChatUpstreamError()))
                return

    return generate()


def build_upstream_request(
    settings: ChatSettings,
    messages: list[dict[str, str]],
    *,
    model: str,
    reasoning_effort: str = 'auto',
) -> urllib.request.Request:
    if not settings.api_key:
        raise ChatSettingsError('api key is not configured')
    model = model.strip() if isinstance(model, str) else ''
    if not settings.base_url or not model or len(model) > 256:
        raise ChatSettingsError('chat settings are incomplete')
    if reasoning_effort not in REASONING_EFFORTS:
        raise ChatSettingsError('reasoning_effort is invalid')
    url = chat_completions_url(settings.base_url)
    payload: dict[str, object] = {
        'model': model,
        'messages': messages,
        'temperature': settings.temperature,
        'stream': False,
    }
    if reasoning_effort != 'auto':
        payload['reasoning_effort'] = reasoning_effort
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST')
    request.add_header('Authorization', f'Bearer {settings.api_key}')
    request.add_header('Content-Type', 'application/json')
    request.add_header('Accept', 'application/json')
    return request


def forward_chat(
    settings: ChatSettings,
    messages: list[dict[str, str]],
    *,
    model: str,
    reasoning_effort: str = 'auto',
    opener=None,
) -> dict[str, object]:
    request = build_upstream_request(settings, messages, model=model, reasoning_effort=reasoning_effort)
    try:
        return _read_json_request(request, opener=opener, timeout=120)
    except ChatUpstreamError as error:
        if reasoning_effort == 'auto' or not error.reasoning_unsupported:
            raise
        fallback_request = build_upstream_request(settings, messages, model=model, reasoning_effort='auto')
        result = _read_json_request(fallback_request, opener=opener, timeout=120)
        result['chat_notice'] = 'reasoning_unsupported'
        return result


def list_chat_models(
    settings: ChatSettings,
    *,
    opener=None,
) -> list[dict[str, object]]:
    """Fetch only safe model identifiers from an OpenAI-compatible endpoint."""
    if not settings.api_key:
        raise ChatSettingsError('api key is not configured')
    if not settings.base_url:
        raise ChatSettingsError('chat settings are incomplete')
    request = urllib.request.Request(chat_models_url(settings.base_url), method='GET')
    request.add_header('Authorization', f'Bearer {settings.api_key}')
    request.add_header('Accept', 'application/json')
    payload = _read_json_request(request, opener=opener, timeout=20)
    data = payload.get('data')
    if not isinstance(data, list):
        raise ChatUpstreamError(200)
    models: list[dict[str, object]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model_id = item.get('id')
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or len(model_id) > 256:
            continue
        name = item.get('name')
        item_result: dict[str, object] = {
            'id': model_id,
            'name': name.strip() if isinstance(name, str) and name.strip() else model_id,
        }
        for key in ('context_window', 'context_length', 'max_context_tokens', 'max_model_len', 'input_token_limit'):
            candidate = item.get(key)
            if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(float(candidate)) and int(candidate) == candidate and 0 < int(candidate) <= 10_000_000:
                item_result['context_window'] = int(candidate)
                break
        models.append(item_result)
        if len(models) >= 256:
            break
    return models


def _read_json_request(request, *, opener=None, timeout: int) -> dict[str, object]:
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=timeout) as response:
            status = int(getattr(response, 'status', 200))
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get('Retry-After') if exc.headers else None
        reasoning_unsupported = False
        if exc.code == 400:
            try:
                error_body = exc.read(MAX_BODY_BYTES + 1).decode('utf-8', 'ignore').lower()
            except (OSError, UnicodeError):
                error_body = ''
            reasoning_unsupported = any(marker in error_body for marker in (
                'reasoning_effort', 'unsupported parameter', 'unknown parameter',
                'unknown field', 'unrecognized parameter',
            ))
        raise ChatUpstreamError(exc.code, retry_after=retry_after, reasoning_unsupported=reasoning_unsupported) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ChatUpstreamError() from None
    if status < 200 or status >= 300 or len(raw) > MAX_BODY_BYTES:
        raise ChatUpstreamError(status)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ChatUpstreamError(status) from None
    if not isinstance(payload, dict):
        raise ChatUpstreamError(status)
    return payload
