"""Release-shape regressions for the Hysteria updater.

Upstream ships namespaced tags ('app/v2.12.2'), one combined hashes.txt
built from `sha256sum build/*`, and a per-asset 'digest' field. Getting
any of those wrong either bricks apply or makes check claim an update
forever.
"""

import hashlib
import json
import tempfile

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


def _release(tag, *, digest=None, with_checksums=True, published_at=''):
    asset = {'name': hu.ASSET_NAME, 'browser_download_url': 'https://x/bin'}
    if digest:
        asset['digest'] = 'sha256:' + digest
    assets = [asset]
    if with_checksums:
        assets.append({
            'name': hu.CHECKSUMS_NAME,
            'browser_download_url': 'https://x/sum',
        })
    return {'tag_name': tag, 'published_at': published_at, 'assets': assets}


def _release_opener(release):
    def opener(req, timeout=0):
        return _Resp(json.dumps(release))
    return opener


def _version_runner(text):
    def runner(cmd, **_k):
        return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
    return runner


def test_checksums_name_matches_the_published_asset():
    assert hu.CHECKSUMS_NAME == 'hashes.txt'


def test_namespaced_tag_is_compared_numerically():
    release = _release('app/v2.12.2', published_at='2026-08-23T00:59:00Z')
    info = hu.check_latest(
        opener=_release_opener(release),
        runner=_version_runner('Version:\tv2.11.0\n'),
    )
    assert info['update_available'] is True
    assert info['latest'] == 'app/v2.12.2'
    assert info['published_at'] == '2026-08-23T00:59:00Z'
    assert info['checksum_url'] == 'https://x/sum'


def test_same_namespaced_version_is_not_an_update():
    info = hu.check_latest(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.12.2\n'),
    )
    assert info['update_available'] is False


def test_older_namespaced_tag_is_not_an_update():
    info = hu.check_latest(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=_version_runner('Version:\tv2.13.0\n'),
    )
    assert info['update_available'] is False


def test_uncomparable_tag_is_not_an_update():
    info = hu.check_latest(
        opener=_release_opener(_release('nightly')),
        runner=_version_runner('Version:\tv2.12.2\n'),
    )
    assert info['update_available'] is False


def test_unknown_local_version_is_not_an_update():
    def runner(cmd, **_k):
        raise OSError('binary missing')

    info = hu.check_latest(
        opener=_release_opener(_release('app/v2.12.2')),
        runner=runner,
    )
    assert info['current'] == ''
    assert info['update_available'] is False


def test_asset_digest_is_exposed_and_normalized():
    digest = 'a' * 64
    info = hu.check_latest(
        opener=_release_opener(_release('app/v2.12.2', digest=digest)),
        runner=_version_runner('Version:\tv2.11.0\n'),
    )
    assert info['asset_sha256'] == digest


def test_parse_checksums_reads_a_sha256sum_listing():
    listing = '\n'.join([
        'b' * 64 + '  build/hysteria-linux-arm64',
        'c' * 64 + '  build/hysteria-linux-amd64-avx',
        'd' * 64 + '  build/hysteria-linux-amd64',
        'e' * 64 + '  build/hysteria-windows-amd64.exe',
    ])
    assert hu._parse_checksums(listing) == 'd' * 64


def test_parse_checksums_rejects_a_nameless_hash_in_a_multi_asset_listing():
    assert hu._parse_checksums('e' * 64 + '\n' + 'f' * 64) == ''


def test_parse_checksums_keeps_single_line_and_bsd_forms():
    assert hu._parse_checksums('a' * 64 + '\n') == 'a' * 64
    assert hu._parse_checksums(
        'SHA256 (hysteria-linux-amd64) = ' + '9' * 64,
    ) == '9' * 64
    assert hu._parse_checksums('*' + hu.ASSET_NAME + '  ' + '7' * 64) == '7' * 64


def test_apply_prefers_the_asset_digest_over_a_checksum_download(tmp_path, monkeypatch):
    # Keep the staging dir on the same filesystem as the target binary so
    # the os.replace() promotion is exercised for real.
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path), raising=False)
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    last_good = tmp_path / 'last-good'
    state_path = tmp_path / 'state.json'
    payload = b'#!/bin/sh\necho Version: v2.12.2\n'
    release = _release('app/v2.12.2', digest=_sha(payload))
    seen = []

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        seen.append(url)
        if 'api.github.com' in url:
            return _Resp(json.dumps(release))
        if url.endswith('/bin'):
            return _Resp(payload)
        raise AssertionError('unexpected fetch: ' + url)

    def runner(cmd, **_k):
        if isinstance(cmd, list) and cmd[-1] == 'version':
            text = (
                'Version:\tv2.11.0\n' if str(cmd[0]) == str(binary)
                else 'Version:\tv2.12.2\n'
            )
            return type('R', (), {'stdout': text, 'stderr': '', 'returncode': 0})()
        return type('R', (), {'stdout': '', 'stderr': '', 'returncode': 0})()

    result = hu.apply_update(
        opener=opener,
        runner=runner,
        binary_path=str(binary),
        last_good_path=str(last_good),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result={'ok': True, 'label': 'x'},
    )
    assert result['status'] == 'done', result
    assert result['error'] == ''
    assert binary.read_bytes() == payload
    assert last_good.read_bytes() == b'old-bin'
    assert not any(url.endswith('/sum') for url in seen)


def test_apply_fails_closed_when_the_release_has_no_checksum(tmp_path):
    hu.LOCK_PATH = str(tmp_path / 'lock')
    binary = tmp_path / 'hysteria'
    binary.write_bytes(b'old-bin')
    state_path = tmp_path / 'state.json'

    def opener(req, timeout=0):
        url = getattr(req, 'full_url', str(req))
        if 'api.github.com' in url:
            return _Resp(json.dumps(_release('app/v2.12.2', with_checksums=False)))
        raise AssertionError('must not download a binary without a checksum')

    result = hu.apply_update(
        opener=opener,
        runner=_version_runner('Version:\tv2.11.0\n'),
        binary_path=str(binary),
        last_good_path=str(tmp_path / 'last-good'),
        state_path=str(state_path),
        ready_probe=lambda: {'ok': True},
        force=True,
        backup_result={'ok': True, 'label': 'x'},
    )
    assert result['status'] == 'failed'
    assert 'checksum' in result['error']
    assert binary.read_bytes() == b'old-bin'
