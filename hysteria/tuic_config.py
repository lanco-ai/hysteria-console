"""TUIC server config helpers.

TUIC authenticates from a static JSON user map, so any admin action that changes
users must keep this file in sync with users.json. We reuse each user's VLESS
UUID as the TUIC UUID and the Hysteria credential (`username:sub_token`) as the
TUIC password so subscriptions stay simple.
"""
import json
import os
import secrets
import subprocess
import time
from pathlib import Path

import user_compat

USERS_FILE = Path('/root/hysteria/users.json')
CONFIG_FILE = Path('/root/hysteria/tuic.json')
LOCKED_USER_FILE = Path('/root/hysteria/state/tuic_locked_user.json')
LOCKED_USER_UUID = '00000000-0000-4000-8000-000000000000'


def _base_config():
    return {
        'server': '[::]:9443',
        'users': {},
        'certificate': '/root/hysteria/server.crt',
        'private_key': '/root/hysteria/server.key',
        'congestion_control': 'bbr',
        'alpn': ['h3'],
        'udp_relay_ipv6': False,
        'zero_rtt_handshake': False,
        'dual_stack': True,
        'auth_timeout': '3s',
        'task_negotiation_timeout': '3s',
        'max_idle_time': '30s',
        'max_external_packet_size': 1500,
        'send_window': 33554432,
        'receive_window': 16777216,
        'gc_interval': '3s',
        'gc_lifetime': '15s',
        'log_level': 'warn',
    }


def _load_json(path, fallback):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return fallback


def _save_config(path, cfg):
    payload = json.dumps(cfg, indent=2, ensure_ascii=False) + '\n'
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    path.chmod(0o600)


def _save_secret(path, data):
    payload = json.dumps(data, indent=2, ensure_ascii=True) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    path.chmod(0o600)


def _locked_user_password():
    data = _load_json(LOCKED_USER_FILE, {})
    password = str((data or {}).get('password') or '').strip()
    if password:
        return password
    password = secrets.token_urlsafe(48)
    _save_secret(LOCKED_USER_FILE, {'password': password})
    return password


def _ensure_tuic_accepts_config(cfg):
    """TUIC server 1.0.0 refuses to start when users is empty.

    Keep the service healthy with a local random locked credential when every
    real user is suspended, expired, over quota, or not yet created. The locked
    user is never emitted in subscriptions, and the password is generated on
    the node rather than hard-coded in the repo.
    """
    users = cfg.setdefault('users', {})
    if not users:
        users[LOCKED_USER_UUID] = _locked_user_password()
    return cfg


def render_from_users(users):
    cfg = _base_config()
    tuic_users = cfg['users']
    for username, user_cfg in sorted((users or {}).items()):
        if not isinstance(user_cfg, dict) or user_cfg.get('disabled'):
            continue
        if not user_compat.tuic_enabled(user_cfg):
            continue
        uid = str(user_cfg.get('vless_uuid') or '').strip()
        token = str(user_cfg.get('sub_token') or '').strip()
        if uid and token:
            tuic_users[uid] = f'{username}:{token}'
    return _ensure_tuic_accepts_config(cfg)


def sync_all(*, users=None, path=None):
    p = Path(path) if path else CONFIG_FILE
    if users is None:
        users = _load_json(USERS_FILE, {})
    desired = render_from_users(users)
    current = _load_json(p, None)
    if current == desired:
        return False
    _save_config(p, desired)
    return True


def render_from_user_plan(users, plan):
    cfg = _base_config()
    tuic_users = cfg['users']
    for username, uid in sorted((plan or {}).items()):
        uid = str(uid or '').strip()
        user_cfg = (users or {}).get(username)
        if not uid or not isinstance(user_cfg, dict):
            continue
        if not user_compat.tuic_enabled(user_cfg):
            continue
        token = str(user_cfg.get('sub_token') or '').strip()
        if token:
            tuic_users[uid] = f'{username}:{token}'
    return _ensure_tuic_accepts_config(cfg)


def sync_user_plan(users, plan, *, path=None):
    if not users and not plan:
        return False
    p = Path(path) if path else CONFIG_FILE
    desired = render_from_user_plan(users, plan)
    current = _load_json(p, None)
    if current == desired:
        return False
    _save_config(p, desired)
    return True


def reload_async():
    try:
        subprocess.Popen(
            ['systemd-run', '--no-block', '--unit', f'tuic-reload-{int(time.time())}',
             'systemctl', 'restart', 'tuic-server.service'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
