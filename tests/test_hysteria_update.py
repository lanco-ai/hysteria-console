"""Hysteria official-binary check / apply / rollback."""

import hashlib
import errno
import json
import os
import stat
from pathlib import Path

import pytest

import hysteria_update as hu


class _Resp:
    def __init__(self, payload):
        self._payload = payload if isinstance(payload, bytes) else payload.encode()

    def read(self, n=-1):
        if n < 0:
            data, self._payload = self._payload, b''
            return data
        data, self._payload = self._payload[:n], self._payload[n:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_policy_defaults_disabled(tmp_path):
    path = tmp_path / 'policy.json'
    policy = hu.load_policy(path)
    assert policy['enabled'] is False
    path.write_text(json.dumps({'enabled': True, 'channel': 'nope'}), encoding='utf-8')
    policy = hu.load_policy(path)
    assert policy['enabled'] is True
    assert policy['channel'] == 'stable'


def test_check_latest_detects_newer(monkeypatch):
    release = {
        'tag_name': 'v2.10.0',
        'assets': [
            {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'},
            {'name': hu.CHECKSUMS_NAME, 'browser_download_url': 'https://x/sum'},
        ],
    }

    def opener(req, timeout=0):
        return _Resp(json.dumps(release))

    def runner(cmd, **_k):
        if cmd[:2] == ['/usr/local/bin/hysteria', 'version']:
            return type('R', (), {'stdout': 'Version:\tv2.9.3\n', 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    info = hu.check_latest(opener=opener, runner=runner)
    assert info['update_available'] is True
    assert info['latest'] == 'v2.10.0'


def test_apply_rejects_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    last_good = tmp_path / 'last-good'
    state_path = tmp_path / 'state.json'
    payload = b'new-bin-contents'
    release = {
        'tag_name': 'v9.9.9',
        'assets': [
            {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'},
            {'name': hu.CHECKSUMS_NAME, 'browser_download_url': 'https://x/sum'},
        ],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        if 'api.github.com' in url:
            return _Resp(json.dumps(release))
        if url.endswith('/bin'):
            return _Resp(payload)
        return _Resp('deadbeef' + '  ' + hu.ASSET_NAME + '\n')

    def runner(cmd, **_k):
        if cmd[:2] == ['/usr/local/bin/hysteria', 'version'] or (
            isinstance(cmd, list) and len(cmd) == 2 and cmd[1] == 'version'
        ):
            return type('R', (), {'stdout': 'Version:\tv2.0.0\n', 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': 'active', 'stderr': '', 'returncode': 0})()

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )
    assert result['status'] in ('failed', 'rolled_back')
    assert 'checksum' in result['error']
    assert binary.read_bytes() == b'old-bin'


def test_preinstall_checksum_failure_never_restores_historical_last_good(
    tmp_path, monkeypatch,
):
    """A stale last-good must not replace a healthy live binary before install."""
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'healthy-current')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'obsolete-history')
    state_path = tmp_path / 'state.json'
    release = {
        'tag_name': 'v9.9.9',
        'assets': [
            {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'},
            {'name': hu.CHECKSUMS_NAME, 'browser_download_url': 'https://x/sum'},
        ],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        if 'api.github.com' in url:
            return _Resp(json.dumps(release))
        if url.endswith('/bin'):
            return _Resp(b'bad-download')
        return _Resp('0' * 64 + '  ' + hu.ASSET_NAME + '\n')

    def runner(cmd, **_kwargs):
        return type('R', (), {
            'stdout': 'Version:\tv2.0.0\n', 'stderr': '', 'returncode': 0,
        })()

    result = hu.apply_update(
        opener=opener, runner=runner, binary_path=str(binary),
        last_good_path=str(last_good), state_path=str(state_path),
        ready_probe=lambda: {'ok': True}, force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )

    assert result['status'] == 'failed'
    assert result['live_replaced'] is False
    assert binary.read_bytes() == b'healthy-current'


def test_apply_rolls_back_when_readyz_fails(tmp_path):
    monkeypatch_lock = tmp_path / 'lock'
    hu.LOCK_PATH = str(monkeypatch_lock)
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    last_good = tmp_path / 'last-good'
    state_path = tmp_path / 'state.json'
    payload = b'#!/bin/sh\necho Version: v9.9.9\n'
    digest = _sha(payload)
    release = {
        'tag_name': 'v9.9.9',
        'assets': [
            {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'},
            {'name': hu.CHECKSUMS_NAME, 'browser_download_url': 'https://x/sum'},
        ],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        if 'api.github.com' in url:
            return _Resp(json.dumps(release))
        if url.endswith('/bin'):
            return _Resp(payload)
        return _Resp(digest + '  ' + hu.ASSET_NAME + '\n')

    def runner(cmd, **_k):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            # Running binary is old; downloaded temp binary is new.
            text = 'Version:\tv9.9.9\n' if str(cmd[0]).endswith(hu.ASSET_NAME) or 'hy-upd' in str(cmd[0]) else 'Version:\tv2.0.0\n'
            if not str(cmd[0]).endswith('hysteria') and 'hy-upd' not in str(cmd[0]) and hu.ASSET_NAME not in str(cmd[0]):
                text = 'Version:\tv2.0.0\n'
            if str(cmd[0]) == str(binary):
                text = 'Version:\tv2.0.0\n'
            elif str(cmd[0]) == '/usr/local/bin/hysteria':
                text = 'Version:\tv2.0.0\n'
            else:
                text = 'Version:\tv9.9.9\n'
            return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    readiness = iter((
        {'ok': False, 'label': '未就绪'},
        {'ok': True, 'label': '回滚后已就绪'},
    ))
    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: next(readiness),
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )
    assert result['status'] == 'rolled_back'
    assert binary.read_bytes() == b'old-bin'
    assert last_good.exists()


def test_apply_records_rollback_failed_when_rollback_readiness_fails(
    tmp_path,
):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    payload = b'#!/bin/sh\necho Version: v9.9.9\n'
    digest = _sha(payload)
    release = {
        'tag_name': 'v9.9.9',
        'assets': [{
            'name': hu.ASSET_NAME,
            'browser_download_url': 'https://x/bin',
            'digest': 'sha256:' + digest,
        }],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        return _Resp(json.dumps(release) if 'api.github.com' in url else payload)

    def runner(cmd, **_kwargs):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            text = 'Version:\tv2.0.0\n' if str(cmd[0]) == str(binary) else 'Version:\tv9.9.9\n'
            return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(tmp_path / 'state.json'),
        ready_probe=lambda: {'ok': False, 'label': 'still down'},
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )

    assert result['status'] == 'rollback_failed'
    assert 'readyz failed after apply' in result['error']
    assert 'rollback readiness failed' in result['error']


def test_apply_records_rollback_failed_when_rollback_restart_fails(
    tmp_path,
):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    payload = b'#!/bin/sh\necho Version: v9.9.9\n'
    digest = _sha(payload)
    release = {
        'tag_name': 'v9.9.9',
        'assets': [{
            'name': hu.ASSET_NAME,
            'browser_download_url': 'https://x/bin',
            'digest': 'sha256:' + digest,
        }],
    }
    restart_results = iter((0, 1))

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        return _Resp(json.dumps(release) if 'api.github.com' in url else payload)

    def runner(cmd, **_kwargs):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            text = 'Version:\tv2.0.0\n' if str(cmd[0]) == str(binary) else 'Version:\tv9.9.9\n'
            return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
        if cmd[:2] == ['systemctl', 'restart']:
            return type('R', (), {
                'stdout': '', 'stderr': '',
                'returncode': next(restart_results),
            })()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(tmp_path / 'state.json'),
        ready_probe=lambda: {'ok': False, 'label': 'apply failed'},
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )

    assert result['status'] == 'rollback_failed'
    assert 'rollback restart failed' in result['error']


def test_reconcile_restores_last_good(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'broken')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'applying',
        'pending_confirm': True,
        'operation_id': 'op-current',
        'live_replaced': True,
        'rollback_operation_id': 'op-current',
        'version': 'v9',
        'previous_version': 'v1',
        'sha256': '',
        'ts': '',
        'error': '',
    }, state_path)
    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
    )
    assert state['status'] == 'rolled_back'
    assert binary.read_bytes() == b'good'


def test_apply_rejects_sanity_failure_without_replacing_live(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    last_good = tmp_path / 'last-good'
    state_path = tmp_path / 'state.json'
    payload = b'not-a-binary'
    digest = _sha(payload)
    release = {
        'tag_name': 'v9.9.9',
        'assets': [
            {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'},
            {'name': hu.CHECKSUMS_NAME, 'browser_download_url': 'https://x/sum'},
        ],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        if 'api.github.com' in url:
            return _Resp(json.dumps(release))
        if url.endswith('/bin'):
            return _Resp(payload)
        return _Resp(digest + '  ' + hu.ASSET_NAME + '\n')

    def runner(cmd, **_k):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            if str(cmd[0]) == str(binary):
                return type('R', (), {'stdout': 'Version:\tv2.0.0\n', 'stderr': '', 'returncode': 0})()
            return type('R', (), {'stdout': 'garbage', 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )
    assert 'sanity' in result['error']
    assert binary.read_bytes() == b'old-bin'


def test_apply_stages_live_replace_on_destination_filesystem(
    tmp_path, monkeypatch,
):
    """A direct private-/tmp to live rename must fail while sibling replace works."""
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'live' / 'hysteria'
    binary.parent.mkdir()
    binary.write_bytes(b'old-bin')
    payload = b'#!/bin/sh\necho Version: v9.9.9\n'
    digest = _sha(payload)
    release = {
        'tag_name': 'v9.9.9',
        'assets': [{
            'name': hu.ASSET_NAME,
            'browser_download_url': 'https://x/bin',
            'digest': 'sha256:' + digest,
        }],
    }

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        return _Resp(json.dumps(release) if 'api.github.com' in url else payload)

    def runner(cmd, **_kwargs):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            text = (
                'Version:\tv2.0.0\n'
                if str(cmd[0]) == str(binary)
                else 'Version:\tv9.9.9\n'
            )
            return type('R', (), {
                'stdout': text, 'stderr': '', 'returncode': 0,
            })()
        return type('R', (), {
            'stdout': '', 'stderr': '', 'returncode': 0,
        })()

    real_replace = os.replace

    def reject_cross_directory_live_replace(src, dest):
        if str(dest) == str(binary) and Path(src).parent != binary.parent:
            raise OSError(errno.EXDEV, 'cross-device link')
        return real_replace(src, dest)

    monkeypatch.setattr(hu.os, 'replace', reject_cross_directory_live_replace)

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'state' / 'last-good'),
        state_path=str(tmp_path / 'state' / 'update.json'),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )

    assert result['status'] == 'done', result
    assert binary.read_bytes() == payload


def test_atomic_install_fsyncs_metadata_and_parent_directory(
    tmp_path, monkeypatch,
):
    src = tmp_path / 'source'
    src.write_bytes(b'new')
    dest = tmp_path / 'bin' / 'hysteria'
    fsync_kinds = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        fsync_kinds.append(
            'directory' if stat.S_ISDIR(os.fstat(fd).st_mode) else 'file'
        )
        return real_fsync(fd)

    monkeypatch.setattr(hu.os, 'fsync', recording_fsync)
    hu._atomic_install(
        src, dest, mode=0o751, uid=os.getuid(), gid=os.getgid(),
    )

    assert dest.read_bytes() == b'new'
    assert stat.S_IMODE(dest.stat().st_mode) == 0o751
    assert fsync_kinds.count('file') >= 2
    assert fsync_kinds.count('directory') >= 1


def test_atomic_install_cleans_tempfile_when_metadata_update_fails(
    tmp_path, monkeypatch,
):
    src = tmp_path / 'source'
    src.write_bytes(b'new')
    dest = tmp_path / 'bin' / 'hysteria'

    def fail_fchmod(_fd, _mode):
        raise OSError('metadata failure')

    monkeypatch.setattr(hu.os, 'fchmod', fail_fchmod)
    with pytest.raises(OSError, match='metadata failure'):
        hu._atomic_install(src, dest, mode=0o755)

    assert not dest.exists()
    assert list(dest.parent.glob('hysteria.*.tmp')) == []


def test_rollback_restores_last_good_mode_and_owner(tmp_path):
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'broken')
    binary.chmod(0o755)
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    last_good.chmod(0o640)
    expected = last_good.stat()

    hu._restore_last_good(
        runner=lambda *_a, **_k: type('R', (), {'returncode': 0})(),
        binary_path=str(binary), last_good_path=str(last_good),
        ready_probe=lambda: {'ok': True},
    )

    actual = binary.stat()
    assert binary.read_bytes() == b'good'
    assert stat.S_IMODE(actual.st_mode) == 0o640
    assert (actual.st_uid, actual.st_gid) == (expected.st_uid, expected.st_gid)


def test_reconcile_applying_after_live_replaced(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'new-unconfirmed')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'applying',
        'pending_confirm': True,
        'operation_id': 'op-current',
        'live_replaced': True,
        'rollback_operation_id': 'op-current',
        'version': 'v9',
        'previous_version': 'v1',
        'sha256': '',
        'ts': '',
        'error': '',
    }, state_path)
    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
    )
    assert state['status'] == 'rolled_back'
    assert state['pending_confirm'] is False
    assert binary.read_bytes() == b'good'


def test_reconcile_preinstall_state_never_uses_historical_last_good(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'healthy-current')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'obsolete-history')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'downloading',
        'pending_confirm': True,
        'operation_id': 'op-current',
        'live_replaced': False,
        'version': 'v9',
        'previous_version': 'v1',
        'sha256': '',
        'ts': '',
        'error': '',
    }, state_path)

    state = hu.reconcile(
        runner=lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError('pre-install recovery must not restart')
        ),
        binary_path=str(binary), last_good_path=str(last_good),
        state_path=str(state_path), ready_probe=lambda: {'ok': True},
    )

    assert state['status'] == 'failed'
    assert state['reason'] == 'interrupted_before_install'
    assert binary.read_bytes() == b'healthy-current'


def test_successful_apply_persists_verifying_before_done(tmp_path, monkeypatch):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    payload = b'#!/bin/sh\necho Version: v9.9.9\n'
    release = {
        'tag_name': 'v9.9.9',
        'assets': [{
            'name': hu.ASSET_NAME,
            'browser_download_url': 'https://x/bin',
            'digest': 'sha256:' + _sha(payload),
        }],
    }
    observed = []
    real_save = hu.save_state

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        return _Resp(json.dumps(release) if 'api.github.com' in url else payload)

    def runner(cmd, **_kwargs):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            text = 'Version:\tv2.0.0\n' if str(cmd[0]) == str(binary) else 'Version:\tv9.9.9\n'
            return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    def recording_save(state, path):
        observed.append(state.get('status'))
        real_save(state, path)

    monkeypatch.setattr(hu, 'save_state', recording_save)
    result = hu.apply_update(
        opener=opener, runner=runner, binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(tmp_path / 'state.json'),
        ready_probe=lambda: {'ok': True}, force=True,
        backup_result={'ok': True, 'label': 'fresh'},
    )

    assert result['status'] == 'done'
    assert observed[-5:] == [
        'scheduled', 'downloading', 'applying', 'verifying', 'done',
    ]


def test_reconcile_default_readiness_uses_shared_stability_probe(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'broken')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'verifying', 'pending_confirm': True,
        'operation_id': 'op-current', 'live_replaced': True,
        'rollback_operation_id': 'op-current',
    }, state_path)
    calls = []

    def stable_probe(runner, connection_factory=None):
        calls.append((runner, connection_factory))
        return {'ok': True, 'label': 'three combined passes'}

    monkeypatch.setattr(hu, '_ready', stable_probe)
    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {
            'returncode': 0, 'stdout': '', 'stderr': '',
        })(),
        binary_path=str(binary), last_good_path=str(last_good),
        state_path=str(state_path),
    )

    assert state['status'] == 'rolled_back'
    assert len(calls) == 1


def test_reconcile_without_last_good_fails_safe(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'live')
    last_good = tmp_path / 'missing-last-good'
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'applying',
        'pending_confirm': True,
        'operation_id': 'op-current',
        'live_replaced': True,
        'rollback_operation_id': 'op-current',
        'version': 'v9',
        'previous_version': 'v1',
        'sha256': '',
        'ts': '',
        'error': '',
    }, state_path)
    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {'returncode': 0, 'stdout': '', 'stderr': ''})(),
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
    )
    assert state['status'] == 'rollback_failed'
    assert 'no last-good' in state['error']
    assert binary.read_bytes() == b'live'


def test_reconcile_records_rollback_failed_when_readiness_stays_down(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'broken')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'applying',
        'pending_confirm': True,
        'operation_id': 'op-current',
        'live_replaced': True,
        'rollback_operation_id': 'op-current',
        'version': 'v9',
        'previous_version': 'v1',
        'sha256': '',
        'ts': '',
        'error': '',
    }, state_path)

    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {
            'returncode': 0, 'stdout': '', 'stderr': '',
        })(),
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': False, 'label': 'still down'},
    )

    assert state['status'] == 'rollback_failed'
    assert 'rollback readiness failed' in state['error']


def test_rollback_failed_barrier_preserves_both_errors_until_recovery(
    tmp_path,
):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'broken')
    last_good = tmp_path / 'last-good'
    last_good.write_bytes(b'good')
    state_path = tmp_path / 'state.json'
    hu.save_state({
        **hu.IDLE_STATE,
        'status': 'rollback_failed',
        'operation_id': 'op-current',
        'live_replaced': True,
        'rollback_operation_id': 'op-current',
        'primary_error': 'primary restart failed',
        'rollback_error': 'first rollback failed',
        'error': 'primary restart failed; rollback failed: first rollback failed',
    }, state_path)

    state = hu.reconcile(
        runner=lambda *_a, **_k: type('R', (), {
            'returncode': 0, 'stdout': '', 'stderr': '',
        })(),
        binary_path=str(binary), last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': False, 'label': 'still down'},
    )

    assert state['status'] == 'rollback_failed'
    assert state['primary_error'] == 'primary restart failed'
    assert 'rollback readiness failed' in state['rollback_error']
    assert 'primary restart failed' in state['error']
    assert 'rollback readiness failed' in state['error']


def test_apply_cannot_overwrite_rollback_failed_barrier(tmp_path, monkeypatch):
    monkeypatch.setattr(hu, 'LOCK_PATH', str(tmp_path / 'lock'))
    state_path = tmp_path / 'state.json'
    hu.save_state({
        **hu.IDLE_STATE,
        'status': 'rollback_failed',
        'operation_id': 'op-current',
        'primary_error': 'primary failure',
        'rollback_error': 'rollback failure',
    }, state_path)
    monkeypatch.setattr(
        hu, 'check_latest',
        lambda **_k: (_ for _ in ()).throw(
            AssertionError('barrier must block a fresh apply')
        ),
    )

    state = hu.apply_update(
        state_path=str(state_path), binary_path=str(tmp_path / 'hysteria'),
        last_good_path=str(tmp_path / 'last-good'), force=False,
    )

    assert state['status'] == 'rollback_failed'
    assert state['primary_error'] == 'primary failure'
    assert state['rollback_error'] == 'rollback failure'
