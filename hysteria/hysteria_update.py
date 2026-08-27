"""Official Hysteria binary check / apply / rollback.

Display-only landing and authorization paths must never import this module
for decision making. Apply is opt-in (policy.enabled defaults to false) and
always fails closed: a hash/sanity/readyz failure restores last-good.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request

import health
import state_store


BINARY_PATH = '/usr/local/bin/hysteria'
LAST_GOOD_PATH = '/root/hysteria/state/hysteria.binary.last-good'
STATE_PATH = '/root/hysteria/state/hysteria-update.json'
POLICY_PATH = '/root/hysteria/state/hysteria-update-policy.json'
LOCK_PATH = '/root/hysteria/state/hysteria-update.lock'
RELEASES_URL = 'https://api.github.com/repos/apernet/hysteria/releases/latest'
ASSET_NAME = 'hysteria-linux-amd64'
CHECKSUMS_NAME = 'hysteria-linux-amd64.sha256'
UNIT = 'hysteria-server.service'
DEFAULT_POLICY = {
    'enabled': False,
    'channel': 'stable',
    'maintenance_window': None,
    'min_release_age_hours': 24,
    'skip_if_online_users': True,
    'cooldown_hours': 24,
}
IDLE_STATE = {
    'status': 'idle',
    'version': '',
    'previous_version': '',
    'sha256': '',
    'ts': '',
    'error': '',
    'pending_confirm': False,
}


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_policy(path=POLICY_PATH):
    raw = {}
    try:
        raw = state_store.load_json(path, {})
    except Exception:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    policy = dict(DEFAULT_POLICY)
    policy['enabled'] = bool(raw.get('enabled')) is True
    channel = str(raw.get('channel') or 'stable').strip().lower()
    policy['channel'] = channel if channel in ('stable', 'pre') else 'stable'
    try:
        age = int(raw.get('min_release_age_hours', 24))
    except (TypeError, ValueError):
        age = 24
    policy['min_release_age_hours'] = max(0, min(age, 720))
    policy['skip_if_online_users'] = bool(raw.get('skip_if_online_users', True))
    try:
        cooldown = int(raw.get('cooldown_hours', 24))
    except (TypeError, ValueError):
        cooldown = 24
    policy['cooldown_hours'] = max(1, min(cooldown, 720))
    window = raw.get('maintenance_window')
    policy['maintenance_window'] = window if isinstance(window, dict) else None
    return policy


def load_state(path=STATE_PATH):
    try:
        data = state_store.load_json(path, dict(IDLE_STATE))
    except Exception:
        return dict(IDLE_STATE)
    if not isinstance(data, dict):
        return dict(IDLE_STATE)
    out = dict(IDLE_STATE)
    out.update({k: data.get(k, out[k]) for k in out})
    return out


def save_state(data, path=STATE_PATH):
    state_store.save_json(path, data)


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _http_json(url, *, opener=None):
    req = urllib.request.Request(
        url,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': 'hysteria-console-update',
        },
    )
    fetch = opener or urllib.request.urlopen
    with fetch(req, timeout=8) as resp:
        payload = resp.read(1024 * 1024)
    return json.loads(payload.decode('utf-8'))


def _download(url, dest, *, opener=None, max_bytes=80 * 1024 * 1024):
    req = urllib.request.Request(url, headers={'User-Agent': 'hysteria-console-update'})
    fetch = opener or urllib.request.urlopen
    with fetch(req, timeout=30) as resp, open(dest, 'wb') as out:
        written = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > max_bytes:
                raise ValueError('download too large')
            out.write(chunk)


def _parse_checksums(text, asset_name=ASSET_NAME):
    for line in str(text or '').splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[-1].endswith(asset_name):
            return parts[0].lower()
        if len(parts) == 1 and re.fullmatch(r'[0-9a-fA-F]{64}', parts[0]):
            return parts[0].lower()
    return ''


def check_latest(*, opener=None, runner=subprocess.run, binary_path=BINARY_PATH):
    """Compare GitHub latest with the running binary. Never writes a binary."""
    try:
        out = runner(
            [binary_path, 'version'],
            capture_output=True, text=True, timeout=3,
        )
        current = health._parse_hysteria_version(
            (out.stdout or '') + '\n' + (out.stderr or ''),
        ) or ''
    except Exception:
        current = health._current_hysteria_version(runner=runner) or ''
    release = _http_json(RELEASES_URL, opener=opener)
    tag = str(release.get('tag_name') or '').strip()
    assets = {
        str(item.get('name') or ''): str(item.get('browser_download_url') or '')
        for item in release.get('assets') or []
        if isinstance(item, dict)
    }
    info = {
        'ok': True,
        'current': current,
        'latest': tag,
        'asset_url': assets.get(ASSET_NAME, ''),
        'checksum_url': assets.get(CHECKSUMS_NAME, ''),
        'update_available': False,
    }
    cand = health._normalize_hysteria_version(tag)
    curr = health._normalize_hysteria_version(current)
    if cand and curr and cand != curr:
        cand_t = health._hysteria_version_tuple(cand)
        curr_t = health._hysteria_version_tuple(curr)
        if cand_t is None or curr_t is None or cand_t > curr_t:
            info['update_available'] = True
    return info


def record_check(info, *, path=STATE_PATH):
    state = load_state(path)
    state['status'] = 'checked'
    state['version'] = info.get('latest') or ''
    state['previous_version'] = info.get('current') or ''
    state['ts'] = _now_iso()
    state['error'] = ''
    state['pending_confirm'] = False
    save_state(state, path)
    return state


def _ready(runner, connection_factory=None):
    kwargs = {'runner': runner} if False else {}
    del kwargs
    if connection_factory is not None:
        return health.probe_auth_readiness(connection_factory=connection_factory)
    return health.probe_auth_readiness()


def _restart(runner):
    result = runner(
        ['systemctl', 'restart', UNIT],
        capture_output=True, text=True, timeout=30,
    )
    return getattr(result, 'returncode', 1) == 0


def _atomic_install(src, dest, *, mode=0o755):
    """Copy src onto dest via a same-directory temp file and os.replace.

    Direct overwrite of the live binary can leave a truncated file if the
    process is killed mid-copy. replace() is atomic on the same filesystem.
    """
    dest = os.fspath(dest)
    parent = os.path.dirname(dest) or '.'
    os.makedirs(parent, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=os.path.basename(dest) + '.',
        suffix='.tmp',
        dir=parent,
    )
    try:
        with os.fdopen(fd, 'wb') as out, open(src, 'rb') as inp:
            shutil.copyfileobj(inp, out)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(tmp_name, mode)
        os.replace(tmp_name, dest)
        tmp_name = None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def apply_update(
    *,
    opener=None,
    runner=subprocess.run,
    binary_path=BINARY_PATH,
    last_good_path=LAST_GOOD_PATH,
    state_path=STATE_PATH,
    connection_factory=None,
    ready_probe=None,
):
    """Download, verify, atomically replace, restart, and roll back on failure."""
    with state_store.file_lock(LOCK_PATH, timeout=5):
        state = load_state(state_path)
        try:
            info = check_latest(
                opener=opener, runner=runner, binary_path=binary_path,
            )
        except Exception as exc:
            state.update({
                'status': 'failed', 'error': f'check failed: {exc}',
                'ts': _now_iso(), 'pending_confirm': False,
            })
            save_state(state, state_path)
            return state
        if not info.get('update_available'):
            state.update({
                'status': 'done', 'version': info.get('current') or '',
                'error': '', 'ts': _now_iso(), 'pending_confirm': False,
            })
            save_state(state, state_path)
            return state
        if not info.get('asset_url') or not info.get('checksum_url'):
            state.update({
                'status': 'failed', 'error': 'release assets missing',
                'ts': _now_iso(),
            })
            save_state(state, state_path)
            return state

        work = tempfile.mkdtemp(prefix='hy-upd.')
        try:
            asset = os.path.join(work, ASSET_NAME)
            sums = os.path.join(work, CHECKSUMS_NAME)
            state.update({
                'status': 'downloading', 'version': info['latest'],
                'previous_version': info.get('current') or '',
                'pending_confirm': True, 'ts': _now_iso(), 'error': '',
            })
            save_state(state, state_path)
            _download(info['asset_url'], asset, opener=opener)
            _download(info['checksum_url'], sums, opener=opener)
            expected = _parse_checksums(open(sums, encoding='utf-8').read())
            actual = _sha256_file(asset)
            if not expected or expected != actual:
                raise ValueError('checksum mismatch')
            os.chmod(asset, 0o755)
            sanity = runner(
                [asset, 'version'],
                capture_output=True, text=True, timeout=5,
            )
            parsed = health._parse_hysteria_version(
                (sanity.stdout or '') + '\n' + (sanity.stderr or ''),
            )
            if not parsed:
                raise ValueError('new binary failed version sanity check')
            if os.path.exists(binary_path):
                _atomic_install(binary_path, last_good_path, mode=0o555)
            state.update({
                'status': 'applying', 'sha256': actual, 'ts': _now_iso(),
                'pending_confirm': True,
            })
            save_state(state, state_path)
            os.replace(asset, binary_path)
            os.chmod(binary_path, 0o755)
            if not _restart(runner):
                raise RuntimeError('restart failed')
            probe = ready_probe or (
                lambda: _ready(runner, connection_factory)
            )
            ready = probe()
            if not ready.get('ok'):
                raise RuntimeError('readyz failed after apply')
            state.update({
                'status': 'done', 'pending_confirm': False,
                'error': '', 'ts': _now_iso(),
            })
            save_state(state, state_path)
            return state
        except Exception as exc:
            rolled = False
            if os.path.exists(last_good_path):
                try:
                    _atomic_install(last_good_path, binary_path, mode=0o755)
                    _restart(runner)
                    probe = ready_probe or (
                        lambda: _ready(runner, connection_factory)
                    )
                    probe()
                    rolled = True
                except Exception:
                    rolled = False
            state.update({
                'status': 'rolled_back' if rolled else 'failed',
                'error': str(exc),
                'pending_confirm': False,
                'ts': _now_iso(),
            })
            save_state(state, state_path)
            return state
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _interrupted_apply(state):
    return bool(state.get('pending_confirm')) and state.get('status') in (
        'downloading', 'applying', 'verifying',
    )


def reconcile(*, runner=subprocess.run, binary_path=BINARY_PATH,
              last_good_path=LAST_GOOD_PATH, state_path=STATE_PATH,
              ready_probe=None):
    """Converge an interrupted apply without assuming success.

    Any unconfirmed apply (pending_confirm plus downloading/applying/
    verifying) is unsafe. If last-good exists, atomically restore it,
    restart, re-check readiness, and persist rolled_back. If last-good is
    missing, leave the live binary untouched and persist failed.
    """
    with state_store.file_lock(LOCK_PATH, timeout=5):
        state = load_state(state_path)
        if not _interrupted_apply(state):
            return state
        probe = ready_probe or (lambda: health.probe_auth_readiness())
        if os.path.exists(last_good_path):
            _atomic_install(last_good_path, binary_path, mode=0o755)
            _restart(runner)
            probe()
            state.update({
                'status': 'rolled_back',
                'error': 'interrupted apply reconciled',
                'pending_confirm': False,
                'ts': _now_iso(),
            })
            save_state(state, state_path)
            return state
        state.update({
            'status': 'failed',
            'error': 'interrupted apply, no last-good',
            'pending_confirm': False,
            'ts': _now_iso(),
        })
        save_state(state, state_path)
        return state


def render_history(state=None):
    data = state or load_state()
    status = html_escape = __import__('html').escape
    st = status(str(data.get('status') or 'idle'))
    ver = status(str(data.get('version') or '—'))
    prev = status(str(data.get('previous_version') or '—'))
    ts = status(str(data.get('ts') or '—'))
    err = status(str(data.get('error') or ''))
    err_row = f'<div class="small">{err}</div>' if err else ''
    return (
        '<div class="card hysteria-update-history">'
        '<h2 class="section-title mb-sm">Hysteria 更新</h2>'
        f'<div class="small">状态：{st} · 目标 {ver} · 之前 {prev}</div>'
        f'<div class="small faint">{ts}</div>'
        f'{err_row}'
        '<form method="post" action="/admin/hysteria-update/check" class="inline-form-row mt-sm">'
        '<button class="btn ghost btn-sm" type="submit">检查更新</button></form>'
        '<form method="post" action="/admin/hysteria-update/apply" class="inline-form-row mt-sm"'
        ' data-confirm="将下载官方二进制、校验哈希并重启 Hysteria。失败会自动回滚。确认继续？">'
        '<button class="btn btn-sm" type="submit">立即更新</button></form>'
        '</div>'
    )
