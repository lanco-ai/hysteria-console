import asyncio
import base64
import json

import httpx
import pytest

from test_chat_workspace import env, turn
from web_api.chat_tools import ToolService, validate_arguments
from web_api.chat_workspace_store import WorkspaceError
from web_api.chat_tool_network import MCPClient, pinned_request


def test_tool_approval_idempotency_isolation_and_artifact_auth(env, monkeypatch):
    store, _, _, client = env
    calls = []
    async def python(code, files):
        calls.append(code)
        return {'stdout': '2.0', 'error': None, 'files': [{'name': 'answer.txt', 'data': base64.b64encode(b'answer').decode()}]}
    monkeypatch.setattr('web_api.chat_tools.run_python', python)
    c = store.create()
    values = {'request_id': 'tool_request_1', 'tool': 'python', 'arguments': {'code': 'print(2.0)'}}
    proposed = client.post(f"/api/chat/conversations/{c['id']}/tools", json=values)
    assert proposed.status_code == 200
    proposed = proposed.json()
    assert proposed['status'] == 'proposed' and not calls
    url = f"/api/chat/tools/runs/{proposed['id']}/execute"
    assert client.post(url, json={'approved': False}).status_code == 422
    assert client.post(url, json={'approved': True}, headers={'origin': 'https://evil.invalid'}).status_code == 403
    assert not calls
    result = client.post(url, json={'approved': True}).json()
    assert result['status'] == 'completed' and calls == ['print(2.0)']
    assert client.post(url, json={'approved': True}).json() == result
    assert len(calls) == 1
    assert client.post(f"/api/chat/conversations/{c['id']}/tools", json=values).json()['id'] == proposed['id']
    artifact = result['artifacts'][0]
    assert client.get(f"/api/chat/tools/artifacts/{artifact['id']}").content == b'answer'
    assert client.get(f"/api/chat/tools/artifacts/{artifact['id']}", headers={'cookie': ''}).status_code == 401
    other = store.create()
    with pytest.raises(WorkspaceError, match='invalid_tool_result'):
        store.begin(other['id'], turn(other, tool_run_ids=[proposed['id']]))
    saved, prompt = store.begin(c['id'], turn(c, tool_run_ids=[proposed['id']]))
    assert '2.0' in json.dumps(prompt)
    assert saved['messages'][-1]['tool_results'][0]['id'] == proposed['id']
    store.event(c['id'], 'request_0001', {'type': 'done'})
    current = store.get(c['id'])
    branch = store.fork(c['id'], {'request_id': 'branch_tools_1', 'revision': current['revision'], 'message_id': current['messages'][-1]['id'], 'mode': 'regenerate'})
    assert len(branch['draft_tool_run_ids']) == 1
    clone = ToolService(store).runs(branch['id'])['items'][0]
    assert clone['source_run_id'] == proposed['id'] and clone['result'] == result['result']
    assert asyncio.run(ToolService(store).execute(clone['id']))['status'] == 'completed'
    assert len(calls) == 1


def test_mcp_credentials_never_in_public_config_export_or_catalog(env):
    store, _, _, client = env
    credential = 'fictional-secret-with-quote"and-slash\\'
    response = client.post('/api/chat/tools/servers', json={'name': 'Test', 'url': 'https://mcp.example.test/mcp', 'token': credential})
    assert response.status_code == 200
    server = response.json()
    assert server['token_configured'] and 'token' not in server
    for path in ('/api/chat/tools/servers', '/api/chat/tools/catalog', '/api/chat/workspace/export'):
        assert credential not in client.get(path).text
    service = ToolService(store)
    assert service.server(server['id'])['token'] == credential
    response = client.put(f"/api/chat/tools/servers/{server['id']}", json={'name': 'Different', 'url': 'https://other.example.test/mcp', 'revision': server['revision']})
    assert response.status_code == 200 and not response.json()['token_configured']
    assert client.post('/api/chat/tools/servers', json={'name': 'Bad', 'url': 'https://example.test/mcp?token=secret'}).status_code == 422


def test_mcp_transport_initialization_sse_and_dns_pinning(monkeypatch):
    events = []
    async def resolve(host, port):
        return ['1.1.1.1']
    monkeypatch.setattr('web_api.chat_tool_network.resolve_addresses', resolve)
    def handler(request):
        assert request.url.host == '1.1.1.1'
        assert request.headers['host'] == 'mcp.example.test'
        assert request.headers['authorization'] == 'Bearer fixture'
        if request.method == 'DELETE':
            return httpx.Response(204)
        value = json.loads(request.content)
        events.append(value['method'])
        if value['method'] == 'initialize':
            return httpx.Response(200, json={'jsonrpc': '2.0', 'id': value['id'], 'result': {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}}}, headers={'Mcp-Session-Id': 'session-fixture'})
        assert request.headers['mcp-session-id'] == 'session-fixture'
        assert request.headers['mcp-protocol-version'] == '2025-11-25'
        if value['method'] == 'notifications/initialized':
            return httpx.Response(202)
        result = {'tools': [{'name': 'lookup', 'inputSchema': {'type': 'object'}}]} if value['method'] == 'tools/list' else {'content': [{'type': 'text', 'text': '研究 evidence'}]}
        body = 'data: ' + json.dumps({'jsonrpc': '2.0', 'id': value['id'], 'result': result}, ensure_ascii=False) + '\n\n'
        return httpx.Response(200, content=body.encode(), headers={'content-type': 'text/event-stream'})
    async def run():
        client = MCPClient({'url': 'https://mcp.example.test/mcp', 'token': 'fixture'}, transport=httpx.MockTransport(handler))
        assert (await client.tools())[0]['name'] == 'lookup'
        result = await client.rpc('tools/call', {'name': 'lookup', 'arguments': {}})
        assert result['content'][0]['text'] == '研究 evidence'
        await client.close()
    asyncio.run(run())
    assert events == ['initialize', 'notifications/initialized', 'tools/list', 'tools/call']


def test_private_dns_and_remote_jsonschema_references_are_blocked(monkeypatch):
    async def resolve(host, port):
        return ['1.1.1.1', '127.0.0.1']
    monkeypatch.setattr('web_api.chat_tool_network.resolve_addresses', resolve)
    with pytest.raises(WorkspaceError, match='private_network_blocked'):
        asyncio.run(pinned_request('GET', 'https://example.test'))
    with pytest.raises(WorkspaceError, match='invalid_tool_arguments'):
        validate_arguments({'$ref': 'http://127.0.0.1/private-schema'}, {})


def test_model_tool_plan_is_review_only_and_replay_safe(env):
    store, _, _, _ = env
    c = store.create()
    service = ToolService(store)
    class Planner:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            return {'choices': [{'message': {'content': json.dumps({'calls': [{'tool': 'python', 'arguments': {'code': 'print(3)'}}]})}}], 'usage': {'prompt_tokens': 12}}
    provider = Planner()
    values = {'question': 'Compute a mean', 'model': 'fixture', 'enabled': ['python'], 'request_id': 'plan_request_1'}
    result = service.plan(provider, c['id'], values)
    assert result['status'] == 'ready'
    assert service.runs(c['id'])['items'][0]['status'] == 'proposed'
    assert service.plan(provider, c['id'], values) == result and provider.calls == 1
    assert store.usage()['prompt_tokens'] == 12
