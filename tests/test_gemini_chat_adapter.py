import asyncio
import json

import httpx

from web_api.ai.gemini import GeminiAdapter


API_KEY = 'gemini-chat-secret-test-value'


def test_generate_chat_translates_multiturn_messages_and_sanitizes_usage():
    seen = {}

    def handler(request):
        seen['url'] = str(request.url)
        seen['key'] = request.headers.get('x-goog-api-key')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, json={
            'candidates': [{'content': {'parts': [{'text': '你好'}], 'role': 'model'}, 'finishReason': 'STOP'}],
            'usageMetadata': {'promptTokenCount': 13, 'candidatesTokenCount': 2, 'totalTokenCount': 15},
        })

    adapter = GeminiAdapter(transport=httpx.MockTransport(handler))
    result = adapter.generate_chat(
        {'api_key': API_KEY}, 'gemini-fast', [
            {'role': 'system', 'content': 'Be concise'},
            {'role': 'user', 'content': '你好'},
            {'role': 'assistant', 'content': '你好！'},
            {'role': 'user', 'content': '再见'},
        ], temperature=0.4, reasoning_effort='high',
    )

    assert seen['url'] == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-fast:generateContent'
    assert seen['key'] == API_KEY
    assert seen['body']['systemInstruction'] == {'parts': [{'text': 'Be concise'}]}
    assert seen['body']['contents'] == [
        {'role': 'user', 'parts': [{'text': '你好'}]},
        {'role': 'model', 'parts': [{'text': '你好！'}]},
        {'role': 'user', 'parts': [{'text': '再见'}]},
    ]
    assert seen['body']['generationConfig']['temperature'] == 0.4
    assert 'reasoning_effort' not in json.dumps(seen['body'])
    assert result['choices'][0]['message']['content'] == '你好'
    assert result['usage'] == {'prompt_tokens': 13, 'completion_tokens': 2, 'total_tokens': 15}
    assert API_KEY not in json.dumps(result)


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def test_stream_chat_emits_incremental_events_and_closes_upstream():
    seen = {}
    text1 = 'data: {"candidates":[{"content":{"parts":[{"text":"你"}]}}]}\r\n\r\n'.encode()
    text2 = 'data: {"candidates":[{"content":{"parts":[{"text":"好"}]}}],"usageMetadata":{"promptTokenCount":3,"candidatesTokenCount":2,"totalTokenCount":5}}\n\n'.encode()
    split = text1.index('你'.encode()) + 1
    upstream = ChunkStream([text1[:split], text1[split:], text2])

    def handler(request):
        seen['url'] = str(request.url)
        seen['key'] = request.headers.get('x-goog-api-key')
        seen['body'] = json.loads(request.content)
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=upstream)

    adapter = GeminiAdapter(async_transport=httpx.MockTransport(handler))

    async def collect():
        return [chunk async for chunk in adapter.stream_chat(
            {'api_key': API_KEY}, 'gemini-fast', [{'role': 'user', 'content': '你好'}],
            temperature=0.7, reasoning_effort='medium',
        )]

    chunks = asyncio.run(collect())
    events = [json.loads(line[6:]) for chunk in chunks for line in chunk.decode().splitlines() if line.startswith('data: ')]
    assert seen['url'] == 'https://generativelanguage.googleapis.com/v1beta/models/gemini-fast:streamGenerateContent?alt=sse'
    assert seen['key'] == API_KEY
    assert seen['body']['contents'] == [{'role': 'user', 'parts': [{'text': '你好'}]}]
    assert 'thinkingConfig' not in seen['body']['generationConfig']
    assert any(event == {'type': 'notice', 'notice': 'reasoning_unsupported'} for event in events)
    assert ''.join(event.get('text', '') for event in events if event.get('type') == 'delta') == '你好'
    assert {'type': 'usage', 'usage': {'prompt_tokens': 3, 'completion_tokens': 2, 'total_tokens': 5}} in events
    assert events[-1] == {'type': 'done'}
    assert upstream.closed is True


def test_stream_chat_closes_upstream_when_client_cancels_after_first_delta():
    first = b'data: {"candidates":[{"content":{"parts":[{"text":"first"}]}}]}\n\n'

    class SlowStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            yield first
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    upstream = SlowStream()
    transport = httpx.MockTransport(lambda _request: httpx.Response(
        200, headers={'content-type': 'text/event-stream'}, stream=upstream,
    ))
    adapter = GeminiAdapter(async_transport=transport)

    async def cancel_after_delta():
        generator = adapter.stream_chat(
            {'api_key': API_KEY}, 'gemini-fast', [{'role': 'user', 'content': 'hello'}],
        )
        event = await anext(generator)
        assert json.loads(event.decode().splitlines()[0][6:]) == {'type': 'delta', 'text': 'first'}
        await generator.aclose()

    asyncio.run(cancel_after_delta())
    assert upstream.closed is True
