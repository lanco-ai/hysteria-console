#!/usr/bin/env python3
"""Install pinned public E5 model artifacts (never downloads remote Python code)."""
import hashlib
import os
from pathlib import Path
import urllib.request

REVISION = '761b726dd34fb83930e26aab4e9ac3899aa1fa78'
ROOT = Path(os.environ.get('HY2_EMBEDDING_MODEL_DIR', '/root/hysteria/models/multilingual-e5-small'))
ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
ARTIFACTS = {
    'onnx/model_quantized.onnx': 'f80102d3f2a1229f387d3c81909990d8945513e347b0eab049f7de3c6f98c193',
    'sentencepiece.bpe.model': 'cfc8146abe2a0488e9e2a0c56de7952f7c11ab059eca145a0a727afce0db2865',
}
for name, expected in ARTIFACTS.items():
    target = ROOT / Path(name).name
    if target.exists() and (not expected or hashlib.sha256(target.read_bytes()).hexdigest() == expected):
        print(target.name + ': already present')
        continue
    url = f'https://huggingface.co/Xenova/multilingual-e5-small/resolve/{REVISION}/{name}'
    temporary = target.with_suffix(target.suffix + '.download')
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, temporary.open('wb') as output:
        total = 0
        while chunk := response.read(1024 * 1024):
            total += len(chunk)
            if total > 130 * 1024 * 1024:
                raise RuntimeError('Model download exceeded size limit')
            h.update(chunk); output.write(chunk)
    if expected and h.hexdigest() != expected:
        temporary.unlink()
        raise RuntimeError('Model digest mismatch')
    temporary.chmod(0o600)
    temporary.replace(target)
    print(f'{target.name}: {total} bytes sha256={h.hexdigest()}')
(ROOT / 'REVISION').write_text(REVISION + '\n')
