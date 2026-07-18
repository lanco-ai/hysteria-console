import json
import sys

import pytest

import auth_backend as ab
import online_snapshot


class _ApiResponse:
    def __init__(self, payload, *, status=200):
        self._payload = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self, _maximum=None):
        return self._payload


class _ApiConnection:
    def __init__(self, response):
        self.response = response
        self.request_args = None
        self.closed = False

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def _configure_fallback(tmp_path, monkeypatch, snapshot, metadata=None):
    snapshot_path = tmp_path / "online.json"
    metadata_path = tmp_path / "online.meta.json"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    if metadata is not None:
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    monkeypatch.setattr(ab, "ONLINE_SNAPSHOT_FILE", str(snapshot_path))
    monkeypatch.setattr(
        ab.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("online API unavailable")
        ),
    )


def test_online_api_failure_accepts_matching_fresh_snapshot(
    tmp_path, monkeypatch
):
    snapshot = {"alice": 1}
    metadata = online_snapshot.build_metadata(
        snapshot, captured_at=1_000.0
    )
    _configure_fallback(tmp_path, monkeypatch, snapshot, metadata)
    monkeypatch.setattr(ab.time, "time", lambda: 1_019.0)

    assert ab.get_online_counts() == snapshot


def test_deep_readiness_requires_live_api_even_with_fresh_snapshot(
    tmp_path,
    monkeypatch,
):
    snapshot = {"alice": 1}
    metadata = online_snapshot.build_metadata(
        snapshot, captured_at=1_000.0
    )
    _configure_fallback(tmp_path, monkeypatch, snapshot, metadata)
    monkeypatch.setattr(ab.time, "time", lambda: 1_001.0)
    monkeypatch.setattr(ab, "authorization_state_ready", lambda: True)
    monkeypatch.setattr(
        ab,
        "device_admission_state_ready",
        lambda **_kwargs: True,
    )

    assert ab.get_online_counts() == snapshot
    assert ab.deep_authorization_state_ready() is False


def test_online_api_failure_rejects_expired_snapshot(
    tmp_path, monkeypatch
):
    snapshot = {"alice": 1}
    metadata = online_snapshot.build_metadata(
        snapshot, captured_at=1_000.0
    )
    _configure_fallback(tmp_path, monkeypatch, snapshot, metadata)
    monkeypatch.setattr(
        ab.time,
        "time",
        lambda: 1_000.0 + ab.ONLINE_SNAPSHOT_TTL_SECONDS + 0.001,
    )

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_online_api_failure_rejects_snapshot_without_capture_time(
    tmp_path, monkeypatch
):
    snapshot = {"alice": 1}
    metadata = online_snapshot.build_metadata(
        snapshot, captured_at=1_000.0
    )
    metadata.pop("captured_at_unix")
    _configure_fallback(tmp_path, monkeypatch, snapshot, metadata)
    monkeypatch.setattr(ab.time, "time", lambda: 1_001.0)

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_online_api_failure_rejects_legacy_snapshot_without_metadata(
    tmp_path, monkeypatch
):
    _configure_fallback(tmp_path, monkeypatch, {"alice": 1})
    monkeypatch.setattr(ab.time, "time", lambda: 1_001.0)

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_online_api_failure_rejects_unparseable_capture_metadata(
    tmp_path, monkeypatch
):
    snapshot = {"alice": 1}
    _configure_fallback(
        tmp_path,
        monkeypatch,
        snapshot,
        online_snapshot.build_metadata(snapshot, captured_at=1_000.0),
    )
    online_snapshot.metadata_path(ab.ONLINE_SNAPSHOT_FILE).write_text(
        '{"captured_at_unix":',
        encoding="utf-8",
    )
    monkeypatch.setattr(ab.time, "time", lambda: 1_001.0)

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_online_api_failure_rejects_metadata_for_different_snapshot(
    tmp_path, monkeypatch
):
    snapshot = {"alice": 1}
    metadata = online_snapshot.build_metadata(
        {"alice": 0}, captured_at=1_000.0
    )
    _configure_fallback(tmp_path, monkeypatch, snapshot, metadata)
    monkeypatch.setattr(ab.time, "time", lambda: 1_001.0)

    with pytest.raises(ab.StateUnavailable):
        ab.get_online_counts()


def test_live_online_api_success_does_not_require_snapshot_metadata(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        ab, "ONLINE_SNAPSHOT_FILE", str(tmp_path / "missing-online.json")
    )
    monkeypatch.setattr(
        ab.http.client,
        "HTTPConnection",
        lambda *_args, **_kwargs: _ApiConnection(
            _ApiResponse({"alice": 2})
        ),
    )

    assert ab.get_online_counts() == {"alice": 2}


def test_online_api_ignores_proxy_environment_and_targets_loopback(
    tmp_path, monkeypatch
):
    seen = {}
    connection = _ApiConnection(_ApiResponse({"alice": 1}))

    def connect(host, port, *, timeout):
        seen.update(host=host, port=port, timeout=timeout)
        return connection

    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setattr(ab.http.client, "HTTPConnection", connect)
    monkeypatch.setattr(ab, "get_api_secret", lambda: "LOCAL-ONLY")
    monkeypatch.setattr(
        ab, "ONLINE_SNAPSHOT_FILE", str(tmp_path / "missing.json")
    )

    assert ab.get_online_counts() == {"alice": 1}
    assert seen["host"] == "127.0.0.1"
    assert seen["port"] == 25413
    assert 0 < seen["timeout"] <= ab.ONLINE_API_TIMEOUT_SECONDS
    args, kwargs = connection.request_args
    assert args == ("GET", "/online")
    assert kwargs["headers"] == {"Authorization": "LOCAL-ONLY"}
    assert connection.closed


def test_user_without_device_cap_does_not_depend_on_online_fallback(
    tmp_path, monkeypatch, capsys
):
    users = tmp_path / "users.json"
    users.write_text(
        json.dumps({"alice": {"sub_token": "SECRET"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(ab, "USERS_FILE", str(users))
    monkeypatch.setattr(
        ab,
        "get_online_counts",
        lambda: (_ for _ in ()).throw(
            AssertionError("uncapped users must not read online state")
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["auth_backend.py", "hysteria", "alice:SECRET"],
    )

    with pytest.raises(SystemExit) as exc:
        ab.main()

    assert exc.value.code == 0
    assert capsys.readouterr().out == "alice"
