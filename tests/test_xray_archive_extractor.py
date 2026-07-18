from __future__ import annotations

import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest


ROOT = Path(__file__).resolve().parents[1]
EXTRACTOR = ROOT / "scripts" / "hy2-extract-xray.py"

XRAY = b"trusted-xray-binary"
GEOIP = b"trusted-geoip"
GEOSITE = b"trusted-geosite"
README = b"readme"
LICENSE = b"license"


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _entry(
    name: str,
    data: bytes,
    mode: int = stat.S_IFREG | 0o644,
) -> tuple[zipfile.ZipInfo, bytes]:
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = mode << 16
    return info, data


def _valid_entries() -> list[tuple[zipfile.ZipInfo, bytes]]:
    return [
        _entry("geoip.dat", GEOIP),
        _entry("README.md", README),
        _entry("LICENSE", LICENSE),
        _entry("geosite.dat", GEOSITE),
        _entry("xray", XRAY, stat.S_IFREG | 0o755),
    ]


def _write_archive(
    path: Path,
    entries: list[tuple[zipfile.ZipInfo, bytes]],
) -> str:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for info, data in entries:
            archive.writestr(info, data)
    path.chmod(0o600)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(
    archive: Path,
    output: Path,
    archive_digest: str,
    *,
    xray_digest: str | None = None,
    geoip_digest: str | None = None,
    geosite_digest: str | None = None,
) -> subprocess.CompletedProcess[str]:
    output.mkdir(mode=0o700)
    return subprocess.run(
        [
            sys.executable,
            "-I",
            str(EXTRACTOR),
            "--archive",
            str(archive),
            "--output-dir",
            str(output),
            "--archive-sha256",
            archive_digest,
            "--xray-sha256",
            xray_digest or _digest(XRAY),
            "--geoip-sha256",
            geoip_digest or _digest(GEOIP),
            "--geosite-sha256",
            geosite_digest or _digest(GEOSITE),
        ],
        capture_output=True,
        text=True,
    )


def test_extracts_only_verified_runtime_members_with_fixed_modes(tmp_path):
    archive = tmp_path / "xray.zip"
    archive_digest = _write_archive(archive, _valid_entries())
    output = tmp_path / "output"

    result = _run(archive, output, archive_digest)

    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        "xray",
        "geoip.dat",
        "geosite.dat",
    }
    assert (output / "xray").read_bytes() == XRAY
    assert (output / "geoip.dat").read_bytes() == GEOIP
    assert (output / "geosite.dat").read_bytes() == GEOSITE
    assert stat.S_IMODE((output / "xray").stat().st_mode) == 0o755
    assert stat.S_IMODE((output / "geoip.dat").stat().st_mode) == 0o644
    assert stat.S_IMODE((output / "geosite.dat").stat().st_mode) == 0o644


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        (_entry("../xray", XRAY), "unsafe ZIP member name"),
        (_entry("/xray", XRAY), "unsafe ZIP member name"),
        (_entry(r"folder\\xray", XRAY), "unsafe ZIP member name"),
        (
            _entry("xray", b"target", stat.S_IFLNK | 0o777),
            "not a Unix regular file",
        ),
        (
            _entry("xray", b"device", stat.S_IFCHR | 0o600),
            "not a Unix regular file",
        ),
        (_entry("extra", b"unexpected"), "unexpected ZIP member"),
    ],
)
def test_rejects_unsafe_or_unexpected_members(tmp_path, replacement, message):
    entries = [
        entry for entry in _valid_entries() if entry[0].filename != "xray"
    ]
    entries.append(replacement)
    archive = tmp_path / "xray.zip"
    archive_digest = _write_archive(archive, entries)
    output = tmp_path / "output"

    result = _run(archive, output, archive_digest)

    assert result.returncode == 1
    assert message in result.stderr
    assert list(output.iterdir()) == []


def test_rejects_duplicate_member_names_before_extraction(tmp_path):
    entries = _valid_entries()
    entries.append(_entry("xray", b"second", stat.S_IFREG | 0o755))
    archive = tmp_path / "xray.zip"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="Duplicate name: 'xray'")
        archive_digest = _write_archive(archive, entries)
    output = tmp_path / "output"

    result = _run(archive, output, archive_digest)

    assert result.returncode == 1
    assert "duplicate ZIP member" in result.stderr
    assert list(output.iterdir()) == []


def test_rejects_archive_digest_before_extraction(tmp_path):
    archive = tmp_path / "xray.zip"
    _write_archive(archive, _valid_entries())
    output = tmp_path / "output"

    result = _run(archive, output, "0" * 64)

    assert result.returncode == 1
    assert "archive SHA-256 mismatch" in result.stderr
    assert list(output.iterdir()) == []


@pytest.mark.parametrize(
    ("digest_name", "message"),
    [
        ("xray_digest", "xray"),
        ("geoip_digest", "geoip.dat"),
        ("geosite_digest", "geosite.dat"),
    ],
)
def test_member_digest_failure_cleans_every_partial_output(
    tmp_path,
    digest_name,
    message,
):
    archive = tmp_path / "xray.zip"
    archive_digest = _write_archive(archive, _valid_entries())
    output = tmp_path / "output"
    trusted_install = tmp_path / "installed-xray"
    trusted_install.write_bytes(b"old-trusted-runtime")

    result = _run(
        archive,
        output,
        archive_digest,
        **{digest_name: "0" * 64},
    )

    assert result.returncode == 1
    assert f"SHA-256 mismatch: {message}" in result.stderr
    assert list(output.iterdir()) == []
    assert trusted_install.read_bytes() == b"old-trusted-runtime"


def test_rejects_member_over_single_file_limit(tmp_path):
    entries = [
        entry
        for entry in _valid_entries()
        if entry[0].filename != "geosite.dat"
    ]
    entries.append(_entry("geosite.dat", b"0" * (32 * 1024 * 1024 + 1)))
    archive = tmp_path / "xray.zip"
    archive_digest = _write_archive(archive, entries)
    output = tmp_path / "output"

    result = _run(archive, output, archive_digest)

    assert result.returncode == 1
    assert "member exceeds its byte limit" in result.stderr
    assert list(output.iterdir()) == []


def test_rejects_archive_over_total_uncompressed_limit():
    spec = importlib.util.spec_from_file_location("xray_extractor", EXTRACTOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    entries = [entry[0] for entry in _valid_entries()]
    sizes = {
        "xray": 64 * 1024 * 1024,
        "geoip.dat": 31 * 1024 * 1024,
        "geosite.dat": 2 * 1024 * 1024,
    }
    for entry in entries:
        if entry.filename in sizes:
            entry.file_size = sizes[entry.filename]

    archive = type("Archive", (), {"infolist": lambda _self: entries})()
    with pytest.raises(ValueError, match="total extraction limit"):
        module.validate_members(archive)
