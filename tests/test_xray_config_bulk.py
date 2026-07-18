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
import grp
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

import state_store
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
        clients_8443=[{'id': 'old', 'email': 'bob@hy2-backup.invalid', 'flow': 'xtls-rprx-vision'}],
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
    assert emails[8443] == {
        'alice@hy2-backup.invalid',
        'carol@hy2-backup.invalid',
    }


def test_apply_user_plan_no_change_returns_false(tmp_path):
    p = _make_cfg(
        tmp_path,
        clients_443=[{'id': 'uuid-A', 'email': 'alice', 'flow': 'xtls-rprx-vision'}],
        clients_8443=[{'id': 'uuid-A', 'email': 'alice@hy2-backup.invalid', 'flow': 'xtls-rprx-vision'}],
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


def test_save_config_sets_restricted_permissions(tmp_path):
    p = _make_cfg(tmp_path)

    xc._save_config(p, {'inbounds': []})

    assert (p.stat().st_mode & 0o777) == 0o640
    if os.geteuid() == 0:
        assert p.stat().st_gid == grp.getgrnam(xc.CONFIG_GROUP).gr_gid


def test_save_config_sets_mode_before_atomic_replace(tmp_path, monkeypatch):
    p = _make_cfg(tmp_path)
    original_replace = xc.os.replace
    observed = {}

    def checked_replace(src, dst):
        observed['mode'] = Path(src).stat().st_mode & 0o777
        observed['gid'] = Path(src).stat().st_gid
        original_replace(src, dst)

    monkeypatch.setattr(xc.os, 'replace', checked_replace)
    xc._save_config(p, {'inbounds': []})

    assert observed['mode'] == 0o640
    if os.geteuid() == 0:
        assert observed['gid'] == grp.getgrnam(xc.CONFIG_GROUP).gr_gid


def test_apply_user_plan_fails_closed_on_unreadable_config(tmp_path):
    with pytest.raises(state_store.InvalidJsonState):
        xc.apply_user_plan(
            {'alice': 'uuid-A'}, path=tmp_path / 'nope.json',
        )


def test_prune_unknown_reconciles_exact_canonical_clients(tmp_path):
    p = _make_cfg(
        tmp_path,
        clients_443=[
            {'id': 'legacy', 'email': 'me'},
            {'id': 'old-A', 'email': 'alice'},
            {'id': 'duplicate', 'email': 'alice'},
        ],
        clients_8443=[
            {'id': 'legacy', 'email': 'me-backup'},
            {'id': 'old-A', 'email': 'alice@hy2-backup.invalid'},
        ],
    )

    assert xc.apply_user_plan(
        {'alice': 'uuid-A', 'disabled': None},
        path=p,
        prune_unknown=True,
    ) is True

    cfg = json.loads(p.read_text())
    clients = {
        inbound['port']: inbound['settings']['clients']
        for inbound in cfg['inbounds']
    }
    assert clients == {
        443: [
            {
                'id': 'uuid-A',
                'email': 'alice',
                'flow': 'xtls-rprx-vision',
            },
        ],
        8443: [
            {
                'id': 'uuid-A',
                'email': 'alice@hy2-backup.invalid',
                'flow': 'xtls-rprx-vision',
            },
        ],
    }


def test_prune_unknown_empty_plan_revokes_every_vless_client(tmp_path):
    p = _make_cfg(
        tmp_path,
        clients_443=[{'id': 'legacy', 'email': 'me'}],
        clients_8443=[{'id': 'legacy', 'email': 'me-backup'}],
    )

    assert xc.apply_user_plan({}, path=p, prune_unknown=True) is True

    cfg = json.loads(p.read_text())
    assert all(
        inbound['settings']['clients'] == []
        for inbound in cfg['inbounds']
    )


def test_concurrent_incremental_syncs_preserve_every_user(tmp_path):
    p = _make_cfg(tmp_path)
    users = {f'user-{index}': f'uuid-{index}' for index in range(40)}

    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda item: xc.sync_user(*item, path=p), users.items()))

    cfg = json.loads(p.read_text())
    clients = {
        ib['port']: {client['email'] for client in ib['settings']['clients']}
        for ib in cfg['inbounds']
    }
    assert clients[443] == set(users)
    assert clients[8443] == {
        f'{user}{xc.BACKUP_SUFFIX}' for user in users
    }
    assert not list(tmp_path.glob('config.json.*.tmp'))
