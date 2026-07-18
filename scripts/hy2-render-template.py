#!/usr/bin/env python3
"""Render deployment templates without shell or sed replacement semantics.

Replacement values are read only from the process environment so credentials
never appear in argv.  The destination is replaced atomically after every
placeholder and value has been validated.
"""

from __future__ import annotations

import os
import re
import stat
import sys
import tempfile
from pathlib import Path


PLACEHOLDER_TO_ENV = {
    "__HY_API_SECRET__": "HY_API_SECRET",
    "__HY_OBFS_PASSWORD__": "HY_OBFS_PASSWORD",
    "__HY_SERVER_HOST__": "HY_SERVER_HOST",
    "__HY_DISPLAY_MULTIPLIER__": "HY_DISPLAY_MULTIPLIER",
    "__XRAY_REALITY_PRIVATE_KEY__": "XRAY_REALITY_PRIVATE_KEY",
    "__XRAY_REALITY_PUBLIC_KEY__": "XRAY_REALITY_PUBLIC_KEY",
    "__XRAY_REALITY_SHORT_ID__": "XRAY_REALITY_SHORT_ID",
}
# Deployment placeholders are deliberately all-uppercase. Restricting the
# grammar keeps ordinary Python dunder names such as ``__name__`` and
# ``__file__`` from being mistaken for unresolved template fields.
PLACEHOLDER_RE = re.compile(r"__[A-Z][A-Z0-9_]*__")
DOTENV_KEYS = frozenset({
    "HY_SERVER_HOST",
    "HY_API_SECRET",
    "HY_OBFS_PASSWORD",
    "HY_DISPLAY_MULTIPLIER",
    "HY_HYSTERIA_VERSION",
    "HY_XRAY_VERSION",
    "XRAY_REALITY_PRIVATE_KEY",
    "XRAY_REALITY_PUBLIC_KEY",
    "XRAY_REALITY_SHORT_ID",
    "HY_ENABLE_HTTPS",
    "HY_CERTBOT_EMAIL",
    "HY_HTTPS_PORT",
})
DOTENV_IGNORED_LEGACY_KEYS = frozenset({
    # Xray credentials are now generated per user. Accept and discard the old
    # global value so an existing secure .env can upgrade without a hard stop.
    "XRAY_CLIENT_UUID",
})
DOTENV_MAX_BYTES = 64 * 1024
SAFE_EXEC_ENVIRONMENT = {
    # Keep the deployment re-exec independent from the invoking account's
    # shell, Python, dynamic-loader, proxy, and tracing configuration.  Every
    # executable used by deploy.sh is resolved from this fixed root-owned path.
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "HOME": "/root",
    "USER": "root",
    "LOGNAME": "root",
    "SHELL": "/bin/bash",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
# Bash creates these after exec even when its input environment is minimal.
# Their values are runtime metadata, not configuration authority.
BASH_RUNTIME_ENV_KEYS = frozenset({
    "PWD",
    "OLDPWD",
    "SHLVL",
    "_",
    # Added only by the hardened lock executor, then revalidated against its
    # inherited descriptor, pathname inode, and kernel flock in deploy.sh.
    "HY2_DEPLOY_LOCK_MARKER",
    "HY2_HTTPS_LOCK_MARKER",
})


class RenderError(RuntimeError):
    """A validation or I/O failure safe to report without credential data."""


def _replacement_values() -> dict[str, str]:
    replacements: dict[str, str] = {}
    for placeholder, env_name in PLACEHOLDER_TO_ENV.items():
        if env_name not in os.environ:
            raise RenderError(f"required environment variable is missing: {env_name}")
        value = os.environ[env_name]
        if "\x00" in value or "\n" in value or "\r" in value:
            raise RenderError(
                f"template value must be a single line without NUL: {env_name}"
            )
        replacements[placeholder] = value
    return replacements


def validate_environment() -> None:
    """Validate all values before deployment performs any mutable operation."""

    _replacement_values()


def validate_env_file(path: Path) -> None:
    """Validate the strict, non-executable dotenv input."""

    parse_env_file(path)


def verify_exec_environment(path: Path) -> None:
    """Prove the re-exec environment came exclusively from ``path``.

    ``HY2_DEPLOY_ENV_LOADED`` is intentionally only a loop-prevention marker;
    callers can set environment variables before invoking deploy.sh.  Exact
    comparison here prevents a forged marker from bypassing the parser or
    filling a key omitted from the dotenv file.
    """

    if os.environ.get("HY2_DEPLOY_ENV_LOADED") != "1":
        raise RenderError("deployment environment marker is invalid")
    expected = parse_env_file(path)
    missing = object()
    for key in DOTENV_KEYS:
        if os.environ.get(key, missing) != expected.get(key, missing):
            raise RenderError(
                f"deployment environment does not match the env file: {key}"
            )
    if any(key in os.environ for key in DOTENV_IGNORED_LEGACY_KEYS):
        raise RenderError(
            "deployment environment retained an ignored legacy key"
        )
    allowed = (
        DOTENV_KEYS
        | frozenset(SAFE_EXEC_ENVIRONMENT)
        | BASH_RUNTIME_ENV_KEYS
        | {"HY2_DEPLOY_ENV_LOADED"}
    )
    unexpected = set(os.environ) - allowed
    if unexpected:
        # Never echo attacker-controlled environment names: names can contain
        # terminal control sequences and may themselves carry sensitive data.
        raise RenderError("deployment environment contains unsupported keys")
    for key, value in SAFE_EXEC_ENVIRONMENT.items():
        if os.environ.get(key) != value:
            raise RenderError("deployment environment baseline is invalid")


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a small literal dotenv subset without shell evaluation.

    Values are split at the first ``=`` and never expanded, unescaped, or
    executed. Matching single or double outer quotes are removed only as a
    compatibility convenience; their contents remain byte-for-byte literal.
    Inline comments are intentionally not special.
    """

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        fd = os.open(path, flags)
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RenderError("environment file must be a regular file")
        if stat.S_IMODE(metadata.st_mode) & 0o077:
            raise RenderError(
                "environment file permissions are too broad; require mode 0600"
            )
        if metadata.st_size > DOTENV_MAX_BYTES:
            raise RenderError("environment file exceeds the 64 KiB limit")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(DOTENV_MAX_BYTES + 1)
    except RenderError:
        raise
    except OSError as exc:
        raise RenderError("could not read the environment file") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    if len(raw) > DOTENV_MAX_BYTES:
        raise RenderError("environment file exceeds the 64 KiB limit")
    if b"\x00" in raw:
        raise RenderError("environment file contains a NUL byte")
    if b"\r" in raw:
        raise RenderError("environment file contains a carriage return")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError("environment file is not valid UTF-8") from exc

    values: dict[str, str] = {}
    seen: set[str] = set()
    for line_number, line in enumerate(text.split("\n"), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        assignment = line.lstrip()
        if assignment.startswith("export "):
            assignment = assignment[7:]
        if "=" not in assignment:
            raise RenderError(
                f"invalid environment assignment on line {line_number}"
            )
        key, value = assignment.split("=", 1)
        key = key.strip()
        if key not in DOTENV_KEYS | DOTENV_IGNORED_LEGACY_KEYS:
            raise RenderError(
                f"unsupported environment key on line {line_number}"
            )
        if key in seen:
            raise RenderError(
                f"duplicate environment key on line {line_number}: {key}"
            )
        seen.add(key)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        elif value[:1] in "'\"" or value[-1:] in "'\"":
            raise RenderError(
                f"unbalanced environment quotes on line {line_number}"
            )
        if key in DOTENV_KEYS:
            values[key] = value
    return values


def exec_with_env_file(path: Path, command: list[str]) -> None:
    """Replace this process with ``command`` under the parsed environment."""

    if not command:
        raise RenderError("missing command for environment re-exec")
    if not os.path.isabs(command[0]):
        raise RenderError("environment re-exec command must be absolute")
    values = parse_env_file(path)
    # Do not copy the parent environment. In particular BASH_ENV, SHELLOPTS,
    # BASHOPTS, PS4/BASH_XTRACEFD, PYTHON*, LD_*, exported shell functions,
    # proxy variables, and test-only HY2_* overrides must never reach the
    # credential-bearing deployment shell.
    environment = dict(SAFE_EXEC_ENVIRONMENT)
    environment.update(values)
    environment["HY2_DEPLOY_ENV_LOADED"] = "1"
    try:
        os.execve(command[0], command, environment)
    except OSError as exc:
        raise RenderError("could not execute deployment with parsed environment") from exc


def _read_template(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RenderError("could not read the source template") from exc
    if b"\x00" in raw:
        raise RenderError("source template contains a NUL byte")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RenderError("source template is not valid UTF-8") from exc


def _render_text(source: str, replacements: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        placeholder = match.group(0)
        try:
            return replacements[placeholder]
        except KeyError as exc:
            raise RenderError(
                "source template contains unknown placeholder(s)"
            ) from exc

    # Substitute only tokens found in the source. Replacement values are
    # opaque literals; scanning the rendered output would incorrectly treat a
    # secret containing text such as ``__LEFTOVER__`` as a new placeholder.
    rendered = PLACEHOLDER_RE.sub(replace, source)
    if "\x00" in rendered:
        raise RenderError("rendered output contains a NUL byte")
    return rendered


def render_template(source: Path, destination: Path) -> None:
    replacements = _replacement_values()
    rendered = _render_text(_read_template(source), replacements)
    parent = destination.parent
    staged_path: Path | None = None
    fd = -1
    try:
        fd, raw_staged = tempfile.mkstemp(
            prefix=f".{destination.name}.deploy.",
            dir=parent,
        )
        staged_path = Path(raw_staged)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as staged:
            fd = -1
            staged.write(rendered)
            staged.flush()
            os.fsync(staged.fileno())
        os.replace(staged_path, destination)
        staged_path = None
        dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except (OSError, UnicodeError) as exc:
        raise RenderError("could not atomically write the rendered template") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if staged_path is not None:
            try:
                staged_path.unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _usage() -> str:
    return (
        "usage: hy2-render-template.py --validate-env-file PATH | "
        "--verify-exec-env-file PATH | "
        "--validate-environment | --exec-env-file PATH COMMAND [ARG ...] | "
        "SOURCE DESTINATION"
    )


def main(argv: list[str]) -> int:
    try:
        if len(argv) == 3 and argv[1] == "--validate-env-file":
            validate_env_file(Path(argv[2]))
        elif len(argv) == 3 and argv[1] == "--verify-exec-env-file":
            verify_exec_environment(Path(argv[2]))
        elif len(argv) == 2 and argv[1] == "--validate-environment":
            validate_environment()
        elif len(argv) >= 4 and argv[1] == "--exec-env-file":
            exec_with_env_file(Path(argv[2]), argv[3:])
        elif len(argv) == 3 and not argv[1].startswith("-"):
            render_template(Path(argv[1]), Path(argv[2]))
        else:
            print(_usage(), file=sys.stderr)
            return 2
    except RenderError as exc:
        print(f"template rendering rejected: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
