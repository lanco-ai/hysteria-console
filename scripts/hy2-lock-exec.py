#!/usr/bin/python3 -I
"""Acquire a hardened advisory lock and exec a command with the lock held.

The inherited marker is deliberately not treated as authority.  Verification
checks the inherited descriptor, the pathname inode, the file contract, and
the kernel lock itself before a nested command may reuse the lock.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import os
import stat
import sys
import time


DEFAULT_LOCK_FILE = "/run/hy2-locks/https-activation.lock"
DEFAULT_MARKER_ENV = "HY2_LOCK_EXEC_MARKER"
MAX_TIMEOUT_SECONDS = 300.0
MARKER_VERSION = "hy2-lock-v1"
DANGEROUS_EXEC_ENV = {
    "BASH_ENV",
    "BASH_XTRACEFD",
    "BASHOPTS",
    "CDPATH",
    "ENV",
    "GLOBIGNORE",
    "LD_AUDIT",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "PYTHONHOME",
    "PYTHONPATH",
    "PS4",
    "SHELLOPTS",
}


class LockError(RuntimeError):
    """A safe, operator-facing lock error."""


class LockBusyError(LockError):
    """The validated lock is currently owned by another process."""


def _die(message: str) -> "None":
    print(f"[x] {message}", file=sys.stderr)
    raise SystemExit(1)


def _canonical_lock_path(raw_path: str) -> str:
    if not raw_path or not os.path.isabs(raw_path):
        raise LockError("Lock path must be absolute.")
    normalized = os.path.normpath(raw_path)
    if normalized != raw_path or normalized == "/":
        raise LockError("Lock path must already be in canonical absolute form.")
    if "\x00" in raw_path:
        raise LockError("Lock path contains an invalid character.")
    return normalized


def _validate_root_owned_directory(fd: int, description: str) -> os.stat_result:
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise LockError(f"{description} is not a directory.")
    if metadata.st_uid != 0:
        raise LockError(f"{description} must be owned by root.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise LockError(
            f"{description} must be root-only (mode 0700 or stricter)."
        )
    return metadata


def _open_parent_directory(lock_path: str) -> int:
    parent = os.path.dirname(lock_path)
    parent_parent = os.path.dirname(parent)
    parent_name = os.path.basename(parent)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )

    try:
        parent_fd = os.open(parent, directory_flags)
    except FileNotFoundError:
        # Create exactly the final parent component.  We intentionally do not
        # recursively create or follow a caller-controlled directory chain.
        try:
            ancestor_fd = os.open(parent_parent, directory_flags)
        except OSError as exc:
            raise LockError(
                "Lock parent cannot be created beneath a safe directory."
            ) from exc
        try:
            ancestor = os.fstat(ancestor_fd)
            if not stat.S_ISDIR(ancestor.st_mode) or ancestor.st_uid != 0:
                raise LockError(
                    "Lock parent ancestor must be a root-owned directory."
                )
            try:
                os.mkdir(parent_name, 0o700, dir_fd=ancestor_fd)
            except FileExistsError:
                pass
            parent_fd = os.open(parent_name, directory_flags, dir_fd=ancestor_fd)
        finally:
            os.close(ancestor_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise LockError("Lock parent must not be a symlink.") from exc
        raise LockError("Unable to open the lock parent directory.") from exc

    try:
        _validate_root_owned_directory(parent_fd, "Lock parent directory")
    except BaseException:
        os.close(parent_fd)
        raise
    return parent_fd


def _validate_lock_fd(fd: int) -> os.stat_result:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise LockError("Lock path must be a regular file.")
    if metadata.st_uid != 0:
        raise LockError("Lock file must be owned by root.")
    if metadata.st_nlink != 1:
        raise LockError("Lock file must have exactly one hard link.")
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise LockError("Lock file mode must be exactly 0600.")
    return metadata


def _open_lock(parent_fd: int, lock_path: str, *, create: bool) -> int:
    basename = os.path.basename(lock_path)
    flags = (
        os.O_RDWR
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    created = False
    try:
        if create:
            try:
                fd = os.open(
                    basename,
                    flags | os.O_CREAT | os.O_EXCL,
                    0o600,
                    dir_fd=parent_fd,
                )
                created = True
            except FileExistsError:
                fd = os.open(basename, flags, dir_fd=parent_fd)
        else:
            fd = os.open(basename, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise LockError("Lock path must not be a symlink.") from exc
        raise LockError("Unable to open the lock file safely.") from exc
    try:
        if created:
            # Do not let a restrictive inherited umask silently violate the
            # stable 0600 contract.  Existing files are never auto-repaired.
            os.fchmod(fd, 0o600)
        _validate_lock_fd(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _acquire_bounded(fd: int, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockBusyError(
                    "Another operation already holds the requested lock."
                )
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
        except InterruptedError:
            if time.monotonic() >= deadline:
                raise LockError("Timed out while acquiring the requested lock.")


def _acquire_until_available(fd: int) -> None:
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            return
        except InterruptedError:
            continue


def _path_digest(lock_path: str) -> str:
    return hashlib.sha256(lock_path.encode("utf-8")).hexdigest()


def _make_marker(fd: int, metadata: os.stat_result, lock_path: str) -> str:
    return ":".join(
        (
            MARKER_VERSION,
            str(fd),
            str(metadata.st_dev),
            str(metadata.st_ino),
            _path_digest(lock_path),
        )
    )


def _parse_marker(raw_marker: str) -> tuple[int, int, int, str]:
    parts = raw_marker.split(":")
    if len(parts) != 5 or parts[0] != MARKER_VERSION:
        raise LockError("Inherited lock marker has an invalid format.")
    try:
        fd = int(parts[1], 10)
        device = int(parts[2], 10)
        inode = int(parts[3], 10)
    except ValueError as exc:
        raise LockError("Inherited lock marker is malformed.") from exc
    if fd < 3 or device < 0 or inode <= 0 or len(parts[4]) != 64:
        raise LockError("Inherited lock marker is malformed.")
    return fd, device, inode, parts[4]


def _verify_inherited(
    lock_path: str,
    marker: str,
) -> tuple[int, os.stat_result]:
    fd, expected_device, expected_inode, expected_digest = _parse_marker(marker)
    if expected_digest != _path_digest(lock_path):
        raise LockError("Inherited lock marker belongs to a different path.")

    try:
        inherited = _validate_lock_fd(fd)
    except OSError as exc:
        raise LockError("Inherited lock descriptor is not open.") from exc
    if (inherited.st_dev, inherited.st_ino) != (
        expected_device,
        expected_inode,
    ):
        raise LockError("Inherited lock descriptor identity changed.")

    parent_fd = _open_parent_directory(lock_path)
    try:
        path_fd = _open_lock(parent_fd, lock_path, create=False)
    finally:
        os.close(parent_fd)
    try:
        current = _validate_lock_fd(path_fd)
        if (current.st_dev, current.st_ino) != (
            inherited.st_dev,
            inherited.st_ino,
        ):
            raise LockError("Inherited lock no longer matches the lock path.")

        # Reassert a nonblocking exclusive lock on the inherited open-file
        # description.  A forged marker therefore cannot bypass acquisition.
        _acquire_bounded(fd, 0.0)

        # An independent open must be excluded.  This verifies that the
        # inherited descriptor now owns the kernel lock, rather than trusting
        # environment state alone.
        try:
            fcntl.flock(path_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pass
        else:
            fcntl.flock(path_fd, fcntl.LOCK_UN)
            raise LockError("Inherited descriptor does not hold the kernel lock.")
    finally:
        os.close(path_fd)

    return fd, inherited


def _timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be numeric") from exc
    if not (0.0 <= timeout <= MAX_TIMEOUT_SECONDS):
        raise argparse.ArgumentTypeError(
            f"timeout must be between 0 and {MAX_TIMEOUT_SECONDS:g} seconds"
        )
    return timeout


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely hold a root-only advisory lock across exec.",
    )
    parser.add_argument("--lock-file", default=DEFAULT_LOCK_FILE)
    parser.add_argument("--timeout", type=_timeout, default=5.0)
    parser.add_argument(
        "--wait",
        action="store_true",
        help="wait until the validated lock becomes available",
    )
    parser.add_argument("--marker-env", default=DEFAULT_MARKER_ENV)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="verify an inherited lock and exit without executing a command",
    )
    parser.add_argument(
        "--success-if-locked",
        action="store_true",
        help=(
            "exit successfully without executing the command when another "
            "process owns the validated lock"
        ),
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.marker_env.isidentifier() or not args.marker_env.isupper():
        parser.error("marker environment name must be an uppercase identifier")
    if args.verify and args.command:
        parser.error("--verify does not accept a command")
    if args.verify and args.success_if_locked:
        parser.error("--verify cannot be combined with --success-if-locked")
    if args.verify and args.wait:
        parser.error("--verify cannot be combined with --wait")
    if args.wait and args.success_if_locked:
        parser.error("--wait cannot be combined with --success-if-locked")
    if args.success_if_locked and args.timeout != 0:
        parser.error("--success-if-locked requires --timeout 0")
    if not args.verify and not args.command:
        parser.error("a command is required unless --verify is used")
    return args


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        _die("Lock helper must run as root.")
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        lock_path = _canonical_lock_path(args.lock_file)
        inherited_marker = os.environ.get(args.marker_env, "")
        if args.verify and not inherited_marker:
            raise LockError("--verify requires an inherited lock marker.")
        if inherited_marker:
            fd, metadata = _verify_inherited(lock_path, inherited_marker)
        else:
            parent_fd = _open_parent_directory(lock_path)
            try:
                fd = _open_lock(parent_fd, lock_path, create=True)
            finally:
                os.close(parent_fd)
            try:
                if args.wait:
                    _acquire_until_available(fd)
                else:
                    try:
                        _acquire_bounded(fd, args.timeout)
                    except LockBusyError:
                        if args.success_if_locked:
                            os.close(fd)
                            print(
                                "[i] Lock is held by the authoritative "
                                "operation; command skipped."
                            )
                            return 0
                        raise
                metadata = _validate_lock_fd(fd)
            except BaseException:
                os.close(fd)
                raise

        marker = _make_marker(fd, metadata, lock_path)
        if args.verify:
            return 0

        os.set_inheritable(fd, True)
        environment = os.environ.copy()
        for variable in DANGEROUS_EXEC_ENV:
            environment.pop(variable, None)
        for variable in tuple(environment):
            if variable.startswith("BASH_FUNC_"):
                environment.pop(variable, None)
        environment[args.marker_env] = marker
        os.execvpe(args.command[0], args.command, environment)
        return 127
    except LockError as exc:
        _die(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
