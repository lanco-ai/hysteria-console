import json
import uuid

import pytest

import landing_egress as le
import subscription_service as ss
from tests.test_share_panel_and_landing import (
    NOW,
    _alice,
    _configure_state,
    _request,
    _running_server,
)


def _registry():
    return {
        "version": 1,
        "nodes": {
            "la-home-1": le.validate_node({
                "id": "la-home-1",
                "name": "洛杉矶家宽",
                "socks_ip": "8.8.8.8",
                "socks_port": 1080,
                "socks_username": "proxy-user",
                "socks_password": "proxy-secret",
                "expected_exit_ip": "1.1.1.1",
                "isp": "Example ISP",
                "region": "Los Angeles",
                "enabled": True,
            }),
            "ny-home-1": le.validate_node({
                "id": "ny-home-1",
                "name": "纽约家宽",
                "socks_ip": "9.9.9.9",
                "socks_port": 1080,
                "socks_username": "ny-user",
                "socks_password": "ny-secret",
                "expected_exit_ip": "8.8.4.4",
                "isp": "Other ISP",
                "region": "New York",
                "enabled": True,
            }),
        },
    }


def _state(tmp_path, monkeypatch, *, session_kind=ss.USER_SESSION_PANEL_PASSWORD,
           selected=None, changed_at=None):
    cfg = _alice(
        landing_vless_uuid="22222222-2222-4222-8222-222222222222",
        landing_allowed_egress_ids=["la-home-1"],
    )
    if selected is not None:
        cfg["landing_selected_egress_id"] = selected
    if changed_at is not None:
        cfg["landing_egress_changed_at"] = changed_at
    state = _configure_state(tmp_path, monkeypatch, users={"alice": cfg})
    registry_file = tmp_path / "landing_egresses.json"
    le.save_registry(_registry(), registry_file)
    monkeypatch.setattr(le, "REGISTRY_FILE", registry_file)
    monkeypatch.setattr(
        ss, "get_logged_in_user_context",
        lambda _handler: ("alice", session_kind),
    )
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users", lambda _users, **_kwargs: (False, False),
    )
    return state


def test_user_panel_password_session_can_select_only_public_node_data(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    cfg = json.loads(state["USERS_FILE"].read_text())["alice"]

    page = ss.render_user_panel(
        "panel.test", "https://panel.test", "alice", "", cfg,
        session_auth=True,
        session_kind=ss.USER_SESSION_PANEL_PASSWORD,
    )

    assert 'action="/user/landing-egress/select"' in page
    assert "洛杉矶家宽" in page
    assert "1.1.1.1" in page
    assert "纽约家宽" not in page
    assert "8.8.8.8" not in page
    assert "proxy-user" not in page
    assert "proxy-secret" not in page


def test_token_panel_session_is_read_only_for_egress_selection(
    tmp_path, monkeypatch,
):
    state = _state(
        tmp_path, monkeypatch,
        session_kind=ss.USER_SESSION_SUBSCRIPTION_TOKEN,
    )
    cfg = json.loads(state["USERS_FILE"].read_text())["alice"]

    page = ss.render_user_panel(
        "panel.test", "https://panel.test", "alice", "", cfg,
        session_auth=True,
        session_kind=ss.USER_SESSION_SUBSCRIPTION_TOKEN,
    )

    assert 'action="/user/landing-egress/select"' not in page
    assert "使用面板密码登录后可切换" in page


def test_password_session_selects_authorized_healthy_egress(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    before = json.loads(state["USERS_FILE"].read_text())["alice"]
    revision = ss.user_config_revision(before)
    monkeypatch.setattr(le, "probe_exit", lambda _node: "1.1.1.1")

    with _running_server() as server:
        status, headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={"egress_id": "la-home-1", "user_revision": revision},
        )

    saved = json.loads(state["USERS_FILE"].read_text())["alice"]
    assert status == 303
    assert headers["location"].startswith("/user/panel?msg=")
    assert saved["landing_selected_egress_id"] == "la-home-1"
    assert saved["landing_egress_changed_at"]


def test_successful_selection_schedules_xray_reload_when_candidate_changes(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    cfg = json.loads(state["USERS_FILE"].read_text())["alice"]
    monkeypatch.setattr(le, "probe_exit", lambda _node: "1.1.1.1")
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users", lambda _users, **_kwargs: (True, False),
    )
    reloads = []
    monkeypatch.setattr(ss.xray_config, "reload_async", lambda: reloads.append(True))

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={
                "egress_id": "la-home-1",
                "user_revision": ss.user_config_revision(cfg),
            },
        )

    assert status == 303
    assert reloads == [True]


def test_selection_rejects_unassigned_node_without_probing_or_mutating(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    before_text = state["USERS_FILE"].read_text()
    revision = ss.user_config_revision(json.loads(before_text)["alice"])
    probed = []
    monkeypatch.setattr(le, "probe_exit", lambda node: probed.append(node))

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={"egress_id": "ny-home-1", "user_revision": revision},
        )

    assert status == 403
    assert probed == []
    assert state["USERS_FILE"].read_text() == before_text


def test_selection_rejects_disabled_user_without_probing(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    users = json.loads(state["USERS_FILE"].read_text())
    users["alice"]["disabled"] = True
    state["USERS_FILE"].write_text(json.dumps(users))
    probed = []
    monkeypatch.setattr(le, "probe_exit", lambda node: probed.append(node))

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={
                "egress_id": "la-home-1",
                "user_revision": ss.user_config_revision(users["alice"]),
            },
        )

    assert status == 403
    assert probed == []


def test_registry_change_during_probe_aborts_without_user_mutation(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    before_text = state["USERS_FILE"].read_text()
    cfg = json.loads(before_text)["alice"]

    def mutate_registry(_node):
        registry = le.load_registry()
        registry["nodes"]["la-home-1"]["socks_ip"] = "9.9.9.9"
        le.save_registry(registry)
        return "1.1.1.1"

    monkeypatch.setattr(le, "probe_exit", mutate_registry)
    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={
                "egress_id": "la-home-1",
                "user_revision": ss.user_config_revision(cfg),
            },
        )

    assert status == 409
    assert state["USERS_FILE"].read_text() == before_text


def test_probe_failure_does_not_mutate_selection(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    before_text = state["USERS_FILE"].read_text()
    revision = ss.user_config_revision(json.loads(before_text)["alice"])

    def fail(_node):
        raise le.LandingEgressProbeError("probe_failed")

    monkeypatch.setattr(le, "probe_exit", fail)
    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={"egress_id": "la-home-1", "user_revision": revision},
        )

    assert status == 422
    assert b"proxy-secret" not in body
    assert state["USERS_FILE"].read_text() == before_text


def test_selection_enforces_sixty_second_cooldown(tmp_path, monkeypatch):
    state = _state(
        tmp_path, monkeypatch,
        selected="la-home-1",
        changed_at=NOW.isoformat(),
    )
    cfg = json.loads(state["USERS_FILE"].read_text())["alice"]
    monkeypatch.setattr(le, "probe_exit", lambda _node: "1.1.1.1")
    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={
                "egress_id": "la-home-1",
                "user_revision": ss.user_config_revision(cfg),
            },
        )
    assert status == 429


def test_selection_sync_failure_rolls_back_user_record(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    before_text = state["USERS_FILE"].read_text()
    cfg = json.loads(before_text)["alice"]
    monkeypatch.setattr(le, "probe_exit", lambda _node: "1.1.1.1")

    def fail_sync(_users, **_kwargs):
        raise ss.state_store.CriticalStateUnavailable("candidate invalid")

    monkeypatch.setattr(ss, "_sync_static_access_from_users", fail_sync)
    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/user/landing-egress/select",
            form={
                "egress_id": "la-home-1",
                "user_revision": ss.user_config_revision(cfg),
            },
        )

    assert status == 503
    assert b"candidate invalid" not in body
    assert state["USERS_FILE"].read_text() == before_text


def test_admin_registry_page_never_renders_socks_password(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    page = ss.render_landing_egresses("panel.test")

    assert "洛杉矶家宽" in page
    assert "8.8.8.8:1080" in page
    assert "proxy-secret" not in page
    assert 'type="password"' in page


def test_admin_can_create_node_without_echoing_credentials(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    with _running_server() as server:
        status, headers, body = _request(
            server,
            "POST",
            "/admin/landing-egress/save",
            form={
                "id": "sf-home-1",
                "name": "旧金山家宽",
                "socks_ip": "2001:4860:4860::8888",
                "socks_port": "1080",
                "socks_username": "sf-user",
                "socks_password": "sf-secret",
                "expected_exit_ip": "2606:4700:4700::1111",
                "isp": "Example ISP",
                "region": "San Francisco",
                "enabled": "1",
                "registry_revision": ss.content_revision(le.load_registry()),
            },
        )

    saved = le.load_registry()
    assert status == 303
    assert headers["location"].startswith("/admin/landing-egresses?msg=")
    assert body == b""
    assert saved["nodes"]["sf-home-1"]["socks_password"] == "sf-secret"


def test_admin_assignment_generates_distinct_landing_uuid_and_syncs(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    syncs = []
    monkeypatch.setattr(
        ss, "_sync_static_access_from_users",
        lambda users, **_kwargs: syncs.append(users) or (False, False),
    )
    before = json.loads(state["USERS_FILE"].read_text())["alice"]
    before.pop("landing_vless_uuid")
    before["landing_allowed_egress_ids"] = []
    state["USERS_FILE"].write_text(json.dumps({"alice": before}))

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/user-landing-access",
            form={
                "user": "alice",
                "user_revision": ss.user_config_revision(before),
                "egress_id": "la-home-1",
            },
        )

    saved = json.loads(state["USERS_FILE"].read_text())["alice"]
    assert status == 303
    assert saved["landing_allowed_egress_ids"] == ["la-home-1"]
    assert saved["landing_vless_uuid"] != saved["vless_uuid"]
    assert str(__import__("uuid").UUID(saved["landing_vless_uuid"])) == saved["landing_vless_uuid"]
    assert syncs


def test_admin_assignment_sync_failure_rolls_back_users(tmp_path, monkeypatch):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    before_text = state["USERS_FILE"].read_text()
    before = json.loads(before_text)["alice"]

    def fail_sync(_users, **_kwargs):
        raise ss.state_store.CriticalStateUnavailable("candidate invalid")

    monkeypatch.setattr(ss, "_sync_static_access_from_users", fail_sync)
    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/admin/user-landing-access",
            form={
                "user": "alice",
                "user_revision": ss.user_config_revision(before),
            },
        )

    assert status == 503
    assert b"candidate invalid" not in body
    assert state["USERS_FILE"].read_text() == before_text


def test_admin_cannot_delete_referenced_node(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/landing-egress/delete",
            form={"id": "la-home-1"},
        )

    assert status == 409
    assert "la-home-1" in le.load_registry()["nodes"]


def test_admin_health_check_records_only_public_result(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    monkeypatch.setattr(le, "probe_exit", lambda _node: "1.1.1.1")

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/landing-egress/check",
            form={"id": "la-home-1"},
        )

    health = le.load_registry()["nodes"]["la-home-1"]["health"]
    assert status == 303
    assert health["status"] == "healthy"
    assert health["observed_ip"] == "1.1.1.1"
    assert health["checked_at"]
    assert "proxy-secret" not in json.dumps(health)


def test_health_check_rejects_node_changed_during_probe(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    def mutate(_node):
        registry = le.load_registry()
        registry["nodes"]["la-home-1"]["socks_port"] = 1081
        le.save_registry(registry)
        return "1.1.1.1"

    monkeypatch.setattr(le, "probe_exit", mutate)
    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/landing-egress/check",
            form={"id": "la-home-1"},
        )

    assert status == 409
    assert le.load_registry()["nodes"]["la-home-1"]["health"] is None


def test_stale_registry_save_cannot_resurrect_rotated_credentials(
    tmp_path, monkeypatch,
):
    _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    stale_revision = ss.content_revision(le.load_registry())
    registry = le.load_registry()
    registry["nodes"]["la-home-1"]["socks_password"] = "rotated-secret"
    le.save_registry(registry)

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/landing-egress/save",
            form={
                "id": "la-home-1",
                "name": "洛杉矶新名称",
                "socks_ip": "8.8.8.8",
                "socks_port": "1080",
                "socks_username": "",
                "socks_password": "",
                "expected_exit_ip": "1.1.1.1",
                "isp": "Example ISP",
                "region": "Los Angeles",
                "enabled": "1",
                "registry_revision": stale_revision,
            },
        )

    assert status == 409
    saved = le.load_registry()["nodes"]["la-home-1"]
    assert saved["socks_password"] == "rotated-secret"
    assert saved["name"] == "洛杉矶家宽"


def test_admin_page_contains_node_controls_and_user_authorization(
    tmp_path, monkeypatch,
):
    _state(tmp_path, monkeypatch, selected="la-home-1")
    page = ss.render_landing_egresses("panel.test")

    assert 'action="/admin/landing-egress/check"' in page
    assert 'action="/admin/landing-egress/delete"' in page
    assert 'action="/admin/user-landing-access"' in page
    assert 'value="la-home-1" checked' in page
    assert 'name="user_revision"' in page


def test_admin_registry_page_uses_existing_accessible_layout_components(
    tmp_path, monkeypatch,
):
    _state(tmp_path, monkeypatch, selected="la-home-1")

    page = ss.render_landing_egresses("panel.test")

    assert (
        '<div class="data-table-wrap" tabindex="0" '
        'aria-label="家宽出口节点表格，可横向滚动">'
    ) in page
    assert '<table class="data-table">' in page
    assert '<caption class="sr-only">家宽出口节点列表</caption>' in page
    assert page.count('<th scope="col">') == 5
    assert '<form method="post" action="/admin/landing-egress/save" class="landing-node-form">' in page
    assert '<div class="form-grid landing-node-grid">' in page
    for field_id in (
        "landing-node-id",
        "landing-node-name",
        "landing-socks-ip",
        "landing-socks-port",
        "landing-socks-username",
        "landing-socks-password",
        "landing-expected-exit-ip",
        "landing-isp",
        "landing-region",
    ):
        assert f'<label for="{field_id}">' in page
        assert f'id="{field_id}"' in page


def test_empty_registry_explains_setup_before_rendering_user_access_forms(
    tmp_path, monkeypatch,
):
    _state(tmp_path, monkeypatch)
    le.save_registry(le.empty_registry())

    page = ss.render_landing_egresses("panel.test")

    assert "请先添加家宽出口节点" in page
    assert 'action="/admin/user-landing-access"' not in page
    assert "保存授权" not in page


def test_create_user_form_offers_only_enabled_public_landing_nodes(
    tmp_path, monkeypatch,
):
    _state(tmp_path, monkeypatch)
    registry = le.load_registry()
    registry["nodes"]["ny-home-1"]["enabled"] = False
    le.save_registry(registry)

    page = ss.render_admin("panel.test", "https://panel.test")

    assert 'id="create-landing-initial-egress"' in page
    assert 'name="landing_initial_egress_id"' in page
    assert '<option value="">暂不分配</option>' in page
    assert '<option value="la-home-1">洛杉矶家宽</option>' in page
    assert '<option value="ny-home-1">' not in page
    assert "proxy-user" not in page
    assert "proxy-secret" not in page
    assert "8.8.8.8:1080" not in page


def test_admin_add_with_initial_egress_creates_ready_landing_assignment(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    with _running_server() as server:
        status, headers, body = _request(
            server,
            "POST",
            "/admin/add",
            form={
                "user": "bob",
                "quota_gb": "150",
                "quota_extra_gb": "0",
                "landing_initial_egress_id": "la-home-1",
            },
        )

    saved = json.loads(state["USERS_FILE"].read_text())["bob"]
    assert status == 302
    assert headers["location"] == "/admin?msg=created+bob"
    assert body == b""
    assert saved["landing_allowed_egress_ids"] == ["la-home-1"]
    assert saved["landing_selected_egress_id"] == "la-home-1"
    assert uuid.UUID(saved["landing_vless_uuid"]).version == 4
    assert saved["landing_vless_uuid"] != saved["vless_uuid"]


@pytest.mark.parametrize("requested_id", ["missing-node", "ny-home-1"])
def test_admin_add_rejects_unknown_or_disabled_initial_egress_without_mutation(
    tmp_path, monkeypatch, requested_id,
):
    state = _state(tmp_path, monkeypatch)
    registry = le.load_registry()
    registry["nodes"]["ny-home-1"]["enabled"] = False
    le.save_registry(registry)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    before = state["USERS_FILE"].read_text()

    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/admin/add",
            form={
                "user": "bob",
                "quota_gb": "150",
                "quota_extra_gb": "0",
                "landing_initial_egress_id": requested_id,
            },
        )

    assert status == 422
    assert "家宽出口已不可用".encode() in body
    assert state["USERS_FILE"].read_text() == before


def test_admin_add_revalidates_initial_egress_after_acquiring_usage_lock(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    before = state["USERS_FILE"].read_text()
    enabled_registry = le.load_registry()
    disabled_registry = json.loads(json.dumps(enabled_registry))
    disabled_registry["nodes"]["la-home-1"]["enabled"] = False
    registries = iter((enabled_registry, disabled_registry))

    def changing_registry():
        return next(registries, disabled_registry)

    monkeypatch.setattr(le, "load_registry", changing_registry)
    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/admin/add",
            form={
                "user": "bob",
                "quota_gb": "150",
                "quota_extra_gb": "0",
                "landing_initial_egress_id": "la-home-1",
            },
        )

    assert status == 422
    assert "家宽出口已不可用".encode() in body
    assert state["USERS_FILE"].read_text() == before


def test_admin_add_without_initial_egress_keeps_landing_access_unassigned(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)

    with _running_server() as server:
        status, _headers, _body = _request(
            server,
            "POST",
            "/admin/add",
            form={
                "user": "bob",
                "quota_gb": "150",
                "quota_extra_gb": "0",
                "landing_initial_egress_id": "",
            },
        )

    saved = json.loads(state["USERS_FILE"].read_text())["bob"]
    assert status == 302
    assert "landing_allowed_egress_ids" not in saved
    assert "landing_selected_egress_id" not in saved
    assert "landing_vless_uuid" not in saved


def test_admin_add_initial_egress_sync_failure_rolls_back_new_user(
    tmp_path, monkeypatch,
):
    state = _state(tmp_path, monkeypatch)
    monkeypatch.setattr(ss, "is_logged_in", lambda _handler: True)
    before_text = state["USERS_FILE"].read_text()

    def fail_sync(_users, **_kwargs):
        raise ss.state_store.CriticalStateUnavailable("candidate invalid")

    monkeypatch.setattr(ss, "_sync_static_access_from_users", fail_sync)
    with _running_server() as server:
        status, _headers, body = _request(
            server,
            "POST",
            "/admin/add",
            form={
                "user": "bob",
                "quota_gb": "150",
                "quota_extra_gb": "0",
                "landing_initial_egress_id": "la-home-1",
            },
        )

    assert status == 503
    assert b"candidate invalid" not in body
    assert state["USERS_FILE"].read_text() == before_text


def test_landing_uuid_generation_rejects_equivalent_noncanonical_collision(
    monkeypatch,
):
    occupied = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    available = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
    users = {
        "alice": {"vless_uuid": occupied.hex},
        "bob": {},
    }
    candidates = iter((occupied, available))
    monkeypatch.setattr(ss.uuid, "uuid4", lambda: next(candidates))

    generated = ss._ensure_landing_vless_uuid(users["bob"], users)

    assert generated == str(available)
    assert users["bob"]["landing_vless_uuid"] == str(available)
