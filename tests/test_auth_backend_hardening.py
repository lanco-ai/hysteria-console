import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime

import pytest

import auth_backend as ab


def _encode(value):
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _password_hash(password, *, rounds=200_000, salt=b"s" * 16):
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, rounds
    )
    return (
        f"pbkdf2_sha256${rounds}${_encode(salt)}${_encode(digest)}"
    )


def test_command_auth_compatibility_path_is_token_only_and_deadlined(
    tmp_path,
    monkeypatch,
    capsys,
):
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps(
            {
                "alice": {
                    "sub_token": "TOKEN",
                    "password_hash": _password_hash("legacy-password"),
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "USERS_FILE", str(users))
    monkeypatch.setattr(
        ab,
        "verify_password_hash",
        lambda *_args: pytest.fail("command auth attempted PBKDF2"),
    )

    monkeypatch.setattr(
        ab.sys,
        "argv",
        ["auth_backend.py", "ignored", "alice:legacy-password"],
    )
    with pytest.raises(SystemExit) as denied:
        ab.main()
    assert denied.value.code == 1

    monkeypatch.setattr(
        ab.sys,
        "argv",
        ["auth_backend.py", "ignored", "alice:TOKEN"],
    )
    with pytest.raises(SystemExit) as accepted:
        ab.main()
    assert accepted.value.code == 0
    assert capsys.readouterr().out == "alice"


def _configure(
    tmp_path,
    monkeypatch,
    *,
    user,
    password="SECRET",
    meta=None,
    daily=None,
    display_state=None,
):
    users_file = tmp_path / "users.json"
    meta_file = tmp_path / "subscription_meta.json"
    daily_file = tmp_path / "usage_daily.json"
    display_file = tmp_path / "display_multiplier.json"
    users_file.write_text(
        json.dumps({"alice": user}), encoding="utf-8"
    )
    meta_file.write_text(
        json.dumps(
            meta
            if meta is not None
            else {
                "settlement_day": 12,
                "cycle_length_days": 30,
                "cycle_anchor_date": "2026-01-12",
            }
        ),
        encoding="utf-8",
    )
    daily_file.write_text(
        json.dumps(daily if daily is not None else {}),
        encoding="utf-8",
    )
    if display_state is not None:
        display_file.write_text(display_state, encoding="utf-8")
    monkeypatch.setattr(ab, "USERS_FILE", str(users_file))
    monkeypatch.setattr(ab, "META_FILE", str(meta_file))
    monkeypatch.setattr(ab, "USAGE_DAILY_FILE", str(daily_file))
    monkeypatch.setattr(
        ab,
        "DEVICE_ADMISSION_FILE",
        str(tmp_path / "device_admissions.json"),
    )
    monkeypatch.setattr(
        ab, "DISPLAY_MULTIPLIER_STATE_FILE", str(display_file)
    )
    monkeypatch.setattr(
        ab,
        "local_now",
        lambda: datetime(2026, 1, 20, 12),
    )
    monkeypatch.delenv("HY_DISPLAY_MULTIPLIER", raising=False)
    monkeypatch.setattr(
        sys,
        "argv",
        ["auth_backend.py", "hysteria", f"alice:{password}"],
    )
    return daily_file


def _run_main():
    with pytest.raises(SystemExit) as exc:
        ab.main()
    return exc.value.code


def test_unicode_token_uses_utf8_constant_time_comparison(
    tmp_path, monkeypatch, capsys
):
    token = "密钥-🔐"
    _configure(
        tmp_path,
        monkeypatch,
        user={"sub_token": token},
        password=token,
    )

    assert _run_main() == 0
    assert capsys.readouterr().out == "alice"


@pytest.mark.parametrize(
    "invalid_field",
    [
        {"monthly_quota_bytes": -1},
        {"quota_extra_bytes": 1.5},
        {"max_devices": -1},
        {"max_devices": 1.0},
        {"expires_at": "not-a-date"},
        {"disabled": "false"},
    ],
)
def test_malformed_authorization_config_fails_closed(
    tmp_path, monkeypatch, invalid_field
):
    _configure(
        tmp_path,
        monkeypatch,
        user={"sub_token": "SECRET", **invalid_field},
    )

    assert _run_main() == 1


def test_pbkdf2_verifier_bounds_password_and_hash_work_factor(monkeypatch):
    valid = _password_hash("correct")
    assert ab.verify_password_hash("correct", valid)

    calls = []
    monkeypatch.setattr(
        ab.hashlib,
        "pbkdf2_hmac",
        lambda *_args, **_kwargs: calls.append(True),
    )
    assert not ab.verify_password_hash(
        "x" * (ab.AUTH_SECRET_MAX_CHARS + 1), valid
    )
    assert not ab.verify_password_hash(
        "correct",
        valid.replace("$200000$", f"${ab.PBKDF2_ROUNDS_MAX + 1}$"),
    )
    assert not ab.verify_password_hash(
        "correct",
        valid.replace("$200000$", f"${ab.PBKDF2_ROUNDS_MIN - 1}$"),
    )
    assert not ab.verify_password_hash(
        "correct", "x" * (ab.PASSWORD_HASH_MAX_CHARS + 1)
    )
    assert calls == []


@pytest.mark.parametrize(
    "salt,digest",
    [
        (b"s" * 15, b"d" * 32),
        (b"s" * 17, b"d" * 32),
        (b"s" * 16, b"d" * 31),
        (b"s" * 16, b"d" * 33),
    ],
)
def test_pbkdf2_verifier_requires_expected_salt_and_digest_sizes(
    salt, digest
):
    encoded = (
        f"pbkdf2_sha256$200000${_encode(salt)}${_encode(digest)}"
    )
    assert not ab.verify_password_hash("correct", encoded)


@pytest.mark.parametrize(
    "meta",
    [
        {"settlement_day": 0},
        {"settlement_day": 29},
        {"settlement_day": "tomorrow"},
        {"cycle_length_days": 0},
        {"cycle_length_days": 91},
        {"cycle_length_days": 1.5},
        {"cycle_anchor_date": "2026-02-30"},
        {"cycle_anchor_date": "2026-1-2"},
    ],
)
def test_metered_user_rejects_invalid_cycle_metadata(
    tmp_path, monkeypatch, meta
):
    _configure(
        tmp_path,
        monkeypatch,
        user={
            "sub_token": "SECRET",
            "metered": True,
            "monthly_quota_bytes": 1_000_000,
        },
        meta=meta,
    )

    assert _run_main() == 1


@pytest.mark.parametrize(
    "entry",
    [
        {"tx": -1, "rx": 2, "total": 1},
        {"tx": 1, "rx": -1, "total": 0},
        {"tx": 1, "rx": 2, "total": 4},
        {"tx": 1, "rx": 2, "total": 3, "other": 0},
        -1,
        None,
        False,
        1.5,
    ],
)
def test_metered_user_rejects_corrupt_daily_usage_entries(
    tmp_path, monkeypatch, entry
):
    _configure(
        tmp_path,
        monkeypatch,
        user={
            "sub_token": "SECRET",
            "metered": True,
            "monthly_quota_bytes": 1_000_000,
        },
        daily={"2026-01-20": {"alice": entry}},
    )

    assert _run_main() == 1


def test_metered_user_rejects_corrupt_runtime_display_policy(
    tmp_path, monkeypatch
):
    _configure(
        tmp_path,
        monkeypatch,
        user={
            "sub_token": "SECRET",
            "metered": True,
            "monthly_quota_bytes": 1_000_000,
        },
        display_state='{"enabled": true, "multiplier": "bad"}',
    )

    assert _run_main() == 1


def test_metered_user_rejects_non_finite_runtime_display_policy(
    tmp_path, monkeypatch
):
    _configure(
        tmp_path,
        monkeypatch,
        user={
            "sub_token": "SECRET",
            "metered": True,
            "monthly_quota_bytes": 1_000_000,
        },
        display_state='{"enabled": true, "multiplier": "nan"}',
    )

    assert _run_main() == 1


def test_runtime_display_multiplier_is_used_for_quota_enforcement(
    tmp_path, monkeypatch
):
    _configure(
        tmp_path,
        monkeypatch,
        user={
            "sub_token": "SECRET",
            "metered": True,
            "monthly_quota_bytes": 150,
        },
        daily={
            "2026-01-20": {
                "alice": {"tx": 40, "rx": 40, "total": 80}
            }
        },
        display_state='{"enabled": true, "multiplier": 2}',
    )

    assert _run_main() == 1


def test_unmetered_user_still_enforces_max_devices(
    tmp_path, monkeypatch
):
    _configure(
        tmp_path,
        monkeypatch,
        user={"sub_token": "SECRET", "max_devices": 2},
    )
    monkeypatch.setattr(
        ab, "get_online_counts", lambda **_kwargs: {"alice": 2}
    )

    assert _run_main() == 1


@pytest.mark.parametrize("online_count", [-1, None, True, 1.5, "two"])
def test_invalid_online_count_fails_closed(
    tmp_path, monkeypatch, online_count
):
    _configure(
        tmp_path,
        monkeypatch,
        user={"sub_token": "SECRET", "max_devices": 2},
    )
    monkeypatch.setattr(
        ab,
        "get_online_counts",
        lambda **_kwargs: {"alice": online_count},
    )

    assert _run_main() == 1


def test_device_admission_reservations_fill_stale_online_slots_atomically(
    tmp_path, monkeypatch, capsys
):
    _configure(
        tmp_path,
        monkeypatch,
        user={"sub_token": "SECRET", "max_devices": 2},
    )
    monkeypatch.setattr(
        ab, "get_online_counts", lambda **_kwargs: {"alice": 0}
    )

    assert _run_main() == 0
    assert _run_main() == 0
    assert _run_main() == 1
    assert capsys.readouterr().out == "alicealice"
    ledger = json.loads(
        Path(ab.DEVICE_ADMISSION_FILE).read_text(encoding="utf-8")
    )
    assert ledger["alice"]["observed"] == 0
    assert len(ledger["alice"]["pending"]) == 2


def test_online_growth_consumes_reflected_reservation_without_losing_slot(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    monkeypatch.setattr(ab, "DEVICE_ADMISSION_FILE", str(ledger))

    assert ab.reserve_device_admission(
        "alice", max_devices=2, online_count=0, now=100
    )
    # The first connection is now reflected by /online. It must no longer be
    # counted again as pending, so the second device can use the remaining slot.
    assert ab.reserve_device_admission(
        "alice", max_devices=2, online_count=1, now=101
    )
    assert not ab.reserve_device_admission(
        "alice", max_devices=2, online_count=1, now=102
    )


def test_device_admission_reservation_expires_after_api_catchup_window(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    monkeypatch.setattr(ab, "DEVICE_ADMISSION_FILE", str(ledger))

    assert ab.reserve_device_admission(
        "alice", max_devices=1, online_count=0, now=100
    )
    assert not ab.reserve_device_admission(
        "alice", max_devices=1, online_count=0, now=101
    )
    assert ab.reserve_device_admission(
        "alice",
        max_devices=1,
        online_count=0,
        now=100 + ab.DEVICE_ADMISSION_TTL_SECONDS + 1,
    )


def test_device_admission_reservation_fails_closed_on_corrupt_state(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    ledger.write_text('{"alice": [', encoding="utf-8")
    monkeypatch.setattr(ab, "DEVICE_ADMISSION_FILE", str(ledger))

    with pytest.raises(ab.StateUnavailable):
        ab.reserve_device_admission(
            "alice", max_devices=2, online_count=0, now=100
        )
    assert ledger.read_text(encoding="utf-8") == '{"alice": ['


def test_device_admission_reservation_fails_closed_on_persist_error(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    monkeypatch.setattr(ab, "DEVICE_ADMISSION_FILE", str(ledger))
    monkeypatch.setattr(
        ab,
        "_save_device_admissions",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("read-only state")
        ),
    )

    with pytest.raises(ab.StateUnavailable):
        ab.reserve_device_admission(
            "alice", max_devices=2, online_count=0, now=100
        )


def test_device_admission_reservation_fails_closed_on_lock_error(
    tmp_path, monkeypatch
):
    ledger = tmp_path / "device_admissions.json"
    monkeypatch.setattr(ab, "DEVICE_ADMISSION_FILE", str(ledger))
    monkeypatch.setattr(
        ab.fcntl,
        "flock",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("lock unavailable")
        ),
    )

    with pytest.raises(ab.StateUnavailable):
        ab.reserve_device_admission(
            "alice", max_devices=2, online_count=0, now=100
        )
    assert not ledger.exists()


def test_device_admission_reservation_is_process_safe(tmp_path):
    """Independent interpreters exercise the real kernel flock."""
    ledger = tmp_path / "device_admissions.json"
    start = tmp_path / "start"
    auth_dir = Path(ab.__file__).resolve().parent
    script = """
import sys
import time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import auth_backend
while not Path(sys.argv[3]).exists():
    time.sleep(0.001)
try:
    allowed = auth_backend.reserve_device_admission(
        "alice",
        max_devices=2,
        online_count=0,
        now=100.0,
        path=sys.argv[2],
    )
except Exception:
    print("error")
else:
    print("allowed" if allowed else "denied")
"""
    processes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(auth_dir),
                str(ledger),
                str(start),
            ],
            cwd=str(tmp_path),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for _ in range(8)
    ]
    start.touch()
    results = []
    errors = []
    for process in processes:
        stdout, stderr = process.communicate(timeout=10)
        results.append(stdout.strip())
        errors.append(stderr)
        assert process.returncode == 0

    assert errors == [""] * len(processes)
    assert results.count("allowed") == 2
    assert results.count("denied") == 6
    assert "error" not in results


@pytest.mark.parametrize(
    "other_uuid",
    [
        "not-a-uuid",
        "4f7f8fcf-b3fe-49db-98db-3ff96b77cb1b",
        "{4F7F8FCF-B3FE-49DB-98DB-3FF96B77CB1B}",
    ],
)
def test_users_file_rejects_invalid_or_duplicate_vless_uuid_before_auth(
    tmp_path, monkeypatch, other_uuid
):
    users_file = tmp_path / "users.json"
    users_file.write_text(
        json.dumps(
            {
                "alice": {
                    "sub_token": "SECRET",
                    "vless_uuid": "4f7f8fcf-b3fe-49db-98db-3ff96b77cb1b",
                },
                "bob": {
                    "sub_token": "OTHER",
                    "vless_uuid": other_uuid,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "USERS_FILE", str(users_file))
    monkeypatch.setattr(
        ab,
        "DEVICE_ADMISSION_FILE",
        str(tmp_path / "device_admissions.json"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["auth_backend.py", "hysteria", "alice:SECRET"],
    )

    assert _run_main() == 1
