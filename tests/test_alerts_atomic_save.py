"""alerts.save_state must be atomic.

A crashed save_state() used to leave alert_state.json truncated; readers
silently fall back to an empty dedup map, which re-fires the same quota
alert on every cron tick. This test pins the temp-file + rename behavior.
"""
import json

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
    assert dst == str(p)
    assert json.loads(p.read_text()) == state
    assert not (p.parent / (p.name + '.tmp')).exists(), 'temp file must be cleaned up'


def test_save_state_overwrites_existing(tmp_path):
    p = tmp_path / 'alert_state.json'
    alerts.save_state({'quota_80': {'a': '2026-04'}, 'quota_100': {}, 'anomaly': {}}, p)
    alerts.save_state({'quota_80': {'a': '2026-05'}, 'quota_100': {}, 'anomaly': {}}, p)
    assert json.loads(p.read_text())['quota_80'] == {'a': '2026-05'}
