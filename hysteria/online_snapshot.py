"""Integrity and freshness metadata for the cached Hysteria online count."""

import hashlib
import hmac
import json
import math
import time
from pathlib import Path


SCHEMA_VERSION = 1


class InvalidOnlineSnapshot(ValueError):
    """The online snapshot cannot be trusted as an authorization fallback."""


def metadata_path(snapshot_path):
    """Keep capture metadata beside a patched or deployed snapshot path."""
    path = Path(snapshot_path)
    return path.with_name(f"{path.stem}.meta{path.suffix}")


def snapshot_sha256(snapshot):
    """Return a stable digest that binds metadata to one snapshot payload."""
    if not isinstance(snapshot, dict):
        raise InvalidOnlineSnapshot("online snapshot must be an object")
    try:
        payload = json.dumps(
            snapshot,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise InvalidOnlineSnapshot(
            "online snapshot cannot be canonicalized"
        ) from exc
    return hashlib.sha256(payload).hexdigest()


def build_metadata(snapshot, *, captured_at=None):
    """Build metadata immediately after a successful `/online` API read."""
    observed_at = time.time() if captured_at is None else captured_at
    if (
        isinstance(observed_at, bool)
        or not isinstance(observed_at, (int, float))
        or not math.isfinite(observed_at)
        or observed_at < 0
    ):
        raise InvalidOnlineSnapshot("invalid online capture time")
    return {
        "schema_version": SCHEMA_VERSION,
        "captured_at_unix": float(observed_at),
        "snapshot_sha256": snapshot_sha256(snapshot),
    }


def validate_fresh_snapshot(
    snapshot,
    metadata,
    *,
    now=None,
    ttl_seconds,
):
    """Validate the fallback's schema, binding, and explicit maximum age."""
    current = time.time() if now is None else now
    if (
        isinstance(current, bool)
        or not isinstance(current, (int, float))
        or not math.isfinite(current)
        or current < 0
    ):
        raise InvalidOnlineSnapshot("invalid current time")
    if (
        isinstance(ttl_seconds, bool)
        or not isinstance(ttl_seconds, (int, float))
        or not math.isfinite(ttl_seconds)
        or ttl_seconds <= 0
    ):
        raise InvalidOnlineSnapshot("invalid online snapshot TTL")
    if not isinstance(metadata, dict) or set(metadata) != {
        "schema_version",
        "captured_at_unix",
        "snapshot_sha256",
    }:
        raise InvalidOnlineSnapshot("online snapshot metadata is incomplete")
    if metadata["schema_version"] != SCHEMA_VERSION:
        raise InvalidOnlineSnapshot("unsupported online snapshot schema")
    captured_at = metadata["captured_at_unix"]
    if (
        isinstance(captured_at, bool)
        or not isinstance(captured_at, (int, float))
        or not math.isfinite(captured_at)
        or captured_at < 0
        or captured_at > current
        or current - captured_at > ttl_seconds
    ):
        raise InvalidOnlineSnapshot("online snapshot is not fresh")
    expected = metadata["snapshot_sha256"]
    if (
        not isinstance(expected, str)
        or len(expected) != hashlib.sha256().digest_size * 2
        or not hmac.compare_digest(snapshot_sha256(snapshot), expected)
    ):
        raise InvalidOnlineSnapshot("online snapshot metadata does not match")
    return snapshot
