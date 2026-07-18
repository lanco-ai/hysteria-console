"""Read-only health probes for the admin status page."""
import html
import http.client
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from stat import S_IMODE


def probe_auth_readiness(
    *,
    host='127.0.0.1',
    port=8082,
    path='/readyz',
    timeout=3,
    connection_factory=http.client.HTTPConnection,
):
    """Require the loopback auth bridge and its persisted dependencies."""
    connection = None
    try:
        connection = connection_factory(host, port, timeout=timeout)
        connection.request(
            'GET',
            path,
            headers={
                'Accept': 'application/json',
                'Connection': 'close',
            },
        )
        response = connection.getresponse()
        body = response.read(4097)
        content_type = str(response.getheader('Content-Type') or '')
        if (
            response.status != 200
            or len(body) > 4096
            or not content_type.lower().startswith('application/json')
        ):
            return {'ok': False, 'label': '未就绪'}
        payload = json.loads(body.decode('utf-8'))
        if not isinstance(payload, dict) or payload.get('ok') is not True:
            return {'ok': False, 'label': '未就绪'}
        return {'ok': True, 'label': '就绪'}
    except Exception:
        return {'ok': False, 'label': '不可达'}
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass


def probe_cron_heartbeat(path):
    """How long since the cron tick last wrote usage.json. Stale if >120s."""
    try:
        mt = Path(path).stat().st_mtime
        age = int(time.time() - mt)
        return {'ok': age < 120, 'label': f'{age} 秒前'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_systemd(unit, *, runner=subprocess.run):
    """`systemctl is-active <unit>` -> ok if 'active'."""
    try:
        out = runner(['systemctl', 'is-active', unit],
                     capture_output=True, text=True, timeout=3)
        v = (out.stdout or '').strip()
        return {'ok': v == 'active', 'label': v or '未知'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_disk(*, disk_usage=shutil.disk_usage):
    try:
        u = disk_usage('/')
        free_pct = u.free * 100 / u.total
        return {'ok': free_pct > 15, 'label': f'{free_pct:.0f}% free'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_cert(path, *, runner=subprocess.run, environ=None, utcnow=datetime.utcnow):
    p = Path(path)
    try:
        # Force C locale so openssl emits English month names that strptime can parse.
        env = {**(environ if environ is not None else os.environ), 'LC_ALL': 'C'}
        out = runner(['openssl', 'x509', '-enddate', '-noout', '-in', str(p)],
                     capture_output=True, text=True, timeout=3, env=env)
        if out.returncode != 0 or '=' not in out.stdout:
            return {'ok': False, 'label': '未知'}
        end_str = out.stdout.split('=', 1)[1].strip()
        end_dt = datetime.strptime(end_str, '%b %d %H:%M:%S %Y %Z')
        days = (end_dt - utcnow()).days
        return {'ok': days > 14, 'label': f'{days} 天剩余'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_panel_tls(
    site_config,
    required_marker,
    *,
    runner=subprocess.run,
    environ=None,
    utcnow=datetime.utcnow,
):
    """Report the certificate selected by the active panel TLS vhost."""
    site = Path(site_config)
    required = Path(required_marker).is_file()
    if not site.exists() and not site.is_symlink():
        return {
            'ok': not required,
            'label': '配置缺失' if required else '未启用',
        }
    try:
        if site.stat().st_size > 64 * 1024:
            return {'ok': False, 'label': '配置异常'}
        config = site.read_text(encoding='utf-8')
        match = re.search(
            r'(?m)^\s*ssl_certificate\s+([^;\r\n]+);\s*$',
            config,
        )
        if match is None:
            return {'ok': False, 'label': '配置异常'}
        certificate = match.group(1).strip()
        if (
            not certificate.startswith('/')
            or len(certificate) > 4096
            or any(char.isspace() for char in certificate)
        ):
            return {'ok': False, 'label': '配置异常'}
    except (OSError, UnicodeError):
        return {'ok': False, 'label': '配置异常'}
    return probe_cert(
        certificate,
        runner=runner,
        environ=environ,
        utcnow=utcnow,
    )


def probe_certbot_renewal(
    required_marker,
    *,
    recovery_marker=(
        '/var/lib/hysteria/https-activation-recovery/renewal-pending'
    ),
    runner=subprocess.run,
):
    """Require the Certbot renewal timer only for HTTPS-required installs."""
    if not Path(required_marker).is_file():
        return {'ok': True, 'label': '未要求'}
    if os.path.lexists(recovery_marker):
        return {'ok': False, 'label': '待恢复'}
    return probe_systemd('snap.certbot.renew.timer', runner=runner)


def probe_online(path, *, load_json):
    try:
        data = load_json(path, {})
        n = sum(int(v) for v in data.values())
        return {'ok': True, 'label': f'{n} 在线'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def _fmt_bytes(num):
    n = float(max(0, int(num)))
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    idx = 0
    while n >= 1024 and idx < len(units) - 1:
        n /= 1024.0
        idx += 1
    return f'{n:.1f}{units[idx]}'


def probe_file_mode(path, *, mode, group=None):
    p = Path(path)
    try:
        st = p.stat()
        actual_mode = S_IMODE(st.st_mode)
        ok = actual_mode == int(str(mode), 8)
        label = oct(actual_mode)[2:]
        if group:
            try:
                import grp
                actual_group = grp.getgrgid(st.st_gid).gr_name
            except Exception:
                actual_group = str(st.st_gid)
            ok = ok and actual_group == group
            label = f'{label} {actual_group}'
        return {'ok': ok, 'label': label}
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_hysteria_update(*, runner=subprocess.run):
    try:
        out = runner(
            ['journalctl', '-u', 'hysteria-server.service', '-n', '200', '--no-pager'],
            capture_output=True, text=True, timeout=3,
        )
        text = out.stdout or ''
        matches = re.findall(r'update available\s+(\{.*?\})(?:\n|$)', text)
        if not matches:
            return {'ok': True, 'label': '无更新提示'}
        payload = json.loads(matches[-1])
        version = str(payload.get('version') or '未知版本')
        urgent = bool(payload.get('urgent'))
        return {
            'ok': not urgent,
            'label': f'{version} urgent' if urgent else f'{version} 可更新',
        }
    except Exception:
        return {'ok': False, 'label': '未知'}


def probe_recent_backup(path, *, max_age_hours=30, disk_usage=shutil.disk_usage):
    p = Path(path)
    try:
        files = [
            f for f in p.glob('hy2-backup-*.tar.gz*')
            if f.is_file() and not f.name.endswith('.sha256')
        ]
        if not files:
            return {'ok': False, 'label': '无备份'}
        latest = max(files, key=lambda f: f.stat().st_mtime)
        age_hours = (time.time() - latest.stat().st_mtime) / 3600
        free = disk_usage('/').free
        age_label = f'{age_hours:.1f} 小时前' if age_hours < 48 else f'{age_hours / 24:.1f} 天前'
        return {
            'ok': age_hours <= max_age_hours,
            'label': f'{age_label} · {_fmt_bytes(free)} free',
        }
    except Exception:
        return {'ok': False, 'label': '未知'}


def health_card(title, probe_result):
    is_ok = bool(probe_result['ok'])
    cls = 'ok' if is_ok else 'bad'
    status = '正常' if is_ok else '异常'
    badge_cls = 'badge-info' if is_ok else 'badge-danger'
    return (f'<div class="card stat health-{cls}">'
            f'<div class="k">{html.escape(title)}</div>'
            f'<div class="v">{html.escape(probe_result["label"])}</div>'
            f'<div class="small"><span class="badge {badge_cls} health-status">'
            f'状态：{status}</span></div>'
            f'</div>')
