#!/usr/bin/python3 -I
"""Durable write-ahead journal for the outer hy2 deployment.

The deploy shell remains responsible for ordering its work and for holding the
global deployment lock.  This helper owns the persistent recovery protocol:

* ``prepare`` durably records pre-deploy runtime state.
* ``snapshot`` freezes an explicit, validated artifact allowlist.
* ``before``/``after`` bracket an externally performed atomic replacement.
* ``replace``/``remove`` provide helper-owned mutation shortcuts.
* ``complete`` durably commits the transaction before removing its residue.
* ``recover`` is idempotent and refuses to overwrite an unknown generation.

All recovery decisions are compare-and-swap decisions.  A destination is
restored only when it is still either the snapshotted original generation or
the exact replacement generation recorded before the mutation.
"""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, NoReturn


MANIFEST_VERSION = 1
DEFAULT_RECOVERY_ROOT = "/var/lib/hysteria/deploy-recovery"
MANIFEST_NAME = "manifest.json"
PENDING_NAME = "pending"
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
VALID_STATES = {
    "preparing",
    "active",
    "restoring",
    "recovery-failed",
    "restored",
    "complete",
}
VALID_ENABLE_STATES = {
    "enabled",
    "enabled-runtime",
    "masked",
    "masked-runtime",
    "disabled",
    "not-found",
    "static",
    "indirect",
    "generated",
    "transient",
    "linked",
    "linked-runtime",
    "alias",
}
UNIT_RE = re.compile(r"[A-Za-z0-9_.@:-]+\.(?:service|timer)")
SYSCTL_RE = re.compile(r"[a-z0-9_]+(?:\.[a-z0-9_]+)+")
TXID_RE = re.compile(r"[0-9a-f]{32}")
HEX64_RE = re.compile(r"[0-9a-f]{64}")
BOOT_ID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}"
)
SNAPSHOT_RE = re.compile(r"snapshot-[0-9]{4}")
SELF_UNIT = "hy2-deploy-recovery.service"
DEFERRED_RESTART_UNITS = {"systemd-journald.service"}


# Production destinations remain deliberately narrow.  The manifest is
# root-only, but recovery still rejects a corrupt journal that points at an
# unrelated system file.
EXACT_ALLOWED_PATHS = {
    "/usr/local/bin/hysteria",
    "/usr/local/bin/xray",
    "/usr/local/bin/tuic-server",
    "/usr/local/share/xray/geoip.dat",
    "/usr/local/share/xray/geosite.dat",
    "/usr/local/sbin/hysteria-porthop.sh",
    "/usr/local/sbin/hysteria-tcp-mss.sh",
    "/usr/local/sbin/hysteria-auth-recover.sh",
    "/usr/local/sbin/hy2-backup.sh",
    "/usr/local/sbin/hy2-backup-git.sh",
    "/usr/local/sbin/hy2-restore-check.sh",
    "/usr/local/sbin/hy2-lock-exec.py",
    "/usr/local/sbin/hy2-enable-https.sh",
    "/usr/local/sbin/hy2-deploy-recovery.py",
    "/usr/local/sbin/hy2-cert-renew-hook.sh",
    "/usr/local/sbin/hy2-health-check.sh",
    "/usr/local/share/hy2/hysteria-panel-log.conf",
    "/usr/local/share/hy2/hysteria-panel-https.conf",
    "/usr/local/share/hy2/hysteria-panel-redirect.conf",
    "/usr/local/share/hy2/hy2-cert-renew-hook.sh",
    "/etc/logrotate.d/xray",
    "/etc/sysctl.d/99-hysteria-udp.conf",
    "/etc/modules-load.d/tcp-bbr.conf",
    "/etc/nginx/conf.d/hysteria-panel-log.conf",
    "/etc/nginx/sites-available/hysteria-panel.conf",
    "/etc/nginx/sites-enabled/hysteria-panel.conf",
    "/etc/nginx/sites-enabled/hysteria-panel-https.conf",
    "/etc/nginx/sites-enabled/default",
    "/etc/systemd/system/hysteria-server.service",
    "/etc/systemd/system/hysteria-auth.service",
    "/etc/systemd/system/hysteria-subscription.service",
    "/etc/systemd/system/hysteria-traffic-limiter.service",
    "/etc/systemd/system/hysteria-traffic-limiter.timer",
    "/etc/systemd/system/codex-quota-collector.service",
    "/etc/systemd/system/codex-quota-collector.timer",
    "/etc/systemd/system/hy2-backup.service",
    "/etc/systemd/system/hy2-backup.timer",
    "/etc/systemd/system/hysteria-porthop.service",
    "/etc/systemd/system/hysteria-tcp-mss.service",
    "/etc/systemd/system/tuic-server.service",
    "/etc/systemd/system/hy2-health-check.service",
    "/etc/systemd/system/hy2-health-check.timer",
    "/etc/systemd/system/hy2-https-recovery.service",
    "/etc/systemd/system/hy2-deploy-recovery.service",
    "/etc/systemd/system/hy2-deploy-watchdog.service",
    "/etc/systemd/system/xray.service",
    "/etc/systemd/system/xray@.service",
    "/etc/systemd/system/xray.service.d/10-donot_touch_single_conf.conf",
    "/etc/systemd/system/xray.service.d/10-donot_touch_multi_conf.conf",
    "/etc/systemd/system/xray.service.d/20-hy2-hardening.conf",
    "/etc/systemd/system/xray@.service.d/10-donot_touch_single_conf.conf",
    "/etc/systemd/system/xray@.service.d/10-donot_touch_multi_conf.conf",
    "/etc/fail2ban/filter.d/tuic-auth.conf",
    "/etc/fail2ban/jail.d/tuic-auth.conf",
    "/etc/systemd/journald.conf.d/60-hy2-limits.conf",
    "/root/hysteria/api_secret",
    "/root/hysteria/config.yaml",
    "/root/hysteria/auth_backend.py",
    "/root/hysteria/auth_service.py",
    "/root/hysteria/subscription_service.py",
    "/root/hysteria/traffic_limiter.py",
    "/root/hysteria/alerts.py",
    "/root/hysteria/anomaly.py",
    "/root/hysteria/charts.py",
    "/root/hysteria/codex_dashboard.py",
    "/root/hysteria/codex_quota.py",
    "/root/hysteria/cost_calibrator.py",
    "/root/hysteria/cycle.py",
    "/root/hysteria/health.py",
    "/root/hysteria/health_widgets.py",
    "/root/hysteria/http_utils.py",
    "/root/hysteria/incident_console.py",
    "/root/hysteria/online_snapshot.py",
    "/root/hysteria/revocation_queue.py",
    "/root/hysteria/rotation_recovery.py",
    "/root/hysteria/static_access.py",
    "/root/hysteria/state_store.py",
    "/root/hysteria/subscription_profiles.py",
    "/root/hysteria/xray_config.py",
    "/root/hysteria/tuic_config.py",
    "/root/hysteria/tuic_meter.py",
    "/root/hysteria/usage_dashboard.py",
    "/root/hysteria/user_compat.py",
    "/root/hysteria/display.py",
    "/root/hysteria/timeutil.py",
    "/root/hysteria/admin.css",
    "/root/hysteria/admin_poll.js",
    "/root/hysteria/codex_quota.js",
    "/root/hysteria/usage.js",
    "/root/hysteria/template.yaml",
    "/root/hysteria/state/https_required",
    "/root/hysteria/server.crt",
    "/root/hysteria/server.key",
}
ALLOWED_LOG_DIRS = {
    "/var/log/xray",
}
EXACT_ALLOWED_UNITS = {
    "hy2-deploy-recovery.service",
    "hy2-https-recovery.service",
    "nginx.service",
    "hysteria-porthop.service",
    "hysteria-tcp-mss.service",
    "hysteria-auth.service",
    "hysteria-server.service",
    "hysteria-subscription.service",
    "hysteria-traffic-limiter.timer",
    "hysteria-traffic-limiter.service",
    "codex-quota-collector.timer",
    "codex-quota-collector.service",
    "hy2-backup.timer",
    "hy2-backup.service",
    "hy2-health-check.timer",
    "hy2-health-check.service",
    "xray.service",
    "tuic-server.service",
    "snap.certbot.renew.timer",
    "fail2ban.service",
    "systemd-journald.service",
}
EXACT_ALLOWED_SYSCTLS = {
    "net.core.rmem_max",
    "net.core.wmem_max",
    "net.core.rmem_default",
    "net.core.wmem_default",
    "net.core.netdev_max_backlog",
    "net.ipv4.udp_rmem_min",
    "net.ipv4.udp_wmem_min",
    "net.ipv4.tcp_mtu_probing",
    "net.core.default_qdisc",
    "net.ipv4.tcp_congestion_control",
}


class RecoveryError(RuntimeError):
    """A fail-closed, operator-facing recovery error."""


def _die(message: str) -> NoReturn:
    print(f"[x] {message}", file=sys.stderr)
    raise SystemExit(1)


def _canonical_path(raw: str, description: str) -> str:
    if not raw or not os.path.isabs(raw):
        raise RecoveryError(f"{description} must be absolute.")
    normalized = os.path.normpath(raw)
    if normalized != raw or normalized == "/" or "\x00" in raw:
        raise RecoveryError(
            f"{description} must be a canonical, non-root absolute path."
        )
    return normalized


def _test_context() -> tuple[bool, str | None]:
    raw_mode = os.environ.get("HY2_DEPLOY_RECOVERY_TEST_MODE", "0")
    if raw_mode not in {"0", "1"}:
        raise RecoveryError(
            "HY2_DEPLOY_RECOVERY_TEST_MODE must be either 0 or 1."
        )
    if raw_mode == "0":
        for name in (
            "HY2_DEPLOY_RECOVERY_TEST_ROOT",
            "HY2_DEPLOY_RECOVERY_ROOT",
            "HY2_DEPLOY_RECOVERY_BOOT_ID",
            "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
            "HY2_DEPLOY_RECOVERY_SYSCTL",
            "HY2_DEPLOY_RECOVERY_TEST_KEEP_COMPLETE",
            "HY2_DEPLOY_RECOVERY_TEST_FAIL_AFTER_RESTORE",
        ):
            if name in os.environ:
                raise RecoveryError(
                    f"{name} is accepted only by the isolated test harness."
                )
        return False, None

    raw_root = os.environ.get("HY2_DEPLOY_RECOVERY_TEST_ROOT", "")
    root = _canonical_path(raw_root, "Test root")
    metadata = os.lstat(root)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise RecoveryError(
            "Test root must be a root-owned, root-only real directory."
        )
    return True, root


def _inside(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _validate_existing_parent_chain(
    path: str,
    *,
    test_mode: bool,
    test_root: str | None,
    allow_syslog_parent: bool = False,
) -> None:
    """Reject symlinked or writable ancestors without requiring leaf parents.

    Some allowlisted destinations are absent on a fresh host, so validation
    stops at the first missing component.  Every existing component from the
    trusted boundary through the destination parent must remain a root-owned
    real directory that unprivileged users cannot replace.
    """

    boundary = test_root if test_mode else "/"
    assert boundary is not None
    parent = os.path.dirname(path)
    if test_mode and not _inside(parent, boundary):
        raise RecoveryError("Artifact parent escapes the isolated test root.")
    relative = os.path.relpath(parent, boundary)
    current = boundary
    components = [] if relative == "." else relative.split(os.sep)
    for component in components:
        current = os.path.join(current, component)
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            break
        mode = stat.S_IMODE(metadata.st_mode)
        trusted_syslog_parent = False
        # Ubuntu's rsyslog tmpfiles policy deliberately keeps /var/log
        # root:syslog 0775. Admit only that exact platform-owned parent, and
        # only while validating the one allowlisted Xray log directory.
        if (
            allow_syslog_parent
            and not test_mode
            and current == "/var/log"
            and stat.S_ISDIR(metadata.st_mode)
            and not stat.S_ISLNK(metadata.st_mode)
            and metadata.st_uid == 0
            and mode == 0o775
        ):
            try:
                trusted_syslog_parent = (
                    metadata.st_gid == grp.getgrnam("syslog").gr_gid
                )
            except KeyError:
                trusted_syslog_parent = False
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or metadata.st_uid != 0
            or (mode & 0o022 and not trusted_syslog_parent)
        ):
            raise RecoveryError(
                f"Artifact parent chain is unsafe at: {current}"
            )


def _recovery_root(test_mode: bool, test_root: str | None) -> str:
    raw = os.environ.get(
        "HY2_DEPLOY_RECOVERY_ROOT",
        (
            os.path.join(test_root, "recovery")
            if test_mode and test_root is not None
            else DEFAULT_RECOVERY_ROOT
        ),
    )
    path = _canonical_path(raw, "Recovery root")
    if test_mode:
        assert test_root is not None
        if not _inside(path, test_root) or path == test_root:
            raise RecoveryError("Recovery root escapes the isolated test root.")
    elif path != DEFAULT_RECOVERY_ROOT:
        raise RecoveryError("Production recovery root is fixed.")
    return path


def _is_allowed_artifact(
    path: str,
    *,
    test_mode: bool,
    test_root: str | None,
    recovery_root: str,
) -> bool:
    if path == recovery_root or _inside(path, recovery_root):
        return False
    if test_mode:
        assert test_root is not None
        return _inside(path, test_root) and path != test_root
    if path in EXACT_ALLOWED_PATHS:
        return True
    return False


def _validate_artifact_path(
    raw: str,
    *,
    test_mode: bool,
    test_root: str | None,
    recovery_root: str,
) -> str:
    path = _canonical_path(raw, "Artifact path")
    if not _is_allowed_artifact(
        path,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    ):
        raise RecoveryError(f"Artifact path is outside the deployment allowlist: {path}")
    _validate_existing_parent_chain(
        path,
        test_mode=test_mode,
        test_root=test_root,
    )
    return path


def _validate_log_dir(
    raw: str,
    *,
    test_mode: bool,
    test_root: str | None,
) -> str:
    path = _canonical_path(raw, "Log directory")
    if test_mode:
        assert test_root is not None
        if not _inside(path, test_root) or path == test_root:
            raise RecoveryError("Log directory escapes the isolated test root.")
    elif path not in ALLOWED_LOG_DIRS:
        raise RecoveryError(f"Unsupported deployment log directory: {path}")
    _validate_existing_parent_chain(
        path,
        test_mode=test_mode,
        test_root=test_root,
        allow_syslog_parent=True,
    )
    return path


def _safe_directory(path: str, description: str, *, root_only: bool) -> os.stat_result:
    metadata = os.lstat(path)
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise RecoveryError(f"{description} must be a real directory.")
    if metadata.st_uid != 0:
        raise RecoveryError(f"{description} must be owned by root.")
    forbidden = 0o077 if root_only else 0o022
    if stat.S_IMODE(metadata.st_mode) & forbidden:
        qualifier = "root-only" if root_only else "not group/world writable"
        raise RecoveryError(f"{description} must be {qualifier}.")
    return metadata


def _open_directory(path: str, description: str, *, root_only: bool) -> int:
    _safe_directory(path, description, root_only=root_only)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        return os.open(path, flags)
    except OSError as exc:
        raise RecoveryError(f"Could not open {description.lower()} safely.") from exc


def _fsync_directory(path: str) -> None:
    fd = _open_directory(path, "Directory", root_only=False)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_parent(path: str) -> None:
    _fsync_directory(os.path.dirname(path))


def _fsync_regular(path: str) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISREG(metadata.st_mode):
        return
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise RecoveryError(f"File changed while syncing: {path}")
        os.fsync(fd)
    finally:
        os.close(fd)


def _ensure_recovery_root(path: str) -> None:
    parent = os.path.dirname(path)
    if not os.path.exists(parent):
        ancestor = os.path.dirname(parent)
        _safe_directory(ancestor, "Recovery-root ancestor", root_only=False)
        os.mkdir(parent, 0o700)
        os.chmod(parent, 0o700)
        _fsync_directory(ancestor)
    _safe_directory(parent, "Recovery-root parent", root_only=False)
    try:
        os.mkdir(path, 0o700)
        os.chmod(path, 0o700)
        _fsync_directory(parent)
    except FileExistsError:
        pass
    _safe_directory(path, "Recovery root", root_only=True)


def _manifest_paths(recovery_root: str) -> tuple[str, str]:
    pending = os.path.join(recovery_root, PENDING_NAME)
    return pending, os.path.join(pending, MANIFEST_NAME)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise RecoveryError("Short write while persisting recovery data.")
        view = view[written:]


def _atomic_write_manifest(manifest: str, payload: dict[str, Any]) -> None:
    pending = os.path.dirname(manifest)
    _safe_directory(pending, "Pending recovery directory", root_only=True)
    encoded = (
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise RecoveryError("Deployment recovery manifest is too large.")
    temporary = os.path.join(
        pending,
        f".manifest-{os.getpid()}-{secrets.token_hex(4)}.tmp",
    )
    fd = os.open(
        temporary,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(fd, 0o600)
        _write_all(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.replace(temporary, manifest)
        _fsync_directory(pending)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _read_limited_file(path: str, *, limit: int) -> bytes:
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
    ):
        raise RecoveryError(f"Unsafe regular file: {path}")
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise RecoveryError(f"File changed while opening: {path}")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise RecoveryError(f"File exceeds the supported size: {path}")
        return b"".join(chunks)
    finally:
        os.close(fd)


def _generation_token(value: dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


ABSENT_GENERATION = _generation_token({"kind": "absent"})


def _hash_open_file(fd: int, *, size_limit: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        size += len(chunk)
        if size > size_limit:
            raise RecoveryError("Artifact exceeds the 64 MiB per-file limit.")
        digest.update(chunk)
    return digest.hexdigest(), size


def _generation(
    path: str,
    *,
    require_root_owner: bool = True,
) -> tuple[str, dict[str, Any]]:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        value = {"kind": "absent"}
        return ABSENT_GENERATION, value

    if metadata.st_nlink != 1:
        raise RecoveryError(f"Artifact must have exactly one hard link: {path}")
    if require_root_owner and metadata.st_uid != 0:
        raise RecoveryError(f"Artifact must be owned by root: {path}")
    if stat.S_ISLNK(metadata.st_mode):
        target = os.readlink(path)
        if len(target.encode("utf-8", "surrogateescape")) > 4096:
            raise RecoveryError(f"Symlink target is too long: {path}")
        value = {
            "kind": "symlink",
            "target": target,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
        }
        return _generation_token(value), value
    if not stat.S_ISREG(metadata.st_mode):
        raise RecoveryError(f"Artifact is not a regular file or symlink: {path}")
    if metadata.st_size > MAX_FILE_BYTES:
        raise RecoveryError(f"Artifact exceeds the 64 MiB per-file limit: {path}")

    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened_before = os.fstat(fd)
        if (opened_before.st_dev, opened_before.st_ino) != (
            metadata.st_dev,
            metadata.st_ino,
        ):
            raise RecoveryError(f"Artifact changed while opening: {path}")
        digest, size = _hash_open_file(fd, size_limit=MAX_FILE_BYTES)
        opened_after = os.fstat(fd)
    finally:
        os.close(fd)
    stable_fields_before = (
        opened_before.st_dev,
        opened_before.st_ino,
        opened_before.st_size,
        opened_before.st_mtime_ns,
        opened_before.st_ctime_ns,
        stat.S_IMODE(opened_before.st_mode),
        opened_before.st_uid,
        opened_before.st_gid,
        opened_before.st_nlink,
    )
    stable_fields_after = (
        opened_after.st_dev,
        opened_after.st_ino,
        opened_after.st_size,
        opened_after.st_mtime_ns,
        opened_after.st_ctime_ns,
        stat.S_IMODE(opened_after.st_mode),
        opened_after.st_uid,
        opened_after.st_gid,
        opened_after.st_nlink,
    )
    if stable_fields_before != stable_fields_after or size != opened_after.st_size:
        raise RecoveryError(f"Artifact changed while hashing: {path}")
    value = {
        "kind": "file",
        "sha256": digest,
        "size": size,
        "mode": stat.S_IMODE(opened_after.st_mode),
        "uid": opened_after.st_uid,
        "gid": opened_after.st_gid,
    }
    return _generation_token(value), value


def _validate_generation(raw: Any, description: str) -> str:
    if not isinstance(raw, str) or not HEX64_RE.fullmatch(raw):
        raise RecoveryError(f"Invalid {description} generation.")
    return raw


def _validate_runtime(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise RecoveryError("Invalid runtime recovery state.")
    units = payload.get("units")
    sysctls = payload.get("sysctls")
    log_dirs = payload.get("log_dirs")
    if not isinstance(units, dict) or not isinstance(sysctls, dict):
        raise RecoveryError("Invalid runtime state maps.")
    if not isinstance(log_dirs, dict):
        raise RecoveryError("Invalid log-directory state map.")
    for unit, state_value in units.items():
        if not isinstance(unit, str) or not UNIT_RE.fullmatch(unit):
            raise RecoveryError("Invalid unit name in recovery manifest.")
        if (
            not isinstance(state_value, dict)
            or not isinstance(state_value.get("active"), bool)
            or state_value.get("enabled") not in VALID_ENABLE_STATES
        ):
            raise RecoveryError(f"Invalid saved state for unit: {unit}")
    for key, value in sysctls.items():
        if (
            not isinstance(key, str)
            or not SYSCTL_RE.fullmatch(key)
            or not isinstance(value, str)
            or not value
            or len(value) > 256
            or "\x00" in value
            or "\n" in value
        ):
            raise RecoveryError("Invalid sysctl state in recovery manifest.")
    for path, state_value in log_dirs.items():
        _canonical_path(path, "Saved log directory")
        if not isinstance(state_value, dict):
            raise RecoveryError("Invalid log-directory metadata.")
        existed = state_value.get("existed")
        if not isinstance(existed, bool):
            raise RecoveryError("Invalid log-directory existence state.")
        if existed:
            for key in ("uid", "gid", "mode"):
                if not isinstance(state_value.get(key), int):
                    raise RecoveryError("Invalid log-directory metadata.")
    return payload


def _validate_manifest(
    payload: Any,
    *,
    test_mode: bool,
    test_root: str | None,
    recovery_root: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("version") != MANIFEST_VERSION:
        raise RecoveryError("Unsupported deployment recovery manifest.")
    txid = payload.get("txid")
    if not isinstance(txid, str) or not TXID_RE.fullmatch(txid):
        raise RecoveryError("Invalid deployment transaction id.")
    state_value = payload.get("state")
    if state_value not in VALID_STATES:
        raise RecoveryError("Invalid deployment transaction state.")
    boot_id = payload.get("boot_id")
    if not isinstance(boot_id, str) or not BOOT_ID_RE.fullmatch(boot_id):
        raise RecoveryError("Invalid deployment boot id.")
    runtime = _validate_runtime(payload.get("runtime"))
    if not test_mode:
        unknown_units = set(runtime["units"]) - EXACT_ALLOWED_UNITS
        if unknown_units:
            raise RecoveryError(
                "Recovery manifest names an unmanaged systemd unit."
            )
        unknown_sysctls = set(runtime["sysctls"]) - EXACT_ALLOWED_SYSCTLS
        if unknown_sysctls:
            raise RecoveryError(
                "Recovery manifest names an unmanaged sysctl key."
            )
    for log_dir in runtime["log_dirs"]:
        _validate_log_dir(
            log_dir,
            test_mode=test_mode,
            test_root=test_root,
        )

    allowlist = payload.get("allowlist")
    artifacts = payload.get("artifacts")
    committed = payload.get("committed")
    pending_commit = payload.get("pending_commit")
    if (
        not isinstance(allowlist, list)
        or not isinstance(artifacts, list)
        or not isinstance(committed, list)
    ):
        raise RecoveryError("Invalid deployment artifact journal.")
    validated_paths: list[str] = []
    for raw_path in allowlist:
        if not isinstance(raw_path, str):
            raise RecoveryError("Invalid path in deployment allowlist.")
        validated_paths.append(
            _validate_artifact_path(
                raw_path,
                test_mode=test_mode,
                test_root=test_root,
                recovery_root=recovery_root,
            )
        )
    if len(validated_paths) != len(set(validated_paths)):
        raise RecoveryError("Deployment allowlist contains duplicates.")

    if len(artifacts) > len(validated_paths):
        raise RecoveryError("Artifact journal exceeds its allowlist.")
    artifact_paths: list[str] = []
    for index, entry in enumerate(artifacts):
        if not isinstance(entry, dict):
            raise RecoveryError("Invalid deployment artifact entry.")
        path = entry.get("path")
        if path != validated_paths[index]:
            raise RecoveryError("Artifact journal order does not match its allowlist.")
        artifact_paths.append(path)
        kind = entry.get("kind")
        if kind not in {"absent", "file", "symlink"}:
            raise RecoveryError(f"Invalid artifact kind for {path}.")
        _validate_generation(entry.get("original_generation"), "original")
        if kind == "file":
            snapshot = entry.get("snapshot")
            if not isinstance(snapshot, str) or not SNAPSHOT_RE.fullmatch(snapshot):
                raise RecoveryError(f"Invalid snapshot name for {path}.")
            for key in ("size", "mode", "uid", "gid"):
                if not isinstance(entry.get(key), int):
                    raise RecoveryError(f"Invalid file metadata for {path}.")
            if (
                entry["size"] < 0
                or entry["size"] > MAX_FILE_BYTES
                or not isinstance(entry.get("sha256"), str)
                or not HEX64_RE.fullmatch(entry["sha256"])
            ):
                raise RecoveryError(f"Invalid snapshot digest for {path}.")
        elif kind == "symlink":
            if not isinstance(entry.get("target"), str):
                raise RecoveryError(f"Invalid symlink target for {path}.")
            for key in ("uid", "gid"):
                if not isinstance(entry.get(key), int):
                    raise RecoveryError(f"Invalid symlink metadata for {path}.")
        replacement = entry.get("replacement_generation")
        if replacement is not None:
            _validate_generation(replacement, "replacement")

    if (
        state_value not in {"preparing", "restored"}
        and len(artifacts) != len(validated_paths)
    ):
        raise RecoveryError("Active transaction has an incomplete snapshot set.")
    if len(committed) != len(set(committed)):
        raise RecoveryError("Committed deployment paths contain duplicates.")
    if any(path not in artifact_paths for path in committed):
        raise RecoveryError("Committed deployment path is outside the journal.")
    for path in committed:
        entry = artifacts[artifact_paths.index(path)]
        if "replacement_generation" not in entry:
            raise RecoveryError("Committed artifact has no replacement generation.")
    if pending_commit is not None:
        if (
            not isinstance(pending_commit, dict)
            or pending_commit.get("path") not in artifact_paths
            or pending_commit.get("path") in committed
        ):
            raise RecoveryError("Invalid pending deployment commit.")
        _validate_generation(
            pending_commit.get("replacement_generation"),
            "pending replacement",
        )
        entry = artifacts[artifact_paths.index(pending_commit["path"])]
        if (
            entry.get("replacement_generation")
            != pending_commit["replacement_generation"]
        ):
            raise RecoveryError("Pending replacement generation is inconsistent.")
        candidate = pending_commit.get("candidate")
        if candidate is not None:
            if not isinstance(candidate, str):
                raise RecoveryError("Invalid pending replacement candidate.")
            candidate = _canonical_path(candidate, "Pending candidate path")
            if (
                candidate == pending_commit["path"]
                or os.path.dirname(candidate)
                != os.path.dirname(pending_commit["path"])
            ):
                raise RecoveryError("Pending candidate path is unsafe.")
            if test_mode:
                assert test_root is not None
                if not _inside(candidate, test_root):
                    raise RecoveryError(
                        "Pending candidate escapes the isolated test root."
                    )
    replacement_paths = {
        entry["path"]
        for entry in artifacts
        if entry.get("replacement_generation") is not None
    }
    journaled_replacements = set(committed)
    if isinstance(pending_commit, dict):
        journaled_replacements.add(pending_commit["path"])
    if replacement_paths != journaled_replacements:
        raise RecoveryError(
            "Replacement generations do not match the mutation journal."
        )
    return payload


def _load_manifest(
    manifest: str,
    *,
    test_mode: bool,
    test_root: str | None,
    recovery_root: str,
) -> dict[str, Any]:
    metadata = os.lstat(manifest)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise RecoveryError("Deployment recovery manifest is unsafe.")
    raw = _read_limited_file(manifest, limit=MAX_MANIFEST_BYTES)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryError("Deployment recovery manifest is corrupt.") from exc
    return _validate_manifest(
        payload,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )


def _boot_id(test_mode: bool) -> str:
    if test_mode:
        value = os.environ.get(
            "HY2_DEPLOY_RECOVERY_BOOT_ID",
            "00000000-0000-4000-8000-000000000001",
        )
    else:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    if not BOOT_ID_RE.fullmatch(value):
        raise RecoveryError("Could not determine a valid kernel boot id.")
    return value


def _command_path(
    name: str,
    production: str,
    *,
    test_mode: bool,
    test_root: str | None,
) -> str:
    raw = os.environ.get(name, production)
    path = _canonical_path(raw, name)
    if test_mode:
        assert test_root is not None
        if not _inside(path, test_root) or path == test_root:
            raise RecoveryError(f"{name} escapes the isolated test root.")
    elif path != production:
        raise RecoveryError(f"{name} cannot be overridden in production.")
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
        or not os.access(path, os.X_OK)
    ):
        raise RecoveryError(f"Unsafe command configured by {name}.")
    return path


def _run(
    argv: list[str],
    *,
    timeout: int = 20,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env={
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                "LANG": "C",
                "LC_ALL": "C",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecoveryError(f"Runtime recovery command failed: {argv[0]}") from exc
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        suffix = f": {detail}" if detail else ""
        raise RecoveryError(f"Runtime recovery command failed{suffix}")
    return result


def _authoritative_active_state(
    result: subprocess.CompletedProcess[str],
    unit: str,
) -> bool:
    active_state = result.stdout.strip()
    if result.returncode == 0 and active_state == "active":
        return True
    if (
        result.returncode in {3, 4}
        and active_state in {"inactive", "failed", "unknown"}
    ):
        return False
    raise RecoveryError(
        f"Could not capture an authoritative active state for {unit}."
    )


def _capture_runtime(
    *,
    units: list[str],
    sysctl_keys: list[str],
    log_dirs: list[str],
    test_mode: bool,
    test_root: str | None,
) -> dict[str, Any]:
    if len(units) != len(set(units)):
        raise RecoveryError("Runtime unit list contains duplicates.")
    if len(sysctl_keys) != len(set(sysctl_keys)):
        raise RecoveryError("Runtime sysctl list contains duplicates.")
    if len(log_dirs) != len(set(log_dirs)):
        raise RecoveryError("Runtime log-directory list contains duplicates.")
    for unit in units:
        if not UNIT_RE.fullmatch(unit):
            raise RecoveryError(f"Invalid managed unit name: {unit}")
        if not test_mode and unit not in EXACT_ALLOWED_UNITS:
            raise RecoveryError(f"Unsupported managed unit name: {unit}")
    for key in sysctl_keys:
        if not SYSCTL_RE.fullmatch(key):
            raise RecoveryError(f"Invalid managed sysctl key: {key}")
        if not test_mode and key not in EXACT_ALLOWED_SYSCTLS:
            raise RecoveryError(f"Unsupported managed sysctl key: {key}")

    runtime: dict[str, Any] = {"units": {}, "sysctls": {}, "log_dirs": {}}
    if units:
        systemctl = _command_path(
            "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
            "/usr/bin/systemctl",
            test_mode=test_mode,
            test_root=test_root,
        )
        for unit in units:
            active_result = _run(
                [systemctl, "is-active", unit],
                check=False,
            )
            active = _authoritative_active_state(active_result, unit)
            enabled_result = _run(
                [systemctl, "is-enabled", unit],
                check=False,
            )
            enabled = enabled_result.stdout.strip() or "not-found"
            if (
                enabled not in VALID_ENABLE_STATES
                or enabled_result.returncode not in {0, 1}
            ):
                raise RecoveryError(
                    f"Could not capture an authoritative enable state for {unit}."
                )
            runtime["units"][unit] = {
                "active": active,
                "enabled": enabled,
            }
    if sysctl_keys:
        sysctl = _command_path(
            "HY2_DEPLOY_RECOVERY_SYSCTL",
            "/usr/sbin/sysctl",
            test_mode=test_mode,
            test_root=test_root,
        )
        for key in sysctl_keys:
            result = _run([sysctl, "-n", key])
            value = result.stdout.strip()
            if not value or len(value) > 256 or "\n" in value or "\x00" in value:
                raise RecoveryError(f"Unsafe live sysctl value for {key}.")
            runtime["sysctls"][key] = value
    for raw_path in log_dirs:
        path = _validate_log_dir(
            raw_path,
            test_mode=test_mode,
            test_root=test_root,
        )
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            runtime["log_dirs"][path] = {"existed": False}
            continue
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise RecoveryError(f"Managed log directory is unsafe: {path}")
        runtime["log_dirs"][path] = {
            "existed": True,
            "uid": metadata.st_uid,
            "gid": metadata.st_gid,
            "mode": stat.S_IMODE(metadata.st_mode),
        }
    return runtime


def _restore_enable_state(systemctl: str, unit: str, state_value: str) -> None:
    if state_value in {"static", "indirect", "generated", "transient", "linked",
                       "linked-runtime", "alias"}:
        return
    _run([systemctl, "disable", unit], check=False)
    _run([systemctl, "unmask", unit], check=False)
    if state_value == "enabled":
        _run([systemctl, "enable", unit])
    elif state_value == "enabled-runtime":
        _run([systemctl, "enable", "--runtime", unit])
    elif state_value == "masked":
        _run([systemctl, "mask", unit])
    elif state_value == "masked-runtime":
        _run([systemctl, "mask", "--runtime", unit])
    elif state_value not in {"disabled", "not-found"}:
        raise RecoveryError(f"Unsupported saved enable state for {unit}.")


def _stop_runtime_units(
    payload: dict[str, Any],
    *,
    test_mode: bool,
    test_root: str | None,
    restart_on_failure: bool = True,
) -> list[str]:
    units = _validate_runtime(payload["runtime"])["units"]
    if not units:
        return []
    systemctl = _command_path(
        "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
        "/usr/bin/systemctl",
        test_mode=test_mode,
        test_root=test_root,
    )
    to_stop: list[str] = []
    for unit in units:
        if unit == SELF_UNIT or unit in DEFERRED_RESTART_UNITS:
            continue
        active = _run(
            [systemctl, "is-active", unit],
            check=False,
        )
        if _authoritative_active_state(active, unit):
            to_stop.append(unit)
    stopped: list[str] = []
    try:
        for unit in to_stop:
            result = _run([systemctl, "stop", unit], check=False)
            if result.returncode != 0:
                raise RecoveryError(f"Could not stop managed unit: {unit}")
            stopped.append(unit)
    except BaseException:
        if restart_on_failure:
            _restart_specific_units(
                stopped,
                test_mode=test_mode,
                test_root=test_root,
            )
        raise
    return stopped


def _restart_specific_units(
    units: Iterable[str],
    *,
    test_mode: bool,
    test_root: str | None,
) -> None:
    unit_list = list(units)
    if not unit_list:
        return
    systemctl = _command_path(
        "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
        "/usr/bin/systemctl",
        test_mode=test_mode,
        test_root=test_root,
    )
    for unit in unit_list:
        _run([systemctl, "--no-block", "start", unit])


def _restore_runtime(
    payload: dict[str, Any],
    *,
    test_mode: bool,
    test_root: str | None,
    resume_units: bool = True,
) -> None:
    runtime = _validate_runtime(payload["runtime"])
    units = runtime["units"]
    sysctls = runtime["sysctls"]
    log_dirs = runtime["log_dirs"]
    systemctl: str | None = None
    if units:
        systemctl = _command_path(
            "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
            "/usr/bin/systemctl",
            test_mode=test_mode,
            test_root=test_root,
        )

    for path, state_value in log_dirs.items():
        if state_value["existed"]:
            metadata = os.lstat(path)
            if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
                raise RecoveryError(
                    f"Could not restore unsafe log directory: {path}"
            )
            os.chown(path, state_value["uid"], state_value["gid"])
            os.chmod(path, state_value["mode"])
            _fsync_directory(path)
            _fsync_parent(path)
        else:
            try:
                os.rmdir(path)
                _fsync_parent(path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise RecoveryError(
                    f"New log directory is not empty and cannot be removed: {path}"
                ) from exc

    if sysctls:
        sysctl = _command_path(
            "HY2_DEPLOY_RECOVERY_SYSCTL",
            "/usr/sbin/sysctl",
            test_mode=test_mode,
            test_root=test_root,
        )
        for key, value in sysctls.items():
            _run([sysctl, "-q", "-w", f"{key}={value}"])

    if systemctl is not None:
        _run([systemctl, "daemon-reload"])
        for unit, state_value in units.items():
            _restore_enable_state(systemctl, unit, state_value["enabled"])
    if resume_units:
        _resume_runtime_units(
            payload,
            test_mode=test_mode,
            test_root=test_root,
        )


def _resume_runtime_units(
    payload: dict[str, Any],
    *,
    test_mode: bool,
    test_root: str | None,
) -> None:
    units = _validate_runtime(payload["runtime"])["units"]
    if not units:
        return
    systemctl = _command_path(
        "HY2_DEPLOY_RECOVERY_SYSTEMCTL",
        "/usr/bin/systemctl",
        test_mode=test_mode,
        test_root=test_root,
    )
    same_boot = _boot_id(test_mode) == payload["boot_id"]
    for unit, state_value in units.items():
        if not state_value["active"] or unit == SELF_UNIT:
            continue
        if unit in DEFERRED_RESTART_UNITS:
            # journald starts before this recovery gate during boot, so its
            # restored configuration must be reloaded on either boot id.
            _run([systemctl, "--no-block", "restart", unit])
        elif same_boot:
            # The boot recovery unit is ordered Before/RequiredBy the managed
            # services.  A synchronous start from inside it would deadlock on
            # the very recovery job issuing this command.
            _run([systemctl, "--no-block", "start", unit])


def _read_allowlist_file(path: str) -> list[str]:
    path = _canonical_path(path, "Allowlist file")
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise RecoveryError("Deployment allowlist file is unsafe.")
    raw = _read_limited_file(path, limit=256 * 1024)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RecoveryError("Deployment allowlist is not UTF-8.") from exc
    paths: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if value != line:
            raise RecoveryError(
                f"Allowlist line {line_number} has surrounding whitespace."
            )
        paths.append(value)
    return paths


def _collect_allowlist(
    direct_paths: list[str],
    allowlist_files: list[str],
    *,
    test_mode: bool,
    test_root: str | None,
    recovery_root: str,
) -> list[str]:
    raw_paths = list(direct_paths)
    for file_path in allowlist_files:
        raw_paths.extend(_read_allowlist_file(file_path))
    if not raw_paths:
        raise RecoveryError("snapshot requires at least one artifact path.")
    paths = [
        _validate_artifact_path(
            raw,
            test_mode=test_mode,
            test_root=test_root,
            recovery_root=recovery_root,
        )
        for raw in raw_paths
    ]
    if len(paths) != len(set(paths)):
        raise RecoveryError("Deployment artifact allowlist contains duplicates.")
    return paths


def _snapshot_entry(path: str, pending: str, index: int) -> dict[str, Any]:
    generation, value = _generation(path)
    entry: dict[str, Any] = {
        "path": path,
        "kind": value["kind"],
        "original_generation": generation,
    }
    if value["kind"] == "absent":
        return entry
    if value["kind"] == "symlink":
        entry.update(
            {
                "target": value["target"],
                "uid": value["uid"],
                "gid": value["gid"],
            }
        )
        return entry

    snapshot_name = f"snapshot-{index:04d}"
    snapshot_path = os.path.join(pending, snapshot_name)
    source_metadata = os.lstat(path)
    source_fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    destination_fd = os.open(
        snapshot_path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        opened_before = os.fstat(source_fd)
        if (opened_before.st_dev, opened_before.st_ino) != (
            source_metadata.st_dev,
            source_metadata.st_ino,
        ):
            raise RecoveryError(f"Artifact changed while snapshotting: {path}")
        os.fchmod(destination_fd, 0o600)
        while True:
            chunk = os.read(source_fd, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise RecoveryError(
                    f"Artifact exceeds the 64 MiB rollback limit: {path}"
                )
            digest.update(chunk)
            _write_all(destination_fd, chunk)
        opened_after = os.fstat(source_fd)
        if (
            opened_before.st_size != opened_after.st_size
            or opened_before.st_mtime_ns != opened_after.st_mtime_ns
            or opened_before.st_ctime_ns != opened_after.st_ctime_ns
            or opened_before.st_uid != opened_after.st_uid
            or opened_before.st_gid != opened_after.st_gid
            or stat.S_IMODE(opened_before.st_mode)
            != stat.S_IMODE(opened_after.st_mode)
            or size != opened_after.st_size
        ):
            raise RecoveryError(f"Artifact changed while snapshotting: {path}")
        os.fsync(destination_fd)
    except BaseException:
        os.close(source_fd)
        os.close(destination_fd)
        try:
            os.unlink(snapshot_path)
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(source_fd)
        os.close(destination_fd)
    _fsync_directory(pending)
    if digest.hexdigest() != value["sha256"] or size != value["size"]:
        raise RecoveryError(f"Artifact changed between hashing and snapshot: {path}")
    entry.update(
        {
            "snapshot": snapshot_name,
            "sha256": value["sha256"],
            "size": value["size"],
            "mode": value["mode"],
            "uid": value["uid"],
            "gid": value["gid"],
        }
    )
    return entry


def _snapshot_integrity(entry: dict[str, Any], pending: str) -> None:
    if entry["kind"] != "file":
        return
    path = os.path.join(pending, entry["snapshot"])
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_nlink != 1
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != entry["size"]
    ):
        raise RecoveryError(f"Rollback snapshot is unsafe: {entry['path']}")
    fd = os.open(
        path,
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        digest, size = _hash_open_file(fd, size_limit=MAX_FILE_BYTES)
    finally:
        os.close(fd)
    if digest != entry["sha256"] or size != entry["size"]:
        raise RecoveryError(f"Rollback snapshot is corrupt: {entry['path']}")


def _entry_by_path(payload: dict[str, Any], path: str) -> dict[str, Any]:
    for entry in payload["artifacts"]:
        if entry["path"] == path:
            return entry
    raise RecoveryError(f"Artifact path is not in the active allowlist: {path}")


def _persist_state(manifest: str, payload: dict[str, Any], state_value: str) -> None:
    if state_value not in VALID_STATES:
        raise RecoveryError("Invalid requested transaction state.")
    payload["state"] = state_value
    _atomic_write_manifest(manifest, payload)


def _prepare(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    _ensure_recovery_root(recovery_root)
    pending, manifest = _manifest_paths(recovery_root)
    if os.path.lexists(pending):
        raise RecoveryError(
            "A deployment recovery transaction already exists; run recover first."
        )
    os.mkdir(pending, 0o700)
    os.chmod(pending, 0o700)
    _fsync_directory(recovery_root)
    try:
        runtime = _capture_runtime(
            units=args.unit,
            sysctl_keys=args.sysctl_key,
            log_dirs=args.log_dir,
            test_mode=test_mode,
            test_root=test_root,
        )
        payload: dict[str, Any] = {
            "version": MANIFEST_VERSION,
            "txid": secrets.token_hex(16),
            "state": "preparing",
            "boot_id": _boot_id(test_mode),
            "created_ns": time.time_ns(),
            "runtime": runtime,
            "allowlist": [],
            "artifacts": [],
            "committed": [],
            "pending_commit": None,
        }
        _atomic_write_manifest(manifest, payload)
    except BaseException:
        try:
            shutil.rmtree(pending)
            _fsync_directory(recovery_root)
        except OSError:
            pass
        raise
    print(f"[i] Prepared durable deployment transaction {payload['txid']}.")


def _snapshot(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    pending, manifest = _manifest_paths(recovery_root)
    payload = _load_manifest(
        manifest,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    if payload["state"] != "preparing":
        raise RecoveryError("snapshot is valid only for a preparing transaction.")
    paths = _collect_allowlist(
        args.path,
        args.allowlist_file,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    if payload["allowlist"]:
        if payload["allowlist"] != paths:
            raise RecoveryError("Snapshot retry changed the deployment allowlist.")
    else:
        payload["allowlist"] = paths
        _atomic_write_manifest(manifest, payload)

    total_bytes = sum(
        entry.get("size", 0) for entry in payload["artifacts"]
    )
    for index, path in enumerate(paths):
        if index < len(payload["artifacts"]):
            entry = payload["artifacts"][index]
            _snapshot_integrity(entry, pending)
            current, _ = _generation(path)
            if current != entry["original_generation"]:
                raise RecoveryError(
                    f"Artifact changed during snapshot retry: {path}"
                )
            continue
        entry = _snapshot_entry(path, pending, index)
        total_bytes += entry.get("size", 0)
        if total_bytes > MAX_TOTAL_BYTES:
            snapshot_name = entry.get("snapshot")
            if isinstance(snapshot_name, str):
                try:
                    os.unlink(os.path.join(pending, snapshot_name))
                    _fsync_directory(pending)
                except FileNotFoundError:
                    pass
            raise RecoveryError(
                "Deployment rollback snapshot exceeds the 256 MiB total limit."
            )
        payload["artifacts"].append(entry)
        _atomic_write_manifest(manifest, payload)
    _persist_state(manifest, payload, "active")
    print(
        f"[i] Snapshotted {len(paths)} deployment artifact generation(s)."
    )


def _load_active(
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> tuple[str, str, dict[str, Any]]:
    pending, manifest = _manifest_paths(recovery_root)
    payload = _load_manifest(
        manifest,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    if payload["state"] != "active":
        raise RecoveryError("Artifact mutation requires an active transaction.")
    return pending, manifest, payload


def _candidate_generation(
    candidate: str,
    destination: str,
    *,
    test_mode: bool,
    test_root: str | None,
) -> str:
    candidate = _canonical_path(candidate, "Candidate path")
    if os.path.dirname(candidate) != os.path.dirname(destination):
        raise RecoveryError("Candidate must be on the destination filesystem and parent.")
    if candidate == destination:
        raise RecoveryError("Candidate and destination must be different paths.")
    if test_mode:
        assert test_root is not None
        if not _inside(candidate, test_root):
            raise RecoveryError("Candidate escapes the isolated test root.")
    _safe_directory(
        os.path.dirname(candidate),
        "Artifact parent directory",
        root_only=False,
    )
    generation, value = _generation(candidate)
    if value["kind"] == "absent":
        raise RecoveryError("Replacement candidate does not exist.")
    _fsync_regular(candidate)
    _fsync_parent(candidate)
    return generation


def _before(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    _, manifest, payload = _load_active(
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )
    path = _validate_artifact_path(
        args.path,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    if payload["pending_commit"] is not None:
        raise RecoveryError("Another deployment artifact commit is already pending.")
    if path in payload["committed"]:
        raise RecoveryError("Deployment artifact was already committed once.")
    entry = _entry_by_path(payload, path)
    current, _ = _generation(path)
    if current != entry["original_generation"]:
        raise RecoveryError(
            f"Artifact changed since the deployment snapshot: {path}"
        )
    if args.absent:
        replacement = ABSENT_GENERATION
    else:
        candidate_path = _canonical_path(args.candidate, "Candidate path")
        if candidate_path in payload["allowlist"]:
            raise RecoveryError(
                "Replacement candidate must not be another allowlisted artifact."
            )
        candidate_name = os.path.basename(candidate_path)
        if not candidate_name.startswith(".") or candidate_name in {".", ".."}:
            raise RecoveryError(
                "Replacement candidate must use a hidden staging basename."
            )
        replacement = _candidate_generation(
            candidate_path,
            path,
            test_mode=test_mode,
            test_root=test_root,
        )
    entry["replacement_generation"] = replacement
    payload["pending_commit"] = {
        "path": path,
        "replacement_generation": replacement,
        "candidate": None if args.absent else candidate_path,
    }
    _atomic_write_manifest(manifest, payload)
    print(f"[i] Journaled pending deployment mutation: {path}")


def _after(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    _, manifest, payload = _load_active(
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )
    path = _validate_artifact_path(
        args.path,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    pending_commit = payload["pending_commit"]
    if not isinstance(pending_commit, dict) or pending_commit.get("path") != path:
        raise RecoveryError("Deployment commit does not match the write-ahead journal.")
    _fsync_regular(path)
    _fsync_parent(path)
    current, _ = _generation(path)
    if current != pending_commit["replacement_generation"]:
        raise RecoveryError(
            f"Committed artifact does not match its replacement generation: {path}"
        )
    _cleanup_candidate_from_pending(pending_commit)
    payload["committed"].append(path)
    payload["pending_commit"] = None
    _atomic_write_manifest(manifest, payload)
    print(f"[i] Committed deployment artifact generation: {path}")


def _replace(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    before_args = argparse.Namespace(
        path=args.path,
        candidate=args.candidate,
        absent=False,
    )
    _before(
        before_args,
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )
    destination = _canonical_path(args.path, "Artifact path")
    candidate = _canonical_path(args.candidate, "Candidate path")
    os.replace(candidate, destination)
    _fsync_regular(destination)
    _fsync_parent(destination)
    _after(
        argparse.Namespace(path=destination),
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )


def _remove(
    args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    _before(
        argparse.Namespace(path=args.path, candidate=None, absent=True),
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )
    try:
        os.unlink(args.path)
    except FileNotFoundError:
        pass
    _fsync_parent(args.path)
    _after(
        argparse.Namespace(path=args.path),
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )


def _preflight_recovery(
    payload: dict[str, Any],
    pending: str,
) -> list[dict[str, Any]]:
    changed_by_path: dict[str, dict[str, Any]] = {}
    for entry in payload["artifacts"]:
        _snapshot_integrity(entry, pending)
        current, _ = _generation(entry["path"])
        original = entry["original_generation"]
        replacement = entry.get("replacement_generation")
        if current == original:
            continue
        if replacement is not None and current == replacement:
            changed_by_path[entry["path"]] = entry
            continue
        raise RecoveryError(
            "Refusing to overwrite an unknown post-crash generation: "
            f"{entry['path']}"
        )
    mutation_order = list(payload["committed"])
    pending_commit = payload["pending_commit"]
    if (
        isinstance(pending_commit, dict)
        and pending_commit["path"] not in mutation_order
    ):
        mutation_order.append(pending_commit["path"])
    if set(changed_by_path) - set(mutation_order):
        raise RecoveryError(
            "Replacement generation exists outside the mutation journal."
        )
    return [
        changed_by_path[path]
        for path in mutation_order
        if path in changed_by_path
    ]


def _cleanup_candidate_from_pending(pending_commit: dict[str, Any] | None) -> None:
    if not isinstance(pending_commit, dict):
        return
    candidate = pending_commit.get("candidate")
    if candidate is None:
        return
    replacement = pending_commit["replacement_generation"]
    current, _ = _generation(candidate)
    if current == ABSENT_GENERATION:
        return
    if current != replacement:
        raise RecoveryError(
            f"Pending candidate has an unknown generation: {candidate}"
        )
    os.unlink(candidate)
    _fsync_parent(candidate)


def _restore_entry(entry: dict[str, Any], pending: str, txid: str) -> None:
    path = entry["path"]
    current, _ = _generation(path)
    if current == entry["original_generation"]:
        return
    if current != entry.get("replacement_generation"):
        raise RecoveryError(
            f"Artifact generation changed during recovery: {path}"
        )
    parent = os.path.dirname(path)
    _safe_directory(parent, "Artifact parent directory", root_only=False)
    basename = os.path.basename(path)
    parent_fd = _open_directory(
        parent,
        "Artifact parent directory",
        root_only=False,
    )
    temporary = f".{basename}.hy2-deploy-restore-{txid}-{secrets.token_hex(4)}"
    try:
        if entry["kind"] == "absent":
            try:
                os.unlink(basename, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.fsync(parent_fd)
        elif entry["kind"] == "symlink":
            os.symlink(entry["target"], temporary, dir_fd=parent_fd)
            try:
                os.chown(
                    temporary,
                    entry["uid"],
                    entry["gid"],
                    dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                os.replace(
                    temporary,
                    basename,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                os.fsync(parent_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                raise
        else:
            snapshot_path = os.path.join(pending, entry["snapshot"])
            source_fd = os.open(
                snapshot_path,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
            )
            destination_fd = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o600,
                dir_fd=parent_fd,
            )
            try:
                while True:
                    chunk = os.read(source_fd, 1024 * 1024)
                    if not chunk:
                        break
                    _write_all(destination_fd, chunk)
                os.fchown(destination_fd, entry["uid"], entry["gid"])
                os.fchmod(destination_fd, entry["mode"])
                os.fsync(destination_fd)
            except BaseException:
                try:
                    os.unlink(temporary, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
                raise
            finally:
                os.close(source_fd)
                os.close(destination_fd)
            os.replace(
                temporary,
                basename,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    restored, _ = _generation(path)
    if restored != entry["original_generation"]:
        raise RecoveryError(f"Restored artifact generation mismatch: {path}")


def _verify_all_original(payload: dict[str, Any]) -> None:
    for entry in payload["artifacts"]:
        current, _ = _generation(entry["path"])
        if current != entry["original_generation"]:
            raise RecoveryError(
                "Artifact changed before rollback could be committed: "
                f"{entry['path']}"
            )


def _cleanup_pending(recovery_root: str, pending: str) -> None:
    if not os.path.lexists(pending):
        return
    _safe_directory(pending, "Pending recovery directory", root_only=True)
    shutil.rmtree(pending)
    _fsync_directory(recovery_root)


def _recover(
    _args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    try:
        _safe_directory(recovery_root, "Recovery root", root_only=True)
    except FileNotFoundError:
        print("[i] Outer deployment recovery state is clean.")
        return
    pending, manifest = _manifest_paths(recovery_root)
    if not os.path.lexists(pending):
        print("[i] Outer deployment recovery state is clean.")
        return
    _safe_directory(pending, "Pending recovery directory", root_only=True)
    if not os.path.lexists(manifest):
        _cleanup_pending(recovery_root, pending)
        print("[i] Removed pre-transaction deployment residue.")
        return
    payload = _load_manifest(
        manifest,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    if payload["state"] == "complete":
        _cleanup_pending(recovery_root, pending)
        print("[i] Removed completed deployment transaction residue.")
        return
    if payload["state"] == "restored":
        _resume_runtime_units(
            payload,
            test_mode=test_mode,
            test_root=test_root,
        )
        _cleanup_pending(recovery_root, pending)
        print("[i] Finished a previously restored outer deployment.")
        return
    if payload["state"] == "preparing":
        # Package installation may have started units after prepare captured
        # an inactive/not-found runtime. Quiesce every current managed unit
        # before restoring enable state, sysctls, or directory metadata so a
        # pre-snapshot failure cannot leave newly activated services running.
        try:
            _stop_runtime_units(
                payload,
                test_mode=test_mode,
                test_root=test_root,
                restart_on_failure=False,
            )
            _restore_runtime(
                payload,
                test_mode=test_mode,
                test_root=test_root,
                resume_units=False,
            )
            _persist_state(manifest, payload, "restored")
        except BaseException:
            try:
                # Keep this retry on the preparing-specific path. Routing an
                # artifact-free prepare failure through generic recovery
                # could restart a package-started unit that was originally
                # inactive.
                _persist_state(manifest, payload, "preparing")
            except BaseException:
                pass
            # Only the units captured as active may be resumed. In
            # particular, never restart a package-started unit whose original
            # state was inactive.
            try:
                _resume_runtime_units(
                    payload,
                    test_mode=test_mode,
                    test_root=test_root,
                )
            except BaseException:
                pass
            raise
        _resume_runtime_units(
            payload,
            test_mode=test_mode,
            test_root=test_root,
        )
        _cleanup_pending(recovery_root, pending)
        print("[i] Recovered an interrupted pre-mutation deployment.")
        return

    # First perform a pure read-only preflight.  A corrupt snapshot or an
    # unknown third generation must not turn a safe refusal into an outage.
    _preflight_recovery(payload, pending)
    stopped_units: list[str] = []
    artifact_restore_started = False
    try:
        stopped_units = _stop_runtime_units(
            payload,
            test_mode=test_mode,
            test_root=test_root,
        )
        # Close the writer-stop race and recompute the exact reverse mutation
        # order after all managed readers/writers are quiescent.
        changed = _preflight_recovery(payload, pending)
        _cleanup_candidate_from_pending(payload["pending_commit"])
        _persist_state(manifest, payload, "restoring")
        fail_after_raw = os.environ.get(
            "HY2_DEPLOY_RECOVERY_TEST_FAIL_AFTER_RESTORE",
            "",
        )
        fail_after = int(fail_after_raw) if test_mode and fail_after_raw else 0
        restored_count = 0
        for entry in reversed(changed):
            artifact_restore_started = True
            _restore_entry(entry, pending, payload["txid"])
            restored_count += 1
            if fail_after and restored_count == fail_after:
                raise RecoveryError("Injected recovery interruption.")
        _restore_runtime(
            payload,
            test_mode=test_mode,
            test_root=test_root,
            resume_units=False,
        )
        _verify_all_original(payload)
        _persist_state(manifest, payload, "restored")
    except BaseException:
        if stopped_units and not artifact_restore_started:
            try:
                _restart_specific_units(
                    stopped_units,
                    test_mode=test_mode,
                    test_root=test_root,
                )
            except BaseException:
                pass
        try:
            _persist_state(manifest, payload, "recovery-failed")
        except BaseException:
            pass
        raise
    # Once restored is durable, retries must never re-interpret legitimate
    # writes from resumed services as a conflicting third generation.
    _resume_runtime_units(
        payload,
        test_mode=test_mode,
        test_root=test_root,
    )
    _cleanup_pending(recovery_root, pending)
    print("[i] Recovered an interrupted outer deployment transaction.")


def _complete(
    _args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    pending, manifest, payload = _load_active(
        recovery_root=recovery_root,
        test_mode=test_mode,
        test_root=test_root,
    )
    if payload["pending_commit"] is not None:
        raise RecoveryError("Cannot complete with a pending deployment commit.")
    committed = set(payload["committed"])
    for entry in payload["artifacts"]:
        current, _ = _generation(entry["path"])
        expected = (
            entry.get("replacement_generation")
            if entry["path"] in committed
            else entry["original_generation"]
        )
        if current != expected:
            raise RecoveryError(
                f"Artifact generation changed before deployment commit: {entry['path']}"
            )
    _persist_state(manifest, payload, "complete")
    if test_mode and os.environ.get(
        "HY2_DEPLOY_RECOVERY_TEST_KEEP_COMPLETE"
    ) == "1":
        print("[i] Deployment transaction is durably complete; residue retained.")
        return
    _cleanup_pending(recovery_root, pending)
    print("[i] Outer deployment transaction committed.")


def _status(
    _args: argparse.Namespace,
    *,
    recovery_root: str,
    test_mode: bool,
    test_root: str | None,
) -> None:
    pending, manifest = _manifest_paths(recovery_root)
    if not os.path.lexists(pending):
        print("clean")
        return
    if not os.path.lexists(manifest):
        print("pre-transaction-residue")
        return
    payload = _load_manifest(
        manifest,
        test_mode=test_mode,
        test_root=test_root,
        recovery_root=recovery_root,
    )
    print(payload["state"])


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Durable, fail-closed outer deployment recovery journal.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="create a durable pre-mutation transaction",
    )
    prepare.add_argument("--unit", action="append", default=[])
    prepare.add_argument("--sysctl-key", action="append", default=[])
    prepare.add_argument("--log-dir", action="append", default=[])

    snapshot = subparsers.add_parser(
        "snapshot",
        help="snapshot and freeze the deployment artifact allowlist",
    )
    snapshot.add_argument("--path", action="append", default=[])
    snapshot.add_argument("--allowlist-file", action="append", default=[])

    before = subparsers.add_parser(
        "before",
        help="durably journal an expected replacement before mutation",
    )
    before.add_argument("--path", required=True)
    replacement = before.add_mutually_exclusive_group(required=True)
    replacement.add_argument("--candidate")
    replacement.add_argument("--absent", action="store_true")

    after = subparsers.add_parser(
        "after",
        help="verify and commit a previously journaled replacement",
    )
    after.add_argument("--path", required=True)

    replace = subparsers.add_parser(
        "replace",
        help="journal and atomically install a replacement candidate",
    )
    replace.add_argument("--path", required=True)
    replace.add_argument("--candidate", required=True)

    remove = subparsers.add_parser(
        "remove",
        help="journal and durably remove an allowlisted artifact",
    )
    remove.add_argument("--path", required=True)

    subparsers.add_parser(
        "complete",
        help="durably commit and clean a successful deployment",
    )
    subparsers.add_parser(
        "recover",
        help="idempotently restore an interrupted deployment",
    )
    subparsers.add_parser(
        "status",
        help="print the current recovery state",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    if os.geteuid() != 0:
        _die("Deployment recovery helper must run as root.")
    parser = _build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        test_mode, test_root = _test_context()
        recovery_root = _recovery_root(test_mode, test_root)
        handlers = {
            "prepare": _prepare,
            "snapshot": _snapshot,
            "before": _before,
            "after": _after,
            "replace": _replace,
            "remove": _remove,
            "complete": _complete,
            "recover": _recover,
            "status": _status,
        }
        handlers[args.command](
            args,
            recovery_root=recovery_root,
            test_mode=test_mode,
            test_root=test_root,
        )
        return 0
    except RecoveryError as exc:
        _die(str(exc))
    except (FileNotFoundError, PermissionError, OSError) as exc:
        _die(f"Deployment recovery operation failed: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
