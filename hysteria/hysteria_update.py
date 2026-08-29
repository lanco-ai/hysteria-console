"""Official Hysteria binary check / apply / rollback.

Display-only landing and authorization paths must never import this module
for decision making. Apply is opt-in (policy.enabled defaults to false) and
always fails closed: a hash/sanity/readyz failure restores last-good.
"""
from datetime import datetime, timezone
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request

import alerts
import health
import online_snapshot
import state_store
import timeutil


BINARY_PATH = '/usr/local/bin/hysteria'
LAST_GOOD_PATH = '/root/hysteria/state/hysteria.binary.last-good'
STATE_PATH = '/root/hysteria/state/hysteria-update.json'
POLICY_PATH = '/root/hysteria/state/hysteria-update-policy.json'
LOCK_PATH = '/root/hysteria/state/hysteria-update.lock'
# apernet/hysteria only survives as a rename redirect; point at the
# canonical repository so checks do not depend on that redirect.
RELEASES_URL = 'https://api.github.com/repos/HyNetworks/hysteria/releases/latest'
ASSET_NAME = 'hysteria-linux-amd64'
# Upstream publishes ONE combined listing for every platform, built with
# `sha256sum build/* >> build/hashes.txt`. There is no per-asset
# '<asset>.sha256' file, so the previous name never resolved and every
# apply failed with 'release assets missing'.
CHECKSUMS_NAME = 'hashes.txt'
# Checksum listings decorate the file name: sha256sum binary mode prefixes
# '*', BSD mode wraps it in parentheses.
_CHECKSUM_NAME_TRIM = '()*'
UNIT = 'hysteria-server.service'
# Operational paths that must stay in sync with subscription_service. This
# module must never import subscription_service (too heavy, and it lives on
# the auth/subscription code path the updater is forbidden to depend on).
BACKUP_DIR = '/root/hysteria/backups'
ONLINE_FILE = '/root/hysteria/state/online.json'
# The producer runs every 90 seconds (with 10 seconds AccuracySec). Keep two
# complete production intervals so normal timer jitter remains usable while a
# stopped/corrupt producer still fails closed promptly.
ONLINE_SNAPSHOT_TTL_SECONDS = 180.0
DEPLOY_LOCK_EXEC = '/usr/local/sbin/hy2-lock-exec.py'
DEPLOY_LOCK_PATH = '/run/hy2-locks/deploy.lock'
# The CLI/timer path uses a short acquire timeout so a busy update lock does
# not stall journald; the next timer tick retries. The web route keeps 5s.
CLI_LOCK_TIMEOUT = 1.0
WORKER_LOCK_TIMEOUT = 30.0
READINESS_STABILITY_PROBES = 3
READINESS_DELAY_SECONDS = 2.0
READINESS_TIMEOUT_SECONDS = 90.0
SCHEDULE_TIMEOUT_SECONDS = 5.0
_ISOLATED_BOOTSTRAP = (
    'import runpy,sys;'
    'sys.path.insert(0,"/root/hysteria");'
    'runpy.run_module("hysteria_update",run_name="__main__")'
)
_PENDING_STATUSES = frozenset((
    'scheduled', 'checking', 'downloading', 'applying', 'verifying',
))
DEFAULT_POLICY = {
    'enabled': False,
    'channel': 'stable',
    # maintenance_window shape: {"start": "03:30", "end": "05:00"} in local
    # time (timeutil.LOCAL_TZ). start later than end is a cross-midnight
    # window. None means "no maintenance restriction".
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
    'reason': '',
    'pending_confirm': False,
    'operation_id': '',
    'operation_mode': '',
    'live_replaced': False,
    'rollback_operation_id': '',
    'cooldown_anchor': '',
    'primary_error': '',
    'rollback_error': '',
    'notification_event_id': '',
    'notification_status': '',
    'notification_attempts': 0,
    'notification_ts': '',
    'notification_events': [],
    'delivery_generation': 0,
}


class StaleOperation(RuntimeError):
    """A worker was delivered for an operation that is no longer pending."""


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(raw):
    """Parse an ISO timestamp to a tz-aware datetime, or None.

    Accepts the trailing 'Z' GitHub uses (Python <3.11 fromisoformat does
    not). A naive value is assumed UTC so it can be compared with local_now.
    """
    text = str(raw or '').strip()
    if not text:
        return None
    try:
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        dt = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_hhmm(text):
    s = str(text or '').strip()
    if ':' not in s:
        return None
    h, _, m = s.partition(':')
    try:
        h, m = int(h), int(m)
    except ValueError:
        return None
    if 0 <= h <= 23 and 0 <= m <= 59:
        return h * 60 + m
    return None


def _in_maintenance_window(window, now):
    """True when `now` (local, tz-aware) is inside window; None = always.

    A malformed window is fail-closed: treated as outside so an auto apply is
    never driven by an unparseable operator config.
    """
    if not isinstance(window, dict):
        return True
    start = _parse_hhmm(window.get('start'))
    end = _parse_hhmm(window.get('end'))
    if start is None or end is None:
        return False
    cur = now.hour * 60 + now.minute
    if start == end:
        return True
    if start < end:
        return start <= cur < end
    return cur >= start or cur < end


def normalize_policy(raw):
    """Type and clamp every policy field. Mirrors cost_calibrator.normalize_auto_policy."""
    src = raw if isinstance(raw, dict) else {}
    policy = dict(DEFAULT_POLICY)
    policy['enabled'] = bool(src.get('enabled')) is True
    channel = str(src.get('channel') or 'stable').strip().lower()
    policy['channel'] = channel if channel in ('stable', 'pre') else 'stable'
    try:
        age = int(src.get('min_release_age_hours', 24))
    except (TypeError, ValueError):
        age = 24
    policy['min_release_age_hours'] = max(0, min(age, 720))
    policy['skip_if_online_users'] = bool(src.get('skip_if_online_users', True))
    try:
        cooldown = int(src.get('cooldown_hours', 24))
    except (TypeError, ValueError):
        cooldown = 24
    policy['cooldown_hours'] = max(1, min(cooldown, 720))
    window = src.get('maintenance_window')
    policy['maintenance_window'] = window if isinstance(window, dict) else None
    return policy


def load_policy(path=POLICY_PATH):
    return normalize_policy(state_store.load_json(path, {}))


def save_policy(policy, path=POLICY_PATH):
    """Persist policy through normalize_policy + state_store.save_json."""
    state_store.save_json(path, normalize_policy(policy))


def _online_count(path=None, *, now=None):
    """Return the validated cached online count, or None when it is unknown."""
    path = ONLINE_FILE if path is None else path
    try:
        data = state_store.load_json_strict(path, None)
        metadata = state_store.load_json_strict(
            online_snapshot.metadata_path(path), None,
        )
        data = online_snapshot.validate_fresh_snapshot(
            data, metadata, now=now,
            ttl_seconds=ONLINE_SNAPSHOT_TTL_SECONDS,
        )
        values = []
        for value in data.values():
            if isinstance(value, bool):
                return None
            count = int(value)
            if count < 0:
                return None
            values.append(count)
        return sum(values)
    except Exception:
        return None


def should_auto_apply(policy, state, info, *, now, online_count, backup_result):
    """Pure policy gate for the auto (timer) path. Never writes state.

    Returns (allowed, reason). Order matches the documented gate list; the
    first blocking reason wins. published_at and stale_backup fail closed.
    """
    policy = normalize_policy(policy)
    if not policy.get('enabled'):
        return (False, 'policy_disabled')
    if not _in_maintenance_window(policy.get('maintenance_window'), now):
        return (False, 'outside_maintenance_window')
    min_age = float(policy.get('min_release_age_hours', 24))
    published = _parse_iso(info.get('published_at'))
    if published is None:
        return (False, 'release_too_young')
    if (now - published).total_seconds() / 3600 < min_age:
        return (False, 'release_too_young')
    if policy.get('skip_if_online_users'):
        if online_count is None:
            return (False, 'online_state_unknown')
        if int(online_count) > 0:
            return (False, 'users_online')
    cooldown_hours = float(policy.get('cooldown_hours', 24))
    last_ts = _parse_iso(state.get('cooldown_anchor'))
    if last_ts is not None:
        elapsed = (now - last_ts).total_seconds() / 3600
        if elapsed < cooldown_hours:
            return (False, 'cooldown_active')
    # last_ts missing/unparseable -> allow: a first run must not be locked out.
    if not (backup_result or {}).get('ok'):
        return (False, 'stale_backup')
    if not info.get('update_available'):
        return (False, 'no_update')
    return (True, 'ok')


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


def public_status(state=None):
    """Return the secret-free updater status exposed to the admin poller."""
    data = state if isinstance(state, dict) else load_state()
    status = str(data.get('status') or 'idle')
    return {
        'ok': True,
        'status': status,
        'reason': str(data.get('reason') or ''),
        'ts': str(data.get('ts') or ''),
        'pending': status in _PENDING_STATUSES,
    }


def schedule_apply_async(
    *, state_path=STATE_PATH, popen=subprocess.Popen, lock_timeout=1.0,
    operation_id=None,
):
    """Persist one operation, then deliver its detached worker outside lock.

    A timeout is ambiguous: systemd may already have accepted the unit. It
    therefore leaves the operation scheduled for deterministic redelivery.
    A definite failure is recorded only if the same generation is still
    scheduled; a worker that already advanced state always wins.
    """
    operation_mode = None
    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        state = load_state(state_path)
        if operation_id is None:
            if public_status(state)['pending'] or state.get('status') == 'rollback_failed':
                raise state_store.LockTimeout('hysteria update already pending')
            operation_id = _new_operation_id()
            operation_mode = 'manual'
        elif (
            state.get('status') != 'scheduled'
            or state.get('operation_id') != operation_id
        ):
            raise StaleOperation('scheduled operation no longer matches')
        generation = int(state.get('delivery_generation') or 0) + 1
        if operation_mode is not None:
            state.update({
                'version': '',
                'previous_version': '',
                'sha256': '',
            })
        state.update({
            'status': 'scheduled',
            'reason': '',
            'error': '',
            'ts': _now_iso(),
            'pending_confirm': False,
            'operation_id': operation_id,
            'operation_mode': operation_mode or str(
                state.get('operation_mode') or 'auto'
            ),
            'live_replaced': False,
            'rollback_operation_id': '',
            'delivery_generation': generation,
        })
        save_state(state, state_path)
    unit = f'hy2-hysteria-update-{operation_id[:16]}-{generation}'
    process = None
    exc = None
    try:
        process = popen(
            [
                'systemd-run', '--no-block', '--collect',
                '--unit', unit,
                '--property=Type=oneshot',
                '--property=NoNewPrivileges=yes',
                '--property=PrivateTmp=yes',
                '--property=ProtectSystem=strict',
                '--property=ReadWritePaths=/usr/local/bin /root/hysteria/state /run/hy2-locks',
                '--property=RuntimeDirectory=hy2-locks',
                '--property=RuntimeDirectoryMode=0700',
                '--property=RuntimeDirectoryPreserve=yes',
                '--property=ProtectKernelTunables=yes',
                '--property=ProtectKernelModules=yes',
                '--property=ProtectControlGroups=yes',
                '--property=RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6',
                '--property=RestrictRealtime=yes',
                '--property=RestrictSUIDSGID=yes',
                '--property=LockPersonality=yes',
                '--property=CapabilityBoundingSet=',
                '--property=SystemCallArchitectures=native',
                '--property=SuccessExitStatus=1',
                '--property=Restart=on-failure',
                '--property=RestartSec=5s',
                DEPLOY_LOCK_EXEC,
                '--lock-file', DEPLOY_LOCK_PATH, '--wait',
                '/usr/bin/python3', '-I', '-c', _ISOLATED_BOOTSTRAP,
                '--apply', operation_id,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        status = process.wait(timeout=SCHEDULE_TIMEOUT_SECONDS)
        if status != 0:
            exc = RuntimeError(f'systemd-run scheduling returned {status}')
    except subprocess.TimeoutExpired:
        try:
            with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
                current = load_state(state_path)
                if (
                    current.get('operation_id') == operation_id
                    and current.get('status') == 'scheduled'
                    and current.get('delivery_generation') == generation
                ):
                    current.update({
                        'reason': 'schedule_delivery_unknown',
                        'ts': _now_iso(),
                    })
                    save_state(current, state_path)
                return current
        except state_store.LockTimeout:
            # The delivered worker may already own the lock. Preserve the
            # durable scheduled answer; never report a contradictory failure.
            state['reason'] = 'schedule_delivery_unknown'
            return state
    except Exception as caught:
        exc = caught

    if exc is not None:
        try:
            with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
                current = load_state(state_path)
                if not (
                    current.get('operation_id') == operation_id
                    and current.get('status') == 'scheduled'
                    and current.get('delivery_generation') == generation
                ):
                    return current
                _terminal_update(
                    current, 'failed',
                    primary_error=f'schedule failed ({type(exc).__name__})',
                    reason='schedule_failed',
                )
                save_state(current, state_path)
        except state_store.LockTimeout:
            return state
        raise RuntimeError('hysteria update scheduling failed') from exc

    try:
        with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
            return load_state(state_path)
    except state_store.LockTimeout:
        # A successfully delivered worker may already hold the lock for the
        # full update transaction. The persisted scheduled state is still the
        # correct HTTP acknowledgement; polling observes subsequent stages.
        return state


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


def _sha256_hex(value):
    """Normalize a digest to bare lowercase hex, or '' when unusable.

    GitHub asset metadata carries digest values as 'sha256:<hex>'.
    """
    text = str(value or '').strip().lower()
    if text.startswith('sha256:'):
        text = text[len('sha256:'):]
    return text if re.fullmatch(r'[0-9a-f]{64}', text) else ''


def _parse_checksums(text, asset_name=ASSET_NAME):
    """Pull the sha256 for asset_name out of a checksum listing.

    Accepts sha256sum output ('<hex>  build/<name>'), the reversed order,
    and BSD 'SHA256 (<name>) = <hex>'. A bare hash with no file name is
    only trusted when the listing is a single line: hashes.txt covers
    every platform, so a nameless match could return another binary's
    hash and turn a real mismatch into a silent pass.
    """
    lines = [line for line in str(text or '').splitlines() if line.strip()]
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        named = any(
            os.path.basename(part.strip(_CHECKSUM_NAME_TRIM)) == asset_name
            for part in parts
        )
        if not named:
            continue
        for candidate in (parts[0], parts[-1]):
            digest = _sha256_hex(candidate)
            if digest:
                return digest
    if len(lines) == 1 and len(lines[0].split()) == 1:
        return _sha256_hex(lines[0].strip())
    return ''


def _release_version(tag):
    """Normalize a release tag for comparison.

    Upstream tags are namespaced ('app/v2.12.2'). Without dropping that
    prefix the numeric tuple parse fails, and an unparseable candidate
    used to be reported as an available update forever.
    """
    text = str(tag or '').strip()
    if '/' in text:
        text = text.rsplit('/', 1)[-1].strip()
    return health._normalize_hysteria_version(text)


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
        # An unreadable local version stays unknown. The comparison below
        # then refuses to claim an update rather than guessing.
        current = ''
    release = _http_json(RELEASES_URL, opener=opener)
    tag = str(release.get('tag_name') or '').strip()
    assets = {}
    for item in release.get('assets') or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get('name') or '')
        if not name:
            continue
        assets[name] = {
            'url': str(item.get('browser_download_url') or ''),
            'sha256': _sha256_hex(item.get('digest')),
        }
    asset = assets.get(ASSET_NAME) or {}
    checksums = assets.get(CHECKSUMS_NAME) or {}
    info = {
        'ok': True,
        'current': current,
        'latest': tag,
        'published_at': str(release.get('published_at') or ''),
        'asset_url': asset.get('url') or '',
        # Per-asset digest published by GitHub: primary source of truth,
        # and it removes one network round trip.
        'asset_sha256': asset.get('sha256') or '',
        'checksum_url': checksums.get('url') or '',
        'update_available': False,
    }
    cand = _release_version(tag)
    curr = health._normalize_hysteria_version(current)
    if cand and curr and cand != curr:
        cand_t = health._hysteria_version_tuple(cand)
        curr_t = health._hysteria_version_tuple(curr)
        # Unknown ordering is not an upgrade signal. The old
        # 'cand_t is None or curr_t is None' branch made update_available
        # permanently true for namespaced tags.
        if cand_t is not None and curr_t is not None and cand_t > curr_t:
            info['update_available'] = True
    return info


def record_check(info, *, path=STATE_PATH):
    state = load_state(path)
    if public_status(state)['pending'] or state.get('status') == 'rollback_failed':
        return state
    state['status'] = 'checked'
    state['version'] = info.get('latest') or ''
    state['previous_version'] = info.get('current') or ''
    state['ts'] = _now_iso()
    state['error'] = ''
    state['reason'] = ''
    state['pending_confirm'] = False
    save_state(state, path)
    return state


def check_and_record(
    *, opener=None, runner=subprocess.run, binary_path=BINARY_PATH,
    state_path=STATE_PATH, lock_timeout=5,
):
    """Check the latest release and record it under the updater lock."""
    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        state = load_state(state_path)
        if public_status(state)['pending'] or state.get('status') == 'rollback_failed':
            raise state_store.LockTimeout('hysteria update operation is active')
        info = check_latest(
            opener=opener,
            runner=runner,
            binary_path=binary_path,
        )
        record_check(info, path=state_path)
        return info


def _ready(runner, connection_factory=None):
    """Require stable systemd and /readyz health before accepting a binary."""
    deadline = time.monotonic() + READINESS_TIMEOUT_SECONDS
    consecutive = 0
    last_label = 'not ready'
    while True:
        service = health.probe_systemd(UNIT, runner=runner)
        auth_kwargs = {'path': '/readyz'}
        if connection_factory is not None:
            auth_kwargs['connection_factory'] = connection_factory
        auth = health.probe_auth_readiness(**auth_kwargs)
        if service.get('ok') and auth.get('ok'):
            consecutive += 1
            if consecutive >= READINESS_STABILITY_PROBES:
                return {
                    'ok': True,
                    'label': f'{consecutive} consecutive probes passed',
                }
        else:
            consecutive = 0
            last_label = (
                f"systemd={service.get('label', 'unknown')}; "
                f"readyz={auth.get('label', 'unknown')}"
            )
        if time.monotonic() >= deadline:
            return {'ok': False, 'label': last_label}
        time.sleep(READINESS_DELAY_SECONDS)


def _restart(runner):
    result = runner(
        ['systemctl', 'restart', UNIT],
        capture_output=True, text=True, timeout=30,
    )
    return getattr(result, 'returncode', 1) == 0


def _fsync_directory(path):
    flags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_install(src, dest, *, mode=None, uid=None, gid=None):
    """Copy src onto dest via a same-directory temp file and os.replace.

    Direct overwrite of the live binary can leave a truncated file if the
    process is killed mid-copy. replace() is atomic on the same filesystem.
    """
    dest = os.fspath(dest)
    source_stat = os.stat(src)
    if mode is None:
        mode = source_stat.st_mode & 0o7777
    if uid is None:
        uid = source_stat.st_uid
    if gid is None:
        gid = source_stat.st_gid
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
            os.fchmod(out.fileno(), mode)
            temp_stat = os.fstat(out.fileno())
            if (temp_stat.st_uid, temp_stat.st_gid) != (uid, gid):
                os.fchown(out.fileno(), uid, gid)
            # chmod/chown are metadata changes separate from the copied data.
            os.fsync(out.fileno())
        os.replace(tmp_name, dest)
        tmp_name = None
        _fsync_directory(parent)
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass


def _restore_last_good(
    *, runner, binary_path, last_good_path, ready_probe,
):
    """Restore last-good and prove the restarted service is healthy."""
    _atomic_install(last_good_path, binary_path)
    if not _restart(runner):
        raise RuntimeError('rollback restart failed')
    ready = ready_probe()
    if not (ready or {}).get('ok'):
        label = str((ready or {}).get('label') or 'unknown')
        raise RuntimeError(f'rollback readiness failed: {label}')


def _new_operation_id():
    return uuid.uuid4().hex


def _live_matches_candidate(state, binary_path):
    expected = str((state or {}).get('sha256') or '')
    if not expected or not os.path.exists(binary_path):
        return False
    try:
        return _sha256_file(binary_path) == expected
    except OSError:
        return False


def _rollback_context_owned(state, *, binary_path):
    operation_id = str((state or {}).get('operation_id') or '')
    owns_last_good = (
        operation_id
        and str((state or {}).get('rollback_operation_id') or '')
        == operation_id
    )
    replaced = bool((state or {}).get('live_replaced')) or _live_matches_candidate(
        state, binary_path,
    )
    return bool(owns_last_good and replaced)


def _rollback_eligible(state, *, binary_path, last_good_path):
    """True only for this operation after the candidate became live.

    The checksum comparison closes the crash window between atomic replace
    and persisting ``live_replaced``: reconcile can prove which binary is
    live instead of treating a historical last-good file as authority.
    """
    return bool(
        _rollback_context_owned(state, binary_path=binary_path)
        and os.path.exists(last_good_path)
    )


def _terminal_update(state, status, *, primary_error='', rollback_error='',
                     reason=''):
    combined = primary_error
    if rollback_error:
        combined = (
            f'{primary_error}; rollback failed: {rollback_error}'
            if primary_error else f'rollback failed: {rollback_error}'
        )
    state.update({
        'status': status,
        'error': combined,
        'primary_error': primary_error,
        'rollback_error': rollback_error,
        'reason': reason,
        'pending_confirm': False,
        'ts': _now_iso(),
    })
    if state.get('live_replaced') and status in (
        'done', 'rolled_back', 'rollback_failed',
    ):
        state['cooldown_anchor'] = state['ts']
    _queue_terminal_notification(state)
    return state


def _queue_terminal_notification(state):
    status = str((state or {}).get('status') or '')
    if status not in ('done', 'failed', 'rolled_back', 'rollback_failed'):
        return state
    operation_id = str((state or {}).get('operation_id') or '')
    if not operation_id:
        material = '|'.join((
            status,
            str((state or {}).get('version') or ''),
            str((state or {}).get('previous_version') or ''),
            str((state or {}).get('ts') or ''),
        ))
        operation_id = hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]
    event_id = f'hysteria-update:{operation_id}:{status}'
    events = [
        dict(item) for item in (state.get('notification_events') or [])
        if isinstance(item, dict) and item.get('event_id')
    ]
    if (
        not events
        and state.get('notification_event_id') == event_id
        and state.get('notification_status')
    ):
        events.append({
            'event_id': event_id,
            'terminal_status': status,
            'version': str((state or {}).get('version') or ''),
            'previous_version': str(
                (state or {}).get('previous_version') or ''
            ),
            'dispatch_status': str(state.get('notification_status')),
            'attempts': int(state.get('notification_attempts') or 0),
            'ts': str(state.get('notification_ts') or _now_iso()),
        })
    if not any(item.get('event_id') == event_id for item in events):
        events.append({
            'event_id': event_id,
            'terminal_status': status,
            'version': str((state or {}).get('version') or ''),
            'previous_version': str(
                (state or {}).get('previous_version') or ''
            ),
            'dispatch_status': 'pending',
            'attempts': 0,
            'ts': _now_iso(),
        })
        # Bound state growth while retaining every undelivered event.
        delivered = [e for e in events if e.get('dispatch_status') == 'sent'][-8:]
        pending = [e for e in events if e.get('dispatch_status') != 'sent']
        events = delivered + pending
    state['notification_events'] = events
    current = next(e for e in events if e.get('event_id') == event_id)
    if state.get('notification_event_id') != event_id:
        state.update({
            'notification_event_id': event_id,
            'notification_status': current.get('dispatch_status') or 'pending',
            'notification_attempts': int(current.get('attempts') or 0),
            'notification_ts': current.get('ts') or _now_iso(),
        })
    return state


def apply_update(
    *,
    opener=None,
    runner=subprocess.run,
    binary_path=BINARY_PATH,
    last_good_path=LAST_GOOD_PATH,
    state_path=STATE_PATH,
    connection_factory=None,
    ready_probe=None,
    policy=None,
    force=False,
    online_count=None,
    backup_result=None,
    now=None,
    lock_timeout=5,
    operation_id=None,
):
    """Download, verify, atomically replace, restart, and roll back on failure.

    force=False (the auto/timer path) runs should_auto_apply first and writes
    status='skipped'+reason when a gate blocks — never 'failed'. force=True
    (an admin clicking "update now") bypasses the auto-ness gates but NOT the
    safety gates: checksum, sanity, fresh backup, readyz.
    """
    policy = normalize_policy(policy) if policy is not None else load_policy()
    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        state = load_state(state_path)
        scheduled_worker = operation_id is not None
        if operation_id is not None and (
            state.get('status') != 'scheduled'
            or state.get('operation_id') != operation_id
        ):
            raise StaleOperation('worker operation no longer scheduled')
        if operation_id is None and public_status(state)['pending']:
            raise state_store.LockTimeout('hysteria update operation is active')
        if state.get('status') == 'rollback_failed':
            # A failed rollback is a durable safety barrier. Recovery belongs
            # to reconcile; a fresh check/apply must not erase its evidence.
            return state
        if scheduled_worker:
            # A legacy scheduled operation has no durable proof that an admin
            # authorized policy bypass. Fail closed to automatic semantics.
            operation_mode = str(state.get('operation_mode') or 'auto')
            force = operation_mode == 'manual'
        else:
            operation_mode = 'manual' if force else 'auto'
        operation_id = operation_id or _new_operation_id()
        state.update({
            'operation_id': operation_id,
            'operation_mode': operation_mode,
            'live_replaced': False,
            'rollback_operation_id': '',
            'primary_error': '',
            'rollback_error': '',
            'version': '',
            'previous_version': '',
            'sha256': '',
        })
        if not scheduled_worker:
            state.update({
                'status': 'scheduled',
                'reason': '',
                'error': '',
                'pending_confirm': False,
                'ts': _now_iso(),
            })
            save_state(state, state_path)
        try:
            info = check_latest(
                opener=opener, runner=runner, binary_path=binary_path,
            )
        except Exception as exc:
            _terminal_update(
                state, 'failed', primary_error=f'check failed: {exc}',
            )
            save_state(state, state_path)
            return state
        if not force:
            decision_now = now or timeutil.local_now()
            decision_online = (
                online_count if online_count is not None else _online_count())
            decision_backup = (
                backup_result if backup_result is not None
                else health.probe_recent_backup(
                    BACKUP_DIR, disk_usage=shutil.disk_usage))
            allowed, reason = should_auto_apply(
                policy, state, info,
                now=decision_now, online_count=decision_online,
                backup_result=decision_backup)
            if not allowed:
                state.update({
                    'status': 'skipped', 'reason': reason, 'error': '',
                    'version': info.get('latest') or info.get('current') or '',
                    'previous_version': info.get('current') or '',
                    'ts': _now_iso(), 'pending_confirm': False,
                })
                save_state(state, state_path)
                return state
            # Reuse the freshly probed backup for the hard gate below.
            backup_result = decision_backup
        if not info.get('update_available'):
            state['version'] = info.get('current') or ''
            _terminal_update(state, 'done')
            save_state(state, state_path)
            return state
        # Fresh-backup hard gate. force does NOT bypass this: a binary replace
        # is only allowed when a recent backup exists to roll back to.
        if backup_result is None:
            backup_result = health.probe_recent_backup(
                BACKUP_DIR, disk_usage=shutil.disk_usage)
        if not (backup_result or {}).get('ok'):
            state.update({
                'status': 'skipped', 'reason': 'stale_backup', 'error': '',
                'ts': _now_iso(), 'pending_confirm': False,
            })
            save_state(state, state_path)
            return state
        if not info.get('asset_url'):
            _terminal_update(
                state, 'failed', primary_error='release asset missing',
            )
            save_state(state, state_path)
            return state
        if not info.get('asset_sha256') and not info.get('checksum_url'):
            _terminal_update(
                state, 'failed',
                primary_error='release checksum unavailable',
            )
            save_state(state, state_path)
            return state

        work = tempfile.mkdtemp(prefix='hy-upd.')
        try:
            asset = os.path.join(work, ASSET_NAME)
            state.update({
                'status': 'downloading', 'version': info['latest'],
                'previous_version': info.get('current') or '',
                'pending_confirm': True, 'ts': _now_iso(), 'error': '',
                'reason': '',
                'operation_id': operation_id,
                'live_replaced': False,
                'rollback_operation_id': '',
            })
            save_state(state, state_path)
            _download(info['asset_url'], asset, opener=opener)
            expected = info.get('asset_sha256') or ''
            if not expected:
                sums = os.path.join(work, CHECKSUMS_NAME)
                _download(info['checksum_url'], sums, opener=opener)
                with open(sums, encoding='utf-8') as handle:
                    expected = _parse_checksums(handle.read())
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
                source_stat = os.stat(binary_path)
                _atomic_install(
                    binary_path, last_good_path,
                    mode=source_stat.st_mode & 0o7777,
                )
                state['rollback_operation_id'] = operation_id
            state.update({
                'status': 'applying', 'sha256': actual, 'ts': _now_iso(),
                'pending_confirm': True, 'reason': '', 'live_replaced': False,
            })
            save_state(state, state_path)
            # `work` may live in systemd's PrivateTmp mount. Copy into a
            # sibling temp file beside the live binary, fsync it, then rename
            # within the destination filesystem so replace cannot raise EXDEV.
            _atomic_install(asset, binary_path, mode=0o755)
            state.update({
                'status': 'verifying',
                'live_replaced': True,
                'pending_confirm': True,
                'ts': _now_iso(),
            })
            save_state(state, state_path)
            if not _restart(runner):
                raise RuntimeError('restart failed')
            probe = ready_probe or (
                lambda: _ready(runner, connection_factory)
            )
            ready = probe()
            if not ready.get('ok'):
                raise RuntimeError('readyz failed after apply')
            _terminal_update(state, 'done')
            save_state(state, state_path)
            return state
        except Exception as exc:
            rolled = False
            rollback_error = ''
            owns_context = _rollback_context_owned(
                state, binary_path=binary_path,
            )
            if _rollback_eligible(
                state,
                binary_path=binary_path,
                last_good_path=last_good_path,
            ):
                try:
                    probe = ready_probe or (
                        lambda: _ready(runner, connection_factory)
                    )
                    _restore_last_good(
                        runner=runner,
                        binary_path=binary_path,
                        last_good_path=last_good_path,
                        ready_probe=probe,
                    )
                    rolled = True
                except Exception as rollback_exc:
                    rollback_error = str(rollback_exc)
            elif owns_context:
                rollback_error = 'no last-good for operation'
            _terminal_update(
                state,
                'rolled_back' if rolled else (
                    'rollback_failed' if rollback_error else 'failed'
                ),
                primary_error=str(exc), rollback_error=rollback_error,
            )
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
              ready_probe=None, connection_factory=None, lock_timeout=5):
    """Converge an interrupted apply without assuming success.

    Any unconfirmed apply (pending_confirm plus downloading/applying/
    verifying) is unsafe. If last-good exists, atomically restore it,
    restart, re-check readiness, and persist rolled_back. If last-good is
    missing, leave the live binary untouched and persist rollback_failed.
    A transient ``_reconciled`` marker is added to the returned mapping only
    when this call performed recovery; it is deliberately added after the
    durable save so callers can distinguish a new recovery from old state.
    """
    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        state = load_state(state_path)
        recovery_barrier = state.get('status') == 'rollback_failed'
        if not recovery_barrier and not _interrupted_apply(state):
            return state
        owns_context = _rollback_context_owned(
            state, binary_path=binary_path,
        )
        eligible = _rollback_eligible(
            state,
            binary_path=binary_path,
            last_good_path=last_good_path,
        )
        if not eligible:
            if recovery_barrier:
                # Preserve both error classes and the barrier until an
                # operator repairs the operation-scoped recovery context.
                state['_reconciled'] = True
                return state
            if owns_context:
                primary = str(state.get('primary_error') or 'interrupted apply')
                _terminal_update(
                    state, 'rollback_failed', primary_error=primary,
                    rollback_error='no last-good for operation',
                )
                save_state(state, state_path)
                state['_reconciled'] = True
                return state
            _terminal_update(
                state, 'failed',
                primary_error='interrupted before live binary replacement',
                reason='interrupted_before_install',
            )
            save_state(state, state_path)
            state['_reconciled'] = True
            return state
        probe = ready_probe or (
            lambda: _ready(runner, connection_factory)
        )
        if eligible:
            try:
                _restore_last_good(
                    runner=runner,
                    binary_path=binary_path,
                    last_good_path=last_good_path,
                    ready_probe=probe,
                )
            except Exception as exc:
                primary = str(state.get('primary_error') or '')
                if not primary:
                    primary = 'interrupted apply'
                _terminal_update(
                    state, 'rollback_failed',
                    primary_error=primary,
                    rollback_error=str(exc),
                )
                save_state(state, state_path)
                state['_reconciled'] = True
                return state
            primary = str(state.get('primary_error') or 'interrupted apply')
            _terminal_update(
                state, 'rolled_back', primary_error=primary,
                reason='interrupted_apply_reconciled',
            )
            save_state(state, state_path)
            state['_reconciled'] = True
            return state


def render_history(state=None):
    data = state or load_state()
    esc = html.escape
    st = esc(str(data.get('status') or 'idle'))
    ver = esc(str(data.get('version') or '—'))
    prev = esc(str(data.get('previous_version') or '—'))
    ts = esc(str(data.get('ts') or '—'))
    err = esc(str(data.get('error') or ''))
    reason = esc(str(data.get('reason') or ''))
    # A policy skip surfaces its reason as faint text — never as a red
    # failure note, so daily "no update yet" does not train operators to
    # ignore the card.
    note_row = ''
    if err:
        note_row = f'<div class="small">{err}</div>'
    elif st == 'skipped' and reason:
        note_row = f'<div class="small faint">{reason}</div>'
    return (
        '<div class="card hysteria-update-history">'
        '<h2 class="section-title mb-sm">Hysteria 更新</h2>'
        f'<div class="small">状态：{st} · 目标 {ver} · 之前 {prev}</div>'
        f'<div class="small faint">{ts}</div>'
        f'{note_row}'
        '<form method="post" action="/admin/hysteria-update/check" class="inline-form-row mt-sm"'
        ' data-action="hysteria-update-check">'
        '<button class="btn ghost btn-sm" type="submit">检查更新</button></form>'
        '<form method="post" action="/admin/hysteria-update/apply" class="inline-form-row mt-sm"'
        ' data-action="hysteria-update-apply"'
        ' data-confirm="将下载官方二进制、校验哈希并重启 Hysteria。失败会自动回滚。确认继续？">'
        '<button class="btn btn-sm" type="submit">立即更新</button></form>'
        '</div>'
    )


def _status_to_exit(status, reason=''):
    """Map an apply/reconcile status to a CLI exit code.

    0 = done or no action (no_update is "nothing to apply", not a skip);
    1 = blocked by a policy gate (the unit file marks 1 as success via
    SuccessExitStatus=1 so daily skips do not read as failures); 2 = real
    failure (failed/rolled_back).
    """
    if status == 'done':
        return 0
    if status == 'skipped':
        return 0 if reason == 'no_update' else 1
    return 2


def _print_lock_busy():
    print('hysteria-update: update lock busy; deferring to next tick')


def _dispatch_pending_alert(
    *, state_path=STATE_PATH, dispatch=None, lock_timeout=5,
):
    """Dispatch one durable terminal event, with retry after transport failure.

    This provides durable deduplication after success and stable-event retries.
    It cannot promise mathematical exactly-once delivery because the alert
    transports do not support an idempotency transaction with this state file.
    """
    send = dispatch or alerts.dispatch
    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        state = load_state(state_path)
        _queue_terminal_notification(state)
        events = [dict(item) for item in (state.get('notification_events') or [])]
        target = next(
            (item for item in events if item.get('dispatch_status') != 'sent'),
            None,
        )
        if target is None:
            return False
        event_id = str(target.get('event_id') or '')
        if target.get('dispatch_status') == 'sending':
            claimed = _parse_iso(target.get('ts'))
            if claimed is not None:
                age = datetime.now(timezone.utc) - claimed.astimezone(timezone.utc)
                if age.total_seconds() < 60:
                    return False
        target['dispatch_status'] = 'sending'
        target['attempts'] = int(target.get('attempts') or 0) + 1
        target['ts'] = _now_iso()
        state['notification_events'] = events
        state.update({
            'notification_event_id': event_id,
            'notification_status': 'sending',
            'notification_attempts': target['attempts'],
            'notification_ts': target['ts'],
        })
        save_state(state, state_path)
        event = {
            'kind': 'hysteria_update',
            'user': 'system',
            'details': {
                'event_id': event_id,
                'status': str(target.get('terminal_status') or ''),
                'version': str(target.get('version') or ''),
                'previous_version': str(
                    target.get('previous_version') or ''
                ),
            },
        }

    failed = True
    try:
        result = send(event)
        if isinstance(result, dict):
            failed = (
                bool(result.get('failed'))
                or not bool(result.get('attempted'))
            )
        else:
            failed = False
    except Exception:
        failed = True

    with state_store.file_lock(LOCK_PATH, timeout=lock_timeout):
        current = load_state(state_path)
        events = [
            dict(item) for item in (current.get('notification_events') or [])
        ]
        target = next(
            (item for item in events if item.get('event_id') == event_id),
            None,
        )
        if target is None:
            return not failed
        target['dispatch_status'] = 'retry' if failed else 'sent'
        target['ts'] = _now_iso()
        current['notification_events'] = events
        current.update({
            'notification_status': 'retry' if failed else 'sent',
            'notification_event_id': event_id,
            'notification_attempts': int(target.get('attempts') or 0),
            'notification_ts': target['ts'],
        })
        save_state(current, state_path)
    return not failed


def _dispatch_terminal_alert(_state=None):
    """Compatibility wrapper; the durable state is the dispatch authority."""
    return _dispatch_pending_alert()


def _notification_retry_due(state):
    events = state.get('notification_events') or []
    if any(
        isinstance(item, dict) and item.get('dispatch_status') != 'sent'
        for item in events
    ):
        return True
    return (
        str(state.get('status') or '')
        in ('done', 'failed', 'rolled_back', 'rollback_failed')
        and state.get('notification_status') != 'sent'
    )


def _main(argv=None):
    """CLI entry for the systemd timer / operator shell.

    --check      probe + record_check, never writes a binary
    --auto       reconcile, then policy-gated apply (force=False)
    --apply      reconcile, then admin apply (force=True) through safety gates
    --reconcile  converge an interrupted apply
    Exit: 0 ok/no-op/lock-busy, 1 policy-skip, 2 real failure.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] not in ('--check', '--apply', '--auto', '--reconcile'):
        print('usage: hysteria_update.py --check|--apply|--auto|--reconcile')
        return 2
    mode = args[0]
    try:
        try:
            if _notification_retry_due(load_state()):
                _dispatch_pending_alert(lock_timeout=CLI_LOCK_TIMEOUT)
        except (state_store.LockTimeout, OSError, ValueError):
            # Alert retry never weakens updater safety or changes its exit.
            pass
        if mode == '--reconcile':
            state = reconcile(lock_timeout=CLI_LOCK_TIMEOUT)
            st = str(state.get('status') or '')
            _dispatch_terminal_alert(state)
            print(f'hysteria-update: reconcile -> {st}')
            return 2 if st in ('failed', 'rolled_back', 'rollback_failed') else 0
        if mode == '--check':
            try:
                info = check_and_record(lock_timeout=CLI_LOCK_TIMEOUT)
            except state_store.LockTimeout:
                _print_lock_busy()
                return 0
            print(
                'hysteria-update: current=%s latest=%s update_available=%s'
                % (
                    info.get('current') or 'unknown',
                    info.get('latest') or 'unknown',
                    info.get('update_available'),
                )
            )
            return 0
        worker_operation_id = (
            args[1] if mode == '--apply' and len(args) > 1 else None
        )
        if worker_operation_id:
            try:
                state = apply_update(
                    force=True,
                    operation_id=worker_operation_id,
                    lock_timeout=WORKER_LOCK_TIMEOUT,
                )
            except StaleOperation:
                print('hysteria-update: stale worker; safe skip')
                return 1
            except state_store.LockTimeout:
                print('hysteria-update: worker lock wait exhausted')
                return 2
            st = str(state.get('status') or '')
            _dispatch_terminal_alert(state)
            print(f'hysteria-update: apply -> {st} {state.get("reason") or ""}'.rstrip())
            return _status_to_exit(st, state.get('reason'))
        # --apply and --auto both reconcile a possibly-interrupted apply first.
        try:
            reconciled = reconcile(lock_timeout=CLI_LOCK_TIMEOUT)
        except state_store.LockTimeout:
            _print_lock_busy()
            return 0
        reconciled_status = str(reconciled.get('status') or '')
        if reconciled.get('_reconciled'):
            _dispatch_terminal_alert(reconciled)
            print(f'hysteria-update: reconcile -> {reconciled_status}')
            return 2
        if mode == '--auto' and reconciled_status == 'scheduled':
            try:
                delivered = schedule_apply_async(
                    operation_id=str(reconciled.get('operation_id') or ''),
                    lock_timeout=CLI_LOCK_TIMEOUT,
                )
            except (state_store.LockTimeout, StaleOperation):
                _print_lock_busy()
                return 0
            except Exception:
                print('hysteria-update: scheduled redelivery failed')
                return 2
            print(
                'hysteria-update: scheduled redelivery -> %s'
                % (delivered.get('status') or 'unknown')
            )
            return 0
        try:
            if mode == '--apply':
                state = apply_update(force=True, lock_timeout=CLI_LOCK_TIMEOUT)
            else:
                state = apply_update(force=False, lock_timeout=CLI_LOCK_TIMEOUT)
        except state_store.LockTimeout:
            _print_lock_busy()
            return 0
        st = str(state.get('status') or '')
        _dispatch_terminal_alert(state)
        # `error` can originate in a URL-bearing transport exception. Keep
        # journald output to controlled reason codes and the terminal status.
        detail = state.get('reason') or ''
        print(f'hysteria-update: {mode[2:]} -> {st} {detail}'.rstrip())
        return _status_to_exit(st, state.get('reason'))
    except Exception:
        # Never leak credential-bearing URLs or tokens; the type is enough
        # for a journal breadcrumb. check_latest/_download carry no auth.
        print('hysteria-update: failed (%s)' % type(sys.exc_info()[1]).__name__)
        return 2


if __name__ == '__main__':
    raise SystemExit(_main())
