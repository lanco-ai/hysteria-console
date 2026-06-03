"""Small file-backed state helpers for hy2 runtime data."""
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

import fcntl


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def save_json(path, data):
    """Atomic JSON write using sibling temp file, fsync, then rename."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=True, indent=2) + "\n"
    _write_atomic(p, payload)


def save_text_atomic(path, text):
    """Atomic UTF-8 text write for operator-edited config files."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(p, text)


def _write_atomic(path, text):
    """Write via a unique sibling temp file, then atomically replace target."""
    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + '.',
            suffix='.tmp',
            dir=str(path.parent),
            text=True,
        )
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            fd = None
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        _fsync_dir(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _fsync_dir(path):
    try:
        fd = os.open(str(path), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        return
    finally:
        os.close(fd)


@contextmanager
def file_lock(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a+', encoding='utf-8') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
