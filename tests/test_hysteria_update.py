"""Hysteria official-binary check / apply / rollback."""

import hashlib
import json
from pathlib import Path

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
    )
    assert result['status'] in ('failed', 'rolled_back')
    assert 'checksum' in result['error']
    assert binary.read_bytes() == b'old-bin'


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

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': False, 'label': '未就绪'},
    )
    assert result['status'] == 'rolled_back'
    assert binary.read_bytes() == b'old-bin'
    assert last_good.exists()


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
    )
    assert 'sanity' in result['error']
    assert binary.read_bytes() == b'old-bin'


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


def test_reconcile_without_last_good_fails_safe(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'live')
    last_good = tmp_path / 'missing-last-good'
    state_path = tmp_path / 'state.json'
    hu.save_state({
        'status': 'applying',
        'pending_confirm': True,
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
    assert state['status'] == 'failed'
    assert 'no last-good' in state['error']
    assert binary.read_bytes() == b'live'
