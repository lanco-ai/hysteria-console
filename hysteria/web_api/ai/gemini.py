"""Minimal server-side transport for Google's native Gemini Developer API."""

import json
import asyncio
from urllib.parse import quote

import httpx

from ..chat_service import ChatSettingsError
from .service_store import GEMINI_BASE_URL


MAX_PROMPT_CHARS = 48_000
MAX_SCHEMA_BYTES = 48_000
MAX_MODEL_PAGES = 10


class GeminiUpstreamError(RuntimeError):
    """Sanitized upstream classification; never retains upstream bodies."""

    def __init__(self, code: str, status: int | None = None, retry_after: str | None = None):
        self.code = code
        self.status = status
        self.retry_after = retry_after
        super().__init__('Gemini API request failed')


def _classify_status(status: int, *, models: bool = False) -> str:
    if status == 401:
        return 'authentication_failed'
    if status == 403:
        return 'permission_denied'
    if status == 404:
        return 'models_endpoint_unavailable' if models else 'model_not_found'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'upstream_unavailable'
    return 'upstream_error'


class GeminiAdapter:
    """Uses fixed Google endpoints and sends credentials only in a header."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        async_transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 20.0,
    ):
        self.transport = transport
        self.async_transport = async_transport
        self.timeout = timeout

    @staticmethod
    def _api_key(profile: dict) -> str:
        api_key = profile.get('api_key') if isinstance(profile, dict) else None
        if not isinstance(api_key, str) or not api_key:
            raise GeminiUpstreamError('service_not_configured')
        return api_key

    def _request(self, method: str, path: str, profile: dict, *, params=None, payload=None, models=False):
        api_key = self._api_key(profile)
        headers = {'x-goog-api-key': api_key, 'Accept': 'application/json'}
        if payload is not None:
            headers['Content-Type'] = 'application/json'
        try:
            with httpx.Client(transport=self.transport, timeout=self.timeout, follow_redirects=False) as client:
                response = client.request(
                    method,
                    f'{GEMINI_BASE_URL}{path}',
                    headers=headers,
                    params=params,
                    json=payload,
                )
        except httpx.TimeoutException as exc:
            raise GeminiUpstreamError('timeout') from exc
        except httpx.TransportError as exc:
            raise GeminiUpstreamError('upstream_unavailable') from exc

        retry_after = response.headers.get('Retry-After')
        if retry_after is not None and (len(retry_after) > 32 or any(ord(char) < 32 for char in retry_after)):
            retry_after = None
        if response.status_code < 200 or response.status_code >= 300:
            raise GeminiUpstreamError(_classify_status(response.status_code, models=models), response.status_code, retry_after)
        try:
            result = response.json()
        except (ValueError, json.JSONDecodeError):
            raise GeminiUpstreamError('invalid_upstream_response', response.status_code) from None
        if not isinstance(result, dict):
            raise GeminiUpstreamError('invalid_upstream_response', response.status_code)
        return result

    def list_models(self, profile: dict) -> list[dict]:
        models = []
        page_token = None
        for _ in range(MAX_MODEL_PAGES):
            params = {'pageSize': 1000}
            if page_token:
                params['pageToken'] = page_token
            result = self._request('GET', '/models', profile, params=params, models=True)
            values = result.get('models', [])
            if not isinstance(values, list):
                raise GeminiUpstreamError('invalid_upstream_response', 200)
            for model in values:
                if not isinstance(model, dict):
                    continue
                methods = model.get('supportedGenerationMethods')
                if not isinstance(methods, list) or 'generateContent' not in methods:
                    continue
                full_name = model.get('name')
                if not isinstance(full_name, str) or not full_name.startswith('models/'):
                    continue
                model_id = full_name.removeprefix('models/').strip()
                if not model_id or len(model_id) > 256:
                    continue
                display_name = model.get('displayName')
                safe = {'id': model_id, 'name': display_name[:256] if isinstance(display_name, str) and display_name else model_id}
                limit = model.get('inputTokenLimit')
                if isinstance(limit, int) and not isinstance(limit, bool) and limit > 0:
                    safe['input_token_limit'] = limit
                models.append(safe)
                if len(models) >= 1000:
                    return models
            page_token = result.get('nextPageToken')
            if not isinstance(page_token, str) or not page_token:
                break
        return models

    def generate_json(self, profile: dict, model: str, prompt: str, schema: dict) -> object:
        if not isinstance(model, str) or not model.strip() or len(model) > 256 or any(ord(char) < 32 for char in model):
            raise GeminiUpstreamError('invalid_model')
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
            raise GeminiUpstreamError('invalid_prompt')
        try:
            schema_size = len(json.dumps(schema, ensure_ascii=False).encode('utf-8'))
        except (TypeError, ValueError):
            raise GeminiUpstreamError('invalid_schema') from None
        if not isinstance(schema, dict) or schema_size > MAX_SCHEMA_BYTES:
            raise GeminiUpstreamError('invalid_schema')
        normalized_model = model.strip().removeprefix('models/')
        path = f'/models/{quote(normalized_model, safe="-_.")}:generateContent'
        payload = {
            'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'generationConfig': {
                'responseMimeType': 'application/json',
                'responseSchema': schema,
            },
        }
        response = self._request('POST', path, profile, payload=payload)
        candidates = response.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise GeminiUpstreamError('invalid_model_response', 200)
        content = candidates[0].get('content')
        parts = content.get('parts') if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise GeminiUpstreamError('invalid_model_response', 200)
        text_parts = [part.get('text') for part in parts if isinstance(part, dict) and isinstance(part.get('text'), str)]
        if not text_parts:
            raise GeminiUpstreamError('invalid_model_response', 200)
        try:
            return json.loads('\n'.join(text_parts))
        except json.JSONDecodeError:
            raise GeminiUpstreamError('invalid_model_response', 200) from None

    @staticmethod
    def _chat_request(model: str, messages: list[dict[str, str]], temperature: float) -> tuple[str, dict]:
        if not isinstance(model, str) or not model.strip() or len(model) > 256 or any(ord(char) < 32 for char in model):
            raise ChatSettingsError('model is invalid')
        if not isinstance(messages, list) or not messages or len(messages) > 128:
            raise ChatSettingsError('messages are invalid')
        if isinstance(temperature, bool) or not isinstance(temperature, (int, float)) or not 0 <= float(temperature) <= 2:
            raise ChatSettingsError('temperature is invalid')

        system_text = []
        contents = []
        for message in messages:
            if not isinstance(message, dict) or message.get('role') not in ('system', 'user', 'assistant'):
                raise ChatSettingsError('message is invalid')
            text = message.get('content')
            if not isinstance(text, str) or not text or len(text) > 32_768:
                raise ChatSettingsError('message is invalid')
            if message['role'] == 'system':
                system_text.append(text)
            else:
                contents.append({
                    'role': 'model' if message['role'] == 'assistant' else 'user',
                    'parts': [{'text': text}],
                })
        if not contents:
            raise ChatSettingsError('messages are invalid')

        payload = {
            'contents': contents,
            'generationConfig': {'temperature': float(temperature)},
        }
        if system_text:
            payload['systemInstruction'] = {'parts': [{'text': '\n\n'.join(system_text)}]}
        normalized_model = model.strip().removeprefix('models/')
        return f'/models/{quote(normalized_model, safe="-_.")}', payload

    @staticmethod
    def _chat_usage(value: object) -> dict[str, int] | None:
        if not isinstance(value, dict):
            return None
        mapping = {
            'promptTokenCount': 'prompt_tokens',
            'candidatesTokenCount': 'completion_tokens',
            'totalTokenCount': 'total_tokens',
        }
        usage = {
            target: value[source]
            for source, target in mapping.items()
            if isinstance(value.get(source), int)
            and not isinstance(value.get(source), bool)
            and value[source] >= 0
        }
        if 'total_tokens' not in usage and {'prompt_tokens', 'completion_tokens'} <= set(usage):
            usage['total_tokens'] = usage['prompt_tokens'] + usage['completion_tokens']
        return usage or None

    def generate_chat(
        self,
        profile: dict,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        reasoning_effort: str = 'auto',
    ) -> dict[str, object]:
        path, payload = self._chat_request(model, messages, temperature)
        if reasoning_effort not in ('auto', 'low', 'medium', 'high'):
            raise ChatSettingsError('reasoning_effort is invalid')
        response = self._request('POST', f'{path}:generateContent', profile, payload=payload)
        candidates = response.get('candidates')
        if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
            raise GeminiUpstreamError('invalid_model_response', 200)
        candidate = candidates[0]
        content = candidate.get('content')
        parts = content.get('parts') if isinstance(content, dict) else None
        if not isinstance(parts, list):
            raise GeminiUpstreamError('invalid_model_response', 200)
        answer = ''.join(
            part['text'] for part in parts
            if isinstance(part, dict) and isinstance(part.get('text'), str) and part.get('thought') is not True
        )
        if not answer:
            raise GeminiUpstreamError('invalid_model_response', 200)
        result: dict[str, object] = {
            'model': model,
            'choices': [{
                'index': 0,
                'message': {'role': 'assistant', 'content': answer},
                'finish_reason': str(candidate.get('finishReason') or 'STOP').lower(),
            }],
        }
        usage = self._chat_usage(response.get('usageMetadata'))
        if usage:
            result['usage'] = usage
        if reasoning_effort != 'auto':
            result['chat_notice'] = 'reasoning_unsupported'
        return result

    @staticmethod
    def _internal_event(value: dict[str, object]) -> bytes:
        return f"data: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}\n\n".encode('utf-8')

    @staticmethod
    def _gemini_event(data_lines: list[str]) -> dict | None:
        if not data_lines:
            return None
        try:
            value = json.loads('\n'.join(data_lines))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def stream_chat(
        self,
        profile: dict,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        reasoning_effort: str = 'auto',
    ):
        path, payload = self._chat_request(model, messages, temperature)
        if reasoning_effort not in ('auto', 'low', 'medium', 'high'):
            raise ChatSettingsError('reasoning_effort is invalid')
        api_key = self._api_key(profile)

        async def generate():
            if reasoning_effort != 'auto':
                yield self._internal_event({'type': 'notice', 'notice': 'reasoning_unsupported'})
            timeout = httpx.Timeout(connect=5.0, read=45.0, write=10.0, pool=5.0)
            usage = None
            data_lines: list[str] = []
            try:
                async with httpx.AsyncClient(
                    transport=self.async_transport, timeout=timeout, follow_redirects=False,
                ) as client:
                    async with asyncio.timeout(600):
                        async with client.stream(
                            'POST',
                            f'{GEMINI_BASE_URL}{path}:streamGenerateContent',
                            params={'alt': 'sse'},
                            headers={
                                'x-goog-api-key': api_key,
                                'Accept': 'text/event-stream',
                                'Content-Type': 'application/json',
                            },
                            json=payload,
                        ) as response:
                            if response.status_code < 200 or response.status_code >= 300:
                                retry_after = response.headers.get('Retry-After')
                                if retry_after is not None and (len(retry_after) > 32 or any(ord(char) < 32 for char in retry_after)):
                                    retry_after = None
                                error: dict[str, object] = {
                                    'type': 'error',
                                    'error': _classify_status(response.status_code),
                                    'upstream_status': response.status_code,
                                }
                                if retry_after:
                                    error['retry_after'] = retry_after
                                yield self._internal_event(error)
                                return
                            content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                            if content_type != 'text/event-stream':
                                yield self._internal_event({'type': 'error', 'error': 'streaming_unavailable'})
                                return

                            async def emit_event(lines: list[str]):
                                nonlocal usage
                                value = self._gemini_event(lines)
                                if value is None:
                                    return []
                                output = []
                                candidates = value.get('candidates')
                                if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
                                    content = candidates[0].get('content')
                                    parts = content.get('parts') if isinstance(content, dict) else None
                                    if isinstance(parts, list):
                                        text = ''.join(
                                            part['text'] for part in parts
                                            if isinstance(part, dict) and isinstance(part.get('text'), str) and part.get('thought') is not True
                                        )
                                        if text:
                                            output.append(self._internal_event({'type': 'delta', 'text': text}))
                                found_usage = self._chat_usage(value.get('usageMetadata'))
                                if found_usage:
                                    usage = found_usage
                                return output

                            async for line in response.aiter_lines():
                                if not line:
                                    for event in await emit_event(data_lines):
                                        yield event
                                    data_lines = []
                                elif line.startswith('data:'):
                                    data_lines.append(line[5:].lstrip(' '))
                                elif line.startswith(':'):
                                    continue
                            for event in await emit_event(data_lines):
                                yield event
            except asyncio.CancelledError:
                raise
            except httpx.TimeoutException:
                yield self._internal_event({'type': 'error', 'error': 'timeout'})
                return
            except (httpx.TransportError, asyncio.TimeoutError):
                yield self._internal_event({'type': 'error', 'error': 'upstream_unavailable'})
                return
            if usage:
                yield self._internal_event({'type': 'usage', 'usage': usage})
            yield self._internal_event({'type': 'done'})

        return generate()
