"""xray_config — sync_user / remove_user / email_for / strip_backup_suffix.

Every test runs against a temp config file; the module never touches the real
/usr/local/etc/xray/config.json.
"""
import json

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
            {'protocol': 'trojan', 'port': 9999,
             'settings': {'clients': [{'id': 'should-stay', 'email': 'unrelated'}]}},
        ],
    }
    p = tmp_path / 'config.json'
    p.write_text(json.dumps(cfg))
    return p


# ---------- pure helpers --------------------------------------------------------

def test_email_for_primary_port_returns_bare_username():
    assert xc.email_for(443, 'alice') == 'alice'


def test_email_for_backup_port_appends_suffix():
    assert xc.email_for(8443, 'alice') == 'alice@hy2-backup.invalid'


def test_strip_backup_suffix_handles_both_forms():
    assert xc.strip_backup_suffix('alice') == 'alice'
    assert xc.strip_backup_suffix(
        'alice@hy2-backup.invalid'
    ) == 'alice'


def test_strip_backup_suffix_does_not_misfire_on_partial_match():
    # Creatable usernames and the legacy "-backup" spelling must not collide
    # with the reserved identity suffix.
    assert xc.strip_backup_suffix('mybackup') == 'mybackup'
    assert xc.strip_backup_suffix('alice-backup') == 'alice-backup'


def test_creatable_backup_named_user_cannot_collide_with_generated_identity():
    assert xc.email_for(443, 'alice-backup') == 'alice-backup'
    assert xc.email_for(8443, 'alice') == 'alice@hy2-backup.invalid'
    assert (
        xc.email_for(443, 'alice-backup')
        != xc.email_for(8443, 'alice')
    )


# ---------- sync_user -----------------------------------------------------------

def test_sync_user_adds_to_both_inbounds(tmp_path):
    p = _make_cfg(tmp_path)
    changed = xc.sync_user('alice', 'uuid-A', path=p)
    assert changed is True
    cfg = json.loads(p.read_text())
    vless = [ib for ib in cfg['inbounds'] if ib['protocol'] == 'vless']
    assert len(vless) == 2
    emails = {ib['port']: [c['email'] for c in ib['settings']['clients']] for ib in vless}
    assert emails[443] == ['alice']
    assert emails[8443] == ['alice@hy2-backup.invalid']


def test_sync_user_uuid_is_set_on_both(tmp_path):
    p = _make_cfg(tmp_path)
    xc.sync_user('alice', 'uuid-A', path=p)
    cfg = json.loads(p.read_text())
    for ib in cfg['inbounds']:
        if ib['protocol'] != 'vless':
            continue
        for c in ib['settings']['clients']:
            assert c['id'] == 'uuid-A'
            assert c['flow'] == 'xtls-rprx-vision'


def test_sync_user_idempotent_when_already_correct(tmp_path):
    p = _make_cfg(tmp_path,
                  clients_443=[{'id': 'uuid-A', 'email': 'alice', 'flow': 'xtls-rprx-vision'}],
                  clients_8443=[{'id': 'uuid-A', 'email': 'alice@hy2-backup.invalid', 'flow': 'xtls-rprx-vision'}])
    assert xc.sync_user('alice', 'uuid-A', path=p) is False


def test_sync_user_updates_uuid_on_existing_entry(tmp_path):
    p = _make_cfg(tmp_path,
                  clients_443=[{'id': 'old-uuid', 'email': 'alice', 'flow': 'xtls-rprx-vision'}],
                  clients_8443=[{'id': 'old-uuid', 'email': 'alice@hy2-backup.invalid', 'flow': 'xtls-rprx-vision'}])
    assert xc.sync_user('alice', 'new-uuid', path=p) is True
    cfg = json.loads(p.read_text())
    for ib in cfg['inbounds']:
        if ib['protocol'] != 'vless':
            continue
        assert ib['settings']['clients'][0]['id'] == 'new-uuid'


def test_sync_user_does_not_touch_non_vless_inbound(tmp_path):
    p = _make_cfg(tmp_path)
    xc.sync_user('alice', 'uuid-A', path=p)
    cfg = json.loads(p.read_text())
    trojan = [ib for ib in cfg['inbounds'] if ib['protocol'] == 'trojan'][0]
    assert trojan['settings']['clients'] == [{'id': 'should-stay', 'email': 'unrelated'}]


def test_sync_user_fails_closed_on_unreadable_config(tmp_path):
    with pytest.raises(state_store.InvalidJsonState):
        xc.sync_user(
            'alice', 'uuid-A', path=tmp_path / 'no-such-file.json',
        )


# ---------- remove_user ---------------------------------------------------------

def test_remove_user_removes_both_clients(tmp_path):
    p = _make_cfg(tmp_path,
                  clients_443=[{'id': 'u', 'email': 'alice'},
                               {'id': 'u', 'email': 'bob'}],
                  clients_8443=[{'id': 'u', 'email': 'alice@hy2-backup.invalid'},
                                {'id': 'u', 'email': 'bob@hy2-backup.invalid'}])
    assert xc.remove_user('alice', path=p) is True
    cfg = json.loads(p.read_text())
    emails = {ib['port']: [c['email'] for c in ib['settings']['clients']]
              for ib in cfg['inbounds'] if ib['protocol'] == 'vless'}
    assert emails[443] == ['bob']
    assert emails[8443] == ['bob@hy2-backup.invalid']


def test_remove_user_returns_false_when_user_absent(tmp_path):
    p = _make_cfg(tmp_path,
                  clients_443=[{'id': 'u', 'email': 'bob'}],
                  clients_8443=[{'id': 'u', 'email': 'bob@hy2-backup.invalid'}])
    assert xc.remove_user('alice', path=p) is False


def test_remove_user_fails_closed_on_unreadable_config(tmp_path):
    with pytest.raises(state_store.InvalidJsonState):
        xc.remove_user('alice', path=tmp_path / 'nope.json')
