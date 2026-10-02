"""TUIC server config helpers.

TUIC authenticates from a static JSON user map, so any admin action that changes
users must keep this file in sync with users.json. We reuse each user's VLESS
UUID as the TUIC UUID and the Hysteria credential (`username:sub_token`) as the
TUIC password so subscriptions stay simple.
"""
import copy
import json
import secrets
import subprocess
import sys
import time
from pathlib import Path

import static_access
import state_store
import user_compat
import tuic_user_meter

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
    with state_store.file_lock(_config_lock_path(p)):
        current = _load_runtime_config(p)
        desired = _render_for_runtime(current, users, None, p)
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
    with state_store.file_lock(_config_lock_path(p)):
        current = _load_runtime_config(p)
        desired = _render_for_runtime(current, users, plan, p)
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
    if accounting_fault_path(p).exists():
        _fail_closed_reload(p, 'TUIC accounting fault requires operator recovery')
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



def metered_parts(cfg):
    """Detect explicit sing-box mode; malformed mode never falls back to legacy."""
    if cfg is None or 'inbounds' not in cfg:
        return None
    try:
        inbounds = cfg['inbounds']
        if not isinstance(inbounds, list) or len(inbounds) != 1 or inbounds[0]['type'] != 'tuic':
            raise ValueError('expected a dedicated TUIC inbound')
        inbound = inbounds[0]
        api = cfg['experimental']['v2ray_api']
        tuic_user_meter.validate_endpoint(api['listen'])
        if api['stats']['enabled'] is not True or not isinstance(api['stats']['users'], list):
            raise ValueError('TUIC user stats must be enabled')
        if not isinstance(inbound['users'], list):
            raise ValueError('named TUIC users required')
        names = [row['name'] for row in inbound['users']]
        stats_names = api['stats']['users']
        if (any(not user_compat.is_valid_username(name) for name in names + stats_names)
                or len(set(names)) != len(names)
                or len(set(stats_names)) != len(stats_names)
                or set(names) != set(stats_names)):
            raise ValueError('every authenticated TUIC user must have unique stats')
        return inbound, api
    except (KeyError, TypeError, ValueError) as exc:
        raise state_store.CriticalStateUnavailable('invalid metered TUIC config') from exc


def canonical_identities(users):
    return {name: tuic_user_meter.credential_id(name, str(user['vless_uuid']), f"{name}:{user['sub_token']}")
            for name, user in users.items()
            if isinstance(user, dict) and user.get('vless_uuid') and user.get('sub_token')}


def validate_named_identities(cfg, users):
    parts = metered_parts(cfg)
    if parts is None:
        raise state_store.CriticalStateUnavailable('named TUIC runtime is missing')
    canonical = canonical_identities(users)
    result = {}
    try:
        for row in parts[0]['users']:
            name = row['name']
            identity = tuic_user_meter.credential_id(name, row['uuid'], row['password'])
            if canonical.get(name) != identity:
                raise ValueError('runtime credential is not the canonical named identity')
            result[name] = identity
    except (KeyError, TypeError, ValueError) as exc:
        raise state_store.CriticalStateUnavailable('TUIC named identity mismatch') from exc
    return result


def _render_for_runtime(current, users, plan, path, *, offline=False):
    mode = 'legacy' if offline else tuic_user_meter.accounting_mode(Path(path).parent / 'state')
    if mode == 'active' and metered_parts(current) is None:
        raise state_store.CriticalStateUnavailable('active TUIC runtime config is missing')
    if metered_parts(current) is not None:
        cfg = copy.deepcopy(current)
        inbound, api = metered_parts(cfg)
        if not offline and accounting_fault_path(path).exists():
            plan = {}
        if plan is None:
            plan = {name: user.get('vless_uuid') for name, user in users.items()
                    if isinstance(user, dict) and not user_compat.is_inactive(user)
                    and not user_compat.is_metered(user)
                    and not user_compat.authorization_config_error(user)}
        named = []
        for name, uid in sorted(plan.items()):
            user = users.get(name)
            if uid and isinstance(user, dict) and user_compat.tuic_enabled(user) and user.get('sub_token'):
                named.append({'name': name, 'uuid': str(uid),
                              'password': f"{name}:{user['sub_token']}"})
        inbound['users'] = named
        api['stats']['users'] = [row['name'] for row in named]
        return cfg
    render = render_from_users if plan is None else render_from_user_plan
    args = (users,) if plan is None else (users, plan)
    rendered = render(*args, locked_user_file=_locked_user_file_for_config(path))
    desired = _base_config()
    if current is not None:
        desired.update(current)
    desired['users'] = rendered['users']
    return desired


def render_metered_runtime(legacy, users, plan, *, endpoint='127.0.0.1:10086', accept_runtime_defaults=False):
    """Offline migration candidate. Reject tuning without an exact mapping."""
    import ipaddress
    supported = {'server', 'users', 'certificate', 'private_key', 'congestion_control',
                 'alpn', 'zero_rtt_handshake', 'dual_stack', 'udp_relay_ipv6',
                 'auth_timeout', 'log_level'}
    unknown = set(legacy) - supported
    if accept_runtime_defaults:
        defaults = _base_config()
        unknown = {key for key in unknown
                   if key not in defaults or legacy[key] != defaults[key]}
    if unknown:
        raise state_store.InvalidJsonState('unmapped legacy TUIC settings: ' + ', '.join(sorted(unknown)))
    tuic_user_meter.validate_endpoint(endpoint)
    host, _, port = str(legacy.get('server', '[::]:9443')).rpartition(':')
    try:
        host = str(ipaddress.ip_address(host.strip('[]')))
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError('invalid port')
    except ValueError as exc:
        raise state_store.InvalidJsonState('invalid legacy TUIC bind') from exc
    if legacy.get('dual_stack', True) is not True:
        raise state_store.InvalidJsonState('dual_stack=false has no verified mapping')
    if legacy.get('zero_rtt_handshake', False) is not False:
        raise state_store.InvalidJsonState('zero RTT must remain disabled')
    inbound = {
        'type': 'tuic', 'tag': 'tuic-in', 'listen': host, 'listen_port': port,
        'users': [], 'congestion_control': legacy.get('congestion_control', 'bbr'),
        'zero_rtt_handshake': False, 'auth_timeout': legacy.get('auth_timeout', '3s'),
        'tls': {'enabled': True, 'alpn': legacy.get('alpn', ['h3']),
                'certificate_path': legacy.get('certificate', '/root/hysteria/server.crt'),
                'key_path': legacy.get('private_key', '/root/hysteria/server.key')},
    }
    cfg = {'log': {'level': legacy.get('log_level', 'warn')}, 'inbounds': [inbound],
           'outbounds': [{'type': 'direct', 'tag': 'direct'}],
           'route': {'rules': []},
           'experimental': {'v2ray_api': {'listen': endpoint, 'stats': {'enabled': True, 'users': []}}}}
    if legacy.get('udp_relay_ipv6', False) is False:
        cfg['route']['rules'].append({'network': 'udp', 'ip_version': 6, 'action': 'reject'})
    return _render_for_runtime(cfg, users, plan, CONFIG_FILE, offline=True)


def accounting_fault_path(path):
    return Path(str(path) + '.accounting-failed')


if __name__ == '__main__':
    raise SystemExit(_main())
