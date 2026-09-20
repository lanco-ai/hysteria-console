import io
import json
import urllib.error

import pytest

from web_api import chat_service
from web_api.ai.assistant_generation import _openai_schema


class _Response:
    status = 200

    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit):
        return self.payload


def _completion(content, *, finish_reason='stop', refusal=None):
    message = {'role': 'assistant', 'content': content}
    if refusal is not None:
        message['refusal'] = refusal
    return {'choices': [{'message': message, 'finish_reason': finish_reason}]}


def test_openai_assistant_schema_closes_every_object_for_strict_json_mode():
    schema = _openai_schema({
        'type': 'OBJECT',
        'properties': {
            'suggestions': {
                'type': 'ARRAY', 'items': {
                    'type': 'OBJECT', 'properties': {
                        'title': {'type': 'STRING'},
                    }, 'required': ['title'],
                },
            },
        }, 'required': ['suggestions'],
    })

    assert schema['type'] == 'object'
    assert schema['additionalProperties'] is False
    nested = schema['properties']['suggestions']['items']
    assert nested['type'] == 'object'
    assert nested['additionalProperties'] is False


def test_openai_structured_generation_uses_the_selected_model_and_schema():
    requests = []
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}

    def opener(request, *, timeout):
        requests.append((request, timeout, json.loads(request.data)))
        return _Response(_completion('{"ok":true}'))

    result, mode = chat_service.forward_structured_json(
        chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
        model='explicit-model-2', prompt='Return a JSON result.', schema=schema, opener=opener,
    )

    assert result == {'ok': True}
    assert mode == 'json_schema'
    assert requests[0][0].full_url == 'https://provider.test/v1/chat/completions'
    assert requests[0][0].get_header('Authorization') == 'Bearer test-secret'
    assert requests[0][2]['model'] == 'explicit-model-2'
    assert requests[0][2]['response_format']['json_schema']['schema'] == schema


def test_openai_structured_generation_retries_once_only_for_explicit_format_unsupported():
    requests = []
    schema = {'type': 'object', 'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']}

    def opener(request, *, timeout):
        requests.append(json.loads(request.data))
        if len(requests) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 400, 'bad request', {},
                io.BytesIO(b'{"error":"response_format json_schema is not supported"}'),
            )
        return _Response(_completion('{"ok":true}'))

    result, mode = chat_service.forward_structured_json(
        chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
        model='chosen-model', prompt='Return JSON only.', schema=schema, opener=opener,
    )

    assert result == {'ok': True}
    assert mode == 'json_text_fallback'
    assert len(requests) == 2
    assert 'response_format' in requests[0]
    assert 'response_format' not in requests[1]
    assert 'Do not include Markdown' in requests[1]['messages'][0]['content']


def test_openai_structured_generation_does_not_retry_invalid_schema_errors():
    attempts = 0

    def opener(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url, 400, 'bad request', {},
            io.BytesIO(b'{"error":{"message":"Invalid schema for response_format: unsupported keyword additionalProperties"}}'),
        )

    with pytest.raises(chat_service.ChatUpstreamError):
        chat_service.forward_structured_json(
            chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
            model='chosen-model', prompt='Return JSON only.',
            schema={'type': 'object'}, opener=opener,
        )
    assert attempts == 1


def test_openai_structured_generation_does_not_retry_a_malformed_success_response():
    attempts = 0

    def opener(_request, *, timeout):
        nonlocal attempts
        attempts += 1
        return _Response(_completion('{not json'))

    with pytest.raises(chat_service.ChatUpstreamError):
        chat_service.forward_structured_json(
            chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
            model='chosen-model', prompt='Return JSON only.',
            schema={'type': 'object'}, opener=opener,
        )
    assert attempts == 1


@pytest.mark.parametrize('status', [401, 403, 429, 500, 504])
def test_openai_structured_generation_never_retries_auth_limit_timeout_or_server_errors(status):
    attempts = 0

    def opener(request, *, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(
            request.full_url, status, 'failure', {},
            io.BytesIO(b'{"error":"response_format json_schema is unsupported"}'),
        )

    with pytest.raises(chat_service.ChatUpstreamError):
        chat_service.forward_structured_json(
            chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
            model='chosen-model', prompt='Return JSON only.',
            schema={'type': 'object'}, opener=opener,
        )
    assert attempts == 1


@pytest.mark.parametrize('choice', [
    _completion('', finish_reason='stop'),
    _completion('{bad json'),
    _completion('{"ok":true}', finish_reason='length'),
    _completion('', refusal='unsafe'),
])
def test_openai_structured_generation_rejects_empty_refused_truncated_and_invalid_json(choice):
    def opener(_request, *, timeout):
        return _Response(choice)

    with pytest.raises(chat_service.ChatUpstreamError) as error:
        chat_service.forward_structured_json(
            chat_service.ChatSettings('https://provider.test/v1', 'test-secret', 0.4),
            model='chosen-model', prompt='Return JSON only.',
            schema={'type': 'object'}, opener=opener,
        )
    assert error.value.code == 'invalid_model_response'
