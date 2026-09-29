"""Bounded document extraction and local lexical (not vector) retrieval."""

import json
import re
import subprocess
import sys
import threading
from pathlib import Path

from .chat_workspace_store import WorkspaceError

MAX_UPLOAD = 10 * 1024 * 1024
_PDF_SLOTS = threading.BoundedSemaphore(1)


def extract(name, raw):
    if not raw or len(raw) > MAX_UPLOAD:
        raise WorkspaceError('file_too_large', 413)
    suffix = Path(name).suffix.lower()
    if suffix in ('.txt', '.md'):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            raise WorkspaceError('utf8_required') from None
        if '\x00' in text or len(text) > 500000 or not text.strip():
            raise WorkspaceError('invalid_document')
        return [text], 'text/plain'
    if suffix != '.pdf' or not raw.startswith(b'%PDF-'):
        raise WorkspaceError('unsupported_document')
    # Separate process: enforce hard CPU, address-space, output and wall-clock limits.
    if not _PDF_SLOTS.acquire(blocking=False):
        raise WorkspaceError('pdf_extraction_busy', 503)
    try:
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('chat_pdf_worker.py'))],
                                input=raw, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=25, check=False)
    except subprocess.TimeoutExpired:
        raise WorkspaceError('pdf_extraction_timeout') from None
    finally:
        _PDF_SLOTS.release()
    if result.returncode != 0:
        code = result.stdout.decode('ascii', errors='ignore').strip()
        raise WorkspaceError(code if code in ('pdf_encrypted', 'pdf_page_limit', 'pdf_no_text', 'pdf_text_limit') else 'invalid_pdf')
    try:
        pages = json.loads(result.stdout)
    except (ValueError, UnicodeDecodeError):
        raise WorkspaceError('invalid_pdf') from None
    return pages, 'application/pdf'


def terms(text):
    words = re.findall(r'[a-z0-9_]{2,}', text.lower())
    for sequence in re.findall(r'[\u3400-\u9fff]+', text):
        words.extend(sequence[i:i + 2] for i in range(max(1, len(sequence) - 1)))
    return set(words)


def retrieve(query, documents):
    query_terms, candidates = terms(query), []
    for metadata, pages in documents:
        for page, text in enumerate(pages, 1):
            for start in range(0, len(text), 1100):
                quote = text[start:start + 1400].strip()
                if not quote:
                    continue
                score = len(query_terms & terms(quote))
                candidates.append((score, {'document_id': metadata['id'], 'title': metadata['title'],
                                           'sha256': metadata['sha256'], 'page': page, 'quote': quote}))
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    selected, represented = [], set()
    # Give every selected document a source, then fill with the best remaining matches.
    for candidate in candidates:
        document_id = candidate[1]['document_id']
        if document_id not in represented:
            selected.append(candidate)
            represented.add(document_id)
    for candidate in candidates:
        if len(selected) >= 8:
            break
        if candidate not in selected:
            selected.append(candidate)
    selected.sort(key=lambda pair: pair[0], reverse=True)
    return [{'id': f'S{index}', **item} for index, (_, item) in enumerate(selected[:8], 1)]
