import json

import pytest

import landing_egress as le
import subscription_profiles as profiles
import subscription_service as ss
import xray_config as xc


def _node(**overrides):
    raw = {
        "id": "la-home-1",
        "name": "LA Home",
        "socks_ip": "8.8.8.8",
        "socks_port": 1080,
        "socks_username": "proxy-user",
        "socks_password": "proxy-secret",
        "expected_exit_ip": "1.1.1.1",
        "isp": "Example ISP",
        "region": "Los Angeles",
        "enabled": True,
    }
    raw.update(overrides)
    return le.validate_node(raw)


def _config(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "inboundTag": ["api"],
                    "outboundTag": "api",
                },
                {
                    "type": "field",
                    "domain": ["example.com"],
                    "outboundTag": "operator-outbound",
                },
            ],
        },
        "inbounds": [
            {
                "protocol": "vless",
                "port": 443,
                "settings": {"clients": []},
            },
            {
                "protocol": "vless",
                "port": 8443,
                "settings": {"clients": []},
            },
        ],
        "outbounds": [
            {"protocol": "freedom", "tag": "direct"},
            {"protocol": "blackhole", "tag": "block"},
            {"protocol": "freedom", "tag": "operator-outbound"},
        ],
    }), encoding="utf-8")
    return path


def _landing_plan(selected_id="la-home-1"):
    return {
        "alice": {
            "uuid": "22222222-2222-4222-8222-222222222222",
            "selected_id": selected_id,
        },
    }


def test_apply_plan_adds_distinct_landing_client_only_on_443(tmp_path):
    path = _config(tmp_path)

    xc.apply_user_plan(
        {"alice": "11111111-1111-4111-8111-111111111111"},
        landing_plan=_landing_plan(),
        egress_nodes={"la-home-1": _node()},
        path=path,
    )

    cfg = json.loads(path.read_text())
    clients = {
        inbound["port"]: inbound["settings"]["clients"]
        for inbound in cfg["inbounds"]
    }
    assert clients[443] == [
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "email": "alice",
            "flow": "xtls-rprx-vision",
        },
        {
            "id": "22222222-2222-4222-8222-222222222222",
            "email": "landing:alice",
            "flow": "xtls-rprx-vision",
        },
    ]
    assert [client["email"] for client in clients[8443]] == [
        "alice@hy2-backup.invalid",
    ]


def test_apply_plan_routes_landing_tcp_to_authenticated_socks_and_udp_to_block(
    tmp_path,
):
    path = _config(tmp_path)

    xc.apply_user_plan(
        {"alice": "11111111-1111-4111-8111-111111111111"},
        landing_plan=_landing_plan(),
        egress_nodes={"la-home-1": _node()},
        path=path,
    )

    cfg = json.loads(path.read_text())
    outbound = next(
        item for item in cfg["outbounds"]
        if item.get("tag") == "landing-egress-la-home-1"
    )
    assert outbound == {
        "protocol": "socks",
        "tag": "landing-egress-la-home-1",
        "settings": {
            "servers": [{
                "address": "8.8.8.8",
                "port": 1080,
                "users": [{"user": "proxy-user", "pass": "proxy-secret"}],
            }],
        },
    }
    managed = [
        rule for rule in cfg["routing"]["rules"]
        if str(rule.get("ruleTag", "")).startswith("hy2-landing-")
    ]
    assert managed == [
        {
            "type": "field",
            "ruleTag": "hy2-landing-udp-alice",
            "user": ["landing:alice"],
            "network": "udp",
            "outboundTag": "block",
        },
        {
            "type": "field",
            "ruleTag": "hy2-landing-tcp-alice",
            "user": ["landing:alice"],
            "network": "tcp",
            "outboundTag": "landing-egress-la-home-1",
        },
    ]


def test_missing_or_disabled_selection_is_explicitly_blackholed(tmp_path):
    for selected, nodes in (
        ("missing", {"la-home-1": _node()}),
        ("la-home-1", {"la-home-1": _node(enabled=False)}),
        (None, {"la-home-1": _node()}),
    ):
        path = _config(tmp_path)
        xc.apply_user_plan(
            {"alice": "11111111-1111-4111-8111-111111111111"},
            landing_plan=_landing_plan(selected),
            egress_nodes=nodes,
            path=path,
        )
        cfg = json.loads(path.read_text())
        managed = [
            rule for rule in cfg["routing"]["rules"]
            if rule.get("ruleTag") == "hy2-landing-block-alice"
        ]
        assert managed == [{
            "type": "field",
            "ruleTag": "hy2-landing-block-alice",
            "user": ["landing:alice"],
            "network": "tcp,udp",
            "outboundTag": "block",
        }]
        assert not any(
            item.get("tag") == "landing-egress-la-home-1"
            for item in cfg["outbounds"]
        ) if not nodes["la-home-1"]["enabled"] else True


def test_reconcile_removes_stale_managed_routes_and_outbounds_only(tmp_path):
    path = _config(tmp_path)
    cfg = json.loads(path.read_text())
    cfg["outbounds"].append({
        "protocol": "socks",
        "tag": "landing-egress-stale",
        "settings": {},
    })
    cfg["routing"]["rules"].insert(1, {
        "type": "field",
        "ruleTag": "hy2-landing-tcp-stale",
        "user": ["landing:stale"],
        "outboundTag": "landing-egress-stale",
    })
    path.write_text(json.dumps(cfg), encoding="utf-8")

    xc.apply_user_plan({}, landing_plan={}, egress_nodes={}, path=path)

    updated = json.loads(path.read_text())
    assert not any(
        item.get("tag", "").startswith("landing-egress-")
        for item in updated["outbounds"]
    )
    assert not any(
        item.get("ruleTag", "").startswith("hy2-landing-")
        for item in updated["routing"]["rules"]
    )
    assert any(
        item.get("tag") == "operator-outbound"
        for item in updated["outbounds"]
    )
    assert any(
        item.get("outboundTag") == "operator-outbound"
        for item in updated["routing"]["rules"]
    )


def test_build_landing_plan_uses_only_active_authorized_unique_credentials():
    users = {
        "alice": {
            "landing_vless_uuid": "22222222-2222-4222-8222-222222222222",
            "landing_allowed_egress_ids": ["la-home-1"],
            "landing_selected_egress_id": "la-home-1",
        },
        "bob": {
            "landing_vless_uuid": "33333333-3333-4333-8333-333333333333",
            "landing_allowed_egress_ids": ["la-home-1"],
            "landing_selected_egress_id": "la-home-1",
        },
        "carol": {
            "landing_vless_uuid": "not-a-uuid",
            "landing_allowed_egress_ids": ["la-home-1"],
            "landing_selected_egress_id": "la-home-1",
        },
    }

    plan = ss._build_landing_access_plan(
        users,
        {
            "alice": "11111111-1111-4111-8111-111111111111",
            "bob": None,
            "carol": None,
        },
        {"la-home-1": _node()},
    )

    assert plan == {
        "alice": {
            "uuid": "22222222-2222-4222-8222-222222222222",
            "selected_id": "la-home-1",
        },
    }


def test_landing_uuid_cannot_collide_with_any_direct_vless_uuid():
    users = {
        "alice": {
            "landing_vless_uuid": "22222222-2222-4222-8222-222222222222",
            "landing_allowed_egress_ids": ["la-home-1"],
        },
        "bob": {
            "landing_vless_uuid": "33333333-3333-4333-8333-333333333333",
            "landing_allowed_egress_ids": ["la-home-1"],
        },
    }

    with pytest.raises(ss.state_store.CriticalStateUnavailable):
        ss._build_landing_access_plan(
            users,
            {
                "alice": "11111111-1111-4111-8111-111111111111",
                "bob": "22222222222242228222222222222222",
            },
            {"la-home-1": _node()},
        )


def test_duplicate_landing_uuid_fails_closed_instead_of_crossing_users():
    shared = "22222222-2222-4222-8222-222222222222"
    users = {
        name: {
            "landing_vless_uuid": shared,
            "landing_allowed_egress_ids": ["la-home-1"],
        }
        for name in ("alice", "bob")
    }

    with pytest.raises(ss.state_store.CriticalStateUnavailable):
        ss._build_landing_access_plan(
            users,
            {
                "alice": "11111111-1111-4111-8111-111111111111",
                "bob": "33333333-3333-4333-8333-333333333333",
            },
            {"la-home-1": _node()},
        )


def test_direct_only_active_user_needs_no_landing_uuid_or_authorization():
    plan = ss._build_landing_access_plan(
        {"alice": {}},
        {"alice": "11111111-1111-4111-8111-111111111111"},
        {"la-home-1": _node()},
    )

    assert plan == {}


def test_subscription_adds_one_landing_vless_without_changing_direct_proxy():
    cfg = {
        "proxies": [{
            "name": profiles.VLESS_TCP_PROXY,
            "type": "vless",
            "server": "panel.test",
            "port": 443,
            "uuid": "11111111-1111-4111-8111-111111111111",
            "network": "tcp",
            "udp": False,
        }],
        "proxy-groups": [{
            "name": profiles.NODE_GROUP,
            "type": "select",
            "proxies": [profiles.VLESS_TCP_PROXY, "DIRECT"],
        }],
    }
    user_cfg = {
        "landing_vless_uuid": "22222222-2222-4222-8222-222222222222",
        "landing_allowed_egress_ids": ["la-home-1"],
        "landing_selected_egress_id": "la-home-1",
    }

    profiles.apply_landing_vless(cfg, user_cfg)

    assert cfg["proxies"][0]["uuid"] == "11111111-1111-4111-8111-111111111111"
    assert cfg["proxies"][1] == {
        "name": profiles.LANDING_VLESS_PROXY,
        "type": "vless",
        "server": "panel.test",
        "port": 443,
        "uuid": "22222222-2222-4222-8222-222222222222",
        "network": "tcp",
        "udp": False,
    }
    assert profiles.LANDING_VLESS_PROXY in cfg["proxy-groups"][0]["proxies"]
