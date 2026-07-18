"""Short-lived, session-bound recovery receipts for credential rotation.

A self-service rotation commits ``users.json`` before an HTTP response can be
delivered.  A process crash or lost response in that gap would otherwise make
the old bearer session invalid and permanently hide the newly generated
token.  This store prepares a receipt before the canonical commit and permits
only the same browser session and idempotency key to replay it for a short,
bounded window.

The receipt contains the prospective token because it must survive a crash.
Its file is created mode 0600, never logged, and aggressively expires.  It is
not an audit log.
"""

import hashlib
import hmac
import re
import time
from pathlib import Path

import state_store


STATE_FILE = Path(
    "/root/hysteria/state/credential_rotation_receipts.json"
)
RECEIPT_TTL_SECONDS = 5 * 60
RECEIPT_MAX_ENTRIES = 256
LOCK_TIMEOUT_SECONDS = 15.0
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{20,128}$")


class RecoveryReceiptCapacityError(state_store.StateStoreError):
    """The bounded receipt store has no safe slot for another rotation."""


def valid_request_id(value):
    return bool(_REQUEST_ID_RE.fullmatch(str(value or "")))


def _digest(label, value):
    payload = (
        str(label).encode("ascii")
        + b"\0"
        + str(value).encode("utf-8")
    )
    return hashlib.sha256(payload).hexdigest()


def _receipt_key(user, request_id):
    return _digest("rotation-request", f"{user}\0{request_id}")


def _session_digest(session_id):
    return _digest("rotation-session", session_id)


def _lock_path(path):
    return Path(str(path) + ".lock")


def _load(path):
    data = state_store.load_json_strict(path, {})
    if not isinstance(data, dict):
        raise state_store.InvalidJsonState(
            f"invalid credential rotation receipt state: {path}"
        )
    for key, item in data.items():
        if (
            not isinstance(key, str)
            or len(key) != 64
            or not isinstance(item, dict)
            or not isinstance(item.get("user"), str)
            or not isinstance(item.get("new_token"), str)
            or not isinstance(item.get("new_uuid"), str)
            or not isinstance(item.get("old_generation"), str)
            or not isinstance(item.get("new_generation"), str)
            or not isinstance(item.get("session_digests"), list)
            or not all(
                isinstance(value, str) and len(value) == 64
                for value in item.get("session_digests", [])
            )
        ):
            raise state_store.InvalidJsonState(
                f"invalid credential rotation receipt entry: {path}"
            )
        try:
            created_at = int(item.get("created_at"))
            expires_at = int(item.get("expires_at"))
        except (TypeError, ValueError) as exc:
            raise state_store.InvalidJsonState(
                f"invalid credential rotation receipt expiry: {path}"
            ) from exc
        if (
            created_at <= 0
            or expires_at <= created_at
            or len(item["new_token"]) > 512
            or len(item["new_uuid"]) > 128
            or not item["session_digests"]
        ):
            raise state_store.InvalidJsonState(
                f"invalid credential rotation receipt bounds: {path}"
            )
    return data


def _prune(data, now):
    return {
        key: item
        for key, item in data.items()
        if int(item.get("expires_at", 0)) > now
    }


def _save(path, data):
    state_store.save_json(path, data)
    Path(path).chmod(0o600)


def prune_expired(path, *, now=None):
    """Actively erase expired plaintext receipts without a new rotation."""
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    # Avoid creating an otherwise unused lock file on installations that have
    # never used self-service rotation. A concurrent creator is safely picked
    # up on the next maintenance tick.
    if not target.exists():
        return 0
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        original = _load(target)
        data = _prune(original, timestamp)
        removed = len(original) - len(data)
        if removed:
            _save(target, data)
        return removed


def lookup_bound(
    path,
    *,
    user,
    request_id,
    session_id,
    now=None,
):
    """Return a copy of a live receipt only for its bound browser session."""
    if not valid_request_id(request_id) or not session_id:
        return None
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        original = _load(target)
        data = _prune(original, timestamp)
        if data != original:
            _save(target, data)
        item = data.get(_receipt_key(user, request_id))
        if not isinstance(item, dict):
            return None
        supplied = _session_digest(session_id)
        if not any(
            hmac.compare_digest(supplied, expected)
            for expected in item["session_digests"]
        ):
            return None
        return dict(item)


def prepare(
    path,
    *,
    user,
    request_id,
    session_id,
    old_generation,
    new_generation,
    new_token,
    new_uuid,
    now=None,
):
    """Durably create or refresh a browser-bound idempotency receipt.

    Refreshing an existing record intentionally rewrites and fsyncs the store.
    If an earlier directory fsync was uncertain, a retry must establish a new
    durability point before the caller is allowed to mutate canonical state.
    """
    if not valid_request_id(request_id):
        raise ValueError("invalid credential rotation request id")
    if not session_id:
        raise ValueError("credential rotation requires a browser session")
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    key = _receipt_key(user, request_id)
    supplied_session = _session_digest(session_id)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _prune(_load(target), timestamp)
        existing = data.get(key)
        if existing is not None:
            if (
                existing.get("user") != user
                or not any(
                    hmac.compare_digest(supplied_session, expected)
                    for expected in existing["session_digests"]
                )
                or not hmac.compare_digest(
                    str(existing.get("old_generation") or ""),
                    str(old_generation or ""),
                )
            ):
                raise PermissionError(
                    "credential rotation receipt binding mismatch"
                )
            # Rewrite even when unchanged; see the durability note above.
            _save(target, data)
            return dict(existing)
        if len(data) >= RECEIPT_MAX_ENTRIES:
            raise RecoveryReceiptCapacityError(
                "credential rotation recovery capacity is temporarily full"
            )
        item = {
            "user": str(user),
            "old_generation": str(old_generation),
            "new_generation": str(new_generation),
            "new_token": str(new_token),
            "new_uuid": str(new_uuid),
            "session_digests": [supplied_session],
            "created_at": timestamp,
            "expires_at": timestamp + RECEIPT_TTL_SECONDS,
        }
        data[key] = item
        _save(target, data)
        return dict(item)


def bind_replacement_session(
    path,
    *,
    user,
    request_id,
    original_session_id,
    replacement_session_id,
    now=None,
):
    """Allow a successfully minted replacement cookie to replay the receipt."""
    if not replacement_session_id:
        return False
    timestamp = int(time.time() if now is None else now)
    target = Path(path)
    key = _receipt_key(user, request_id)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _prune(_load(target), timestamp)
        item = data.get(key)
        if not isinstance(item, dict):
            return False
        supplied = _session_digest(original_session_id)
        if not any(
            hmac.compare_digest(supplied, expected)
            for expected in item["session_digests"]
        ):
            return False
        replacement = _session_digest(replacement_session_id)
        if not any(
            hmac.compare_digest(replacement, expected)
            for expected in item["session_digests"]
        ):
            item["session_digests"].append(replacement)
            # Keep the record small even if a client retries repeatedly.
            item["session_digests"] = item["session_digests"][-4:]
        _save(target, data)
        return True


def discard(path, *, user, request_id):
    """Remove a superseded receipt without disclosing whether it existed."""
    if not valid_request_id(request_id):
        return
    target = Path(path)
    with state_store.file_lock(
        _lock_path(target),
        timeout=LOCK_TIMEOUT_SECONDS,
    ):
        data = _load(target)
        if data.pop(_receipt_key(user, request_id), None) is not None:
            _save(target, data)
