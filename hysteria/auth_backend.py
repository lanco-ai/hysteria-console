#!/usr/bin/env python3
import base64
import errno
import fcntl
import hashlib
import hmac
import http.client
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
import uuid
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import cycle as cycle_util
from display import (
    DEPLOYED_DISPLAY_MULTIPLIER,
    DISPLAY_MULTIPLIER_STATE_FILE,
    effective_display_multiplier_strict,
)
import online_snapshot
from timeutil import local_now
import user_compat

USERS_FILE = "/root/hysteria/users.json"
USAGE_FILE = "/root/hysteria/state/usage.json"
USAGE_DAILY_FILE = "/root/hysteria/state/usage_daily.json"
ONLINE_SNAPSHOT_FILE = "/root/hysteria/state/online.json"
DEVICE_ADMISSION_FILE = "/root/hysteria/state/device_admissions.json"
META_FILE = "/root/hysteria/subscription_meta.json"
SETTLEMENT_DAY_DEFAULT = cycle_util.SETTLEMENT_DAY_DEFAULT
CYCLE_LENGTH_DAYS_DEFAULT = cycle_util.CYCLE_LENGTH_DAYS_DEFAULT
CYCLE_LENGTH_MIN = cycle_util.CYCLE_LENGTH_MIN
CYCLE_LENGTH_MAX = cycle_util.CYCLE_LENGTH_MAX
API_HOST = "127.0.0.1"
API_PORT = 25413
API_SECRET_FILE = "/root/hysteria/api_secret"
API_SECRET_PLACEHOLDER = "__HY_API_SECRET__"
API_SECRET_FALLBACK = "__HY_API_SECRET__"
AUTH_SECRET_MAX_CHARS = 256
AUTH_SECRET_MAX_BYTES = AUTH_SECRET_MAX_CHARS * 4
PASSWORD_HASH_MAX_CHARS = 512
PBKDF2_ROUNDS_MIN = 100_000
PBKDF2_ROUNDS_MAX = 1_000_000
PBKDF2_SALT_BYTES = 16
PBKDF2_DIGEST_BYTES = hashlib.sha256().digest_size
_INTEGER_TEXT = re.compile(r"^[0-9]+$")
DEVICE_ADMISSION_TTL_SECONDS = 20.0
DEVICE_ADMISSION_MAX_BYTES = 1 << 20
DEVICE_ADMISSION_LOCK_TIMEOUT_SECONDS = 0.25
DEVICE_ADMISSION_LOCK_POLL_SECONDS = 0.01
ONLINE_SNAPSHOT_TTL_SECONDS = 20.0
ONLINE_API_TIMEOUT_SECONDS = 1.5
ONLINE_RESPONSE_MAX_BYTES = 1 << 20
ONLINE_MAX_USERS = 10_000
COMMAND_AUTH_DEADLINE_SECONDS = 2.0
_STDLIB_HTTP_CONNECTION = http.client.HTTPConnection


class StateUnavailable(RuntimeError):
    pass


def _remaining_timeout(deadline, maximum):
    """Return a positive timeout capped by an optional monotonic deadline."""
    if (
        isinstance(maximum, bool)
        or not isinstance(maximum, (int, float))
        or not math.isfinite(maximum)
        or maximum <= 0
    ):
        raise StateUnavailable("invalid timeout")
    if deadline is None:
        return float(maximum)
    if (
        isinstance(deadline, bool)
        or not isinstance(deadline, (int, float))
        or not math.isfinite(deadline)
    ):
        raise StateUnavailable("invalid request deadline")
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise StateUnavailable("authentication deadline exceeded")
    return min(float(maximum), remaining)


def _deadline_expired(deadline):
    if deadline is None:
        return False
    try:
        return time.monotonic() >= float(deadline)
    except (TypeError, ValueError, OverflowError):
        return True


class _DeadlineSocket(socket.socket):
    """Socket whose every blocking operation shares one absolute deadline."""

    def __init__(self, *args, deadline, maximum_timeout, **kwargs):
        super().__init__(*args, **kwargs)
        self._hy2_deadline = deadline
        self._hy2_maximum_timeout = maximum_timeout

    def _arm(self):
        socket.socket.settimeout(
            self,
            _remaining_timeout(
                self._hy2_deadline,
                self._hy2_maximum_timeout,
            ),
        )

    def connect(self, address):
        self._arm()
        return super().connect(address)

    def recv(self, buffersize, flags=0):
        self._arm()
        return super().recv(buffersize, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._arm()
        return super().recv_into(buffer, nbytes, flags)

    def send(self, data, flags=0):
        self._arm()
        return super().send(data, flags)

    def sendall(self, data, flags=0):
        view = memoryview(data)
        while view:
            sent = self.send(view, flags)
            if sent <= 0:
                raise StateUnavailable("online API connection closed")
            view = view[sent:]


class _DeadlineHTTPConnection(_STDLIB_HTTP_CONNECTION):
    """Direct IPv4 HTTP connection with a whole-operation deadline."""

    def __init__(self, host, port, *, timeout, deadline):
        super().__init__(host, port, timeout=timeout)
        self._hy2_deadline = deadline

    def connect(self):
        request_socket = _DeadlineSocket(
            socket.AF_INET,
            socket.SOCK_STREAM,
            deadline=self._hy2_deadline,
            maximum_timeout=ONLINE_API_TIMEOUT_SECONDS,
        )
        try:
            request_socket.connect((self.host, self.port))
            self.sock = request_socket
            if self._tunnel_host:
                self._tunnel()
        except Exception:
            request_socket.close()
            raise


def _strict_json_bytes(payload, *, label):
    def reject_duplicates(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise StateUnavailable(f"{label} has duplicate fields")
            result[key] = value
        return result

    def reject_constant(_value):
        raise StateUnavailable(f"{label} contains non-finite numbers")

    try:
        return json.loads(
            payload.decode("utf-8"),
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except StateUnavailable:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise StateUnavailable(f"{label} is invalid") from exc


def _load_bounded_json(path, default, *, required, maximum_bytes, label):
    target = Path(path)
    try:
        size = target.stat().st_size
        if size > maximum_bytes:
            raise StateUnavailable(f"{label} is too large")
        with target.open("rb") as handle:
            payload = handle.read(maximum_bytes + 1)
    except FileNotFoundError:
        if required:
            raise StateUnavailable(str(path))
        return default
    except StateUnavailable:
        raise
    except OSError as exc:
        raise StateUnavailable(str(path)) from exc
    if len(payload) > maximum_bytes:
        raise StateUnavailable(f"{label} is too large")
    data = _strict_json_bytes(payload, label=label)
    if default is not None and not isinstance(data, type(default)):
        raise StateUnavailable(f"{label} has invalid type")
    return data


def get_api_secret():
    """Same file-first contract as traffic_limiter.get_api_secret.

    The module is shared by the persistent HTTP service and the retained
    command-auth CLI, so the deployed secret remains runtime data rather than
    a rendered source constant.
    """
    try:
        with open(API_SECRET_FILE, "r", encoding="utf-8") as f:
            v = f.read().strip()
        if v and v != API_SECRET_PLACEHOLDER:
            return v
    except OSError:
        pass
    return API_SECRET_FALLBACK


def load_json(path, default, *, required=False):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        if required:
            raise StateUnavailable(path)
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateUnavailable(path) from exc
    if default is not None and not isinstance(data, type(default)):
        raise StateUnavailable(path)
    return data


def _non_negative_integer(value, field):
    if isinstance(value, bool):
        raise StateUnavailable(f"{field} must be a non-negative integer")
    if isinstance(value, int):
        parsed = value
    elif (
        isinstance(value, str)
        and _INTEGER_TEXT.fullmatch(value.strip())
    ):
        try:
            parsed = int(value.strip())
        except ValueError as exc:
            raise StateUnavailable(
                f"{field} must be a non-negative integer"
            ) from exc
    else:
        raise StateUnavailable(f"{field} must be a non-negative integer")
    if parsed < 0:
        raise StateUnavailable(f"{field} must be a non-negative integer")
    return parsed


def usage_total(entry, *, field="usage"):
    if isinstance(entry, dict):
        unknown = set(entry) - {"tx", "rx", "total"}
        if unknown:
            raise StateUnavailable(f"{field} contains unsupported fields")
        tx = _non_negative_integer(entry.get("tx", 0), f"{field}.tx")
        rx = _non_negative_integer(entry.get("rx", 0), f"{field}.rx")
        total = _non_negative_integer(
            entry.get("total", tx + rx), f"{field}.total"
        )
        if total != tx + rx:
            raise StateUnavailable(f"{field}.total must equal tx + rx")
        return total
    return _non_negative_integer(entry, field)


def validate_daily_usage(daily):
    if not isinstance(daily, dict):
        raise StateUnavailable("daily usage must be an object")
    for day_key, rows in daily.items():
        if not isinstance(day_key, str):
            raise StateUnavailable("daily usage key must be an ISO date")
        try:
            parsed_day = datetime.strptime(day_key, "%Y-%m-%d")
        except ValueError as exc:
            raise StateUnavailable(
                "daily usage key must be an ISO date"
            ) from exc
        if parsed_day.strftime("%Y-%m-%d") != day_key:
            raise StateUnavailable("daily usage key must be an ISO date")
        if not isinstance(rows, dict):
            raise StateUnavailable(f"daily usage bucket {day_key} is invalid")
        for username, entry in rows.items():
            if not isinstance(username, str) or not username:
                raise StateUnavailable(
                    f"daily usage bucket {day_key} has an invalid user"
                )
            usage_total(entry, field=f"{day_key}.{username}")
    return daily


def validate_meta(meta):
    if not isinstance(meta, dict):
        raise StateUnavailable("subscription metadata must be an object")
    if "settlement_day" in meta:
        settlement_day = _non_negative_integer(
            meta["settlement_day"], "settlement_day"
        )
        if not 1 <= settlement_day <= 28:
            raise StateUnavailable("settlement_day must be between 1 and 28")
    if "cycle_length_days" in meta:
        cycle_length = _non_negative_integer(
            meta["cycle_length_days"], "cycle_length_days"
        )
        if not CYCLE_LENGTH_MIN <= cycle_length <= CYCLE_LENGTH_MAX:
            raise StateUnavailable(
                "cycle_length_days is outside the supported range"
            )
    anchor = meta.get("cycle_anchor_date")
    if anchor not in (None, ""):
        if not isinstance(anchor, str):
            raise StateUnavailable("cycle_anchor_date must be an ISO date")
        try:
            parsed_anchor = datetime.strptime(anchor, "%Y-%m-%d")
        except ValueError as exc:
            raise StateUnavailable(
                "cycle_anchor_date must be an ISO date"
            ) from exc
        if parsed_anchor.strftime("%Y-%m-%d") != anchor:
            raise StateUnavailable("cycle_anchor_date must be an ISO date")
    return meta


def validate_unique_vless_uuids(users):
    """Reject invalid or shared static credentials anywhere in users.json."""
    seen = {}
    for username, cfg in users.items():
        if not user_compat.is_valid_username(username):
            raise StateUnavailable("users.json contains an invalid username")
        if not isinstance(cfg, dict):
            raise StateUnavailable(f"user {username} must be an object")
        raw_uuid = cfg.get("vless_uuid")
        if raw_uuid in (None, ""):
            continue
        if not isinstance(raw_uuid, str):
            raise StateUnavailable(
                f"user {username} has an invalid vless_uuid"
            )
        try:
            canonical = uuid.UUID(raw_uuid).hex
        except (ValueError, AttributeError, TypeError) as exc:
            raise StateUnavailable(
                f"user {username} has an invalid vless_uuid"
            ) from exc
        previous = seen.get(canonical)
        if previous is not None:
            raise StateUnavailable(
                f"users {previous} and {username} share a vless_uuid"
            )
        seen[canonical] = username
    return users


def _bounded_utf8(value):
    if not isinstance(value, str) or len(value) > AUTH_SECRET_MAX_CHARS:
        return None
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        return None
    if len(raw) > AUTH_SECRET_MAX_BYTES:
        return None
    return raw


def _b64url_decode_nopad(s):
    if not isinstance(s, str) or not s or len(s) > 128 or "=" in s:
        raise ValueError("invalid base64url component")
    raw = s.encode("ascii")
    pad = b"=" * ((4 - (len(raw) % 4)) % 4)
    return base64.b64decode(raw + pad, altchars=b"-_", validate=True)


def verify_password_hash(password, encoded):
    try:
        password_bytes = _bounded_utf8(password)
        if password_bytes is None:
            return False
        if (
            not isinstance(encoded, str)
            or len(encoded) > PASSWORD_HASH_MAX_CHARS
        ):
            return False
        algo, rounds_s, salt_b64, digest_b64 = encoded.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        if not rounds_s.isascii() or not rounds_s.isdigit():
            return False
        rounds = int(rounds_s)
        if not PBKDF2_ROUNDS_MIN <= rounds <= PBKDF2_ROUNDS_MAX:
            return False
        salt = _b64url_decode_nopad(salt_b64)
        expected = _b64url_decode_nopad(digest_b64)
        if len(salt) != PBKDF2_SALT_BYTES:
            return False
        if len(expected) != PBKDF2_DIGEST_BYTES:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password_bytes, salt, rounds
        )
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def validate_online_counts(online):
    if not isinstance(online, dict) or len(online) > ONLINE_MAX_USERS:
        raise StateUnavailable("online state must be a bounded object")
    validated = {}
    for username, count in online.items():
        if not user_compat.is_valid_username(username):
            raise StateUnavailable("online state contains an invalid user")
        validated[username] = _non_negative_integer(
            count, f"online.{username}"
        )
    return validated


def get_online_counts(*, deadline=None, allow_snapshot=True):
    connection = None
    try:
        request_deadline = deadline
        if request_deadline is None:
            request_deadline = (
                time.monotonic() + ONLINE_API_TIMEOUT_SECONDS
            )
        timeout = _remaining_timeout(
            request_deadline,
            ONLINE_API_TIMEOUT_SECONDS,
        )
        # Use a direct loopback HTTPConnection so HTTP_PROXY/NO_PROXY cannot
        # redirect the management API secret outside this host. Production
        # sockets recompute their timeout before every read/write, so a peer
        # dripping headers or body bytes cannot extend the whole deadline.
        connection_class = http.client.HTTPConnection
        if connection_class is _STDLIB_HTTP_CONNECTION:
            connection = _DeadlineHTTPConnection(
                API_HOST,
                API_PORT,
                timeout=timeout,
                deadline=request_deadline,
            )
        else:
            # Retain a narrow injection seam for deterministic unit tests.
            connection = connection_class(
                API_HOST,
                API_PORT,
                timeout=timeout,
            )
        connection.request(
            "GET",
            "/online",
            headers={"Authorization": get_api_secret()},
        )
        response = connection.getresponse()
        if response.status != 200:
            raise StateUnavailable("online API returned an error")
        payload = response.read(ONLINE_RESPONSE_MAX_BYTES + 1)
        if _deadline_expired(request_deadline):
            raise StateUnavailable("authentication deadline exceeded")
        if len(payload) > ONLINE_RESPONSE_MAX_BYTES:
            raise StateUnavailable("online API response is too large")
        data = _strict_json_bytes(payload, label="online API response")
        return validate_online_counts(data)
    except Exception:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass
            connection = None
        try:
            if _deadline_expired(request_deadline):
                raise StateUnavailable("authentication deadline exceeded")
            if not allow_snapshot:
                raise StateUnavailable("live online API is unavailable")
            snapshot = _load_bounded_json(
                ONLINE_SNAPSHOT_FILE,
                {},
                required=True,
                maximum_bytes=ONLINE_RESPONSE_MAX_BYTES,
                label="online snapshot",
            )
            metadata = _load_bounded_json(
                online_snapshot.metadata_path(ONLINE_SNAPSHOT_FILE),
                {},
                required=True,
                maximum_bytes=4096,
                label="online snapshot metadata",
            )
            return validate_online_counts(
                online_snapshot.validate_fresh_snapshot(
                    snapshot,
                    metadata,
                    now=time.time(),
                    ttl_seconds=ONLINE_SNAPSHOT_TTL_SECONDS,
                )
            )
        except (
            StateUnavailable,
            online_snapshot.InvalidOnlineSnapshot,
        ) as exc:
            raise StateUnavailable(
                "fresh online state is unavailable"
            ) from exc
    finally:
        if connection is not None:
            try:
                connection.close()
            except OSError:
                pass


def _load_device_admissions(path):
    try:
        if path.stat().st_size > DEVICE_ADMISSION_MAX_BYTES:
            raise StateUnavailable("device admission ledger is too large")
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except StateUnavailable:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StateUnavailable(
            "device admission ledger is unavailable"
        ) from exc
    if not isinstance(data, dict):
        raise StateUnavailable("device admission ledger must be an object")
    validated = {}
    for username, record in data.items():
        if not isinstance(username, str) or not username:
            raise StateUnavailable(
                "device admission ledger has invalid entries"
            )
        # Accept the first implementation's list-only format for a safe,
        # one-way migration. Its missing baseline is initialized from the
        # current online observation by reserve_device_admission().
        if isinstance(record, list):
            observed = None
            timestamps = record
        elif isinstance(record, dict) and set(record) == {
            "observed", "pending"
        }:
            observed = _non_negative_integer(
                record["observed"],
                f"device_admissions.{username}.observed",
            )
            timestamps = record["pending"]
            if not isinstance(timestamps, list):
                raise StateUnavailable(
                    "device admission ledger has invalid entries"
                )
        else:
            raise StateUnavailable(
                "device admission ledger has invalid entries"
            )
        clean = []
        for timestamp in timestamps:
            if (
                isinstance(timestamp, bool)
                or not isinstance(timestamp, (int, float))
                or not math.isfinite(timestamp)
                or timestamp < 0
            ):
                raise StateUnavailable(
                    "device admission ledger has invalid timestamps"
                )
            clean.append(float(timestamp))
        validated[username] = {
            "observed": observed,
            "pending": clean,
        }
    return validated


def _save_device_admissions(path, admissions):
    payload = json.dumps(
        admissions,
        ensure_ascii=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n"
    encoded_size = len(payload.encode("utf-8"))
    if encoded_size > DEVICE_ADMISSION_MAX_BYTES:
        raise StateUnavailable("device admission ledger is too large")
    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
            text=True,
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fd = None
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        directory_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except (OSError, UnicodeError, ValueError) as exc:
        raise StateUnavailable(
            "device admission ledger could not be persisted"
        ) from exc
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def _acquire_device_lock(
    lock_fd,
    *,
    deadline=None,
    timeout=DEVICE_ADMISSION_LOCK_TIMEOUT_SECONDS,
):
    """Acquire an admission flock without ever waiting past a short deadline."""
    wait_budget = _remaining_timeout(deadline, timeout)
    lock_deadline = time.monotonic() + wait_budget
    nonblocking = fcntl.LOCK_EX | getattr(fcntl, "LOCK_NB", 4)
    while True:
        try:
            fcntl.flock(lock_fd, nonblocking)
            return
        except BlockingIOError:
            pass
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
        remaining = lock_deadline - time.monotonic()
        if remaining <= 0:
            raise StateUnavailable("device admission lock timed out")
        time.sleep(min(DEVICE_ADMISSION_LOCK_POLL_SECONDS, remaining))


def reserve_device_admission(
    username,
    *,
    max_devices,
    online_count,
    now=None,
    path=None,
    deadline=None,
):
    """Atomically reserve one short-lived device slot.

    The online API can lag a successful authentication. Live reservations are
    therefore added to the observed online count until the API has had time to
    reflect them. This deliberately errs toward a brief false rejection rather
    than admitting more than ``max_devices`` concurrent clients.
    """
    ledger_path = Path(
        DEVICE_ADMISSION_FILE if path is None else path
    )
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    current = time.time() if now is None else now
    if (
        not isinstance(username, str)
        or not username
        or isinstance(current, bool)
        or not isinstance(current, (int, float))
        or not math.isfinite(current)
        or current < 0
    ):
        raise StateUnavailable("invalid device admission request")
    maximum = _non_negative_integer(max_devices, "max_devices")
    observed = _non_negative_integer(online_count, f"online.{username}")
    if maximum <= 0:
        raise StateUnavailable("device admission requires a positive limit")

    lock_fd = None
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = os.open(str(lock_path), flags, 0o600)
        os.fchmod(lock_fd, 0o600)
        _acquire_device_lock(lock_fd, deadline=deadline)

        admissions = _load_device_admissions(ledger_path)
        cutoff = float(current) - DEVICE_ADMISSION_TTL_SECONDS
        pruned = {}
        for user, record in admissions.items():
            pending = [
                stamp for stamp in record["pending"] if stamp > cutoff
            ]
            if pending:
                pruned[user] = {
                    "observed": record["observed"],
                    "pending": pending,
                }
        changed = pruned != admissions
        record = pruned.get(username, {
            "observed": observed,
            "pending": [],
        })
        previous_observed = record["observed"]
        if previous_observed is None:
            previous_observed = observed
        pending = record["pending"]
        # When the authoritative online count rises, consume the oldest
        # reservations now reflected there. This prevents double-counting a
        # just-connected device while retaining protection against concurrent
        # authentications that all saw the same stale count.
        reflected = max(0, observed - previous_observed)
        if reflected:
            pending = pending[min(reflected, len(pending)):]
        allowed = observed + len(pending) < maximum
        if allowed:
            pending.append(float(current))
        if pending:
            pruned[username] = {
                "observed": observed,
                "pending": pending,
            }
        else:
            pruned.pop(username, None)
        if allowed or reflected or record.get("observed") != observed:
            changed = True
        if changed:
            _save_device_admissions(ledger_path, pruned)
        return allowed
    except StateUnavailable:
        raise
    except (OSError, ValueError) as exc:
        raise StateUnavailable("device admission state is unavailable") from exc
    finally:
        if lock_fd is not None:
            cleanup_error = None
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError as exc:
                cleanup_error = exc
            try:
                os.close(lock_fd)
            except OSError as exc:
                cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise StateUnavailable(
                    "device admission lock could not be released"
                ) from cleanup_error


def device_admission_state_ready(*, path=None, deadline=None):
    """Deep-probe the admission ledger without reserving a device slot."""
    ledger_path = Path(
        DEVICE_ADMISSION_FILE if path is None else path
    )
    lock_path = ledger_path.with_name(ledger_path.name + ".lock")
    lock_fd = None
    probe_fd = None
    probe_name = None
    try:
        ledger_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        lock_fd = os.open(str(lock_path), flags, 0o600)
        os.fchmod(lock_fd, 0o600)
        _acquire_device_lock(lock_fd, deadline=deadline)
        _load_device_admissions(ledger_path)

        # Prove the state directory remains writable/fsyncable without
        # touching the real ledger or consuming a pending admission.
        probe_fd, probe_name = tempfile.mkstemp(
            prefix=".device-admission-ready.",
            suffix=".tmp",
            dir=str(ledger_path.parent),
        )
        os.fchmod(probe_fd, 0o600)
        os.write(probe_fd, b"{}\n")
        os.fsync(probe_fd)
        os.close(probe_fd)
        probe_fd = None
        os.unlink(probe_name)
        probe_name = None
        return not _deadline_expired(deadline)
    except (StateUnavailable, OSError, ValueError):
        return False
    finally:
        if probe_fd is not None:
            try:
                os.close(probe_fd)
            except OSError:
                pass
        if probe_name is not None:
            try:
                os.unlink(probe_name)
            except OSError:
                pass
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_fd)
            except OSError:
                pass


def _validated_authorization_user(users, username, *, today):
    validate_unique_vless_uuids(users)
    user = users.get(username)
    if not isinstance(user, dict):
        raise StateUnavailable("authorization user is unavailable")
    if user_compat.authorization_config_error(user):
        raise StateUnavailable("authorization user is invalid")
    for field in (
        "monthly_quota_bytes",
        "quota_extra_bytes",
        "max_devices",
    ):
        if field in user:
            _non_negative_integer(user[field], field)
    if user_compat.is_inactive(user, today=today):
        raise StateUnavailable("authorization user is inactive")
    return user


def _authorization_generation(user):
    """Bind credentials and all persisted per-user policy to one digest."""
    try:
        canonical = json.dumps(
            user,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise StateUnavailable(
            "authorization generation cannot be computed"
        ) from exc
    return hashlib.sha256(canonical).digest()


def _verify_user_credential(
    user,
    password,
    *,
    source,
    deadline,
    password_limiter,
    allow_password,
):
    token = str(user.get("sub_token") or "")
    password_bytes = _bounded_utf8(password)
    token_bytes = _bounded_utf8(token)
    if password_bytes is None or (token and token_bytes is None):
        return False
    if token_bytes and hmac.compare_digest(password_bytes, token_bytes):
        return True
    if (
        not allow_password
        or not user.get("password_hash")
        or _deadline_expired(deadline)
    ):
        return False

    lease = None
    if password_limiter is not None:
        try:
            lease = password_limiter.try_acquire(
                source, deadline=deadline
            )
        except Exception:
            return False
        if lease is None:
            return False
    try:
        if _deadline_expired(deadline):
            return False
        accepted = verify_password_hash(
            password, str(user.get("password_hash") or "")
        )
        return accepted and not _deadline_expired(deadline)
    finally:
        if lease is not None:
            try:
                lease.release()
            except Exception:
                pass


def authenticate(
    username,
    password,
    *,
    addr=None,
    source=None,
    deadline=None,
    password_limiter=None,
    allow_password=True,
):
    """Return the canonical user id when credentials may connect.

    The callable deliberately has no process-control side effects: the
    persistent HTTP bridge can reuse it for every request, while ``main`` keeps
    the historical command-auth exit-code contract as a compatibility path.
    All malformed credentials and unavailable authorization state fail closed.
    """
    if (
        not user_compat.is_valid_username(username)
        or not isinstance(password, str)
        or _deadline_expired(deadline)
    ):
        return None
    try:
        users = load_json(USERS_FILE, {}, required=True)
        u = _validated_authorization_user(
            users, username, today=local_now().date()
        )
        initial_generation = _authorization_generation(u)
    except StateUnavailable:
        return None
    if not _verify_user_credential(
        u,
        password,
        source=source or addr or "unknown",
        deadline=deadline,
        password_limiter=password_limiter,
        allow_password=allow_password,
    ):
        return None

    if user_compat.is_metered(u):
        now = local_now()
        try:
            meta = load_json(META_FILE, {}, required=True) or {}
            daily = load_json(USAGE_DAILY_FILE, {}, required=True)
            validate_meta(meta)
            validate_daily_usage(daily)
            display_multiplier = effective_display_multiplier_strict(
                default=DEPLOYED_DISPLAY_MULTIPLIER,
                path=DISPLAY_MULTIPLIER_STATE_FILE,
            )
            if not math.isfinite(display_multiplier):
                raise ValueError("display multiplier must be finite")
        except StateUnavailable:
            return None
        except (OSError, UnicodeError, ValueError):
            return None
        try:
            used = sum(
                usage_total(
                    (daily.get(day_key) or {}).get(username, 0),
                    field=f"{day_key}.{username}",
                )
                for day_key in cycle_util.cycle_days(now, meta=meta)
            )
        except StateUnavailable:
            return None
        quota = user_compat.total_quota_bytes(u)
        if (
            quota > 0
            and Decimal(used) * Decimal(str(display_multiplier))
            >= Decimal(quota)
        ):
            return None

    max_devices = _non_negative_integer(
        u.get("max_devices", 0), "max_devices"
    )
    if max_devices > 0:
        try:
            online = get_online_counts(deadline=deadline)
            online_count = _non_negative_integer(
                online.get(username, 0), f"online.{username}"
            )
        except StateUnavailable:
            return None
        try:
            allowed = reserve_device_admission(
                username,
                max_devices=max_devices,
                online_count=online_count,
                deadline=deadline,
            )
        except StateUnavailable:
            return None
        if not allowed:
            return None

    # Management writes and this request do not share a long-running lock.
    # Re-read immediately before success and compare a fixed-size digest so a
    # credential rotation, disable, expiry, or policy edit observed during the
    # expensive part of auth can never succeed using the stale generation.
    try:
        current_users = load_json(USERS_FILE, {}, required=True)
        current = _validated_authorization_user(
            current_users, username, today=local_now().date()
        )
        current_generation = _authorization_generation(current)
    except StateUnavailable:
        return None
    if (
        _deadline_expired(deadline)
        or not hmac.compare_digest(
            initial_generation, current_generation
        )
    ):
        return None
    return username


def authenticate_payload(
    auth_payload,
    *,
    addr=None,
    source=None,
    deadline=None,
    password_limiter=None,
    allow_password=True,
):
    """Authenticate Hysteria's opaque ``username:secret`` payload."""
    if not isinstance(auth_payload, str) or ":" not in auth_payload:
        return None
    username, password = auth_payload.split(":", 1)
    return authenticate(
        username,
        password,
        addr=addr,
        source=source,
        deadline=deadline,
        password_limiter=password_limiter,
        allow_password=allow_password,
    )


def authorization_state_ready():
    """Validate the immutable authorization inputs used by every request.

    Quota/device dependencies remain request-specific and continue to fail
    closed in ``authenticate``.  This lightweight check makes service
    readiness meaningful without mutating ledgers or contacting Hysteria.
    """
    try:
        users = load_json(USERS_FILE, {}, required=True)
        validate_unique_vless_uuids(users)
        for cfg in users.values():
            if user_compat.authorization_config_error(cfg):
                return False
            for field in (
                "monthly_quota_bytes",
                "quota_extra_bytes",
                "max_devices",
            ):
                if field in cfg:
                    _non_negative_integer(cfg[field], field)
        if any(user_compat.is_metered(cfg) for cfg in users.values()):
            meta = load_json(META_FILE, {}, required=True)
            daily = load_json(USAGE_DAILY_FILE, {}, required=True)
            validate_meta(meta)
            validate_daily_usage(daily)
            multiplier = effective_display_multiplier_strict(
                default=DEPLOYED_DISPLAY_MULTIPLIER,
                path=DISPLAY_MULTIPLIER_STATE_FILE,
            )
            if not math.isfinite(multiplier):
                return False
        return True
    except (StateUnavailable, OSError, UnicodeError, ValueError):
        return False


def deep_authorization_state_ready(*, deadline=None):
    """Probe request-specific dependencies without consuming admission quota."""
    if not authorization_state_ready() or _deadline_expired(deadline):
        return False
    try:
        validate_online_counts(
            get_online_counts(
                deadline=deadline,
                allow_snapshot=False,
            )
        )
    except StateUnavailable:
        return False
    return device_admission_state_ready(deadline=deadline)


def main():
    if len(sys.argv) < 3:
        sys.exit(1)

    username = authenticate_payload(
        sys.argv[2] or "",
        source="command-auth",
        deadline=time.monotonic() + COMMAND_AUTH_DEADLINE_SECONDS,
        # The persistent service owns the global/concurrent PBKDF2 limiter.
        # A process-per-request compatibility path cannot enforce that budget,
        # so it intentionally accepts only the constant-time token credential.
        allow_password=False,
    )
    if username is None:
        sys.exit(1)

    sys.stdout.write(username)
    sys.exit(0)


if __name__ == "__main__":
    main()
