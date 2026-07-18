import json
from pathlib import Path

import pytest

import state_store
import xray_config as xc


def _config(*, clients=None, marker=None):
    clients = clients or {}
    config = {
        "log": {"loglevel": marker or "warning"},
        "inbounds": [
            {
                "protocol": "dokodemo-door",
                "port": 10085,
                "settings": {"address": "127.0.0.1"},
            },
            {
                "protocol": "vless",
                "port": 443,
                "settings": {"clients": list(clients.get(443, []))},
            },
            {
                "protocol": "vless",
                "port": 8443,
                "settings": {"clients": list(clients.get(8443, []))},
            },
        ],
    }
    return config


def _write(path, value):
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_initialize_missing_config_uses_atomic_writer_without_reload_marker(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.json"
    target = tmp_path / "runtime.json"
    _write(candidate, _config(marker="candidate"))
    replace_calls = []
    real_replace = xc.os.replace

    monkeypatch.setattr(xc.os, "geteuid", lambda: 1000)

    def tracking_replace(source, destination):
        replace_calls.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(xc.os, "replace", tracking_replace)

    assert xc.initialize_from_file(candidate, path=target) is True
    assert json.loads(target.read_text(encoding="utf-8")) == _config(
        marker="candidate"
    )
    assert target.stat().st_mode & 0o777 == xc.CONFIG_MODE
    assert len(replace_calls) == 1
    assert replace_calls[0][0].parent == target.parent
    assert replace_calls[0][1] == target
    assert not xc._reload_pending_path(target).exists()


def test_existing_runtime_config_and_pending_marker_are_preserved_byte_for_byte(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.json"
    target = tmp_path / "runtime.json"
    _write(candidate, _config(marker="new-template"))
    existing = _config(
        marker="operator-custom",
        clients={
            443: [
                {"id": "base", "email": "me"},
                {"id": "uuid-A", "email": "alice"},
                {"id": "uuid-B", "email": "bob"},
            ],
            8443: [
                {"id": "base", "email": "me-backup"},
                {"id": "uuid-A", "email": "alice-backup"},
                {"id": "uuid-B", "email": "bob-backup"},
            ],
        },
    )
    _write(target, existing)
    before = target.read_bytes()
    marker = xc._reload_pending_path(target)
    marker.write_text("pending-token\n", encoding="utf-8")
    replace_calls = []
    monkeypatch.setattr(
        xc.os,
        "replace",
        lambda *_args: replace_calls.append(_args),
    )

    assert xc.initialize_from_file(candidate, path=target) is False
    assert target.read_bytes() == before
    assert marker.read_text(encoding="utf-8") == "pending-token\n"
    assert replace_calls == []


def test_existing_runtime_config_permissions_are_normalized(
    tmp_path, monkeypatch
):
    candidate = tmp_path / "candidate.json"
    target = tmp_path / "runtime.json"
    _write(candidate, _config(marker="candidate"))
    _write(target, _config(marker="operator-custom"))
    target.chmod(0o600)
    secure_calls = []
    original_secure = xc._secure_config_permissions

    def tracking_secure(path):
        secure_calls.append(Path(path))
        original_secure(path)

    monkeypatch.setattr(xc, "_secure_config_permissions", tracking_secure)

    assert xc.initialize_from_file(candidate, path=target) is False
    assert secure_calls == [target]
    assert target.stat().st_mode & 0o777 == xc.CONFIG_MODE


@pytest.mark.parametrize(
    ("candidate_text", "existing_text", "message"),
    [
        ('{"inbounds":[]}', None, "both ports"),
        ('{"broken":', None, "candidate"),
        (
            json.dumps(_config(marker="candidate")),
            '{"broken":',
            "existing",
        ),
    ],
)
def test_invalid_candidate_or_existing_config_never_replaces_live_file(
    tmp_path, monkeypatch, candidate_text, existing_text, message
):
    candidate = tmp_path / "candidate.json"
    target = tmp_path / "runtime.json"
    candidate.write_text(candidate_text, encoding="utf-8")
    if existing_text is not None:
        target.write_text(existing_text, encoding="utf-8")
    before = target.read_bytes() if target.exists() else None
    replace_calls = []
    monkeypatch.setattr(
        xc.os,
        "replace",
        lambda *_args: replace_calls.append(_args),
    )

    with pytest.raises(state_store.InvalidJsonState, match=message):
        xc.initialize_from_file(candidate, path=target)

    if before is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before
    assert replace_calls == []
