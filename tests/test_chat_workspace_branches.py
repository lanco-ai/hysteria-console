"""Branching must preserve provenance, retries and source conversation isolation."""
import pytest

from test_chat_workspace import env, turn
from web_api.chat_workspace_store import WorkspaceError


def finished(store, item, request_id):
    store.begin(item['id'], turn(item, request_id=request_id))
    store.event(item['id'], request_id, {'type': 'delta', 'text': request_id})
    store.event(item['id'], request_id, {'type': 'usage', 'usage': {'prompt_tokens': 10}})
    store.event(item['id'], request_id, {'type': 'done'})
    return store.get(item['id'])


def test_branch_edit_regenerate_preserve_original_and_usage(env):
    store, _, _, client = env
    source = finished(store, finished(store, store.create(), 'request_first'), 'request_second')
    values = dict(revision=source['revision'], request_id='branch_request_1',
                  message_id=source['messages'][2]['id'], mode='edit', content='A revised question')
    url = f"/api/chat/conversations/{source['id']}/branches"
    response = client.post(url, json=values)
    assert response.status_code == 200
    branch = response.json()
    assert branch['messages'] == source['messages'][:2]
    assert branch['draft'] == 'A revised question'
    assert branch['parent_conversation_id'] == source['id']
    assert client.post(url, json=values).json()['id'] == branch['id']
    assert client.post(url, json={**values, 'content': 'Changed'}).status_code == 409
    assert store.get(source['id']) == source
    assert store.usage()['requests'] == 2
    assert store.usage()['prompt_tokens'] == 20
    regenerated = store.fork(source['id'], {**values, 'request_id': 'branch_request_2',
                           'message_id': source['messages'][-1]['id'], 'mode': 'regenerate'})
    assert regenerated['messages'] == source['messages'][:2]
    assert regenerated['draft'] == source['messages'][2]['content']
    assert store.usage()['requests'] == 2
    branch = finished(store, branch, 'request_branch')
    assert store.usage()['requests'] == 3
    assert store.get(source['id']) == source


def test_branch_drops_future_summary_and_rejects_stale_or_active(env):
    store, _, _, client = env
    source = finished(store, finished(store, store.create(), 'request_first'), 'request_second')
    source = store.update(source['id'], {'revision': source['revision'], 'summary': {
        'through_message_id': source['messages'][-1]['id'], 'text': 'The later conclusion'}})
    values = dict(revision=source['revision'], request_id='branch_request_3',
                  message_id=source['messages'][1]['id'], mode='continue', content='')
    fork = store.fork(source['id'], values)
    assert 'summary' not in fork
    assert len(fork['messages']) == 2
    with pytest.raises(WorkspaceError, match='conflict'):
        store.fork(source['id'], {**values, 'revision': 1, 'request_id': 'branch_request_4'})
    store.begin(source['id'], turn(source, request_id='request_active'))
    current = store.get(source['id'])
    with pytest.raises(WorkspaceError, match='generation_in_progress'):
        store.fork(source['id'], {**values, 'revision': current['revision'], 'request_id': 'branch_request_5'})
    url = f"/api/chat/conversations/{source['id']}/branches"
    assert client.post(url, json=values, headers={'origin': 'https://attacker.invalid'}).status_code == 403
    assert client.post(url, json=values, headers={'cookie': 'no-admin'}).status_code == 401
