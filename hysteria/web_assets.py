"""Explicit, content-versioned resources for extracted page interactions."""

import hashlib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / 'static'
SCRIPT_NAMES = (
    'ui-core',
    'login',
    'config-editor',
    'rules',
    'user-panel',
    'user-poll',
    'shell',
    'shell-preferences',
)
ASSETS = {}
for _name in SCRIPT_NAMES:
    _payload = (_ROOT / f'{_name}.js').read_bytes()
    _etag = '"' + hashlib.sha256(_payload).hexdigest()[:16] + '"'
    ASSETS[f'/static/{_name}.js'] = (_payload, _etag)


def script_tag(name, *, defer=True):
    """Only known assets may be rendered; never interpolate arbitrary paths."""
    path = f'/static/{name}.js'
    _, etag = ASSETS[path]
    version = etag.strip('"')
    loading = ' defer' if defer else ''
    return f'<script src="{path}?v={version}"{loading}></script>'
