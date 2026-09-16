"""Server-side settings and transport helpers for the self-use chat page.

The module deliberately keeps credentials on the server.  It does not log
request bodies, upstream headers, or upstream response bodies.
"""

import json
import math
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import state_store


DEFAULT_SETTINGS_PATH = Path('/root/hysteria/state/chat/settings.json')
MAX_BODY_BYTES = 128 * 1024
MAX_MESSAGES = 128
MAX_MESSAGE_CHARS = 32 * 1024
_MISSING = object()


class ChatSettingsError(ValueError):
    """A settings value is missing or outside the supported safe shape."""


class ChatUpstreamError(RuntimeError):
    """A third-party API failed without retaining its response body."""

    def __init__(self, status: int | None = None, *, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__('chat upstream request failed')


@dataclass(frozen=True, slots=True)
class ChatSettings:
    base_url: str = ''
    api_key: str = ''
    model: str = ''
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
    model = str(raw.get('model') or '').strip()
    temperature = raw.get('temperature', 0.7)
    try:
        temperature = float(temperature)
    except (TypeError, ValueError):
        raise ChatSettingsError('temperature must be a number') from None
    _validate_values(base_url=base_url, api_key=api_key, model=model, temperature=temperature)
    return ChatSettings(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
    )


def _validate_values(*, base_url: str, api_key: str, model: str, temperature: float) -> None:
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
    if len(model) > 256:
        raise ChatSettingsError('model is too long')
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
            'model': settings.model,
            'temperature': settings.temperature,
            'api_key_configured': bool(settings.api_key),
            'api_key_masked': mask_api_key(settings.api_key),
        }

    def update(
        self,
        *,
        base_url: object = _MISSING,
        model: object = _MISSING,
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
                next_model = current.model if model is _MISSING else str(model or '').strip()
                next_temperature = current.temperature if temperature is _MISSING else temperature
                next_api_key = current.api_key if api_key is _MISSING else str(api_key or '')
                try:
                    next_temperature = float(next_temperature)
                except (TypeError, ValueError):
                    raise ChatSettingsError('temperature must be a number') from None
                _validate_values(
                    base_url=next_base_url,
                    api_key=next_api_key,
                    model=next_model,
                    temperature=next_temperature,
                )
                payload = {
                    'base_url': next_base_url,
                    'api_key': next_api_key,
                    'model': next_model,
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
    normalized = base_url.rstrip('/')
    while normalized.endswith('/v1/v1'):
        normalized = normalized[:-3]
    suffix = '/chat/completions'
    if normalized.endswith(suffix):
        return normalized
    return normalized + suffix


def build_upstream_request(
    settings: ChatSettings,
    messages: list[dict[str, str]],
) -> urllib.request.Request:
    if not settings.api_key:
        raise ChatSettingsError('api key is not configured')
    if not settings.base_url or not settings.model:
        raise ChatSettingsError('chat settings are incomplete')
    url = chat_completions_url(settings.base_url)
    body = json.dumps(
        {
            'model': settings.model,
            'messages': messages,
            'temperature': settings.temperature,
            'stream': False,
        },
        ensure_ascii=False,
    ).encode('utf-8')
    request = urllib.request.Request(url, data=body, method='POST')
    request.add_header('Authorization', f'Bearer {settings.api_key}')
    request.add_header('Content-Type', 'application/json')
    request.add_header('Accept', 'application/json')
    return request


def forward_chat(
    settings: ChatSettings,
    messages: list[dict[str, str]],
    *,
    opener=None,
) -> dict[str, object]:
    request = build_upstream_request(settings, messages)
    opener = opener or urllib.request.urlopen
    try:
        with opener(request, timeout=120) as response:
            status = int(getattr(response, 'status', 200))
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as exc:
        retry_after = exc.headers.get('Retry-After') if exc.headers else None
        raise ChatUpstreamError(exc.code, retry_after=retry_after) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ChatUpstreamError() from None
    if status < 200 or status >= 300:
        raise ChatUpstreamError(status)
    if len(raw) > MAX_BODY_BYTES:
        raise ChatUpstreamError(status)
    try:
        payload = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ChatUpstreamError(status) from None
    if not isinstance(payload, dict):
        raise ChatUpstreamError(status)
    return payload
