"""xray_config.apply_user_plan + atomic save.

The cron tick used to call sync_user/remove_user once per user, which
re-parsed the entire xray config file N times per tick. apply_user_plan
folds the loop into a single read + single (atomic) write. These tests pin:
  - one read (the in-memory parse happens once)
  - one write (no intermediate writes)
  - atomic write (temp file + rename, never a half-written config.json)
  - mixed add/remove/no-op behavior is correct
"""
import json

import xray_config as xc


def _make_cfg(tmp_path, clients_443=None, clients_8443=None):
    cfg = {
        'inbounds': [
            {'protocol': 'vless', 'port': 443,
             'settings': {'clients': clients_443 or []}},
            {'protocol': 'vless', 'port': 8443,
             'settings': {'clients': clients_8443 or []}},
        ],
    }
    p = tmp_path / 'config.json'
    p.write_text(json.dumps(cfg))
    return p


def test_apply_user_plan_mixed_add_and_remove(tmp_path):
    p = _make_cfg(
        tmp_path,
        clients_443=[{'id': 'old', 'email': 'bob', 'flow': 'xtls-rprx-vision'}],
        clients_8443=[{'id': 'old', 'email': 'bob-backup', 'flow': 'xtls-rprx-vision'}],
    )
    plan = {
        'alice': 'uuid-A',  # add
        'bob': None,        # remove (over-quota)
        'carol': 'uuid-C',  # add
    }
    assert xc.apply_user_plan(plan, path=p) is True

    cfg = json.loads(p.read_text())
    emails = {
        ib['port']: {c['email'] for c in ib['settings']['clients']}
        for ib in cfg['inbounds']
    }
    assert emails[443] == {'alice', 'carol'}
    assert emails[8443] == {'alice-backup', 'carol-backup'}


def test_apply_user_plan_no_change_returns_false(tmp_path):
    p = _make_cfg(
        tmp_path,
        clients_443=[{'id': 'uuid-A', 'email': 'alice', 'flow': 'xtls-rprx-vision'}],
        clients_8443=[{'id': 'uuid-A', 'email': 'alice-backup', 'flow': 'xtls-rprx-vision'}],
    )
    assert xc.apply_user_plan({'alice': 'uuid-A'}, path=p) is False


def test_apply_user_plan_empty_plan_returns_false(tmp_path):
    p = _make_cfg(tmp_path)
    assert xc.apply_user_plan({}, path=p) is False


def test_apply_user_plan_reads_file_only_once(tmp_path, monkeypatch):
    p = _make_cfg(tmp_path)
    plan = {f'user{i}': f'uuid-{i}' for i in range(50)}

    calls = {'load': 0, 'save': 0}
    orig_load = xc._load_config
    orig_save = xc._save_config

    def counting_load(path):
        calls['load'] += 1
        return orig_load(path)

    def counting_save(path, cfg):
        calls['save'] += 1
        return orig_save(path, cfg)

    monkeypatch.setattr(xc, '_load_config', counting_load)
    monkeypatch.setattr(xc, '_save_config', counting_save)

    xc.apply_user_plan(plan, path=p)
    assert calls['load'] == 1, 'bulk apply must read xray config exactly once'
    assert calls['save'] == 1, 'bulk apply must write xray config exactly once'


def test_save_config_is_atomic(tmp_path):
    """A crash mid-_save_config must not leave a half-written config.json.
    The atomic write uses a sibling .tmp file and os.replace, so even if the
    rename is interrupted the original file is untouched. We exercise the
    happy path here and confirm the tmp file is cleaned up."""
    p = _make_cfg(tmp_path)
    original = p.read_text()

    xc._save_config(p, {'inbounds': [{'protocol': 'vless', 'port': 443,
                                       'settings': {'clients': [{'id': 'u', 'email': 'alice'}]}}]})
    new = p.read_text()
    assert new != original
    assert 'alice' in new
    assert not (p.parent / (p.name + '.tmp')).exists(), 'temp file must be renamed away'


def test_apply_user_plan_returns_false_on_unreadable_config(tmp_path):
    assert xc.apply_user_plan({'alice': 'uuid-A'}, path=tmp_path / 'nope.json') is False
