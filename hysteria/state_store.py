"""Small file-backed state helpers for hy2 runtime data."""
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import fcntl


class StateStoreError(RuntimeError):
    """Base error for state that cannot be read or safely persisted."""


class InvalidJsonState(StateStoreError):
    """A JSON state file exists but is unreadable or has the wrong shape."""


class CriticalStateUnavailable(InvalidJsonState):
    """Canonical authorization/accounting state cannot be trusted.

    Callers may use this narrower type to distinguish a core state failure,
    which requires revoking generated static credentials, from corruption in
    an optional dashboard/cache file that should only degrade that feature.
    """


class LockTimeout(StateStoreError):
    """A state mutation could not acquire its advisory lock in time."""


class AtomicReplaceDurabilityUncertain(StateStoreError):
    """The new file is visible, but its directory entry may not be durable.

    This is deliberately distinct from failures before ``os.replace``.  A
    caller performing a credential or accounting transaction can re-read the
    target and determine whether the intended generation became visible,
    while still treating crash durability as uncertain and failing closed.
    """

    def __init__(self, path):
        self.path = Path(path)
        super().__init__(
            f'atomic replace completed but directory durability is uncertain: '
            f'{self.path}',
        )


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return default


def load_json_strict(path, default, *, required=False):
    """Load JSON without conflating a missing file with corrupt live state.

    Missing files may legitimately use their initialization default unless
    ``required`` is true. Existing files that cannot be read, parsed, or whose
    top-level shape differs from the supplied default fail closed so callers
    never overwrite them with an empty fallback.
    """
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding='utf-8'))
    except FileNotFoundError:
        if required:
            raise InvalidJsonState(f'missing required JSON state: {p}')
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidJsonState(f'cannot load JSON state: {p}') from exc
    if default is not None and not isinstance(data, type(default)):
        raise InvalidJsonState(f'invalid JSON state shape: {p}')
    return data


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
        try:
            _fsync_dir(path.parent)
        except OSError as exc:
            raise AtomicReplaceDurabilityUncertain(path) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _fsync_dir(path):
    """Persist directory-entry changes or propagate the durability failure.

    Returning success after ``os.replace`` but before the containing
    directory is durable lets callers report a committed authorization or
    accounting change that may disappear after a crash.  Core callers need
    that uncertainty surfaced so they can fail closed.
    """
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    fd = os.open(str(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def file_lock(path, *, timeout=None, poll_interval=0.05):
    if timeout is not None:
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError('timeout must be a non-negative number or None')
        if (
            isinstance(poll_interval, bool)
            or not isinstance(poll_interval, (int, float))
            or not math.isfinite(poll_interval)
            or poll_interval <= 0
        ):
            raise ValueError('poll_interval must be a positive number')
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open('a+', encoding='utf-8') as f:
        if timeout is None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        else:
            deadline = time.monotonic() + float(timeout)
            while True:
                try:
                    fcntl.flock(
                        f.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError as exc:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise LockTimeout(
                            f'timed out acquiring state lock: {p}',
                        ) from exc
                    time.sleep(min(float(poll_interval), remaining))
        try:
            yield
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
