"""Private multilingual semantic retrieval over persistent source chunks."""
from array import array
import json
import math
import os
from pathlib import Path
import subprocess
import threading

from .chat_workspace_store import WorkspaceError, encoded

MODEL_REVISION = 'multilingual-e5-small:761b726dd34fb83930e26aab4e9ac3899aa1fa78'
COMPUTE_LOCK = threading.Lock()


class LocalEmbedder:
    def __init__(self):
        self.python = Path(os.environ.get('HY2_EMBEDDING_PYTHON', '/root/hysteria/.venv-knowledge/bin/python'))
        self.root = Path(os.environ.get('HY2_EMBEDDING_MODEL_DIR', '/root/hysteria/models/multilingual-e5-small'))

    def available(self):
        revision = self.root / 'REVISION'
        return (self.python.exists() and all((self.root / name).exists() for name in ('model_quantized.onnx', 'sentencepiece.bpe.model'))
                and revision.exists() and revision.read_text().strip() == MODEL_REVISION.split(':', 1)[1])

    def embed(self, texts, *, query=False):
        if not self.available():
            raise WorkspaceError('embedding_unavailable', 503)
        if not COMPUTE_LOCK.acquire(timeout=3):
            raise WorkspaceError('knowledge_busy', 503)
        try:
            result = subprocess.run([str(self.python), str(Path(__file__).with_name('chat_embedding_worker.py')), str(self.root)],
                                    input=encoded({'texts': texts, 'query': query}).encode(),
                                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=260, check=False)
            values = json.loads(result.stdout)
            if result.returncode or not isinstance(values, list) or len(values) != len(texts):
                raise WorkspaceError('embedding_failed', 503)
            if any(len(v) != 384 or any(not math.isfinite(n) for n in v) for v in values):
                raise WorkspaceError('embedding_failed', 503)
            return values
        except (subprocess.TimeoutExpired, ValueError, TypeError):
            raise WorkspaceError('embedding_failed', 503) from None
        finally:
            COMPUTE_LOCK.release()


class KnowledgeIndex:
    def __init__(self, store):
        self.store = store
        self.embedder = getattr(store, 'embedder', None) or LocalEmbedder()

    def index(self, document_id):
        with self.store.db() as db:
            row = db.execute('SELECT data,pages FROM documents WHERE id=?', (document_id,)).fetchone()
            if not row:
                return
            meta, pages = json.loads(row[0]), json.loads(row[1])
        chunks = []
        for page, text in enumerate(pages, 1):
            for start in range(0, len(text), 480):
                quote = text[start:start + 650].strip()
                if quote:
                    chunks.append({'document_id': document_id, 'title': meta['title'], 'sha256': meta['sha256'],
                                   'page': page, 'quote': quote, 'offset': start,
                                   'location_kind': 'page' if meta['media_type'] == 'application/pdf' else 'section'})
        if len(chunks) > 2000:
            raise WorkspaceError('knowledge_chunk_limit')
        vectors = self.embedder.embed([c['quote'] for c in chunks]) if chunks else []
        with self.store.db() as db:
            current = db.execute('SELECT data FROM documents WHERE id=?', (document_id,)).fetchone()
            if not current:
                return
            db.execute('DELETE FROM document_vectors WHERE document_id=?', (document_id,))
            db.executemany('INSERT INTO document_vectors VALUES (?,?,?,?,?)',
                           [(document_id, i, encoded(c), array('f', vector).tobytes(), MODEL_REVISION) for i, (c, vector) in enumerate(zip(chunks, vectors))])
            item = json.loads(current[0])
            item.update(index_status='ready', index_error=None, chunk_count=len(chunks), embedding_model=MODEL_REVISION)
            db.execute('UPDATE documents SET data=? WHERE id=?', (encoded(item), document_id))

    def search(self, query, project_id=None, document_ids=None):
        with self.store.db() as db:
            rows = db.execute('SELECT v.data,v.vector,d.project_id FROM document_vectors v JOIN documents d ON d.id=v.document_id WHERE v.model=?', (MODEL_REVISION,)).fetchall()
        allowed = set(document_ids) if document_ids is not None else None
        candidates = []
        for data, raw, project in rows:
            chunk = json.loads(data)
            if project_id is not None and project_id != project or allowed is not None and chunk['document_id'] not in allowed:
                continue
            candidates.append((chunk, array('f', raw)))
        if not candidates:
            return {'items': [], 'model': MODEL_REVISION}
        vector = self.embedder.embed([query], query=True)[0]
        ranked = sorted(((sum(a * b for a, b in zip(vector, v)), c) for c, v in candidates), key=lambda pair: pair[0], reverse=True)
        selected = []
        for score, chunk in ranked:
            # Overlapping chunks should not crowd out distinct evidence.
            if any(c['document_id'] == chunk['document_id'] and c['page'] == chunk['page'] and abs(c['offset'] - chunk['offset']) < 480 for c in selected):
                continue
            selected.append({**chunk, 'score': round(score, 5), 'id': f'S{len(selected) + 1}'})
            if len(selected) == 8:
                break
        return {'items': selected, 'model': MODEL_REVISION}

    def status(self):
        with self.store.db() as db:
            items = [json.loads(row[0]) for row in db.execute('SELECT data FROM documents')]
        return {'available': self.embedder.available(), 'model': MODEL_REVISION, 'documents': len(items),
                'indexed': sum(d.get('index_status') == 'ready' for d in items),
                'pending': sum(d.get('index_status', 'queued') in ('queued', 'processing') for d in items),
                'failed': sum(d.get('index_status') == 'error' for d in items)}
