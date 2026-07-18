"""Durable, bounded retry queue for credential-revocation side effects."""

import hashlib
import hmac
import time
from pathlib import Path

import state_store


STATE_FILE = Path("/root/hysteria/state/credential_revocations.json")
# Kept in the on-disk schema for backward compatibility only. Pending
# revocations are never discarded by wall-clock age: a long outage must not
# resurrect a credential or strand a deletion after its canonical commit.
TASK_TTL_SECONDS = 10 * 60
TASK_MAX_ENTRIES = 512
LOCK_TIMEOUT_SECONDS = 15.0
CLAIM_LEASE_SECONDS = 15
SECOND_KICK_DELAY_SECONDS = 2
RETRY_DELAY_SECONDS = 5
ALLOWED_STATIC_SERVICES = frozenset(
    {"xray.service", "tuic-server.service"}
)


class RevocationQueueCapacityError(state_store.StateStoreError):
    """The bounded retry queue has no safe slot for another revocation."""


def task_id_for(user, nonce):
    return hashlib.sha256(
        b"credential-revocation\0"
        + str(user).encode("utf-8")
        + b"\0"
        + str(nonce).encode("utf-8")
    ).hexdigest()


def _lock_path(path):
    return Path(str(path) + ".lock")


def _load(path):
    data = state_store.load_json_strict(path, {})
    if not isinstance(data, dict):
        raise state_store.InvalidJsonState(
            f"invalid credential revocation queue: {path}"
        )
    for key, item in data.items():
        if (
            not isinstance(key, str)
            or len(key) != 64
            or not isinstance(item, dict)
            or not isinstance(item.get("user"), str)
            or not isinstance(item.get("previous_generation"), str)
            or not isinstance(item.get("target_generation"), str)
            or not isinstance(item.get("static_services"), list)
            or not all(
                isinstance(service, str)
                and service in ALLOWED_STATIC_SERVICES
                for service in item.get("static_services", [])
            )
        ):
            raise state_store.InvalidJsonState(
                f"invalid credential revocation task: {path}"
            )
        try:
            values = [
                int(item.get("created_at")),
                int(item.get("expires_at")),
                int(item.get("next_attempt_at", 0)),
                int(item.get("lease_until", 0)),
                int(item.get("kick_successes", 0)),
                int(item.get("attempts", 0)),
            ]
        except (TypeError, ValueError) as exc:
            raise state_store.InvalidJsonState(
                f"invalid credential revocation task counters: {path}"
            ) from exc
        if (
            values[0] <= 0
            or values[1] <= values[0]
            or not 0 <= values[4] <= 2
            or values[5] < 0
        ):
            raise state_store.InvalidJsonState(
                f"invalid credential revocation task bounds: {path}"
            )
    return data


def _save(path, data):
    state_store.save_json(path, data)
    Path(path).chmod(0o600)


def _retain_pending(data, now):
    """Retain every unfinished task regardless of its legacy expiry field."""
    del now
    return dict(data)


def prepare(
    path,
    *,
    task_id,
    user,
    previous_generation,
    target_generation,
    static_services=(),
    now=None,
):
    """Persist revocation intent before the canonical credential changes."""
    if len(str(task_id)) != 64:
        raise ValueError("invalid credential revocation task id")
    timestamp = int(time.time() if now is None else now)
    pending_services = {
        str(service) for service in static_services if service
    }
    if not pending_services.issubset(ALLOWED_STATIC_SERVICES):
        raise ValueError("invalid static service in revocation task")
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _retain_pending(_load(target), timestamp)
        existing = data.get(task_id)
        if existing is not None:
            if (
                existing.get("user") != user
                or not hmac.compare_digest(
                    str(existing.get("previous_generation") or ""),
                    str(previous_generation or ""),
                )
                or not hmac.compare_digest(
                    str(existing.get("target_generation") or ""),
                    str(target_generation or ""),
                )
            ):
                raise PermissionError(
                    "credential revocation task binding mismatch"
                )
            # Re-establish a durability point after an uncertain directory
            # fsync before any canonical mutation is retried.
            _save(target, data)
            return dict(existing)
        if len(data) >= TASK_MAX_ENTRIES:
            raise RevocationQueueCapacityError(
                "credential revocation retry capacity is temporarily full"
            )
        item = {
            "user": str(user),
            "previous_generation": str(previous_generation),
            "target_generation": str(target_generation),
            "created_at": timestamp,
            "expires_at": timestamp + TASK_TTL_SECONDS,
            "next_attempt_at": timestamp,
            "lease_until": 0,
            "kick_successes": 0,
            "attempts": 0,
            "static_services": sorted(pending_services),
        }
        data[task_id] = item
        _save(target, data)
        return dict(item)


def add_static_services(path, task_id, services, *, now=None):
    """Persist static services whose stop/reload outcome needs a retry."""
    timestamp = int(time.time() if now is None else now)
    wanted = {str(service) for service in services if service}
    if not wanted:
        return False
    if not wanted.issubset(ALLOWED_STATIC_SERVICES):
        raise ValueError("invalid static service in revocation task")
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _retain_pending(_load(target), timestamp)
        item = data.get(task_id)
        if not isinstance(item, dict):
            return False
        item["static_services"] = sorted(
            set(item.get("static_services", [])) | wanted
        )
        item["next_attempt_at"] = min(
            int(item.get("next_attempt_at", timestamp)),
            timestamp,
        )
        _save(target, data)
        return True


def claim_due(path, *, now=None):
    """Lease one due task so network/process I/O can happen outside the lock."""
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        original = _load(target)
        data = _retain_pending(original, timestamp)
        candidates = sorted(
            (
                (key, item)
                for key, item in data.items()
                if int(item.get("next_attempt_at", 0)) <= timestamp
                and int(item.get("lease_until", 0)) <= timestamp
            ),
            key=lambda pair: (
                int(pair[1].get("next_attempt_at", 0)),
                int(pair[1].get("created_at", 0)),
                pair[0],
            ),
        )
        if not candidates:
            if data != original:
                _save(target, data)
            return None
        key, item = candidates[0]
        item["lease_until"] = timestamp + CLAIM_LEASE_SECONDS
        item["attempts"] = int(item.get("attempts", 0)) + 1
        _save(target, data)
        claimed = dict(item)
        claimed["task_id"] = key
        return claimed


def release_claim(path, task_id, *, delay=RETRY_DELAY_SECONDS, now=None):
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _retain_pending(_load(target), timestamp)
        item = data.get(task_id)
        if not isinstance(item, dict):
            return False
        item["lease_until"] = 0
        item["next_attempt_at"] = timestamp + max(1, int(delay))
        _save(target, data)
        return True


def complete_attempt(
    path,
    task_id,
    *,
    kick_ok,
    stopped_services=(),
    now=None,
):
    """Record one attempt; two spaced successful kicks complete revocation."""
    timestamp = int(time.time() if now is None else now)
    stopped = {str(service) for service in stopped_services}
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _retain_pending(_load(target), timestamp)
        item = data.get(task_id)
        if not isinstance(item, dict):
            return {"complete": False, "missing": True}
        successes = int(item.get("kick_successes", 0))
        if kick_ok:
            successes = min(2, successes + 1)
        else:
            # Require two successful observations after the latest failure.
            successes = 0
        item["kick_successes"] = successes
        item["static_services"] = [
            service
            for service in item.get("static_services", [])
            if service not in stopped
        ]
        complete = successes >= 2 and not item["static_services"]
        if complete:
            data.pop(task_id, None)
        else:
            item["lease_until"] = 0
            item["next_attempt_at"] = timestamp + (
                SECOND_KICK_DELAY_SECONDS
                if kick_ok
                else RETRY_DELAY_SECONDS
            )
        _save(target, data)
        return {
            "complete": complete,
            "kick_successes": successes,
            "static_pending": tuple(item.get("static_services", ())),
        }


def discard(path, task_id):
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _load(target)
        if data.pop(task_id, None) is not None:
            _save(target, data)
