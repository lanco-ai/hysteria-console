"""Contracts for subscription and panel compatibility routes on FastAPI."""

import json

from fastapi.testclient import TestClient

from web_api import create_app
from web_api.services import LegacyPanelServices


class _Module:
    USER_SESSION_SUBSCRIPTION_TOKEN = "subscription"
    PASSWORD_MAX_LENGTH = 128

    class state_store:
        class StateStoreError(Exception):
            pass

    @staticmethod
    def request_multiplier_snapshot(function):
        return function

    @staticmethod
    def configured_public_host(raw):
        return raw.split(":", 1)[0]

    @staticmethod
    def safe_base_url(host, proto, port):
        return f"{proto}://{host}" + (f":{port}" if port else "")

    @staticmethod
    def check_user_token(user, token):
        if user == "alice" and token == "token":
            return {"sub_token": "token", "monthly_quota_bytes": 1000}
        return None

    @staticmethod
    def local_now():
        from datetime import datetime, timezone

        return datetime.now(timezone.utc)

    @staticmethod
    def normalize_subscription_profile(raw):
        return raw or "default"

    @staticmethod
    def subscription_template_mtime():
        return "1"

    @staticmethod
    def build_yaml(user, token, profile="default", generated_at=None):
        return f"user: {user}\ntoken: {token}\nprofile: {profile}\ngenerated: {generated_at}\n"

    @staticmethod
    def scaled_usage_for_user(_user):
        return 2, 3, 5

    @staticmethod
    def user_total_quota(_cfg):
        return 1000

    @staticmethod
    def render_profile_qr_svg(_base, user, token, profile):
        return f"<svg data-user='{user}' data-token='{token}' data-profile='{profile}'/>"

    @staticmethod
    def _build_panel_json_payload(user, _cfg, now=None):
        return {"user": user, "now": str(now)}

    @staticmethod
    def _credential_generation(_token):
        return "generation"

    @staticmethod
    def create_user_session(_user, _generation, _kind):
        return "sid"

    @staticmethod
    def user_session_cookie(sid, *, secure=False):
        return f"usid={sid}; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")

    @staticmethod
    def is_secure_request(_request):
        return False


class _Services:
    service_module = _Module()


def _client():
    return TestClient(create_app(LegacyPanelServices(_Module())))


def test_subscription_yaml_route_preserves_download_contract():
    with _client() as client:
        response = client.get("/sub/alice?token=token&profile=clash")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-subscription-profile"] == "clash"
    assert "token: token" in response.text


def test_panel_json_and_qr_reject_invalid_tokens_without_html():
    with _client() as client:
        json_response = client.get("/panel/alice.json?token=wrong")
        qr_response = client.get("/panel/alice/qr.svg?token=wrong")
    assert json_response.status_code == 403
    assert json_response.headers["content-type"].startswith("application/json")
    assert json_response.json() == {"error": "forbidden"}
    assert qr_response.status_code == 403
    assert "text/html" not in qr_response.headers["content-type"]


def test_panel_exchange_mints_user_session_and_redirects():
    with _client() as client:
        response = client.get("/panel/alice?token=token", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/user/panel"
    assert response.headers["set-cookie"].startswith("usid=sid;")
