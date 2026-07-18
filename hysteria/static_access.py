"""Fail-closed lifecycle helpers for static-auth proxy services.

Xray and TUIC keep authorization in generated config files, unlike Hysteria's
request-time auth backend. When canonical state is unreadable, callers stop
those services and leave a durable marker. A later successful exact-plan
reconciliation may start only services carrying that marker, so an operator's
intentional manual stop is never undone.
"""
from datetime import datetime, timezone
from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
import time

import state_store


STATE_DIR = Path("/root/hysteria/state")
COMMAND_TIMEOUT_SECONDS = 10
RECOVERY_READINESS_DELAY_SECONDS = 0.25
RECOVERY_READINESS_TIMEOUT_SECONDS = 5
RECOVERY_READINESS_STABILITY_PROBES = 3
XRAY_SERVICE = "xray.service"
TUIC_SERVICE = "tuic-server.service"
SERVICES = (XRAY_SERVICE, TUIC_SERVICE)


@dataclass(frozen=True)
class ServiceActionResult:
    """Machine-readable outcome for a fail-closed service action."""

    service: str
    action: str
    attempted: bool
    ok: bool
    effect_confirmed: bool
    marker_persisted: bool
    code: str
    retryable: bool

    def __bool__(self):
        return self.ok


def _service_key(service):
    if service in (XRAY_SERVICE, "xray"):
        return "xray"
    if service == TUIC_SERVICE:
        return "tuic"
    raise ValueError(f"unsupported static proxy service: {service}")


def marker_path(service, *, state_dir=None):
    root = Path(state_dir) if state_dir is not None else STATE_DIR
    return root / f"{_service_key(service)}.fail-closed.pending"


def _mark_pending(service, reason, *, state_dir=None):
    marker = marker_path(service, state_dir=state_dir)
    payload = {
        "service": service,
        "reason_type": type(reason).__name__,
        "stopped_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
    }
    state_store.save_text_atomic(
        marker,
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n",
    )
    marker.chmod(0o600)
    return marker


def stop_fail_closed(
    service,
    *,
    reason,
    live,
    state_dir=None,
    runner=None,
):
    """Persist recovery intent, then stop a static-auth service.

    ``live`` is an explicit caller-owned guard. Tests and alternate state roots
    must pass false so a unit test can never stop a host service.
    """
    if not live:
        return ServiceActionResult(
            service=service,
            action="stop_fail_closed",
            attempted=False,
            ok=False,
            effect_confirmed=False,
            marker_persisted=False,
            code="not_live",
            retryable=False,
        )
    marker_persisted = False
    try:
        _mark_pending(service, reason, state_dir=state_dir)
        marker_persisted = True
    except (state_store.StateStoreError, OSError) as exc:
        print(
            f"CRITICAL: {service}: could not persist fail-closed "
            f"recovery marker: {exc}",
            file=sys.stderr,
        )
    run = runner or subprocess.run
    try:
        result = run(
            ["systemctl", "stop", service],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{service}: fail-closed stop failed: {exc}", file=sys.stderr)
        return ServiceActionResult(
            service=service,
            action="stop_fail_closed",
            attempted=True,
            ok=False,
            effect_confirmed=False,
            marker_persisted=marker_persisted,
            code=type(exc).__name__,
            retryable=True,
        )
    if result.returncode != 0:
        print(
            f"{service}: fail-closed stop returned {result.returncode}",
            file=sys.stderr,
        )
        return ServiceActionResult(
            service=service,
            action="stop_fail_closed",
            attempted=True,
            ok=False,
            effect_confirmed=False,
            marker_persisted=marker_persisted,
            code="nonzero_exit",
            retryable=True,
        )
    return ServiceActionResult(
        service=service,
        action="stop_fail_closed",
        attempted=True,
        ok=marker_persisted,
        effect_confirmed=True,
        marker_persisted=marker_persisted,
        code="stopped" if marker_persisted else "stopped_marker_pending",
        retryable=not marker_persisted,
    )


def recover_if_pending(
    service,
    *,
    live,
    state_dir=None,
    runner=None,
):
    """Restart a service only when this module previously marked it.

    Callers must reconcile the service's exact safe config before invoking this
    function. Recovery always confirms a fresh stop before starting: a prior
    fail-closed stop may have returned an error while leaving the old process
    active, and ``systemctl start`` alone would then be a no-op that falsely
    appears to load the reconciled config. The marker is removed only after the
    fresh stop/start cycle and readiness probes succeed.
    """
    if not live:
        return False
    marker = marker_path(service, state_dir=state_dir)
    if not marker.exists():
        return False
    run = runner or subprocess.run
    try:
        result = run(
            ["systemctl", "stop", service],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(
            f"{service}: fail-closed recovery pre-stop failed: {exc}",
            file=sys.stderr,
        )
        return False
    if result.returncode != 0:
        print(
            f"{service}: fail-closed recovery pre-stop returned "
            f"{result.returncode}",
            file=sys.stderr,
        )
        return False
    try:
        result = run(
            ["systemctl", "start", service],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"{service}: fail-closed recovery failed: {exc}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(
            f"{service}: fail-closed recovery returned {result.returncode}",
            file=sys.stderr,
        )
        return False
    for _probe in range(RECOVERY_READINESS_STABILITY_PROBES):
        time.sleep(RECOVERY_READINESS_DELAY_SECONDS)
        try:
            readiness = run(
                ["systemctl", "is-active", "--quiet", service],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=RECOVERY_READINESS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(
                f"{service}: fail-closed recovery readiness failed: {exc}",
                file=sys.stderr,
            )
            return False
        if readiness.returncode != 0:
            print(
                f"{service}: fail-closed recovery readiness returned "
                f"{readiness.returncode}",
                file=sys.stderr,
            )
            return False
    try:
        marker.unlink()
        state_store._fsync_dir(marker.parent)
    except OSError as exc:
        print(
            f"{service}: could not clear fail-closed recovery marker: {exc}",
            file=sys.stderr,
        )
        return False
    return True
