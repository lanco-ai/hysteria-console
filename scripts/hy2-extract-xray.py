#!/usr/bin/python3
"""Validate and extract the repository-pinned Xray release archive.

Only the three runtime files are materialized. Archive identity, member names,
types, sizes, and runtime-file digests are all checked before deploy may
execute or install any archive content.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import PurePosixPath
import stat
import sys
import zipfile


MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 96 * 1024 * 1024
EXPECTED_MEMBERS = {
    "xray": (64 * 1024 * 1024, 0o755),
    "geoip.dat": (32 * 1024 * 1024, 0o644),
    "geosite.dat": (32 * 1024 * 1024, 0o644),
    "README.md": (1024 * 1024, None),
    "LICENSE": (1024 * 1024, None),
}
RUNTIME_MEMBERS = {"xray", "geoip.dat", "geosite.dat"}
CHUNK_SIZE = 1024 * 1024


def valid_sha256(value: str) -> str:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise argparse.ArgumentTypeError("expected a lowercase SHA-256 digest")
    return value


def hash_open_file(fd: int, *, limit: int) -> str:
    digest = hashlib.sha256()
    total = 0
    while True:
        chunk = os.read(fd, CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("file exceeds its byte limit")
        digest.update(chunk)
    return digest.hexdigest()


def validate_member_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or "\x00" in name
        or "\\" in name
        or path.is_absolute()
        or name != path.as_posix()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError(f"unsafe ZIP member name: {name!r}")


def open_archive(path: str, expected_digest: str) -> tuple[int, os.stat_result]:
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_nlink != 1
        or metadata.st_size > MAX_ARCHIVE_BYTES
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise ValueError("unsafe Xray archive metadata")
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    opened = os.fstat(fd)
    if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
        os.close(fd)
        raise ValueError("Xray archive changed while opening")
    try:
        actual_digest = hash_open_file(fd, limit=MAX_ARCHIVE_BYTES)
        if actual_digest != expected_digest:
            raise ValueError("Xray archive SHA-256 mismatch")
        os.lseek(fd, 0, os.SEEK_SET)
    except Exception:
        os.close(fd)
        raise
    return fd, opened


def validate_output_directory(path: str) -> int:
    metadata = os.lstat(path)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("output directory must be private and caller-owned")
    return os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )


def validate_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    for info in archive.infolist():
        validate_member_name(info.filename)
        if info.filename in members:
            raise ValueError(f"duplicate ZIP member: {info.filename}")
        if info.filename not in EXPECTED_MEMBERS:
            raise ValueError(f"unexpected ZIP member: {info.filename}")
        unix_mode = info.external_attr >> 16
        if info.create_system != 3 or not stat.S_ISREG(unix_mode):
            raise ValueError(f"ZIP member is not a Unix regular file: {info.filename}")
        byte_limit, _mode = EXPECTED_MEMBERS[info.filename]
        if info.file_size < 0 or info.file_size > byte_limit:
            raise ValueError(f"ZIP member exceeds its byte limit: {info.filename}")
        if info.compress_size < 0:
            raise ValueError(f"invalid compressed size: {info.filename}")
        total += info.file_size
        if total > MAX_TOTAL_UNCOMPRESSED_BYTES:
            raise ValueError("Xray archive exceeds the total extraction limit")
        members[info.filename] = info
    if set(members) != set(EXPECTED_MEMBERS):
        missing = sorted(set(EXPECTED_MEMBERS) - set(members))
        raise ValueError(f"Xray archive is missing members: {', '.join(missing)}")
    return members


def extract_runtime_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    output_fd: int,
    expected_digest: str,
) -> None:
    byte_limit, output_mode = EXPECTED_MEMBERS[info.filename]
    assert output_mode is not None
    destination_fd = os.open(
        info.filename,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
        dir_fd=output_fd,
    )
    digest = hashlib.sha256()
    total = 0
    complete = False
    try:
        with archive.open(info, "r") as source:
            while True:
                chunk = source.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > byte_limit or total > info.file_size:
                    raise ValueError(
                        f"ZIP member expanded beyond its limit: {info.filename}"
                    )
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
                digest.update(chunk)
        if total != info.file_size:
            raise ValueError(f"ZIP member size mismatch: {info.filename}")
        if digest.hexdigest() != expected_digest:
            raise ValueError(f"ZIP member SHA-256 mismatch: {info.filename}")
        os.fchmod(destination_fd, output_mode)
        os.fsync(destination_fd)
        complete = True
    finally:
        os.close(destination_fd)
        if not complete:
            try:
                os.unlink(info.filename, dir_fd=output_fd)
            except FileNotFoundError:
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--archive-sha256", required=True, type=valid_sha256)
    parser.add_argument("--xray-sha256", required=True, type=valid_sha256)
    parser.add_argument("--geoip-sha256", required=True, type=valid_sha256)
    parser.add_argument("--geosite-sha256", required=True, type=valid_sha256)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    archive_fd = -1
    output_fd = -1
    created: list[str] = []
    try:
        archive_fd, _metadata = open_archive(
            args.archive, args.archive_sha256
        )
        output_fd = validate_output_directory(args.output_dir)
        with os.fdopen(os.dup(archive_fd), "rb") as archive_file:
            with zipfile.ZipFile(archive_file) as archive:
                members = validate_members(archive)
                digests = {
                    "xray": args.xray_sha256,
                    "geoip.dat": args.geoip_sha256,
                    "geosite.dat": args.geosite_sha256,
                }
                for name in sorted(RUNTIME_MEMBERS):
                    extract_runtime_member(
                        archive,
                        members[name],
                        output_fd,
                        digests[name],
                    )
                    created.append(name)
        os.fsync(output_fd)
    except (OSError, ValueError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        if output_fd >= 0:
            for name in reversed(created):
                try:
                    os.unlink(name, dir_fd=output_fd)
                except FileNotFoundError:
                    pass
            try:
                os.fsync(output_fd)
            except OSError:
                pass
        print(f"[x] Refusing Xray archive: {exc}", file=sys.stderr)
        return 1
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        if archive_fd >= 0:
            os.close(archive_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
