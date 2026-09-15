#!/usr/bin/env python3
"""Validate and atomically activate a React panel asset release.

The release pointer is intentionally independent from the production deploy
transaction.  Callers can install and inspect a release in a temporary root,
then make the pointer part of a separately approved deployment transaction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

ALLOWED_SUFFIXES = frozenset({'.css', '.html', '.ico', '.js', '.json', '.png', '.svg', '.woff2'})
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_RELEASE_BYTES = 64 * 1024 * 1024
RELEASE_ID_RE = re.compile(r'[0-9a-f]{24}')


class ReleaseError(RuntimeError):
    """The source tree or release pointer is unsafe or invalid."""


@dataclass(frozen=True)
class ReleaseManifest:
    release_id: str
    files: tuple[tuple[str, str, int], ...]


def _regular_directory(path: Path, *, create: bool = False) -> Path:
    if not path.exists() and create:
        path.mkdir(parents=True, mode=0o755)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise ReleaseError(f'missing release directory: {path}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseError(f'release directory is not a regular directory: {path}')
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_file(path: Path) -> None:
    with path.open('rb') as stream:
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _relative_files(root: Path) -> list[tuple[Path, str, int]]:
    records = []
    total = 0
    for candidate in sorted(root.rglob('*')):
        metadata = candidate.lstat()
        if stat.S_ISDIR(metadata.st_mode):
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ReleaseError(f'release contains a symlink: {candidate.relative_to(root)}')
        if not stat.S_ISREG(metadata.st_mode):
            raise ReleaseError(
                f'release contains a non-regular file: {candidate.relative_to(root)}'
            )
        relative = candidate.relative_to(root)
        if any(part.startswith('.') for part in relative.parts):
            raise ReleaseError(f'unsupported release artifact: {relative}')
        suffix = candidate.suffix.lower()
        if suffix not in ALLOWED_SUFFIXES or suffix == '.map':
            raise ReleaseError(f'unsupported release artifact: {relative}')
        if metadata.st_size > MAX_FILE_BYTES:
            raise ReleaseError(f'release artifact too large: {relative}')
        total += metadata.st_size
        if total > MAX_RELEASE_BYTES:
            raise ReleaseError('release exceeds the maximum total size')
        records.append((candidate, relative.as_posix(), metadata.st_size))
    return records


def _manifest_asset(value, files: set[str]) -> str:
    if not isinstance(value, str) or not value or value.startswith('/'):
        raise ReleaseError('manifest asset reference is invalid')
    parts = value.replace('\\', '/').split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ReleaseError(f'manifest asset reference is unsafe: {value}')
    normalized = '/'.join(parts)
    if normalized not in files:
        raise ReleaseError(f'missing manifest asset: {normalized}')
    return normalized


def _validate_manifest_references(payload: dict, files: set[str]) -> None:
    for key, entry in payload.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            raise ReleaseError('manifest entry is invalid')
        file = entry.get('file')
        if file is None:
            raise ReleaseError(f'manifest entry has no file: {key}')
        _manifest_asset(file, files)
        for field in ('css', 'assets'):
            references = entry.get(field, [])
            if not isinstance(references, list):
                raise ReleaseError(f'manifest {field} list is invalid: {key}')
            for reference in references:
                _manifest_asset(reference, files)
        for field in ('imports', 'dynamicImports'):
            references = entry.get(field, [])
            if not isinstance(references, list) or any(
                not isinstance(reference, str) or reference not in payload
                for reference in references
            ):
                raise ReleaseError(f'manifest {field} entry is invalid: {key}')


def validate_dist(path: Path | str) -> ReleaseManifest:
    """Validate a Vite dist tree and return its deterministic content ID."""

    candidate = Path(path)
    try:
        metadata = candidate.lstat()
    except FileNotFoundError as exc:
        raise ReleaseError(f'missing release directory: {candidate}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseError(f'release root is not a regular directory: {candidate}')
    root = candidate.resolve()
    records = _relative_files(root)
    required = {relative for _, relative, _ in records}
    if 'index.html' not in required:
        raise ReleaseError('release is missing index.html')
    if 'manifest.json' not in required:
        raise ReleaseError('release is missing manifest.json')
    try:
        manifest_payload = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError('manifest.json is invalid') from exc
    if not isinstance(manifest_payload, dict):
        raise ReleaseError('manifest.json must contain an object')
    entry = manifest_payload.get('index.html')
    if not isinstance(entry, dict) or not entry.get('css'):
        raise ReleaseError('React entry has no stylesheet')
    _validate_manifest_references(manifest_payload, required)

    files = tuple((relative, _sha256(file), 0o644) for file, relative, _size in records)
    release_id = hashlib.sha256(
        json.dumps(files, ensure_ascii=True, separators=(',', ':')).encode('ascii')
    ).hexdigest()[:24]
    return ReleaseManifest(release_id=release_id, files=files)


def _release_root(path: Path | str, *, create: bool = False) -> Path:
    root = Path(path)
    if not root.exists() and create:
        root.mkdir(parents=True, mode=0o755)
    _regular_directory(root)
    releases = _regular_directory(root / 'releases', create=create)
    del releases
    return root


def _release_directory(root: Path, release_id: str) -> Path:
    if not RELEASE_ID_RE.fullmatch(release_id):
        raise ReleaseError(f'invalid release id: {release_id}')
    target = root / 'releases' / release_id
    try:
        metadata = target.lstat()
    except FileNotFoundError as exc:
        raise ReleaseError(f'unknown release: {release_id}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
        raise ReleaseError(f'unknown release: {release_id}')
    return target


def install_release(dist: Path | str, release_root: Path | str) -> ReleaseManifest:
    """Copy a validated dist into ``releases/<id>`` without partial output."""

    manifest = validate_dist(dist)
    root = _release_root(release_root, create=True)
    releases = root / 'releases'
    target = releases / manifest.release_id
    if target.exists() or target.is_symlink():
        existing = _release_directory(root, manifest.release_id)
        if validate_dist(existing) != manifest:
            raise ReleaseError(f'release id collision: {manifest.release_id}')
        return manifest

    staging = Path(tempfile.mkdtemp(prefix=f'.staging-{manifest.release_id}-', dir=releases))
    try:
        staging.chmod(0o755)
        expected = {relative: digest for relative, digest, _mode in manifest.files}
        for source, relative, _size in _relative_files(Path(dist).resolve()):
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            shutil.copyfile(source, destination)
            destination.chmod(0o644)
            if _sha256(destination) != expected[relative]:
                raise ReleaseError(f'copied release hash mismatch: {relative}')
            _fsync_file(destination)
        for directory in sorted(
            (item for item in staging.rglob('*') if item.is_dir()), reverse=True
        ):
            directory.chmod(0o755)
            _fsync_directory(directory)
        _fsync_directory(staging)
        os.rename(staging, target)
        _fsync_directory(releases)
    except FileExistsError:
        shutil.rmtree(staging, ignore_errors=True)
        existing = _release_directory(root, manifest.release_id)
        if validate_dist(existing) != manifest:
            raise ReleaseError(f'release id collision: {manifest.release_id}')
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return manifest


def _current_target(root: Path) -> str | None:
    current = root / 'current'
    if not current.exists() and not current.is_symlink():
        return None
    metadata = current.lstat()
    if not stat.S_ISLNK(metadata.st_mode):
        raise ReleaseError('current pointer is not a symlink')
    target = os.readlink(current)
    expected = re.fullmatch(r'releases/([0-9a-f]{24})', target)
    if expected is None:
        raise ReleaseError('current pointer is outside the managed release root')
    _release_directory(root, expected.group(1))
    return expected.group(1)


def activate_release(release_root: Path | str, release_id: str) -> str | None:
    """Atomically point ``current`` at an existing validated release."""

    root = _release_root(release_root)
    target = _release_directory(root, release_id)
    manifest = validate_dist(target)
    if manifest.release_id != release_id:
        raise ReleaseError(f'release content does not match id: {release_id}')
    previous = _current_target(root)
    temporary = root / f'.current-{release_id}-{uuid.uuid4().hex}'
    if temporary.exists() or temporary.is_symlink():
        raise ReleaseError('temporary current pointer already exists')
    os.symlink(f'releases/{release_id}', temporary)
    os.replace(temporary, root / 'current')
    _fsync_directory(root)
    return previous


def inspect_release(release_root: Path | str, release_id: str | None = None) -> ReleaseManifest:
    root = _release_root(release_root)
    selected = release_id or _current_target(root)
    if selected is None:
        raise ReleaseError('no active release')
    target = _release_directory(root, selected)
    manifest = validate_dist(target)
    if manifest.release_id != selected:
        raise ReleaseError(f'release content does not match id: {selected}')
    return manifest


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    validate = commands.add_parser('validate')
    validate.add_argument('dist', type=Path)
    install = commands.add_parser('install')
    install.add_argument('dist', type=Path)
    install.add_argument('release_root', type=Path)
    activate = commands.add_parser('activate')
    activate.add_argument('release_root', type=Path)
    activate.add_argument('release_id')
    inspect = commands.add_parser('inspect')
    inspect.add_argument('release_root', type=Path)
    inspect.add_argument('release_id', nargs='?')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == 'validate':
        result = validate_dist(args.dist)
    elif args.command == 'install':
        result = install_release(args.dist, args.release_root)
    elif args.command == 'activate':
        previous = activate_release(args.release_root, args.release_id)
        result = inspect_release(args.release_root, args.release_id)
        print(json.dumps({'previous': previous, 'release_id': result.release_id}))
        return 0
    else:
        result = inspect_release(args.release_root, args.release_id)
    print(json.dumps({'release_id': result.release_id, 'files': result.files}))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except ReleaseError as exc:
        raise SystemExit(f'error: {exc}')
