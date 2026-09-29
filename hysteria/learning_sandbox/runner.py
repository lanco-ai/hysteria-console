"""Runs only inside a disposable, unprivileged, network-disabled Docker container."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import stat
import sys
import traceback


class LimitedText(io.StringIO):
    def write(self, text):
        remaining = max(0, 20000 - self.tell())
        super().write(text[:remaining])
        return len(text)


def main():
    payload = json.loads(sys.stdin.buffer.read(24 * 1024 * 1024))
    root = Path('/workspace')
    (root / 'input').mkdir()
    (root / 'output').mkdir()
    for file in payload.get('files', []):
        name = file['name']
        if Path(name).name != name or name in ('.', '..'):
            raise ValueError('Invalid input filename')
        (root / 'input' / name).write_bytes(base64.b64decode(file['data'], validate=True))
    out, error = LimitedText(), None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            exec(compile(payload['code'], 'learning.py', 'exec'), {'__name__': '__main__'})
        except BaseException:
            error = traceback.format_exc(limit=5)[-5000:]
    files, total = [], 0
    for path in sorted((root / 'output').iterdir()):
        if len(files) >= 8 or path.suffix.lower() not in ('.png', '.csv', '.txt', '.json', '.pdf'):
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as file:
                info = os.fstat(file.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2 * 1024 * 1024:
                    continue
                raw = file.read(2 * 1024 * 1024 + 1)
                total += len(raw)
                if total > 6 * 1024 * 1024 or len(raw) > 2 * 1024 * 1024:
                    continue
                files.append({'name': path.name[:120], 'data': base64.b64encode(raw).decode('ascii')})
        except OSError:
            continue
    sys.stdout.write(json.dumps({'stdout': out.getvalue(), 'error': error, 'files': files}))


if __name__ == '__main__':
    main()
