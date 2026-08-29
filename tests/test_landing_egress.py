import json

import pytest

import landing_egress as le
import state_store


def _node(**overrides):
    value = {
        "id": "la-home-1",
        "name": "洛杉矶家宽 1",
        "socks_ip": "8.8.8.8",
        "socks_port": 1080,
        "socks_username": "proxy-user",
        "socks_password": "proxy-secret",
        "expected_exit_ip": "1.1.1.1",
        "isp": "Example ISP",
        "region": "Los Angeles",
        "enabled": True,
    }
    value.update(overrides)
    return value


def test_validate_node_normalizes_public_ip_and_port():
    node = le.validate_node(_node(
        socks_ip="2001:4860:4860:0:0:0:0:8888",
        socks_port="1080",
        expected_exit_ip="2606:4700:4700:0:0:0:0:1111",
    ))

    assert node["socks_ip"] == "2001:4860:4860::8888"
    assert node["socks_port"] == 1080
    assert node["expected_exit_ip"] == "2606:4700:4700::1111"


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("id", "bad id", "node_id_invalid"),
        ("socks_ip", "proxy.example.com", "socks_ip_invalid"),
        ("socks_ip", "127.0.0.1", "socks_ip_not_global"),
        ("socks_ip", "10.0.0.1", "socks_ip_not_global"),
        ("socks_ip", "8.8.8.8/32", "socks_ip_invalid"),
        ("socks_port", 0, "socks_port_invalid"),
        ("socks_port", 65536, "socks_port_invalid"),
        ("expected_exit_ip", "https://1.1.1.1", "exit_ip_invalid"),
        ("expected_exit_ip", "127.0.0.1", "exit_ip_not_global"),
        ("expected_exit_ip", "10.0.0.1", "exit_ip_not_global"),
        ("name", "<script>x</script>", "node_text_invalid"),
        ("socks_password", "secret\nleak", "credential_invalid"),
    ],
)
def test_validate_node_rejects_unsafe_values(field, value, code):
    with pytest.raises(le.LandingEgressValidationError) as caught:
        le.validate_node(_node(**{field: value}))

    assert caught.value.code == code
    assert "proxy-secret" not in str(caught.value)


def test_public_node_never_exposes_endpoint_or_credentials():
    public = le.public_node(le.validate_node(_node()))

    assert public == {
        "id": "la-home-1",
        "name": "洛杉矶家宽 1",
        "exit_ip": "1.1.1.1",
        "isp": "Example ISP",
        "region": "Los Angeles",
        "enabled": True,
        "health": None,
    }
    payload = json.dumps(public)
    assert "8.8.8.8" not in payload
    assert "1080" not in payload
    assert "proxy-user" not in payload
    assert "proxy-secret" not in payload


def test_registry_missing_is_empty_but_corrupt_fails_closed(tmp_path):
    path = tmp_path / "landing_egresses.json"
    assert le.load_registry(path) == {"version": 1, "nodes": {}}

    path.write_text('{"version":1,"nodes":', encoding="utf-8")
    with pytest.raises(state_store.InvalidJsonState):
        le.load_registry(path)


def test_save_registry_is_atomic_and_root_only(tmp_path):
    path = tmp_path / "landing_egresses.json"
    registry = {
        "version": 1,
        "nodes": {"la-home-1": le.validate_node(_node())},
    }

    le.save_registry(registry, path)

    assert path.stat().st_mode & 0o777 == 0o600
    assert le.load_registry(path) == registry


def test_probe_exit_requires_observed_ip_to_match_expected():
    node = le.validate_node(_node())

    assert le.probe_exit(
        node,
        requester=lambda _node, _url, _timeout: " 1.1.1.1 ",
    ) == "1.1.1.1"

    with pytest.raises(le.LandingEgressProbeError) as caught:
        le.probe_exit(
            node,
            requester=lambda _node, _url, _timeout: "9.9.9.9",
        )
    assert caught.value.code == "exit_ip_mismatch"


def test_probe_exit_collapses_transport_errors_without_secret_text():
    node = le.validate_node(_node())

    def fail(_node, _url, _timeout):
        raise OSError("proxy-secret permission denied")

    with pytest.raises(le.LandingEgressProbeError) as caught:
        le.probe_exit(node, requester=fail)

    assert caught.value.code == "probe_failed"
    assert "proxy-secret" not in str(caught.value)


def test_probe_deadline_never_resets_between_socket_operations(monkeypatch):
    ticks = iter((100.0, 101.0, 104.9, 105.1))
    monkeypatch.setattr(le.time, "monotonic", lambda: next(ticks))
    deadline = le._probe_deadline(5)

    assert le._remaining_probe_time(deadline) == pytest.approx(4.0)
    assert le._remaining_probe_time(deadline) == pytest.approx(0.1)
    with pytest.raises(TimeoutError):
        le._remaining_probe_time(deadline)


def test_deadline_aware_send_refreshes_timeout_immediately_before_send(
    monkeypatch,
):
    class FakeSocket:
        def __init__(self):
            self.timeouts = []
            self.payloads = []

        def settimeout(self, value):
            self.timeouts.append(value)

        def sendall(self, payload):
            self.payloads.append(payload)

    sock = FakeSocket()
    monkeypatch.setattr(le.time, "monotonic", lambda: 104.9)

    le._send_with_deadline(sock, b"next", 105.0)

    assert sock.payloads == [b"next"]
    assert sock.timeouts == [pytest.approx(0.1)]
