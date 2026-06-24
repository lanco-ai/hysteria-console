"""xray VLESS Reality config helpers — owns ADR-0002.

Each user must exist as a `clients[]` entry in BOTH inbound ports (443 primary,
8443 backup) so that clients have transparent failover when the primary path is
blocked. Inside xray's config, the 8443 entry's email field carries a `-backup`
suffix; outside this module the suffix is invisible — usage aggregation strips
it via `strip_backup_suffix()`.

Maintenance rule: any code that mutates xray clients must go through `sync_user`
/ `remove_user`, never edit the config file directly. Forgetting one of the two
inbound ports leaves the user reachable on one and rejected on the other, with
no obvious error.
"""
import json
import grp
import os
import subprocess
import time
from pathlib import Path

CONFIG_FILE = Path('/usr/local/etc/xray/config.json')
CONFIG_GROUP = 'nogroup'
CONFIG_MODE = 0o640
INBOUND_PORTS = (443, 8443)
PRIMARY_PORT = 443
BACKUP_SUFFIX = '-backup'


def email_for(port, username):
    """Return the xray client `email` field for `username` on `port`.

    The 8443 inbound carries the `-backup` suffix; 443 carries the bare username.
    """
    return username if port == PRIMARY_PORT else f'{username}{BACKUP_SUFFIX}'


def strip_backup_suffix(email):
    """Reduce a possibly-suffixed xray client email back to its canonical user id."""
    return email[: -len(BACKUP_SUFFIX)] if email.endswith(BACKUP_SUFFIX) else email


def _load_config(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None


def _secure_config_permissions(path):
    if os.geteuid() == 0:
        try:
            os.chown(path, 0, grp.getgrnam(CONFIG_GROUP).gr_gid)
        except KeyError:
            pass
    path.chmod(CONFIG_MODE)


def _save_config(path, cfg):
    """Atomic write: serialize to a sibling temp file, fsync, then rename.
    A naked write_text() can leave config.json truncated if the process is
    killed mid-write — xray then refuses to start, taking both inbounds with it.
    """
    payload = json.dumps(cfg, indent=2, ensure_ascii=False) + '\n'
    tmp = path.with_name(path.name + '.tmp')
    with tmp.open('w', encoding='utf-8') as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _secure_config_permissions(path)


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


def sync_user(username, vless_uuid, *, path=None):
    """Ensure `username` is present in every vless inbound under both ports
    with the given uuid. Returns True if the config file was modified.
    """
    p = Path(path) if path else CONFIG_FILE
    cfg = _load_config(p)
    if cfg is None:
        return False
    changed = _apply_sync(cfg, username, vless_uuid)
    if changed:
        _save_config(p, cfg)
    return changed


def remove_user(username, *, path=None):
    """Remove `username` from every vless inbound under both ports.
    Returns True if the config file was modified.
    """
    p = Path(path) if path else CONFIG_FILE
    cfg = _load_config(p)
    if cfg is None:
        return False
    changed = _apply_remove(cfg, username)
    if changed:
        _save_config(p, cfg)
    return changed


def apply_user_plan(plan, *, path=None):
    """Batch-apply a `{username: vless_uuid_or_None}` plan with one read + one write.

    The cron tick used to call sync_user / remove_user once per user, each of
    which re-read and re-parsed the full xray config — O(N) full-file reads
    every 5 s. This helper folds them into a single load and a single (atomic)
    save, only writing when something actually changed. None / empty uuid means
    'remove the user from both inbounds'.
    Returns True if the config file was modified.
    """
    if not plan:
        return False
    p = Path(path) if path else CONFIG_FILE
    cfg = _load_config(p)
    if cfg is None:
        return False
    changed = False
    for username, vless_uuid in plan.items():
        uid = str(vless_uuid or '').strip()
        if uid:
            changed = _apply_sync(cfg, username, uid) or changed
        else:
            changed = _apply_remove(cfg, username) or changed
    if changed:
        _save_config(p, cfg)
    return changed


def reload_async():
    """Restart xray asynchronously via systemd-run so the HTTP response is not
    held by the restart and the new xray inherits no SSH parent.
    """
    try:
        subprocess.Popen(
            ['systemd-run', '--no-block', '--unit', f'xray-reload-{int(time.time())}',
             'systemctl', 'restart', 'xray'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass
