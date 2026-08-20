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

    # Row-level identity is preserved via data-health.
    assert 'data-health="&lt;磁盘&gt;"' in healthy
    assert 'data-health="证书"' in unhealthy
    # The status string lives in a small badge (not in a Serif value).
    assert '<span class="bold">&lt;磁盘&gt;</span>' in healthy
    assert '<span class="badge">剩余 42%（要求 &gt; 15%）</span>' in healthy
    assert '<span class="badge badge-danger">剩余 &lt; 14 天</span>' in unhealthy
    # Context text is HTML-escaped.
    assert "&lt;磁盘&gt;" in healthy
    assert "剩余 42%（要求 &gt; 15%）" in healthy
    assert "剩余 &lt; 14 天" in unhealthy
    # Row markup, no card wrapper.
    assert healthy.startswith("<tr")
    assert healthy.rstrip().endswith("</tr>")
    assert "<div" not in healthy


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

    # Fragment is bare rows for tbody.innerHTML — no outer <tbody>.
    assert "<tbody" not in fragment.lower()
    assert "</tbody" not in fragment.lower()
    # Each probe renders exactly one row, identified by data-health.
    assert fragment.count("<tr") == 15
    assert fragment.count("data-health=") == 15
    for probe_title in (
        "CRON 心跳",
        "鉴权服务",
        "鉴权依赖",
        "Hysteria",
        "Xray",
        "TUIC",
        "限流 Timer",
        "TLS 证书",
        "面板 HTTPS",
        "证书自动续期",
        "在线用户",
        "Xray 配置权限",
        "Hysteria 更新",
        "最近备份",
        "磁盘",
    ):
        assert f'data-health="{probe_title}"' in fragment
    # Healthy probes use neutral badge, the unhealthy disk probe uses danger.
    assert fragment.count('<span class="badge">') == 14
    assert fragment.count('<span class="badge badge-danger">') == 1
    assert "剩余 5%（要求 &gt; 15%）" in fragment
    # Fragment rows are embedded inside the SSR page tbody.
    assert "<tbody>" in page
    assert fragment in page
