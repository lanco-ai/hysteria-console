import json

import httpx
import pytest

from web_api.ai.gemini import GeminiAdapter, GeminiUpstreamError


API_KEY = 'g-test-key-must-not-leave-server'


def test_list_models_uses_server_header_and_returns_supported_sanitized_models():
    observed = {}

    def handler(request):
        observed['url'] = str(request.url)
        observed['key'] = request.headers.get('x-goog-api-key')
        observed['authorization'] = request.headers.get('authorization')
        return httpx.Response(200, json={'models': [
            {'name': 'models/gemini-fast', 'displayName': 'Gemini Fast', 'supportedGenerationMethods': ['generateContent'], 'inputTokenLimit': 32768},
            {'name': 'models/embedding-only', 'supportedGenerationMethods': ['embedContent'], 'inputTokenLimit': 8192},
        ]})

    adapter = GeminiAdapter(transport=httpx.MockTransport(handler))
    models = adapter.list_models({'api_key': API_KEY})

    assert models == [{'id': 'gemini-fast', 'name': 'Gemini Fast', 'input_token_limit': 32768}]
    assert observed['url'] == 'https://generativelanguage.googleapis.com/v1beta/models?pageSize=1000'
    assert observed['key'] == API_KEY
    assert observed['authorization'] is None
    assert API_KEY not in observed['url']


def test_generate_json_posts_structured_request_and_extracts_candidate_text():
    observed = {}
    output = {'segments': [{'title': '开场', 'prompt': '厨房清晨'}]}

    def handler(request):
        observed['url'] = str(request.url)
        observed['key'] = request.headers.get('x-goog-api-key')
        observed['body'] = json.loads(request.content)
        return httpx.Response(200, json={'candidates': [{'content': {'parts': [{'text': json.dumps(output, ensure_ascii=False)}]}}]})

    adapter = GeminiAdapter(transport=httpx.MockTransport(handler))
    result = adapter.generate_json(
        {'api_key': API_KEY}, 'gemini-fast', '用户的创作思路',
        {'type': 'OBJECT', 'properties': {'segments': {'type': 'ARRAY'}}},
    )

    assert result == output
    assert observed['url'] == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-fast:generateContent'
    assert observed['key'] == API_KEY
    assert observed['body']['generationConfig']['responseMimeType'] == 'application/json'
    assert observed['body']['generationConfig']['responseSchema']['type'] == 'OBJECT'


@pytest.mark.parametrize(('status', 'retry_after', 'expected'), [
    (401, None, 'authentication_failed'),
    (403, None, 'permission_denied'),
    (404, None, 'models_endpoint_unavailable'),
    (429, '17', 'rate_limited'),
    (503, None, 'upstream_unavailable'),
])
def test_upstream_errors_are_classified_without_retaining_body(status, retry_after, expected):
    def handler(_request):
        headers = {'Retry-After': retry_after} if retry_after else {}
        return httpx.Response(status, headers=headers, text=f'private upstream detail {API_KEY}')

    adapter = GeminiAdapter(transport=httpx.MockTransport(handler))
    with pytest.raises(GeminiUpstreamError) as captured:
        adapter.list_models({'api_key': API_KEY})

    assert captured.value.code == expected
    assert captured.value.status == status
    assert captured.value.retry_after == retry_after
    assert API_KEY not in str(captured.value)
    assert 'private upstream detail' not in str(captured.value)


def test_malformed_structured_output_has_sanitized_error():
    adapter = GeminiAdapter(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={'candidates': []})))
    with pytest.raises(GeminiUpstreamError) as captured:
        adapter.generate_json({'api_key': API_KEY}, 'gemini-fast', 'brief', {'type': 'OBJECT'})
    assert captured.value.code == 'invalid_model_response'
    assert API_KEY not in str(captured.value)
