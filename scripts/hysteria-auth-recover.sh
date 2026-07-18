#!/usr/bin/env bash
set -Eeuo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH

readonly ACTION="${1:-recover}"
readonly SERVER_UNIT="hysteria-server.service"
readonly AUTH_UNIT="hysteria-auth.service"
readonly TEST_MODE="${HY2_AUTH_INTENT_TEST_MODE:-0}"
readonly SYSTEMCTL_BIN="${HY2_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
readonly INTENT_DIR="${HY2_AUTH_INTENT_DIR:-/run/hy2-auth-intent}"

die() {
  printf '[x] %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Must run as root."
[[ "$TEST_MODE" == 0 || "$TEST_MODE" == 1 ]] ||
  die "HY2_AUTH_INTENT_TEST_MODE must be 0 or 1."

if [[ "$TEST_MODE" == 0 ]]; then
  [[ ! -v HY2_SYSTEMCTL_BIN && ! -v HY2_AUTH_INTENT_DIR ]] ||
    die "Auth intent path and command overrides are test-only."
else
  readonly TEST_ROOT="${HY2_AUTH_INTENT_TEST_ROOT:-}"
  [[ -n "$TEST_ROOT" ]] || die "Auth intent tests require a test root."
  /usr/bin/python3 - "$TEST_ROOT" "$SYSTEMCTL_BIN" "$INTENT_DIR" <<'PY' ||
import os
import stat
import sys

root, *paths = sys.argv[1:]
if not os.path.isabs(root) or os.path.normpath(root) != root:
    raise SystemExit("test root must be canonical and absolute")
metadata = os.lstat(root)
if (
    not stat.S_ISDIR(metadata.st_mode)
    or stat.S_ISLNK(metadata.st_mode)
    or metadata.st_uid != 0
    or stat.S_IMODE(metadata.st_mode) & 0o077
):
    raise SystemExit("test root must be root-owned and root-only")
real_root = os.path.realpath(root)
for path in paths:
    if not os.path.isabs(path):
        raise SystemExit("test override paths must be absolute")
    try:
        contained = os.path.commonpath(
            (real_root, os.path.realpath(path))
        ) == real_root
    except ValueError:
        contained = False
    if not contained:
        raise SystemExit("test override escapes the isolated root")
PY
  die "Auth intent test overrides are unsafe."
fi

marker_action() {
  /usr/bin/python3 - "$INTENT_DIR" "$1" <<'PY'
import os
import stat
import sys

directory, action = sys.argv[1:]
if (
    not os.path.isabs(directory)
    or os.path.normpath(directory) != directory
    or directory == "/"
):
    raise SystemExit("auth intent directory must be canonical and absolute")
parent = os.path.dirname(directory)
basename = os.path.basename(directory)
directory_flags = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
parent_fd = os.open(parent, directory_flags)
directory_fd = -1
try:
    parent_meta = os.fstat(parent_fd)
    if not stat.S_ISDIR(parent_meta.st_mode) or parent_meta.st_uid != 0:
        raise RuntimeError("unsafe auth intent parent")
    if action == "mark":
        try:
            os.mkdir(basename, 0o700, dir_fd=parent_fd)
        except FileExistsError:
            pass
    try:
        directory_fd = os.open(
            basename,
            directory_flags,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        if action in {"check", "clear"}:
            raise SystemExit(10)
        raise
    directory_meta = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(directory_meta.st_mode)
        or directory_meta.st_uid != 0
        or stat.S_IMODE(directory_meta.st_mode) != 0o700
    ):
        raise RuntimeError("unsafe auth intent directory")

    marker_name = "server-wanted"
    marker_flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    if action == "mark":
        temporary = f".server-wanted.{os.getpid()}.tmp"
        fd = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=directory_fd,
        )
        try:
            os.write(fd, b"wanted\n")
            os.fchmod(fd, 0o600)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.replace(
                temporary,
                marker_name,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            os.fsync(directory_fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        raise SystemExit(0)
    if action == "clear":
        try:
            os.unlink(marker_name, dir_fd=directory_fd)
        except FileNotFoundError:
            raise SystemExit(10)
        os.fsync(directory_fd)
        raise SystemExit(0)
    if action != "check":
        raise RuntimeError("invalid auth intent action")

    try:
        fd = os.open(marker_name, marker_flags, dir_fd=directory_fd)
    except FileNotFoundError:
        raise SystemExit(10)
    try:
        marker_meta = os.fstat(fd)
        payload = os.read(fd, 16)
        if (
            not stat.S_ISREG(marker_meta.st_mode)
            or marker_meta.st_uid != 0
            or marker_meta.st_nlink != 1
            or stat.S_IMODE(marker_meta.st_mode) != 0o600
            or payload != b"wanted\n"
        ):
            raise RuntimeError("unsafe auth intent marker")
    finally:
        os.close(fd)
finally:
    if directory_fd >= 0:
        os.close(directory_fd)
    os.close(parent_fd)
PY
}

case "$ACTION" in
  mark)
    marker_action mark
    ;;
  clear-if-manual)
    # ExecStopPost reports "success" for an operator stop. During an auth
    # outage/restart the auth unit is failed or deactivating, so preserve the
    # marker and let recovery restore only a server that was actually wanted.
    if [[ "${SERVICE_RESULT:-}" == "success" ]] &&
      "$SYSTEMCTL_BIN" is-active --quiet "$AUTH_UNIT"; then
      if marker_action clear; then
        :
      else
        rc=$?
        (( rc == 10 )) || exit "$rc"
      fi
    fi
    ;;
  recover)
    if marker_action check; then
      if "$SYSTEMCTL_BIN" is-enabled --quiet "$SERVER_UNIT"; then
        exec "$SYSTEMCTL_BIN" --no-block start "$SERVER_UNIT"
      fi
    else
      rc=$?
      (( rc == 10 )) || exit "$rc"
    fi
    ;;
  *)
    die "Usage: hysteria-auth-recover.sh {mark|clear-if-manual|recover}"
    ;;
esac
