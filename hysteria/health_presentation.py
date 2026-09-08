"""Concurrent health probe summaries and stable ordered presentation."""

from __future__ import annotations

import html
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class HealthPresentation:
    ONLINE_FILE: Path
    load_json: Callable[..., object]
    probe_systemd: Callable[..., object]
    probe_certbot_renewal: Callable[..., object]
    probe_cert: Callable[..., object]
    probe_panel_tls: Callable[..., object]
    probe_disk: Callable[..., object]
    probe_cron_heartbeat: Callable[..., object]
    probe_auth_readiness: Callable[..., object]
    probe_online: Callable[..., object]
    probe_xray_config_permissions: Callable[..., object]
    probe_hysteria_update: Callable[..., object]
    probe_recent_backup: Callable[..., object]
    _health_card: Callable[..., object]

    def _render_health_top_kpis(self):
        """Run all probes and derive 4 top-level KPIs."""
        probes = (
            ('整体状态', self._probe_overall_status),
            ('在线服务', self._probe_online_services),
            ('HTTPS 证书', self._probe_https_cert),
            ('磁盘空间', self._probe_disk_kpi),
        )

        def run(item):
            title, fn = item
            try:
                result = fn()
            except Exception:
                result = {'ok': False, 'label': '—'}
            return title, result

        with ThreadPoolExecutor(max_workers=4, thread_name_prefix='health-kpi') as ex:
            results = dict(ex.map(run, probes))
        return results

    def _probe_overall_status(self):
        """Healthy if all core services are up and no certs are expiring."""
        checks = [
            ('鉴权服务', lambda: self.probe_systemd('hysteria-auth.service')),
            ('Hysteria', lambda: self.probe_systemd('hysteria-server.service')),
            ('Xray', lambda: self.probe_systemd('xray.service')),
            ('TUIC', lambda: self.probe_systemd('tuic-server.service')),
            ('证书自动续期', self.probe_certbot_renewal),
        ]
        ok_count = 0
        for name, fn in checks:
            try:
                r = fn()
                if r.get('ok'):
                    ok_count += 1
            except Exception:
                pass
        total = len(checks)
        ok = ok_count == total
        label = f'{ok_count}/{total} 正常' if ok else f'{ok_count}/{total} 异常'
        return {'ok': ok, 'label': label}

    def _probe_online_services(self):
        try:
            data = self.load_json(self.ONLINE_FILE, {})
            n = sum(int(v) for v in data.values())
            return {'ok': n > 0, 'label': f'{n} 在线'}
        except Exception:
            return {'ok': False, 'label': '未知'}

    def _probe_https_cert(self):
        certs = [
            self.probe_cert(),
            self.probe_panel_tls(),
        ]
        worst = None
        for r in certs:
            if not r.get('ok'):
                worst = r
        if worst is None:
            return {'ok': True, 'label': '全部有效'}
        return worst

    def _probe_disk_kpi(self):
        return self.probe_disk()

    def _health_top_kpi_card(self, title, probe_result, is_text=False):
        is_ok = bool(probe_result['ok'])
        label = probe_result.get('label', '—')
        badge_cls = 'badge' if is_ok else 'badge badge-danger'
        badge_inner = (
            f'<span class="{badge_cls}">{html.escape(label)}</span>'
            if is_text
            else html.escape(label)
        )
        return (
            f'<div class="health-kpi-card">'
            f'<div class="health-kpi-label">{html.escape(title)}</div>'
            f'<div class="health-kpi-value{" is-text" if is_text else ""}">{badge_inner}</div>'
            f'</div>'
        )

    def _render_health_cards(self):
        """Run independent probes concurrently while preserving card order."""
        probes = (
            ('CRON 心跳', self.probe_cron_heartbeat),
            ('鉴权服务', lambda: self.probe_systemd('hysteria-auth.service')),
            ('鉴权依赖', self.probe_auth_readiness),
            ('Hysteria', lambda: self.probe_systemd('hysteria-server.service')),
            ('Xray', lambda: self.probe_systemd('xray.service')),
            ('TUIC', lambda: self.probe_systemd('tuic-server.service')),
            ('限流 Timer', lambda: self.probe_systemd('hysteria-traffic-limiter.timer')),
            ('TLS 证书', self.probe_cert),
            ('面板 HTTPS', self.probe_panel_tls),
            ('证书自动续期', self.probe_certbot_renewal),
            ('在线用户', self.probe_online),
            ('Xray 配置权限', self.probe_xray_config_permissions),
            ('Hysteria 更新', self.probe_hysteria_update),
            ('最近备份', self.probe_recent_backup),
            ('磁盘', self.probe_disk),
        )

        def run_probe(item):
            title, probe = item
            try:
                result = probe()
                if not isinstance(result, dict):
                    raise ValueError('invalid health probe result')
            except Exception:
                result = {'ok': False, 'label': '探测失败'}
            return self._health_card(title, result)

        with ThreadPoolExecutor(
            max_workers=min(8, len(probes)),
            thread_name_prefix='health-probe',
        ) as executor:
            return ''.join(executor.map(run_probe, probes))
