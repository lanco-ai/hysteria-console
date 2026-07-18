"""alerts.save_state must be atomic and durable.

The state writer uses unique sibling tempfiles, fsyncs data and the directory,
and strict readers preserve a corrupt file for operator repair.
"""
import json
import os

import alerts


def test_save_state_writes_via_temp_file(tmp_path, monkeypatch):
    p = tmp_path / 'alert_state.json'

    seen = {'tmp_written': False, 'replace_called_with': None}

    real_replace = __import__('os').replace

    def spying_replace(src, dst):
        seen['replace_called_with'] = (str(src), str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr('os.replace', spying_replace)

    state = {'quota_80': {'alice': '2026-05'}, 'quota_100': {}, 'anomaly': {}}
    alerts.save_state(state, p)

    src, dst = seen['replace_called_with']
    assert src.endswith('.tmp'), 'save_state must write through a .tmp sibling'
    assert src != str(p) + '.tmp', 'temp file name must be unique per writer'
    assert dst == str(p)
    assert json.loads(p.read_text()) == state
    assert not (p.parent / (p.name + '.tmp')).exists(), 'temp file must be cleaned up'


def test_save_state_overwrites_existing(tmp_path):
    p = tmp_path / 'alert_state.json'
    alerts.save_state({'quota_80': {'a': '2026-04'}, 'quota_100': {}, 'anomaly': {}}, p)
    alerts.save_state({'quota_80': {'a': '2026-05'}, 'quota_100': {}, 'anomaly': {}}, p)
    assert json.loads(p.read_text())['quota_80'] == {'a': '2026-05'}


def test_save_state_fsyncs_file_and_directory(tmp_path, monkeypatch):
    p = tmp_path / 'alert_state.json'
    calls = []
    real_fsync = os.fsync

    def spying_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(os, 'fsync', spying_fsync)
    alerts.save_state({'quota_80': {'a': '2026-05'}}, p)

    assert len(calls) >= 2, 'both tempfile data and renamed directory entry need fsync'


def test_save_state_uses_distinct_tempfiles(tmp_path, monkeypatch):
    p = tmp_path / 'alert_state.json'
    sources = []
    real_replace = os.replace

    def spying_replace(src, dst):
        sources.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(os, 'replace', spying_replace)
    alerts.save_state({'quota_80': {'a': '2026-04'}}, p)
    alerts.save_state({'quota_80': {'a': '2026-05'}}, p)

    assert len(set(sources)) == 2
    assert all(src != str(p) + '.tmp' for src in sources)
