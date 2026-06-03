"""Read-only health probes for the admin status page."""
import html
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path


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


def probe_online(path, *, load_json):
    try:
        data = load_json(path, {})
        n = sum(int(v) for v in data.values())
        return {'ok': True, 'label': f'{n} 在线'}
    except Exception:
        return {'ok': False, 'label': '未知'}


def health_card(title, probe_result):
    cls = 'ok' if probe_result['ok'] else 'bad'
    return (f'<div class="card stat health-{cls}">'
            f'<div class="k">{html.escape(title)}</div>'
            f'<div class="v">{html.escape(probe_result["label"])}</div>'
            f'</div>')
