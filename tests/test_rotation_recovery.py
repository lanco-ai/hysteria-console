import json
from types import SimpleNamespace

import pytest

import revocation_queue
import rotation_recovery
import state_store
import static_access
import subscription_service as ss


def test_receipt_is_session_bound_short_lived_and_rebindable(tmp_path):
    path = tmp_path / "receipts.json"
    request_id = "rotation-request-id-00000001"
    receipt = rotation_recovery.prepare(
        path,
        user="alice",
        request_id=request_id,
        session_id="old-browser-session",
        old_generation="old-generation",
        new_generation="new-generation",
        new_token="secret-new-token",
        new_uuid="33333333-3333-4333-8333-333333333333",
        now=100,
    )

    assert receipt["new_token"] == "secret-new-token"
    assert path.stat().st_mode & 0o777 == 0o600
    serialized = path.read_text(encoding="utf-8")
    assert "old-browser-session" not in serialized
    assert request_id not in serialized
    assert rotation_recovery.lookup_bound(
        path,
        user="alice",
        request_id=request_id,
        session_id="other-browser",
        now=101,
    ) is None
    assert rotation_recovery.lookup_bound(
        path,
        user="alice",
        request_id=request_id,
        session_id="old-browser-session",
        now=101,
    )["new_token"] == "secret-new-token"

    assert rotation_recovery.bind_replacement_session(
        path,
        user="alice",
        request_id=request_id,
        original_session_id="old-browser-session",
        replacement_session_id="new-browser-session",
        now=102,
    )
    assert rotation_recovery.lookup_bound(
        path,
        user="alice",
        request_id=request_id,
        session_id="new-browser-session",
        now=103,
    )["new_token"] == "secret-new-token"
    assert rotation_recovery.lookup_bound(
        path,
        user="alice",
        request_id=request_id,
        session_id="old-browser-session",
        now=100 + rotation_recovery.RECEIPT_TTL_SECONDS,
    ) is None
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_receipt_store_is_bounded_after_expiry_pruning(
    tmp_path, monkeypatch
):
    path = tmp_path / "receipts.json"
    monkeypatch.setattr(rotation_recovery, "RECEIPT_MAX_ENTRIES", 2)
    for index in range(2):
        rotation_recovery.prepare(
            path,
            user=f"user-{index}",
            request_id=f"rotation-request-id-0000000{index}",
            session_id=f"session-{index}",
            old_generation="old",
            new_generation=f"new-{index}",
            new_token=f"token-{index}",
            new_uuid=f"uuid-{index}",
            now=100,
        )

    with pytest.raises(rotation_recovery.RecoveryReceiptCapacityError):
        rotation_recovery.prepare(
            path,
            user="overflow",
            request_id="rotation-request-id-99999999",
            session_id="overflow-session",
            old_generation="old",
            new_generation="new-overflow",
            new_token="overflow-token",
            new_uuid="overflow-uuid",
            now=101,
        )

    rotation_recovery.prepare(
        path,
        user="after-expiry",
        request_id="rotation-request-id-88888888",
        session_id="fresh-session",
        old_generation="old",
        new_generation="new-fresh",
        new_token="fresh-token",
        new_uuid="fresh-uuid",
        now=100 + rotation_recovery.RECEIPT_TTL_SECONDS,
    )
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1


def test_background_maintenance_erases_expired_plaintext_without_new_request(
    tmp_path, monkeypatch
):
    usage_file = tmp_path / "usage.json"
    usage_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ss, "USAGE_FILE", usage_file)
    path = ss._rotation_receipts_path()
    rotation_recovery.prepare(
        path,
        user="alice",
        request_id="rotation-request-id-00000077",
        session_id="old-browser-session",
        old_generation="old-generation",
        new_generation="new-generation",
        new_token="expired-plaintext-token",
        new_uuid="33333333-3333-4333-8333-333333333333",
        now=100,
    )
    assert "expired-plaintext-token" in path.read_text(encoding="utf-8")
    monkeypatch.setattr(
        rotation_recovery.time,
        "time",
        lambda: 100 + rotation_recovery.RECEIPT_TTL_SECONDS,
    )

    assert ss._prune_expired_rotation_receipts() == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {}
    assert "expired-plaintext-token" not in path.read_text(encoding="utf-8")


def test_repeated_receipt_cleanup_failure_is_log_throttled(
    monkeypatch, capsys
):
    ss._reset_worker_error_log("rotation_receipt_cleanup")
    monkeypatch.setattr(
        ss.rotation_recovery,
        "prune_expired",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            state_store.LockTimeout("sensitive lock path")
        ),
    )
    monkeypatch.setattr(ss.time, "monotonic", lambda: 100.0)

    for _index in range(20):
        assert ss._prune_expired_rotation_receipts() == 0

    errors = capsys.readouterr().err.splitlines()
    assert errors == [
        "credential rotation receipt cleanup deferred: LockTimeout"
    ]
    assert "sensitive" not in errors[0]
    ss._reset_worker_error_log("rotation_receipt_cleanup")


def test_revocation_queue_requires_two_spaced_kick_successes(tmp_path):
    path = tmp_path / "revocations.json"
    task_id = revocation_queue.task_id_for("alice", "request")
    revocation_queue.prepare(
        path,
        task_id=task_id,
        user="alice",
        previous_generation="old-generation",
        target_generation="new-generation",
        now=100,
    )

    first = revocation_queue.complete_attempt(
        path,
        task_id,
        kick_ok=True,
        now=100,
    )
    assert first["complete"] is False
    assert first["kick_successes"] == 1
    assert revocation_queue.claim_due(path, now=101) is None
    claimed = revocation_queue.claim_due(path, now=102)
    assert claimed["task_id"] == task_id
    second = revocation_queue.complete_attempt(
        path,
        task_id,
        kick_ok=True,
        now=102,
    )
    assert second["complete"] is True
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_revocations_are_not_silently_age_pruned_and_capacity_stays_bounded(
    tmp_path, monkeypatch,
):
    path = tmp_path / "revocations.json"
    first_id = revocation_queue.task_id_for("alice", "request-1")
    revocation_queue.prepare(
        path,
        task_id=first_id,
        user="alice",
        previous_generation="old-1",
        target_generation="new-1",
        now=100,
    )
    long_after_legacy_expiry = 100 + revocation_queue.TASK_TTL_SECONDS + 1
    claimed = revocation_queue.claim_due(
        path,
        now=long_after_legacy_expiry,
    )
    assert claimed["task_id"] == first_id

    monkeypatch.setattr(revocation_queue, "TASK_MAX_ENTRIES", 2)
    second_id = revocation_queue.task_id_for("bob", "request-2")
    revocation_queue.prepare(
        path,
        task_id=second_id,
        user="bob",
        previous_generation="old-2",
        target_generation="new-2",
        now=long_after_legacy_expiry,
    )
    with pytest.raises(revocation_queue.RevocationQueueCapacityError):
        revocation_queue.prepare(
            path,
            task_id=revocation_queue.task_id_for("carol", "request-3"),
            user="carol",
            previous_generation="old-3",
            target_generation="new-3",
            now=long_after_legacy_expiry,
        )
    assert set(json.loads(path.read_text(encoding="utf-8"))) == {
        first_id,
        second_id,
    }


def test_failed_kick_and_static_stop_remain_durably_retryable(tmp_path):
    path = tmp_path / "revocations.json"
    task_id = revocation_queue.task_id_for("alice", "request")
    revocation_queue.prepare(
        path,
        task_id=task_id,
        user="alice",
        previous_generation="old",
        target_generation="new",
        now=100,
    )
    assert revocation_queue.add_static_services(
        path,
        task_id,
        [static_access.XRAY_SERVICE],
        now=100,
    )
    outcome = revocation_queue.complete_attempt(
        path,
        task_id,
        kick_ok=False,
        now=100,
    )
    assert outcome["kick_successes"] == 0
    assert outcome["static_pending"] == (static_access.XRAY_SERVICE,)
    stored = json.loads(path.read_text(encoding="utf-8"))[task_id]
    assert stored["next_attempt_at"] == (
        100 + revocation_queue.RETRY_DELAY_SECONDS
    )
    assert "token" not in json.dumps(stored)


def test_stop_result_distinguishes_confirmed_effect_from_marker_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        static_access,
        "_mark_pending",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            state_store.StateStoreError("marker unavailable")
        ),
    )

    result = static_access.stop_fail_closed(
        static_access.XRAY_SERVICE,
        reason=RuntimeError("unsafe state"),
        live=True,
        state_dir=tmp_path,
        runner=lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )

    assert result.ok is False
    assert result.effect_confirmed is True
    assert result.marker_persisted is False
    assert result.code == "stopped_marker_pending"
    assert result.retryable is True


def test_hy_kick_returns_secret_free_structured_failure(monkeypatch):
    class FailingConnection:
        sock = None

        def __init__(self, *_args, **_kwargs):
            pass

        def request(self, *_args, **_kwargs):
            raise TimeoutError("sensitive transport detail")

        def close(self):
            pass

    monkeypatch.setattr(
        ss.http.client,
        "HTTPConnection",
        FailingConnection,
    )
    monkeypatch.setattr(ss, "get_hy_api_secret", lambda: "test-secret")

    result = ss.hy_kick(["alice"])

    assert result.action == "hysteria_kick"
    assert result.target == "alice"
    assert result.ok is False
    assert result.retryable is True
    assert result.code == "TimeoutError"
    assert "sensitive" not in repr(result)


def test_hy_kick_ignores_proxy_environment_and_bounds_response(
    monkeypatch,
):
    calls = []

    class FakeSocket:
        def settimeout(self, value):
            calls.append(("timeout", value))

    class FakeResponse:
        status = 200

        def read(self, limit):
            calls.append(("read", limit))
            return b"{}"

    class DirectConnection:
        def __init__(self, host, port, *, timeout):
            calls.append(("connect", host, port, timeout))
            self.sock = FakeSocket()

        def request(self, method, path, *, body, headers):
            calls.append(("request", method, path, body, dict(headers)))

        def getresponse(self):
            return FakeResponse()

        def close(self):
            calls.append(("close",))

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:9999")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:9999")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr(
        ss.http.client,
        "HTTPConnection",
        DirectConnection,
    )
    monkeypatch.setattr(ss, "get_hy_api_secret", lambda: "test-secret")

    result = ss.hy_kick(["alice"])

    assert result.ok is True
    assert calls[0][:3] == ("connect", "127.0.0.1", 25413)
    request = next(call for call in calls if call[0] == "request")
    assert request[1:3] == ("POST", "/kick")
    assert next(call for call in calls if call[0] == "read") == (
        "read",
        ss.HY_KICK_MAX_RESPONSE_BYTES + 1,
    )
    assert calls[-1] == ("close",)


def test_retry_worker_performs_delayed_second_kick_and_acks_task(
    tmp_path, monkeypatch
):
    users_file = tmp_path / "users.json"
    usage_file = tmp_path / "usage.json"
    usage_lock = tmp_path / "usage.lock"
    users_file.write_text(
        json.dumps(
            {
                "alice": {
                    "sub_token": "new-token",
                    "vless_uuid": (
                        "33333333-3333-4333-8333-333333333333"
                    ),
                },
            },
        ),
        encoding="utf-8",
    )
    usage_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ss, "USERS_FILE", users_file)
    monkeypatch.setattr(ss, "USAGE_FILE", usage_file)
    monkeypatch.setattr(ss, "USAGE_LOCK_FILE", usage_lock)
    queue_path = ss._revocation_queue_path()
    task_id = revocation_queue.task_id_for("alice", "request")
    revocation_queue.prepare(
        queue_path,
        task_id=task_id,
        user="alice",
        previous_generation=ss._credential_generation("old-token"),
        target_generation=ss._credential_generation("new-token"),
        static_services=ss.static_access.SERVICES,
        now=100,
    )
    revocation_queue.complete_attempt(
        queue_path,
        task_id,
        kick_ok=True,
        now=100,
    )
    monkeypatch.setattr(revocation_queue.time, "time", lambda: 102)
    kicks = []
    static_syncs = []
    monkeypatch.setattr(
        ss,
        "_sync_static_access_from_users",
        lambda users: static_syncs.append(dict(users)) or (False, False),
    )
    monkeypatch.setattr(
        ss,
        "hy_kick",
        lambda users: (
            kicks.append(list(users))
            or ss.CredentialActionResult(
                action="hysteria_kick",
                target="alice",
                attempted=True,
                ok=True,
                code="accepted",
                retryable=False,
            )
        ),
    )

    assert ss._process_one_revocation_task() is True
    assert kicks == [["alice"]]
    assert static_syncs[0]["alice"]["sub_token"] == "new-token"
    assert json.loads(queue_path.read_text(encoding="utf-8")) == {}


def test_expired_precommit_rotation_intent_is_safely_discarded(
    tmp_path, monkeypatch,
):
    users_file = tmp_path / "users.json"
    usage_file = tmp_path / "usage.json"
    usage_lock = tmp_path / "usage.lock"
    users_file.write_text(
        json.dumps({"alice": {"sub_token": "old-token"}}),
        encoding="utf-8",
    )
    usage_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ss, "USERS_FILE", users_file)
    monkeypatch.setattr(ss, "USAGE_FILE", usage_file)
    monkeypatch.setattr(ss, "USAGE_LOCK_FILE", usage_lock)
    queue_path = ss._revocation_queue_path()
    task_id = revocation_queue.task_id_for("alice", "expired-precommit")
    revocation_queue.prepare(
        queue_path,
        task_id=task_id,
        user="alice",
        previous_generation=ss._credential_generation("old-token"),
        target_generation=ss._credential_generation("new-token"),
        now=1,
    )
    monkeypatch.setattr(
        ss,
        "hy_kick",
        lambda *_args, **_kwargs: pytest.fail(
            "an uncommitted rotation must not kick"
        ),
    )

    assert ss._process_one_revocation_task() is False
    assert json.loads(queue_path.read_text(encoding="utf-8")) == {}


def test_expired_committed_rotation_still_completes_both_kicks(
    tmp_path, monkeypatch,
):
    users_file = tmp_path / "users.json"
    usage_file = tmp_path / "usage.json"
    usage_lock = tmp_path / "usage.lock"
    users_file.write_text(
        json.dumps({"alice": {"sub_token": "new-token"}}),
        encoding="utf-8",
    )
    usage_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(ss, "USERS_FILE", users_file)
    monkeypatch.setattr(ss, "USAGE_FILE", usage_file)
    monkeypatch.setattr(ss, "USAGE_LOCK_FILE", usage_lock)
    queue_path = ss._revocation_queue_path()
    task_id = revocation_queue.task_id_for("alice", "expired-committed")
    revocation_queue.prepare(
        queue_path,
        task_id=task_id,
        user="alice",
        previous_generation=ss._credential_generation("old-token"),
        target_generation=ss._credential_generation("new-token"),
        now=1,
    )
    kicks = []
    monkeypatch.setattr(
        ss,
        "hy_kick",
        lambda users: kicks.append(list(users)),
    )

    assert ss._process_one_revocation_task() is True
    assert json.loads(
        queue_path.read_text(encoding="utf-8")
    )[task_id]["kick_successes"] == 1
    revocation_queue.release_claim(
        queue_path,
        task_id,
        delay=1,
        now=int(ss.time.time()) - 2,
    )
    assert ss._process_one_revocation_task() is True
    assert kicks == [["alice"], ["alice"]]
    assert json.loads(queue_path.read_text(encoding="utf-8")) == {}
