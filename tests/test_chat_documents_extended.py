import io
import json
from pathlib import Path
import subprocess
import sys
import time
import zipfile

from PIL import Image, ImageDraw, ImageFont
import pytest

from tests.test_chat_workspace import env, project, turn
from web_api.ai.gemini import GeminiAdapter
from web_api.chat_workspace_store import WorkspaceError, WorkspaceStore


def picture():
    im = Image.new('RGB', (1000, 400), 'white')
    draw = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=36)
    draw.text((40, 100), 'Storage research evidence 2026', fill='black', font=font)
    return im


def upload_ready(client, p, filename, raw):
    response = client.post('/api/chat/documents/upload', params={'project_id': p['id'], 'filename': filename}, content=raw)
    assert response.status_code == 200
    document_id = response.json()['id']
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        docs = client.get(f"/api/chat/projects/{p['id']}/documents").json()['items']
        doc = next(d for d in docs if d['id'] == document_id)
        if doc.get('status') in ('ready', 'error'):
            return doc
        time.sleep(.15)
    pytest.fail('document worker did not finish')


def test_docx_image_and_scanned_pdf_are_real_extracts(env):
    store, _, _, client = env
    p = project(store)
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>存储论文中的写入放大</w:t></w:r></w:p></w:body></w:document>')
    doc = upload_ready(client, p, 'paper.docx', archive.getvalue())
    assert doc['status'] == 'ready'
    im = picture()
    raw = io.BytesIO(); im.save(raw, format='PNG')
    image = upload_ready(client, p, 'paper.png', raw.getvalue())
    assert image['status'] == 'ready' and image['has_image']
    item = store.create(p['id'])
    saved, prompt = store.begin(item['id'], turn(item, document_ids=[image['id'], doc['id']]))
    assert any('2026' in c['quote'] for c in saved['messages'][-1]['citations'])
    assert prompt[-1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
    _, gemini = GeminiAdapter._chat_request('vision-model', prompt, 0.7)
    assert gemini['contents'][-1]['parts'][1]['inlineData']['mimeType'] == 'image/jpeg'
    store.event(item['id'], 'request_0001', {'type': 'done'})
    current = store.get(item['id'])
    _, followup = store.begin(item['id'], turn(current, request_id='image_followup', content='What does the image show?'))
    assert any(isinstance(m['content'], list) for m in followup[:-1])
    pdf = io.BytesIO(); im.save(pdf, format='PDF')
    scanned = upload_ready(client, p, 'scanned.pdf', pdf.getvalue())
    assert scanned['status'] == 'ready' and scanned['page_count'] == 1
    with store.db() as db:
        pages = json.loads(db.execute('SELECT pages FROM documents WHERE id=?', (scanned['id'],)).fetchone()[0])
    assert 'Storage research' in pages[0]
    store.delete_document(image['id'])
    with store.db() as db:
        assert db.execute('SELECT count(*) FROM document_images').fetchone()[0] == 0


def test_invalid_upload_state_cannot_enter_prompt_and_delete_does_not_resurrect(env):
    store, _, _, client = env
    p = project(store)
    bad = upload_ready(client, p, 'bad.docx', b'not a zip')
    assert bad['status'] == 'error'
    item = store.create(p['id'])
    with pytest.raises(WorkspaceError, match='document_not_ready'):
        store.begin(item['id'], turn(item, document_ids=[bad['id']]))
    queued = store.add_document(p['id'], 'queued.pdf', b'%PDF-test', [], 'application/octet-stream', 'queued')
    with pytest.raises(WorkspaceError, match='document_not_ready'):
        store.begin(item['id'], turn(item, document_ids=[queued['id']]))
    store.delete_document(queued['id'])
    assert store.finish_document(queued['id'], ['Late result'], 'application/pdf') is None
    assert len(store.documents(p['id'])['items']) == 1


def test_docx_rejects_entity_expansion():
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('word/document.xml', '<!DOCTYPE a [<!ENTITY a "attack">]><a>&a;</a>')
    worker = Path(__file__).parents[1] / 'hysteria/web_api/chat_document_worker.py'
    result = subprocess.run([sys.executable, str(worker), '.docx'], input=archive.getvalue(), capture_output=True)
    assert result.returncode == 1
    assert json.loads(result.stdout)['error'] == 'invalid_document'


def test_v1_store_upgrade_keeps_history_and_documents(tmp_path):
    import sqlite3
    path = tmp_path / 'chat' / 'workspace.sqlite3'
    store = WorkspaceStore(path)
    item = store.create()
    with sqlite3.connect(path) as db:
        db.execute('DROP TABLE document_images')
        db.execute('PRAGMA user_version=1')
    reopened = WorkspaceStore(path)
    assert reopened.get(item['id']) == item
    with reopened.db() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 2
