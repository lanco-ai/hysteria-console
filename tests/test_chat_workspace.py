import asyncio
import io
import json
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from web_api import create_app
from web_api.chat_documents import extract, retrieve
from web_api.chat_workspace_store import WorkspaceStore, WorkspaceError
from web_api.chat_workspace_routes import sse
from web_api.journal_service import JournalStore
from web_api.services import LoginRequired


class Services:
    def read_session(self, *, headers, path):
        if headers.get('cookie') == 'sid=admin':
            return {'role': 'admin'}
        raise LoginRequired


class Provider:
    calls = 0
    received = None

    def stream(self, messages, **kwargs):
        self.calls += 1
        self.received = messages

        async def generate():
            yield sse({'type': 'delta', 'text': 'Evidence [S1].'})
            yield sse({'type': 'usage', 'usage': {'prompt_tokens': 30}})
            yield sse({'type': 'done'})
        return generate()


class TestEmbedder:
    """Cheap deterministic fixture; real semantic inference has its own integration test."""
    def available(self):
        return True

    def embed(self, texts, *, query=False):
        import hashlib
        from math import sqrt
        result = []
        for text in texts:
            vector = [0.] * 384
            for word in text.lower().split():
                vector[int(hashlib.sha256(word.encode()).hexdigest()[:8], 16) % 384] += 1
            norm = sqrt(sum(v * v for v in vector)) or 1
            result.append([v / norm for v in vector])
        return result


@pytest.fixture
def env(tmp_path):
    store = WorkspaceStore(tmp_path / 'chat' / 'workspace.sqlite3')
    store.embedder = TestEmbedder()
    journal = JournalStore(tmp_path / 'journal' / 'entries.json')
    provider = Provider()
    app = create_app(Services(), chat_workspace_store=store, journal_store=journal, chat_settings_store=provider)
    client = TestClient(app, headers={'cookie': 'sid=admin', 'origin': 'http://testserver'})
    return store, journal, provider, client


def project(store, name='AI'):
    return store.save_project({'name': name, 'goal': 'Understand storage', 'instructions': 'Ask questions'})


def turn(item, **changes):
    return {'request_id': 'request_0001', 'revision': item['revision'], 'content': 'LSM write amplification?',
            'model': 'test-model', 'reasoning_effort': 'auto', 'document_ids': [], **changes}


def test_persistence_import_search_and_conflict(env):
    store, _, _, _ = env
    legacy = {'id': 'browser-1', 'messages': [{'role': 'user', 'content': 'special research phrase'}]}
    assert store.import_legacy([legacy]) == {'imported': 1, 'skipped': 0}
    assert store.import_legacy([legacy]) == {'imported': 0, 'skipped': 1}
    item = store.list(q='research phrase')['items'][0]
    store.update(item['id'], {'revision': item['revision'], 'title': 'Renamed'})
    with pytest.raises(WorkspaceError, match='conflict'):
        store.update(item['id'], {'revision': item['revision'], 'title': 'Lost update'})
    second = WorkspaceStore(store.path)
    assert second.get(item['id'])['title'] == 'Renamed'
    assert os.stat(store.path).st_mode & 0o777 == 0o600
    assert os.stat(store.path.parent).st_mode & 0o777 == 0o700
    updated = second.get(item['id'])
    second.delete(item['id'], updated['revision'])
    assert store.import_legacy([legacy])['skipped'] == 1


def test_concurrent_send_idempotent_stop_and_recovery(env):
    store, _, _, _ = env
    item = store.create()
    values = turn(item)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: store.begin(item['id'], values), range(2)))
    assert sum(result[1] is not None for result in results) == 1
    assert len(store.get(item['id'])['messages']) == 2
    with pytest.raises(WorkspaceError, match='request_id_conflict'):
        store.begin(item['id'], {**values, 'content': 'Different'})
    store.event(item['id'], values['request_id'], {'type': 'delta', 'text': 'partial'})
    store.event(item['id'], values['request_id'], {'type': 'stopped'})
    assert not store.event(item['id'], values['request_id'], {'type': 'delta', 'text': 'late'})
    saved = store.get(item['id'])
    assert saved['messages'][-1]['content'] == 'partial'
    assert saved['messages'][-1]['status'] == 'stopped'
    store.begin(item['id'], turn(saved, request_id='request_0002'))
    with store.db() as db:
        data = json.loads(db.execute('SELECT data FROM conversations').fetchone()[0])
        data['updatedAt'] = (time.time() - 700) * 1000
        db.execute('UPDATE conversations SET data=?', (json.dumps(data),))
    recovered = WorkspaceStore(store.path).get(item['id'])
    assert recovered['active'] is None
    assert recovered['messages'][-1]['status'] == 'interrupted'


def test_project_isolation_and_citation_snapshots(env):
    store, _, _, _ = env
    a, b = project(store, 'A'), project(store, 'B')
    doc = store.add_document(a['id'], 'paper.txt', b'text', ['First page', 'LSM write amplification evidence'], 'text/plain')
    item = store.create(b['id'])
    with pytest.raises(WorkspaceError, match='document_outside_project'):
        store.begin(item['id'], turn(item, document_ids=[doc['id']]))
    assert not store.get(item['id'])['messages']
    item = store.create(a['id'])
    saved, messages = store.begin(item['id'], turn(item, document_ids=[doc['id']]))
    assert saved['messages'][-1]['citations'][0]['page'] == 2
    assert 'Ask questions' in messages[0]['content']
    store.delete_document(doc['id'])
    assert store.get(item['id'])['messages'][-1]['citations'][0]['quote'] == 'LSM write amplification evidence'


def test_routes_stream_replay_privacy_and_journal(env):
    store, journal, provider, client = env
    p = client.post('/api/chat/projects', json={'name': 'Paper'}).json()
    upload = client.post('/api/chat/documents/upload', params={'filename': 'paper.txt', 'project_id': p['id']}, content=b'LSM write amplification evidence')
    assert upload.status_code == 200
    doc = upload.json()
    c = client.post('/api/chat/conversations', json={'project_id': p['id']}).json()
    journal.create({'kind': 'life', 'occurred_at': '2026-09-29T12:00:00Z', 'timezone': 'UTC', 'body': 'PRIVATE JOURNAL NEVER SENT'})
    values = turn(c, document_ids=[doc['id']])
    response = client.post(f"/api/chat/conversations/{c['id']}/turns", json=values)
    assert response.status_code == 200
    assert 'Evidence' in response.text
    saved = client.get(f"/api/chat/conversations/{c['id']}").json()
    message = saved['messages'][-1]
    assert message['status'] == 'completed'
    assert message['usage']['prompt_tokens'] == 30
    assert 'PRIVATE JOURNAL' not in json.dumps(provider.received)
    client.post(f"/api/chat/conversations/{c['id']}/turns", json=values)
    assert provider.calls == 1
    draft = {'kind': 'paper', 'occurred_at': '2026-09-29T12:00:00Z', 'timezone': 'UTC', 'body': 'My own explanation'}
    url = f"/api/chat/conversations/{c['id']}/journal"
    first = client.post(url, json={'message_id': message['id'], 'draft': draft})
    assert first.status_code == 200
    record = first.json()['item']
    assert record['chat_source']['conversation_id'] == c['id']
    assert client.post(url, json={'message_id': message['id'], 'draft': draft}).json()['already_saved']
    updated = journal.update(record['id'], {**draft, 'revision': 1, 'body': 'Edited later'})['item']
    assert updated['chat_source'] == record['chat_source']
    raw = client.get(f"/api/chat/documents/{doc['id']}/file")
    assert raw.content == b'LSM write amplification evidence'
    assert raw.headers['cache-control'] == 'no-store'


def test_auth_csrf_and_body_limits(env):
    store, _, _, client = env
    assert client.get('/api/chat/projects', headers={'cookie': ''}).status_code == 401
    assert client.post('/api/chat/projects', json={'name': 'bad'}, headers={'origin': 'https://evil.invalid'}).status_code == 403
    assert client.post('/api/chat/projects', json={'name': 'ok', 'api_key': 'no'}).status_code == 422
    assert client.post('/api/chat/import/legacy', content=b' ' * 140000, headers={'content-type': 'application/json'}).status_code == 413
    assert client.get('/api/chat/workspace/export', headers={'cookie': ''}).status_code == 401


def test_pdf_rejection_and_local_retrieval():
    with pytest.raises(WorkspaceError, match='unsupported_document'):
        extract('file.pdf', b'not PDF')
    writer = PdfWriter(); writer.add_blank_page(width=100, height=100)
    raw = io.BytesIO(); writer.write(raw)
    with pytest.raises(WorkspaceError, match='pdf_no_text'):
        extract('scan.pdf', raw.getvalue())
    writer.encrypt('test-fixture')
    raw = io.BytesIO(); writer.write(raw)
    with pytest.raises(WorkspaceError, match='pdf_encrypted'):
        extract('encrypted.pdf', raw.getvalue())
    metadata = {'id': 'doc', 'title': 'paper', 'sha256': 'hash'}
    assert retrieve('写入放大', [(metadata, ['背景', '写入放大与 LSM Tree'])])[0]['page'] == 2
    assert extract('paper.md', '研究结果'.encode())[0] == ['研究结果']


def test_context_boundaries_and_no_unselected_document(env):
    store, _, _, _ = env
    p = project(store)
    store.add_document(p['id'], 'private.txt', b'ONLY SELECTED', ['ONLY SELECTED'], 'text/plain')
    item = store.create(p['id'])
    with store.db() as db:
        item['messages'] = [{'id': str(i), 'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'x' * 2000, 'status': 'completed', 'citations': []} for i in range(40)]
        store._save(db, item)
    saved, messages = store.begin(item['id'], turn(item))
    assert saved['messages'][-1]['context_truncated']
    assert 'ONLY SELECTED' not in json.dumps(messages)
    assert sum(len(m['content']) for m in messages) < 25000


def test_disconnect_finishes_partial_stream(env):
    store, _, _, _ = env
    item = store.create()
    disconnected = asyncio.Event()

    class SlowProvider:
        def stream(self, *args, **kwargs):
            async def generate():
                yield sse({'type': 'delta', 'text': 'saved before disconnect'})
                await asyncio.sleep(30)
            return generate()

    app = create_app(Services(), chat_workspace_store=store, chat_workspace_settings=SlowProvider())

    async def run():
        body_sent = False
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.0'}, 'http_version': '1.1',
                 'scheme': 'http', 'method': 'POST', 'path': f"/api/chat/conversations/{item['id']}/turns",
                 'raw_path': b'/', 'query_string': b'', 'server': ('testserver', 80), 'client': ('127.0.0.1', 1234),
                 'headers': [(b'host', b'testserver'), (b'cookie', b'sid=admin'), (b'origin', b'http://testserver'), (b'content-type', b'application/json')]}
        async def receive():
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {'type': 'http.request', 'body': json.dumps(turn(item)).encode(), 'more_body': False}
            await disconnected.wait()
            return {'type': 'http.disconnect'}
        async def send(event):
            if event['type'] == 'http.response.body' and b'saved before disconnect' in event.get('body', b''):
                disconnected.set()
        await asyncio.wait_for(app(scope, receive, send), 3)
    asyncio.run(run())
    saved = store.get(item['id'])
    assert saved['active'] is None
    assert saved['messages'][-1]['status'] == 'interrupted'
    assert saved['messages'][-1]['content'] == 'saved before disconnect'


def test_backup_contains_consistent_database_and_original_documents(tmp_path):
    import subprocess
    import tarfile
    from pathlib import Path
    from test_runtime_scripts import isolated_backup_env, ROOT
    hy_dir = tmp_path / 'hysteria'
    store = WorkspaceStore(hy_dir / 'state/chat/workspace.sqlite3')
    p = project(store)
    doc = store.add_document(p['id'], 'paper.txt', b'original paper', ['original paper'], 'text/plain')
    item = store.create(p['id'])
    store.begin(item['id'], turn(item))
    result = subprocess.run(['bash', str(ROOT / 'scripts/hy2-backup.sh')], env=isolated_backup_env(tmp_path, hy_dir), check=True, capture_output=True, text=True)
    with tarfile.open(result.stdout.strip()) as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith('workspace.sqlite3'))
        raw = archive.extractfile(member).read()
        assert not any(m.name.endswith(('-wal', '-shm')) for m in archive.getmembers())
    restore = tmp_path / 'restored' / 'workspace.sqlite3'
    restore.parent.mkdir(); restore.write_bytes(raw)
    recovered = WorkspaceStore(restore)
    assert recovered.document(doc['id'])[1] == b'original paper'
    assert recovered.get(item['id'])['messages'][0]['content'] == 'LSM write amplification?'


def test_pdf_page_numbers_and_original_text():
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    for text in ('Introduction', 'LSM write amplification evidence'):
        page = writer.add_blank_page(width=300, height=300)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject(); stream.set_data(f'BT /F1 12 Tf 20 200 Td ({text}) Tj ET'.encode())
        page[NameObject('/Contents')] = writer._add_object(stream)
    raw = io.BytesIO(); writer.write(raw)
    pages, media = extract('two-page.pdf', raw.getvalue())
    assert media == 'application/pdf'
    assert len(pages) == 2
    assert 'LSM write amplification evidence' in pages[1]
    citation = retrieve('write amplification', [({'id': 'doc', 'title': 'Paper', 'sha256': 'hash'}, pages)])[0]
    assert citation['page'] == 2
    assert 'LSM' in citation['quote']


def test_preferences_save_with_revision_and_reach_every_system_prompt(env):
    store, _, provider, client = env
    url = '/api/chat/workspace/preferences'
    assert client.get(url).json() == {'instructions': '', 'default_model': '', 'default_reasoning': 'auto', 'revision': 0}
    values = {'revision': 0, 'instructions': '  回答用中文。\r\n先给结论。  ', 'default_model': ' test-model ', 'default_reasoning': 'high'}
    saved = client.put(url, json=values).json()
    assert saved == {'instructions': '回答用中文。\n先给结论。', 'default_model': 'test-model', 'default_reasoning': 'high', 'revision': 1}
    assert client.get(url).json() == saved
    # A stale revision is refused instead of silently overwriting.
    assert client.put(url, json=values).status_code == 409
    assert client.put(url, json={**values, 'revision': 1, 'default_reasoning': 'max'}).status_code == 422
    assert client.put(url, json={**values, 'revision': 1, 'instructions': 'x' * 2001}).status_code == 422
    assert client.put(url, json={'revision': 1}).status_code == 422
    assert client.get(url, headers={'cookie': ''}).status_code == 401
    assert client.put(url, json={**values, 'revision': 1}, headers={'origin': 'https://evil.invalid'}).status_code == 403

    p = project(store)
    c = client.post('/api/chat/conversations', json={'project_id': p['id']}).json()
    assert client.post(f"/api/chat/conversations/{c['id']}/turns", json=turn(c)).status_code == 200
    system = provider.received[0]['content']
    assert '回答用中文。\n先给结论。' in system
    # Workspace-wide instructions come before the more specific project instructions.
    assert system.index('先给结论') < system.index('Ask questions')
    assert client.get('/api/chat/workspace/export').json()['preferences'] == saved

    cleared = client.put(url, json={**values, 'revision': 1, 'instructions': '   '}).json()
    assert cleared['instructions'] == '' and cleared['revision'] == 2
    c = client.get(f"/api/chat/conversations/{c['id']}").json()
    assert client.post(f"/api/chat/conversations/{c['id']}/turns", json=turn(c, request_id='request_0002')).status_code == 200
    assert '长期说明' not in provider.received[0]['content']
