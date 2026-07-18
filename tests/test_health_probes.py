"""Health page probes — each one read-only and individually testable."""
from datetime import datetime, timedelta
from unittest.mock import patch

import health
import subscription_service as ss


def test_probe_cron_fresh(tmp_path, monkeypatch):
    f = tmp_path / 'usage.json'
    f.write_text('{}')
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', f, raising=False)
    out = ss.probe_cron_heartbeat()
    assert out['ok'] is True
    assert '秒前' in out['label']


def test_probe_cron_stale(tmp_path, monkeypatch):
    import os
    f = tmp_path / 'usage.json'
    f.write_text('{}')
    old = (datetime.now() - timedelta(seconds=600)).timestamp()
    os.utime(f, (old, old))
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', f, raising=False)
    out = ss.probe_cron_heartbeat()
    assert out['ok'] is False


def test_probe_cron_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ss, 'USAGE_DAILY_FILE', tmp_path / 'nope.json', raising=False,
    )
    out = ss.probe_cron_heartbeat()
    assert out['ok'] is False
    assert out['label'] == '未知'


def test_probe_systemd_active():
    fake = type('R', (), {'stdout': 'active\n', 'returncode': 0})()
    with patch.object(ss.subprocess, 'run', return_value=fake):
        out = ss.probe_systemd('xray.service')
        assert out['ok'] is True


def test_probe_systemd_inactive():
    fake = type('R', (), {'stdout': 'inactive\n', 'returncode': 3})()
    with patch.object(ss.subprocess, 'run', return_value=fake):
        out = ss.probe_systemd('xray.service')
        assert out['ok'] is False


def test_probe_systemd_missing():
    with patch.object(ss.subprocess, 'run', side_effect=FileNotFoundError):
        out = ss.probe_systemd('xray.service')
        assert out['ok'] is False
        assert out['label'] == '未知'


class _ReadinessResponse:
    def __init__(
        self,
        status=200,
        body=b'{"ok":true}',
        content_type='application/json',
    ):
        self.status = status
        self._body = body
        self._content_type = content_type

    def read(self, _limit):
        return self._body

    def getheader(self, name):
        assert name == 'Content-Type'
        return self._content_type


class _ReadinessConnection:
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


def test_probe_auth_readiness_requires_bounded_json_success():
    connection = _ReadinessConnection(_ReadinessResponse())

    out = health.probe_auth_readiness(
        connection_factory=lambda host, port, timeout: connection
    )

    assert out == {'ok': True, 'label': '就绪'}
    assert connection.request_args[0] == ('GET', '/readyz')
    assert connection.closed


def test_probe_auth_readiness_fails_closed_on_shallow_or_malformed_response():
    cases = [
        _ReadinessResponse(status=503, body=b'{"ok":false}'),
        _ReadinessResponse(body=b'{"ok":false}'),
        _ReadinessResponse(body=b'not-json'),
        _ReadinessResponse(content_type='text/plain'),
        _ReadinessResponse(body=b'{' + (b'x' * 4096)),
    ]

    for response in cases:
        connection = _ReadinessConnection(response)
        result = health.probe_auth_readiness(
            connection_factory=lambda host, port, timeout: connection
        )
        assert result['ok'] is False
        assert connection.closed


def test_probe_disk():
    fake = type('U', (), {'total': 100, 'free': 50, 'used': 50})()
    with patch.object(ss.shutil, 'disk_usage', return_value=fake):
        out = ss.probe_disk()
        assert out['ok'] is True
        assert '50' in out['label']


def test_probe_disk_low():
    fake = type('U', (), {'total': 100, 'free': 5, 'used': 95})()
    with patch.object(ss.shutil, 'disk_usage', return_value=fake):
        out = ss.probe_disk()
        assert out['ok'] is False


def test_probe_online_sums_values(tmp_path, monkeypatch):
    f = tmp_path / 'online.json'
    f.write_text('{"alice": 2, "bob": 3}')
    monkeypatch.setattr(ss, 'ONLINE_FILE', f, raising=False)
    out = ss.probe_online()
    assert out['ok'] is True
    assert '5' in out['label']


def test_render_health_page_loads(tmp_path, monkeypatch):
    f = tmp_path / 'usage.json'; f.write_text('{}')
    g = tmp_path / 'online.json'; g.write_text('{}')
    c = tmp_path / 'cost_calibration.json'; c.write_text('{}')
    p = tmp_path / 'protocol_usage_hourly.json'; p.write_text('{}')
    monkeypatch.setattr(ss, 'USAGE_FILE', f, raising=False)
    monkeypatch.setattr(ss, 'ONLINE_FILE', g, raising=False)
    monkeypatch.setattr(ss, 'COST_CALIBRATION_FILE', c, raising=False)
    monkeypatch.setattr(ss, 'PROTOCOL_USAGE_HOURLY_FILE', p, raising=False)
    with patch.object(ss.subprocess, 'run', side_effect=FileNotFoundError):
        with patch.object(ss.shutil, 'disk_usage',
                          return_value=type('U', (), {'total': 100, 'free': 50, 'used': 50})()):
            html_out = ss.render_health('panel.example.com')
    assert '健康状态' in html_out
    assert 'cron' in html_out.lower() or '心跳' in html_out
    assert '成本校准器' in html_out
    assert '线路质量雷达' in html_out


def test_probe_cert_happy_path():
    """Mock openssl returning a known English-locale enddate; verify parsed days."""
    fake_out = type('R', (), {
        'stdout': 'notAfter=May  5 12:34:56 2099 GMT\n',
        'returncode': 0,
    })()
    with patch.object(ss.subprocess, 'run', return_value=fake_out):
        out = ss.probe_cert(path='/dev/null')
    assert out['ok'] is True
    assert '剩余' in out['label']


def test_probe_cert_near_expiry():
    """Cert with only 7 days left → not ok."""
    near = datetime.utcnow() + timedelta(days=7)
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
              'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    enddate = (f"{months[near.month-1]} {near.day:>2} "
               f"{near.hour:02d}:{near.minute:02d}:{near.second:02d} "
               f"{near.year} GMT")
    fake_out = type('R', (), {
        'stdout': f'notAfter={enddate}\n',
        'returncode': 0,
    })()
    with patch.object(ss.subprocess, 'run', return_value=fake_out):
        out = ss.probe_cert(path='/dev/null')
    assert out['ok'] is False


def test_probe_cert_openssl_failure():
    """Non-zero returncode → 未知 without trying to parse stdout."""
    fake_out = type('R', (), {'stdout': '', 'returncode': 1})()
    with patch.object(ss.subprocess, 'run', return_value=fake_out):
        out = ss.probe_cert(path='/dev/null')
    assert out['ok'] is False
    assert out['label'] == '未知'


def test_panel_tls_probe_distinguishes_optional_and_required_absence(tmp_path):
    site = tmp_path / 'panel-tls.conf'
    marker = tmp_path / 'https_required'

    assert health.probe_panel_tls(site, marker) == {
        'ok': True,
        'label': '未启用',
    }
    marker.write_text('required\n', encoding='utf-8')
    assert health.probe_panel_tls(site, marker) == {
        'ok': False,
        'label': '配置缺失',
    }


def test_certbot_renewal_probe_is_conditional_on_https_requirement(tmp_path):
    marker = tmp_path / 'https_required'
    calls = []

    def runner(command, **_kwargs):
        calls.append(command)
        return type('R', (), {'stdout': 'inactive\n', 'returncode': 3})()

    recovery = tmp_path / 'renewal-pending'
    assert health.probe_certbot_renewal(
        marker,
        recovery_marker=recovery,
        runner=runner,
    ) == {
        'ok': True,
        'label': '未要求',
    }
    assert calls == []

    marker.write_text('required\n', encoding='utf-8')
    assert health.probe_certbot_renewal(
        marker,
        recovery_marker=recovery,
        runner=runner,
    ) == {
        'ok': False,
        'label': 'inactive',
    }
    assert calls == [[
        'systemctl',
        'is-active',
        'snap.certbot.renew.timer',
    ]]

    recovery.write_text('transaction\n', encoding='utf-8')
    assert health.probe_certbot_renewal(
        marker,
        recovery_marker=recovery,
        runner=runner,
    ) == {
        'ok': False,
        'label': '待恢复',
    }
    assert len(calls) == 1


def test_panel_tls_probe_checks_the_certificate_selected_by_nginx(tmp_path):
    certificate = tmp_path / 'fullchain.pem'
    certificate.write_text('fixture\n', encoding='utf-8')
    site = tmp_path / 'panel-tls.conf'
    site.write_text(
        f'server {{\n  ssl_certificate {certificate};\n}}\n',
        encoding='utf-8',
    )
    seen = []

    def runner(command, **_kwargs):
        seen.append(command)
        return type('R', (), {
            'stdout': 'notAfter=May  5 12:34:56 2099 GMT\n',
            'returncode': 0,
        })()

    result = health.probe_panel_tls(
        site,
        tmp_path / 'not-required',
        runner=runner,
    )

    assert result['ok'] is True
    assert seen[0][-1] == str(certificate)


def test_panel_tls_probe_rejects_ambiguous_certificate_directive(tmp_path):
    site = tmp_path / 'panel-tls.conf'
    site.write_text(
        'ssl_certificate relative.pem;\n',
        encoding='utf-8',
    )

    assert health.probe_panel_tls(
        site, tmp_path / 'not-required'
    ) == {'ok': False, 'label': '配置异常'}


def test_probe_hysteria_update_marks_urgent_bad():
    payload = ('Jun 22 15:37 hysteria[1]: update available '
               '{"version":"v2.9.2","url":"https://example","urgent":true}\n')

    def runner(*_args, **_kwargs):
        return type('R', (), {'stdout': payload, 'returncode': 0})()

    out = health.probe_hysteria_update(runner=runner)

    assert out['ok'] is False
    assert 'v2.9.2' in out['label']
    assert 'urgent' in out['label']


def test_probe_recent_backup_reports_latest_age_and_disk(tmp_path):
    import os
    backup = tmp_path / 'hy2-backup-20260622T010000Z.tar.gz'
    backup.write_text('x')
    old = (datetime.now() - timedelta(hours=2)).timestamp()
    os.utime(backup, (old, old))
    fake_disk = type('U', (), {'total': 10 * 1024 ** 3, 'free': 4 * 1024 ** 3, 'used': 6 * 1024 ** 3})()

    out = health.probe_recent_backup(
        tmp_path, max_age_hours=30, disk_usage=lambda _p: fake_disk)

    assert out['ok'] is True
    assert '小时前' in out['label']
    assert '4.0GB' in out['label']
