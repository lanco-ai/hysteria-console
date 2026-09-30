import json

import pytest

from tests.test_chat_workspace import env, project, turn
from web_api.chat_context import prepare_context
from web_api.chat_knowledge import KnowledgeIndex
from web_api.chat_workspace_store import WorkspaceError


def test_semantic_index_scope_persistence_and_deleted_sources(env):
    store, _, _, client = env
    a, b = project(store, 'storage'), project(store, 'English')
    doc_a = store.add_document(a['id'], 'storage.txt', b'storage', ['compaction write amplification'], 'text/plain')
    doc_b = store.add_document(b['id'], 'English.txt', b'English', ['IELTS English speaking'], 'text/plain')
    index = KnowledgeIndex(store)
    index.index(doc_a['id']); index.index(doc_b['id'])
    found = index.search('IELTS', a['id'])['items']
    assert {c['document_id'] for c in found} == {doc_a['id']}
    assert index.search('IELTS')['items'][0]['document_id'] == doc_b['id']
    c = store.create(a['id'])
    values = turn(c, knowledge_scope='project')
    with pytest.raises(WorkspaceError, match='document_outside_project'):
        store.begin(c['id'], values, index.search('IELTS')['items'])
    result = client.post(f"/api/chat/conversations/{c['id']}/turns", json=values)
    assert result.status_code == 200
    saved = store.get(c['id'])
    assert saved['messages'][-1]['retrieval'] == 'semantic'
    assert {s['document_id'] for s in saved['messages'][-1]['citations']} == {doc_a['id']}
    store.delete_document(doc_b['id'])
    assert all(c['document_id'] != doc_b['id'] for c in index.search('IELTS')['items'])
    assert client.post('/api/chat/knowledge/search', json={'query': 'x'}, headers={'origin': 'https://evil.invalid'}).status_code == 403
    assert client.get('/api/chat/knowledge/documents', headers={'cookie': ''}).status_code == 401


def test_project_memory_is_explicit_editable_and_isolated(env):
    store, _, _, client = env
    a, b = project(store), project(store)
    result = client.post(f"/api/chat/projects/{a['id']}/memories", json={'revision': a['revision'], 'text': 'I confuse durability and consistency'})
    assert result.status_code == 200
    a = result.json()
    m = a['memories'][0]
    item = store.create(a['id'])
    _, prompt = store.begin(item['id'], turn(item))
    assert m['text'] in json.dumps(prompt)
    other = store.create(b['id'])
    _, prompt = store.begin(other['id'], turn(other))
    assert m['text'] not in json.dumps(prompt)
    result = client.request('DELETE', f"/api/chat/projects/{a['id']}/memories/{m['id']}", json={'revision': a['revision']})
    assert result.status_code == 200 and result.json()['memories'] == []


def test_rolling_summary_preserves_history_and_is_used_by_new_turns(env):
    store, _, _, _ = env
    item = store.create()
    messages = [{'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant', 'content': f'old fact {i}: ' + 'evidence ' * 160,
                 'status': 'completed', 'citations': []} for i in range(50)]
    with store.db() as db:
        item['messages'] = messages
        store._save(db, item)
    item, prompt = store.begin(item['id'], turn(item))

    class Summarizer:
        calls = 0
        def complete(self, messages, **kwargs):
            self.calls += 1
            assert kwargs['max_output_tokens'] == 2048
            assert 'old fact' in messages[-1]['content']
            return {'choices': [{'message': {'content': 'Remember the verified early conclusion.'}}], 'usage': {'prompt_tokens': 100}}

    provider = Summarizer()
    prompt, incomplete = prepare_context(store, provider, item['id'], 'request_0001', 'test-model', prompt)
    assert not incomplete and provider.calls >= 1
    assert any('verified early conclusion' in m['content'] for m in prompt)
    saved = store.get(item['id'])
    assert saved['messages'][:50] == messages
    assert saved['messages'][-1]['context_summary_used']
    assert saved['summary']['through_message_id'] in {m['id'] for m in messages}
    store.event(item['id'], 'request_0001', {'type': 'done'})
    saved = store.get(item['id'])
    changed = store.edit_summary(item['id'], {'revision': saved['revision'], 'text': 'My corrected conclusion'})
    assert changed['summary']['edited']
    with pytest.raises(WorkspaceError, match='conflict'):
        store.edit_summary(item['id'], {'revision': saved['revision'], 'text': 'Stale'})
    assert store.usage()['prompt_tokens'] == 100 * provider.calls
