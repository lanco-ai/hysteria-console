"""Disposable, resource-bounded DOCX/image/OCR reader; never executes document code."""
import base64
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET

resource.setrlimit(resource.RLIMIT_AS, (384 * 1024 * 1024,) * 2)
resource.setrlimit(resource.RLIMIT_CPU, (90, 90))
resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024,) * 2)
os.environ['OMP_THREAD_LIMIT'] = '1'
MAX_TEXT = 500000


def fail(code):
    sys.stdout.write(json.dumps({'error': code}))
    raise SystemExit(1)


def image_file(raw, destination):
    from PIL import Image, ImageOps
    Image.MAX_IMAGE_PIXELS = 8_000_000
    with Image.open(io.BytesIO(raw)) as im:
        if im.format not in ('PNG', 'JPEG', 'WEBP') or im.width * im.height > 8_000_000:
            fail('image_dimensions_limit')
        im = ImageOps.exif_transpose(im).convert('RGB')
        im.thumbnail((2400, 2400))
        im.save(destination, format='PNG')


def ocr(path):
    result = subprocess.run(['tesseract', str(path), 'stdout', '-l', 'eng+chi_sim'],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=True)
    if len(result.stdout) > 2_000_000:
        fail('pdf_text_limit')
    return result.stdout.decode('utf-8').strip()


def main():
    raw = sys.stdin.buffer.read(10 * 1024 * 1024 + 1)
    suffix = sys.argv[1]
    vision = None
    with tempfile.TemporaryDirectory(prefix='learning-document-') as directory:
        root = Path(directory)
        if suffix == '.docx':
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 512 or sum(info.file_size for info in infos) > 20 * 1024 * 1024:
                    fail('document_expansion_limit')
                pages = []
                for name in ('word/document.xml', 'word/footnotes.xml', 'word/endnotes.xml'):
                    if name not in archive.namelist():
                        continue
                    xml = archive.read(name)
                    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
                        fail('invalid_document')
                    ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
                    doc = ET.fromstring(xml)
                    text = '\n'.join(''.join(p.itertext()) if not list(p.iter(ns + 't')) else ''.join(t.text or '' for t in p.iter(ns + 't')) for p in doc.iter(ns + 'p'))
                    if text.strip():
                        pages.append(text)
            if not pages:
                fail('document_no_text')
            media = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        elif suffix in ('.png', '.jpg', '.jpeg', '.webp'):
            if len(raw) > 4 * 1024 * 1024:
                fail('image_size_limit')
            target = root / 'image.png'
            image_file(raw, target)
            pages = [ocr(target)]
            media = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}[suffix]
            # Verify suffix and file signature agree before serving or forwarding.
            from PIL import Image
            if Image.open(io.BytesIO(raw)).get_format_mimetype() != media:
                fail('invalid_image')
            with Image.open(target) as preview:
                preview.thumbnail((1600, 1600))
                output = io.BytesIO()
                preview.save(output, format='JPEG', quality=90)
                vision = base64.b64encode(output.getvalue()).decode('ascii')
        elif suffix == '.pdf':
            from pypdf import PdfReader
            if not raw.startswith(b'%PDF-'):
                fail('invalid_pdf')
            reader = PdfReader(io.BytesIO(raw))
            if reader.is_encrypted:
                fail('pdf_encrypted')
            if len(reader.pages) > 200:
                fail('pdf_page_limit')
            target = root / 'source.pdf'
            target.write_bytes(raw)
            pages, scans = [], 0
            for index, page in enumerate(reader.pages, 1):
                text = page.extract_text() or ''
                # Rasterize pages lacking text, including vector-outlined glyphs.
                if not text.strip():
                    scans += 1
                    if scans > 30:
                        fail('ocr_page_limit')
                    prefix = root / 'page'
                    subprocess.run(['pdftoppm', '-f', str(index), '-l', str(index), '-singlefile',
                                    '-scale-to', '2400', '-png', str(target), str(prefix)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True)
                    text = ocr(root / 'page.png')
                pages.append(text)
                if sum(map(len, pages)) > MAX_TEXT:
                    fail('pdf_text_limit')
            if not any(p.strip() for p in pages):
                fail('pdf_no_text')
            media = 'application/pdf'
        else:
            fail('unsupported_document')
        if sum(map(len, pages)) > MAX_TEXT:
            fail('pdf_text_limit')
        sys.stdout.write(json.dumps({'pages': pages, 'media_type': media, 'vision': vision}, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except FileNotFoundError:
        fail('ocr_unavailable')
    except subprocess.TimeoutExpired:
        fail('document_processing_timeout')
    except Exception:
        fail('invalid_document')
