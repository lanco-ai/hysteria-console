import hashlib
import io
import os
import subprocess
import tarfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
RESTORE_CHECK = ROOT / "scripts/hy2-restore-check.sh"


def _write_checksum(archive):
    archive = Path(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    Path(f"{archive}.sha256").write_text(
        f"{digest}  {archive.name}\n",
        encoding="ascii",
    )


def _archive_with_members(tmp_path, members):
    archive = tmp_path / "restore-test.tar.gz"
    with tarfile.open(archive, "w:gz") as handle:
        for name, member_type, payload in members:
            member = tarfile.TarInfo(name)
            member.type = member_type
            if member_type in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "root/hysteria/users.json"
            if member_type == tarfile.REGTYPE:
                member.size = len(payload)
                handle.addfile(member, io.BytesIO(payload))
            else:
                handle.addfile(member)
    _write_checksum(archive)
    return archive


def _run_restore_check(tmp_path, archive, **overrides):
    env = os.environ.copy()
    env.update(
        {
            "HY2_HY_DIR": str(tmp_path / "live"),
            "HY2_RESTORE_MAX_ARCHIVE_BYTES": str(16 * 1024 * 1024),
            "HY2_RESTORE_MAX_MEMBERS": "128",
            "HY2_RESTORE_MAX_FILE_BYTES": str(8 * 1024 * 1024),
            "HY2_RESTORE_MAX_TOTAL_BYTES": str(16 * 1024 * 1024),
        }
    )
    env.update(overrides)
    return subprocess.run(
        ["bash", str(RESTORE_CHECK), str(archive)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize(
    "member_name",
    (
        "__ABSOLUTE__",
        "root/hysteria/../../tmp/hy2-traversal",
        "C:/tmp/hy2-drive-path",
        r"root\hysteria\users.json",
        "root//hysteria/users.json",
    ),
)
def test_restore_check_rejects_noncanonical_paths_before_unpack(
    tmp_path,
    member_name,
):
    outside = tmp_path / "must-not-be-created"
    if member_name == "__ABSOLUTE__":
        member_name = str(outside)
    archive = _archive_with_members(
        tmp_path,
        [(member_name, tarfile.REGTYPE, b"{}")],
    )

    result = _run_restore_check(tmp_path, archive)

    assert result.returncode != 0
    assert "Unsafe archive:" in result.stderr
    assert not outside.exists()


@pytest.mark.parametrize(
    ("member_type", "label"),
    (
        (tarfile.SYMTYPE, "symlink"),
        (tarfile.LNKTYPE, "hardlink"),
        (tarfile.CHRTYPE, "character-device"),
        (tarfile.BLKTYPE, "block-device"),
        (tarfile.FIFOTYPE, "fifo"),
        (b"s", "socket"),
    ),
)
def test_restore_check_rejects_non_regular_members_before_unpack(
    tmp_path,
    member_type,
    label,
):
    archive = _archive_with_members(
        tmp_path,
        [(f"root/hysteria/{label}", member_type, b"")],
    )

    result = _run_restore_check(tmp_path, archive)

    assert result.returncode != 0
    assert "unsupported member type" in result.stderr


@pytest.mark.parametrize(
    ("members", "overrides", "expected_error"),
    (
        (
            [
                ("root/hysteria/one", tarfile.REGTYPE, b""),
                ("root/hysteria/two", tarfile.REGTYPE, b""),
                ("root/hysteria/three", tarfile.REGTYPE, b""),
            ],
            {"HY2_RESTORE_MAX_MEMBERS": "2"},
            "member count exceeds the 2-member limit",
        ),
        (
            [("root/hysteria/large", tarfile.REGTYPE, b"1234")],
            {"HY2_RESTORE_MAX_FILE_BYTES": "3"},
            "file exceeds the 3-byte limit",
        ),
        (
            [
                ("root/hysteria/one", tarfile.REGTYPE, b"123"),
                ("root/hysteria/two", tarfile.REGTYPE, b"456"),
            ],
            {"HY2_RESTORE_MAX_TOTAL_BYTES": "5"},
            "total file size exceeds the 5-byte limit",
        ),
    ),
)
def test_restore_check_enforces_expansion_limits(
    tmp_path,
    members,
    overrides,
    expected_error,
):
    archive = _archive_with_members(tmp_path, members)

    result = _run_restore_check(tmp_path, archive, **overrides)

    assert result.returncode != 0
    assert expected_error in result.stderr


def test_restore_check_rejects_file_directory_collisions(tmp_path):
    archive = _archive_with_members(
        tmp_path,
        [
            ("root/hysteria", tarfile.REGTYPE, b"not-a-directory"),
            ("root/hysteria/users.json", tarfile.REGTYPE, b"{}"),
        ],
    )

    result = _run_restore_check(tmp_path, archive)

    assert result.returncode != 0
    assert "regular file is also the parent" in result.stderr


def test_netfilter_oneshot_units_keep_only_required_privilege():
    required = {
        "User=root",
        "Group=root",
        "TimeoutStartSec=30s",
        "UMask=0077",
        "NoNewPrivileges=true",
        "PrivateDevices=true",
        "PrivateTmp=true",
        "ProtectHome=true",
        "ProtectSystem=strict",
        "ReadWritePaths=/run",
        "ProtectClock=true",
        "ProtectControlGroups=true",
        "ProtectHostname=true",
        "ProtectKernelLogs=true",
        "ProtectKernelModules=true",
        "ProtectKernelTunables=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK",
        "RestrictNamespaces=true",
        "RestrictRealtime=true",
        "RestrictSUIDSGID=true",
        "LockPersonality=true",
        "MemoryDenyWriteExecute=true",
        "AmbientCapabilities=CAP_NET_ADMIN",
        "CapabilityBoundingSet=CAP_NET_ADMIN",
        "SystemCallArchitectures=native",
        "TasksMax=16",
        "MemoryMax=64M",
    }

    for name in (
        "hysteria-porthop.service",
        "hysteria-tcp-mss.service",
    ):
        unit = (ROOT / "systemd" / name).read_text(encoding="utf-8")
        settings = {
            line.strip()
            for line in unit.splitlines()
            if line.strip() and not line.lstrip().startswith(("#", "["))
        }
        assert required <= settings
        assert "PrivateNetwork=true" not in settings
