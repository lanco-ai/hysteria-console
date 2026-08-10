"""xray VLESS Reality config helpers — owns ADR-0002.

Each user must exist as a `clients[]` entry in BOTH inbound ports (443 primary,
8443 backup) so that clients have transparent failover when the primary path is
blocked. Inside xray's config, the 8443 entry's email field carries a reserved
`@hy2-backup.invalid` suffix; outside this module the suffix is invisible —
usage aggregation strips it via `strip_backup_suffix()`.

Maintenance rule: any code that mutates xray clients must go through `sync_user`
/ `remove_user`, never edit the config file directly. Forgetting one of the two
inbound ports leaves the user reachable on one and rejected on the other, with
no obvious error.
"""
import json
import grp
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import static_access
import state_store

CONFIG_FILE = Path('/usr/local/etc/xray/config.json')
PRODUCTION_CONFIG_FILE = Path('/usr/local/etc/xray/config.json')
XRAY_BIN = '/usr/local/bin/xray'
CONFIG_GROUP = 'hy2-xray'
CONFIG_MODE = 0o640
INBOUND_PORTS = (443, 8443)
PRIMARY_PORT = 443
# ``@`` is outside the creatable username alphabet, so a primary user such as
# ``alice-backup`` can never collide with a generated backup identity.
BACKUP_SUFFIX = '@hy2-backup.invalid'
RELOAD_SCHEDULE_TIMEOUT_SECONDS = 5
RELOAD_RESTART_TIMEOUT_SECONDS = 30
RELOAD_READINESS_DELAY_SECONDS = 0.25
RELOAD_READINESS_TIMEOUT_SECONDS = 5
RELOAD_READINESS_STABILITY_PROBES = 3
RELOAD_WORKER_FLAG = '--complete-reload'
RELOAD_SERVICE = 'xray'


def email_for(port, username):
    """Return the xray client `email` field for `username` on `port`.

    The 8443 inbound carries the reserved backup suffix; 443 carries the bare
    username.
    """
    return username if port == PRIMARY_PORT else f'{username}{BACKUP_SUFFIX}'


def strip_backup_suffix(email):
    """Reduce a possibly-suffixed xray client email back to its canonical user id."""
    return email[: -len(BACKUP_SUFFIX)] if email.endswith(BACKUP_SUFFIX) else email


def _load_config(path):
    try:
        config = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise state_store.InvalidJsonState(
            f'cannot load xray config: {path}'
        ) from exc
    return _validate_runtime_config(config, source=path)


def _validate_runtime_config(config, *, source):
    """Validate the structural invariants required by the sync helpers."""
    if not isinstance(config, dict):
        raise state_store.InvalidJsonState(
            f'xray config must be an object: {source}'
        )
    ports = set()
    inbounds = config.get('inbounds')
    if not isinstance(inbounds, list):
        raise state_store.InvalidJsonState(
            f'xray config inbounds must be a list: {source}'
        )
    for inbound in inbounds:
        if not isinstance(inbound, dict) or inbound.get('protocol') != 'vless':
            continue
        port = inbound.get('port')
        if port not in INBOUND_PORTS:
            continue
        clients = (inbound.get('settings') or {}).get('clients')
        if not isinstance(clients, list):
            raise state_store.InvalidJsonState(
                f'xray vless clients must be a list: {source}'
            )
        ports.add(port)
    if ports != set(INBOUND_PORTS):
        raise state_store.InvalidJsonState(
            f'xray config must contain vless inbounds on both ports: {source}'
        )
    return config


def initialize_from_file(candidate, *, path=None):
    """Atomically initialize a missing runtime config from a validated file.

    An existing runtime config is state, not a disposable template: it contains
    dynamic user clients. Deployments therefore validate and preserve it
    byte-for-byte instead of truncating it back to the repository template.
    """
    candidate_path = Path(candidate)
    target = Path(path) if path else CONFIG_FILE
    try:
        desired = json.loads(candidate_path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise state_store.InvalidJsonState(
            f'cannot load xray candidate config: {candidate_path}'
        ) from exc
    _validate_runtime_config(desired, source=candidate_path)

    with state_store.file_lock(_config_lock_path(target)):
        if target.exists():
            try:
                current = json.loads(target.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise state_store.InvalidJsonState(
                    f'cannot load existing xray config: {target}'
                ) from exc
            _validate_runtime_config(current, source=target)
            # Preserve operator-managed bytes, but normalize metadata for the
            # hardened service account that will read the file after deploy.
            _secure_config_permissions(target)
            return False
        _save_config(target, desired)
        return True


def _secure_config_permissions(path):
    if os.geteuid() == 0:
        try:
            target_gid = grp.getgrnam(CONFIG_GROUP).gr_gid
            if path.stat().st_gid != target_gid:
                os.chown(path, 0, target_gid)
        except KeyError:
            pass
    path.chmod(CONFIG_MODE)


def _prepare_temp_permissions(fd):
    """Set final ownership/mode before rename so a crash cannot strand an
    unreadable root:root config at the live path."""
    if os.geteuid() == 0:
        try:
            target_gid = grp.getgrnam(CONFIG_GROUP).gr_gid
        except KeyError as exc:
            raise RuntimeError(
                f'required xray config group does not exist: {CONFIG_GROUP}'
            ) from exc
        os.fchown(fd, 0, target_gid)
    os.fchmod(fd, CONFIG_MODE)


def _save_config(path, cfg):
    """Atomic write: serialize to a sibling temp file, fsync, then rename.
    A naked write_text() can leave config.json truncated if the process is
    killed mid-write — xray then refuses to start, taking both inbounds with it.
    """
    payload = json.dumps(cfg, indent=2, ensure_ascii=False) + '\n'
    fd = None
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + '.', suffix='.tmp', dir=str(path.parent),
            text=True,
        )
        _prepare_temp_permissions(fd)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            fd = None
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        if path.absolute() == PRODUCTION_CONFIG_FILE.absolute():
            try:
                validation = subprocess.run(
                    [
                        XRAY_BIN,
                        'run',
                        '-test',
                        '-format',
                        'json',
                        '-config',
                        tmp_name,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=RELOAD_RESTART_TIMEOUT_SECONDS,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise state_store.InvalidJsonState(
                    'cannot validate staged xray config',
                ) from exc
            if validation.returncode != 0:
                raise state_store.InvalidJsonState(
                    'staged xray config failed native validation',
                )
        os.replace(tmp_name, path)
        tmp_name = None
        _secure_config_permissions(path)
        state_store._fsync_dir(path.parent)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink()
            except FileNotFoundError:
                pass


def _config_lock_path(path):
    return Path(str(path) + '.lock')


def _reload_pending_path(path):
    return Path(str(path) + '.reload.pending')


def _has_reload_pending(path):
    return _reload_pending_path(path).exists()


def _mark_reload_pending(path):
    """Persist intent before replacing the live config.

    Writing and fsyncing this unique token first makes a crash immediately
    after the config rename recoverable: the next sync reports that a reload is
    still required even when the desired config already matches the file.
    """
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
    """Clear only the marker whose restart actually completed successfully.

    A newer config write replaces the marker with a new token. Comparing under
    the config lock prevents an older reload completion from erasing that newer
    pending intent.
    """
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
    """Stop live static auth after a reload lifecycle failure.

    Passing our subprocess runner keeps every systemd call injectable in tests.
    Alternate config paths are never allowed to contact host systemd.
    """
    if not _is_live_config_path(path):
        return False
    return static_access.stop_fail_closed(
        RELOAD_SERVICE,
        reason=reason,
        live=True,
        runner=subprocess.run,
    )


def _apply_sync(cfg, username, vless_uuid):
    """Mutate `cfg` so `username` is present on both inbounds with `vless_uuid`.
    Returns True if anything was changed.
    """
    changed = False
    for ib in cfg.get('inbounds') or []:
        if ib.get('protocol') != 'vless':
            continue
        port = ib.get('port')
        if port not in INBOUND_PORTS:
            continue
        clients = ib.setdefault('settings', {}).setdefault('clients', [])
        email = email_for(port, username)
        existing = next((c for c in clients if c.get('email') == email), None)
        if existing is None:
            clients.append({'id': vless_uuid, 'email': email, 'flow': 'xtls-rprx-vision'})
            changed = True
        elif existing.get('id') != vless_uuid or existing.get('flow') != 'xtls-rprx-vision':
            existing['id'] = vless_uuid
            existing['flow'] = 'xtls-rprx-vision'
            changed = True
    return changed


def _apply_remove(cfg, username):
    """Mutate `cfg` to drop `username` from both inbounds. Returns True if changed."""
    targets = {email_for(port, username) for port in INBOUND_PORTS}
    changed = False
    for ib in cfg.get('inbounds') or []:
        if ib.get('protocol') != 'vless':
            continue
        clients = ib.get('settings', {}).get('clients') or []
        kept = [c for c in clients if c.get('email') not in targets]
        if len(kept) != len(clients):
            ib['settings']['clients'] = kept
            changed = True
    return changed


def _apply_managed_plan(cfg, plan):
    """Replace both VLESS client lists with the exact canonical user plan.

    This is the reconciliation path used by the limiter. It deliberately drops
    legacy/bootstrap clients, duplicate emails, and any other credential that
    is not backed by the canonical users state. Non-VLESS inbounds and all
    unrelated Xray configuration remain untouched.
    """
    desired_users = [
        (str(username), str(vless_uuid).strip())
        for username, vless_uuid in sorted(plan.items())
        if str(vless_uuid or '').strip()
    ]
    changed = False
    for inbound in cfg.get('inbounds') or []:
        if (
            inbound.get('protocol') != 'vless'
            or inbound.get('port') not in INBOUND_PORTS
        ):
            continue
        port = inbound['port']
        desired_clients = [
            {
                'id': vless_uuid,
                'email': email_for(port, username),
                'flow': 'xtls-rprx-vision',
            }
            for username, vless_uuid in desired_users
        ]
        settings = inbound.setdefault('settings', {})
        if settings.get('clients') != desired_clients:
            settings['clients'] = desired_clients
            changed = True
    return changed


def sync_user(username, vless_uuid, *, path=None):
    """Ensure `username` is present in every vless inbound under both ports
    with the given uuid. Returns True when a proxy reload is required, either
    because this call modified the config or an earlier reload is still pending.
    """
    p = Path(path) if path else CONFIG_FILE
    with state_store.file_lock(_config_lock_path(p)):
        cfg = _load_config(p)
        changed = _apply_sync(cfg, username, vless_uuid)
        if changed:
            _mark_reload_pending(p)
            _save_config(p, cfg)
        return changed or _has_reload_pending(p)


def remove_user(username, *, path=None):
    """Remove `username` from every vless inbound under both ports.
    Returns True when a proxy reload is required.
    """
    p = Path(path) if path else CONFIG_FILE
    with state_store.file_lock(_config_lock_path(p)):
        cfg = _load_config(p)
        changed = _apply_remove(cfg, username)
        if changed:
            _mark_reload_pending(p)
            _save_config(p, cfg)
        return changed or _has_reload_pending(p)


def apply_user_plan(plan, *, path=None, prune_unknown=False):
    """Batch-apply a `{username: vless_uuid_or_None}` plan with one read + one write.

    The cron tick used to call sync_user / remove_user once per user, each of
    which re-read and re-parsed the full xray config — O(N) full-file reads
    every 5 s. This helper folds them into a single load and a single (atomic)
    save, only writing when something actually changed. None / empty uuid means
    'remove the user from both inbounds'. When ``prune_unknown`` is true, the
    target inbounds are reconciled to the exact plan so no unmanaged or stale
    credential can survive outside canonical user state.
    Returns True when a proxy reload is required.
    """
    p = Path(path) if path else CONFIG_FILE
    with state_store.file_lock(_config_lock_path(p)):
        if not plan and not prune_unknown:
            return _has_reload_pending(p)
        cfg = _load_config(p)
        if prune_unknown:
            changed = _apply_managed_plan(cfg, plan)
        else:
            changed = False
            for username, vless_uuid in plan.items():
                uid = str(vless_uuid or '').strip()
                if uid:
                    changed = _apply_sync(cfg, username, uid) or changed
                else:
                    changed = _apply_remove(cfg, username) or changed
        if changed:
            _mark_reload_pending(p)
            _save_config(p, cfg)
        return changed or _has_reload_pending(p)


def _run_reload_worker(path, expected_token):
    """Restart xray and ACK only the exact config generation it loaded.

    This runs inside the transient systemd unit scheduled by ``reload_async``.
    A failed or timed-out restart deliberately leaves the durable marker for a
    later sync to retry.
    """
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

    # A single successful restart/active result can race with an immediate
    # crash. Require several spaced active observations before acknowledging
    # the durable generation.
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
    """Restart xray asynchronously via systemd-run so the HTTP response is not
    held by the restart and the new xray inherits no SSH parent.

    The transient unit runs this module as a completion worker. Merely
    scheduling that unit never acknowledges the marker: only a zero exit from
    the worker's real ``systemctl restart`` can do so.
    """
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
            p, RuntimeError('xray reload marker is invalid'),
        )
        return False
    unit = f'xray-reload-{time.time_ns()}-{secrets.token_hex(8)}'
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
