from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_services_use_the_packaged_interpreter_directly():
    expected = {
        "hysteria-auth.service": "auth_service.py",
        "hysteria-subscription.service": "subscription_service.py",
        "hysteria-traffic-limiter.service": "traffic_limiter.py",
    }

    for unit_name, program in expected.items():
        unit = (ROOT / "systemd" / unit_name).read_text(encoding="utf-8")
        assert f"ExecStart=/usr/bin/python3 /root/hysteria/{program}" in unit
        assert "/usr/bin/env python3" not in unit


def test_state_writing_python_services_default_to_private_files():
    for unit_name in (
        "hysteria-auth.service",
        "hysteria-subscription.service",
        "hysteria-traffic-limiter.service",
    ):
        unit = (ROOT / "systemd" / unit_name).read_text(encoding="utf-8")
        assert "UMask=0077" in unit
