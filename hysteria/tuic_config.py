"""TUIC server config helpers.

TUIC authenticates from a static JSON user map, so any admin action that changes
users must keep this file in sync with users.json. We reuse each user's VLESS
UUID as the TUIC UUID and the Hysteria credential (`username:sub_token`) as the
TUIC password so subscriptions stay simple.
"""
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import static_access
import state_store
import user_compat

USERS_FILE = Path('/root/hysteria/users.json')
CONFIG_FILE = Path('/root/hysteria/tuic.json')
LOCKED_USER_FILE = Path('/root/hysteria/state/tuic_locked_user.json')
PRODUCTION_LOCKED_USER_FILE = Path(
    '/root/hysteria/state/tuic_locked_user.json'
)
LOCKED_USER_UUID = '00000000-0000-4000-8000-000000000000'
RELOAD_SCHEDULE_TIMEOUT_SECONDS = 5
RELOAD_RESTART_TIMEOUT_SECONDS = 30
RELOAD_READINESS_DELAY_SECONDS = 0.25
RELOAD_READINESS_TIMEOUT_SECONDS = 5
RELOAD_READINESS_STABILITY_PROBES = 3
RELOAD_WORKER_FLAG = '--complete-reload'
RELOAD_SERVICE = 'tuic-server.service'


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


def _load_runtime_config(path):
    """Preserve an existing config on parse/type failures instead of replacing it."""
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise state_store.InvalidJsonState(
            f'cannot load TUIC config: {path}',
        ) from exc
    if not isinstance(data, dict):
        raise state_store.InvalidJsonState(
            f'TUIC config must be an object: {path}',
        )
    return data


def _save_config(path, cfg):
    payload = json.dumps(cfg, indent=2, ensure_ascii=False) + '\n'
    state_store.save_text_atomic(path, payload)
    path.chmod(0o600)


def _save_secret(path, data):
    payload = json.dumps(data, indent=2, ensure_ascii=True) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    state_store.save_text_atomic(path, payload)
    path.chmod(0o600)


def _config_lock_path(path):
    return Path(str(path) + '.lock')


def _reload_pending_path(path):
    return Path(str(path) + '.reload.pending')


def _has_reload_pending(path):
    return _reload_pending_path(path).exists()


def _mark_reload_pending(path):
    """Persist reload intent before the live TUIC config is replaced."""
    token = f'{time.time_ns()}-{secrets.token_hex(16)}'
    state_store.save_text_atomic(_reload_pending_path(path), token + '\n')
    return token


def _read_reload_pending(path):
    try:
        return (
            _reload_pending_path(path).read_text(encoding='utf-8').strip()
            or None
        )
    except (OSError, UnicodeError):
        return None


def _valid_reload_token(token):
    stamp, separator, nonce = str(token or '').partition('-')
    return (
        separator == '-'
        and 1 <= len(stamp) <= 30
        and stamp.isdigit()
        and len(nonce) == 32
        and all(ch in '0123456789abcdef' for ch in nonce)
    )


def _clear_reload_pending(path, expected_token):
    """Remove only the marker covered by a successful completed restart."""
    if expected_token is None:
        return
    marker = _reload_pending_path(path)
    with state_store.file_lock(_config_lock_path(path)):
        try:
            current_token = marker.read_text(encoding='utf-8').strip()
        except (OSError, UnicodeError):
            return
        if current_token != expected_token:
            return
        try:
            marker.unlink()
        except OSError:
            return
        state_store._fsync_dir(marker.parent)


def _is_live_config_path(path):
    """Return true only for the configured production runtime path."""
    return Path(path).absolute() == Path(CONFIG_FILE).absolute()


def _fail_closed_reload(path, reason):
    """Stop live static auth after a reload lifecycle failure."""
    if not _is_live_config_path(path):
        return False
    return static_access.stop_fail_closed(
        RELOAD_SERVICE,
        reason=reason,
        live=True,
        runner=subprocess.run,
    )


def _locked_user_password(path=None):
    secret_path = Path(path) if path is not None else LOCKED_USER_FILE
    with state_store.file_lock(Path(str(secret_path) + '.lock')):
        data = _load_json(secret_path, {})
        password = str((data or {}).get('password') or '').strip()
        if password:
            return password
        password = secrets.token_urlsafe(48)
        _save_secret(secret_path, {'password': password})
        return password


def _ensure_tuic_accepts_config(cfg, *, locked_user_file=None):
    """TUIC server 1.0.0 refuses to start when users is empty.

    Keep the service healthy with a local random locked credential when every
    real user is suspended, expired, over quota, or not yet created. The locked
    user is never emitted in subscriptions, and the password is generated on
    the node rather than hard-coded in the repo.
    """
    users = cfg.setdefault('users', {})
    if not users:
        users[LOCKED_USER_UUID] = _locked_user_password(locked_user_file)
    return cfg


def render_from_users(users, *, locked_user_file=None):
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
    return _ensure_tuic_accepts_config(
        cfg, locked_user_file=locked_user_file,
    )


def _locked_user_file_for_config(config_path):
    if Path(LOCKED_USER_FILE) != PRODUCTION_LOCKED_USER_FILE:
        return LOCKED_USER_FILE
    if Path(config_path).absolute() == Path(CONFIG_FILE).absolute():
        return LOCKED_USER_FILE
    return Path(config_path).parent / 'tuic_locked_user.json'


def sync_all(*, users=None, path=None):
    p = Path(path) if path else CONFIG_FILE
    if users is None:
        users = state_store.load_json_strict(
            USERS_FILE, {}, required=True,
        )
    rendered = render_from_users(
        users,
        locked_user_file=_locked_user_file_for_config(p),
    )
    with state_store.file_lock(_config_lock_path(p)):
        current = _load_runtime_config(p)
        desired = _base_config()
        if current is not None:
            desired.update(current)
        desired['users'] = rendered['users']
        if current == desired:
            return _has_reload_pending(p)
        _mark_reload_pending(p)
        _save_config(p, desired)
        return True


def render_from_user_plan(users, plan, *, locked_user_file=None):
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
    return _ensure_tuic_accepts_config(
        cfg, locked_user_file=locked_user_file,
    )


def sync_user_plan(users, plan, *, path=None):
    p = Path(path) if path else CONFIG_FILE
    rendered = render_from_user_plan(
        users,
        plan,
        locked_user_file=_locked_user_file_for_config(p),
    )
    with state_store.file_lock(_config_lock_path(p)):
        current = _load_runtime_config(p)
        desired = _base_config()
        if current is not None:
            desired.update(current)
        desired['users'] = rendered['users']
        if current == desired:
            return _has_reload_pending(p)
        _mark_reload_pending(p)
        _save_config(p, desired)
        return True


def _run_reload_worker(path, expected_token):
    """Restart TUIC and ACK only the exact config generation it loaded."""
    p = Path(path)
    if not _is_live_config_path(p):
        return False
    try:
        result = subprocess.run(
            ['systemctl', 'restart', RELOAD_SERVICE],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=RELOAD_RESTART_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _fail_closed_reload(p, exc)
        return False
    if result.returncode != 0:
        _fail_closed_reload(
            p,
            RuntimeError(
                f'{RELOAD_SERVICE} restart returned {result.returncode}'
            ),
        )
        return False

    for _probe in range(RELOAD_READINESS_STABILITY_PROBES):
        time.sleep(RELOAD_READINESS_DELAY_SECONDS)
        try:
            readiness = subprocess.run(
                ['systemctl', 'is-active', '--quiet', RELOAD_SERVICE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=RELOAD_READINESS_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _fail_closed_reload(p, exc)
            return False
        if readiness.returncode != 0:
            _fail_closed_reload(
                p,
                RuntimeError(
                    f'{RELOAD_SERVICE} readiness returned '
                    f'{readiness.returncode}'
                ),
            )
            return False
    _clear_reload_pending(p, expected_token)
    return True


def reload_async(*, path=None):
    """Schedule a TUIC restart; its worker ACKs only after real completion."""
    p = Path(path) if path else CONFIG_FILE
    if not _is_live_config_path(p):
        return False
    try:
        pending_token = _reload_pending_path(p).read_text(
            encoding='utf-8',
        ).strip()
    except FileNotFoundError:
        return False
    except (OSError, UnicodeError) as exc:
        _fail_closed_reload(p, exc)
        return False
    if not _valid_reload_token(pending_token):
        _fail_closed_reload(
            p, RuntimeError('TUIC reload marker is invalid'),
        )
        return False
    unit = f'tuic-reload-{time.time_ns()}-{secrets.token_hex(8)}'
    process = None
    try:
        process = subprocess.Popen(
            ['systemd-run', '--no-block', '--unit', unit,
             sys.executable, str(Path(__file__).resolve()),
             RELOAD_WORKER_FLAG, str(p), pending_token or ''],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        status = process.wait(timeout=RELOAD_SCHEDULE_TIMEOUT_SECONDS)
        if status != 0:
            _fail_closed_reload(
                p,
                RuntimeError(
                    f'systemd-run scheduling returned {status}'
                ),
            )
            return False
    except subprocess.TimeoutExpired as exc:
        try:
            process.kill()
            process.wait()
        except Exception:
            pass
        _fail_closed_reload(p, exc)
        return False
    except Exception as exc:
        _fail_closed_reload(p, exc)
        return False
    return True


def _main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != RELOAD_WORKER_FLAG:
        return 2
    return 0 if _run_reload_worker(args[1], args[2] or None) else 1


if __name__ == '__main__':
    raise SystemExit(_main())
