"""Single worker for durable document jobs; restart resumes unfinished uploads."""
import base64
import json
from pathlib import Path
import subprocess
import sys
import threading

from .chat_workspace_store import WorkspaceError, encoded
from .chat_knowledge import KnowledgeIndex, COMPUTE_LOCK


class DocumentJobs:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.running = False
        self.knowledge = KnowledgeIndex(store)

    def resume(self):
        with self.lock:
            if self.running:
                return
            self.running = True
            threading.Thread(target=self._drain, name='learning-documents', daemon=True).start()

    def _next(self):
        with self.store.db() as db:
            for document_id, data in db.execute('SELECT id,data FROM documents').fetchall():
                item = json.loads(data)
                if item.get('status') in ('queued', 'processing'):
                    item['status'] = 'processing'
                    db.execute('UPDATE documents SET data=? WHERE id=?', (encoded(item), document_id))
                    return document_id, item, 'extract'
            if self.knowledge.embedder.available():
                for document_id, data in db.execute('SELECT id,data FROM documents').fetchall():
                    item = json.loads(data)
                    if item.get('status', 'ready') == 'ready' and item.get('index_status', 'queued') in ('queued', 'processing'):
                        item['index_status'] = 'processing'
                        db.execute('UPDATE documents SET data=? WHERE id=?', (encoded(item), document_id))
                        return document_id, item, 'index'
        return None

    def _drain(self):
        normal_exit = False
        try:
            while True:
                job = self._next()
                if job is None:
                    # Serialize empty-queue check with resume() to avoid lost wakeups.
                    with self.lock:
                        job = self._next()
                        if job is None:
                            self.running = False
                            normal_exit = True
                            return
                document_id, meta, phase = job
                if phase == 'index':
                    try:
                        self.knowledge.index(document_id)
                    except Exception as error:
                        with self.store.db() as db:
                            current = db.execute('SELECT data FROM documents WHERE id=?', (document_id,)).fetchone()
                            if current:
                                value = json.loads(current[0])
                                value.update(index_status='error', index_error=error.code if isinstance(error, WorkspaceError) else 'embedding_failed')
                                db.execute('UPDATE documents SET data=? WHERE id=?', (encoded(value), document_id))
                    continue
                try:
                    _, raw = self.store.document(document_id)
                    with COMPUTE_LOCK:
                        result = subprocess.run([sys.executable, str(Path(__file__).with_name('chat_document_worker.py')), Path(meta['title']).suffix.lower()],
                                                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                                timeout=300, check=False)
                    payload = json.loads(result.stdout)
                    if result.returncode or 'error' in payload:
                        raise WorkspaceError(payload.get('error', 'invalid_document'))
                    self.store.finish_document(document_id, payload['pages'], payload['media_type'],
                                               vision=base64.b64decode(payload['vision']) if payload.get('vision') else None)
                except WorkspaceError as error:
                    self.store.finish_document(document_id, error=error.code)
                except subprocess.TimeoutExpired:
                    self.store.finish_document(document_id, error='document_processing_timeout')
                except Exception:
                    self.store.finish_document(document_id, error='invalid_document')
        finally:
            if not normal_exit:
                with self.lock:
                    self.running = False
