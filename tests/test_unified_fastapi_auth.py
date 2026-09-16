"""Contracts for the unified FastAPI Hysteria HTTP-auth endpoint."""

import json

from fastapi.testclient import TestClient

import auth_backend
import auth_service
from web_api import create_app


class _Services:
    service_module = object()


def _client():
    return TestClient(create_app(_Services()))


def _payload(auth="alice:SECRET"):
    return json.dumps(
        {"addr": "198.51.100.4:44321", "auth": auth, "tx": 12500000},
        separators=(",", ":"),
    ).encode()


def test_unified_auth_liveness_and_readiness_are_json(monkeypatch):
    monkeypatch.setattr(auth_backend, "deep_authorization_state_ready", lambda **_: True)
    with _client() as client:
        live = client.get("/livez")
        ready = client.get("/readyz")
    assert live.status_code == 200
    assert live.json() == {"ok": True}
    assert ready.status_code == 200
    assert ready.json() == {"ok": True}
    assert live.headers["cache-control"] == "no-store"


def test_unified_auth_reuses_strict_decoder_and_returns_protocol_200(monkeypatch):
    seen = []

    def authenticate(payload, **_kwargs):
        seen.append(payload)
        return "alice" if payload == "alice:SECRET" else None

    monkeypatch.setattr(auth_service.auth_backend, "authenticate_payload", authenticate)
    with _client() as client:
        headers = {"Content-Type": "application/json"}
        accepted = client.post("/auth", content=_payload(), headers=headers)
        rejected = client.post("/auth", content=_payload("alice:WRONG"), headers=headers)
        malformed = client.post("/auth", content=b"not-json", headers=headers)
    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True, "id": "alice"}
    assert rejected.status_code == 200
    assert rejected.json() == {"ok": False}
    assert malformed.status_code == 400
    assert malformed.json() == {"ok": False}
    assert seen == ["alice:SECRET", "alice:WRONG"]


def test_unified_auth_rejects_wrong_content_type_and_method():
    with _client() as client:
        wrong_type = client.post("/auth", content=_payload(), headers={"Content-Type": "text/plain"})
        unsupported = client.put("/auth", content=b"")
    assert wrong_type.status_code == 415
    assert unsupported.status_code == 405
    assert unsupported.json() == {"ok": False}
