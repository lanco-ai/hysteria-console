from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hysteria"))

import health  # noqa: E402
import subscription_service as ss  # noqa: E402


def test_health_card_exposes_visible_status_and_escapes_context() -> None:
    healthy = health.health_card(
        "<磁盘>",
        {"ok": True, "label": "剩余 42%（要求 > 15%）"},
    )
    unhealthy = health.health_card(
        "证书",
        {"ok": False, "label": "剩余 < 14 天"},
    )

    assert 'class="card stat health-ok"' in healthy
    assert 'class="badge badge-info health-status">状态：正常</span>' in healthy
    assert "&lt;磁盘&gt;" in healthy
    assert "剩余 42%（要求 &gt; 15%）" in healthy

    assert 'class="card stat health-bad"' in unhealthy
    assert 'class="badge badge-danger health-status">状态：异常</span>' in unhealthy
    assert "剩余 &lt; 14 天" in unhealthy


def test_ssr_and_live_fragment_render_text_status_for_every_card(
    monkeypatch,
) -> None:
    healthy = {"ok": True, "label": "在阈值内"}
    unhealthy = {"ok": False, "label": "剩余 5%（要求 > 15%）"}
    for probe_name in (
        "probe_cron_heartbeat",
        "probe_systemd",
        "probe_auth_readiness",
        "probe_cert",
        "probe_panel_tls",
        "probe_certbot_renewal",
        "probe_online",
        "probe_xray_config_permissions",
        "probe_hysteria_update",
        "probe_recent_backup",
    ):
        monkeypatch.setattr(
            ss,
            probe_name,
            lambda *_args, **_kwargs: healthy,
        )
    monkeypatch.setattr(ss, "probe_disk", lambda: unhealthy)
    monkeypatch.setattr(ss, "render_line_radar", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        ss,
        "render_cost_calibrator",
        lambda *_args, **_kwargs: "",
    )

    fragment = ss.render_health_fragment()
    page = ss.render_health("panel.test")

    assert fragment.count('class="badge badge-info health-status"') == 14
    assert fragment.count('class="badge badge-danger health-status"') == 1
    assert fragment.count("状态：正常") == 14
    assert fragment.count("状态：异常") == 1
    assert "剩余 5%（要求 &gt; 15%）" in fragment
    assert fragment in page
