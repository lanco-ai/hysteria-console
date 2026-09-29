"""One-shot PDF text extraction worker. stdout is bounded JSON or a safe error code."""
import io
import json
import resource
import sys

resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024, 384 * 1024 * 1024))
resource.setrlimit(resource.RLIMIT_CPU, (20, 20))


def fail(code):
    sys.stdout.write(code)
    raise SystemExit(1)


try:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(sys.stdin.buffer.read(10 * 1024 * 1024 + 1)))
    if reader.is_encrypted:
        fail('pdf_encrypted')
    if len(reader.pages) > 200:
        fail('pdf_page_limit')
    pages, size = [], 0
    for page in reader.pages:
        text = page.extract_text() or ''
        size += len(text)
        if size > 500000:
            fail('pdf_text_limit')
        pages.append(text)
    if not any(page.strip() for page in pages):
        fail('pdf_no_text')
    sys.stdout.write(json.dumps(pages, ensure_ascii=False))
except Exception:
    fail('invalid_pdf')
