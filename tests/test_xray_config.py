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


# =============================================================================
# Validation-only log-patch regression tests
# =============================================================================

def test_patch_logs_redirects_production_paths_to_dev_null():
    """Production log paths must be redirected to /dev/null in the validation
    copy so that native validation does not require write permission on the
    real log directory."""
    cfg = {
        'log': {
            'access': '/var/log/xray/hy2-access.log',
            'error': '/var/log/xray/hy2-error.log',
        },
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    assert patched['log']['access'] == '/dev/null'
    assert patched['log']['error'] == '/dev/null'


def test_patch_logs_preserves_original_cfg():
    """The original cfg dict passed to _patch_logs_for_validation must never
    be mutated; the returned dict is a deep copy."""
    cfg = {
        'log': {
            'access': '/var/log/xray/hy2-access.log',
            'error': '/var/log/xray/hy2-error.log',
        },
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    original_access = cfg['log']['access']
    original_error = cfg['log']['error']
    xc._patch_logs_for_validation(cfg)
    assert cfg['log']['access'] == original_access
    assert cfg['log']['error'] == original_error


def test_patch_logs_handles_missing_log_section():
    """If the cfg has no 'log' key, leave it absent — Xray will use defaults."""
    cfg = {
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    # No 'log' key was added — the patch function must not inject one.
    assert 'log' not in patched, (
        "patch function must not add a log section when none existed; "
        "Xray's own validator must detect any resulting error"
    )


def test_patch_logs_handles_null_access_or_error():
    """If log.access or log.error is already null (disabled), it must stay null
    in the validation copy (null means 'disabled', which requires no log path)."""
    cfg = {
        'log': {'access': None, 'error': None},
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    assert patched['log']['access'] is None
    assert patched['log']['error'] is None


def test_patch_logs_handles_already_dev_null():
    """If log.access or log.error already points to /dev/null, it must stay
    /dev/null in the validation copy."""
    cfg = {
        'log': {'access': '/dev/null', 'error': '/dev/null'},
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    assert patched['log']['access'] == '/dev/null'
    assert patched['log']['error'] == '/dev/null'


def test_patch_logs_missing_access_key_left_untouched():
    """If log exists but has no 'access' key, the missing key is left as-is.
    Xray treats a missing access logger as disabled, which is safe."""
    cfg = {
        'log': {'error': '/var/log/xray/hy2-error.log'},
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    assert 'access' not in patched['log'], (
        "missing 'access' must not be added by patch function"
    )
    assert patched['log']['error'] == '/dev/null', patched['log']['error']


def test_patch_logs_does_NOT_repair_non_dict_log():
    """If the 'log' key exists but is a non-dict (int, list, etc.), the config
    must pass through unchanged so that Xray reports the real schema error rather
    than silently succeeding on a 'repaired' config."""
    for bad_log in (123, [], {'a': 1}):
        cfg = {
            'log': bad_log,
            'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
        }
        patched = xc._patch_logs_for_validation(cfg)
        # The bad log structure must survive untouched.
        assert patched['log'] == bad_log, (
            f"non-dict log {bad_log!r} must not be repaired; "
            "Xray must detect and reject the invalid schema"
        )


def test_patch_logs_does_NOT_repair_non_string_access():
    """If log.access is a non-string value (int, dict, list, etc.), it must NOT
    be rewritten to '/dev/null'.  Xray must detect and reject the type error."""
    for bad_val in (123, {}, [], True):
        cfg = {
            'log': {'access': bad_val, 'error': '/var/log/xray/hy2-error.log'},
            'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
        }
        patched = xc._patch_logs_for_validation(cfg)
        assert patched['log']['access'] == bad_val, (
            f"non-string access {bad_val!r} must not be repaired; "
            "Xray must detect the type error"
        )


def test_patch_logs_does_NOT_repair_non_string_error():
    """If log.error is a non-string value (int, dict, list, etc.), it must NOT
    be rewritten to '/dev/null'.  Xray must detect and reject the type error."""
    for bad_val in (123, {}, [], True):
        cfg = {
            'log': {'access': '/var/log/xray/hy2-access.log', 'error': bad_val},
            'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
        }
        patched = xc._patch_logs_for_validation(cfg)
        assert patched['log']['error'] == bad_val, (
            f"non-string error {bad_val!r} must not be repaired; "
            "Xray must detect the type error"
        )


def test_patch_logs_does_NOT_redirect_custom_paths():
    """Custom log paths that are not in the known managed set must be passed
    through unchanged so that Xray can report any errors for them."""
    custom_paths = [
        '/tmp/my-access.log',
        '/var/log/myapp.log',
        '/opt/custom/xray-access.log',
        '/some/bad/path/access.log',
    ]
    for custom in custom_paths:
        cfg = {
            'log': {'access': custom, 'error': '/var/log/xray/hy2-error.log'},
            'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
        }
        patched = xc._patch_logs_for_validation(cfg)
        assert patched['log']['access'] == custom, (
            f"custom path {custom!r} must NOT be redirected; "
            "Xray must validate it and report any errors"
        )
        # Only the known managed error path should be redirected
        assert patched['log']['error'] == '/dev/null'


def test_patch_logs_handles_legacy_log_paths():
    """Legacy log paths are known managed paths and must be redirected to /dev/null."""
    cfg = {
        'log': {
            'access': '/var/log/xray/access.log',
            'error': '/var/log/xray/error.log',
        },
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    patched = xc._patch_logs_for_validation(cfg)
    assert patched['log']['access'] == '/dev/null'
    assert patched['log']['error'] == '/dev/null'


def test_save_config_production_log_paths_preserved(tmp_path, monkeypatch):
    """Prove by source inspection that only the ORIGINAL cfg is rename'd to the
    production path.  The val_tmp (patched copy) is used only for validation
    and is deleted; only the original staging temp may be rename'd."""
    import inspect
    src = inspect.getsource(xc._save_config)
    # The rename line must reference prod_tmp, not val_tmp
    assert 'os.replace(prod_tmp' in src, (
        "only prod_tmp (original cfg) may be rename'd to production path"
    )
    assert 'os.replace(val_tmp' not in src, (
        "val_tmp (patched copy) must NEVER be rename'd to production path"
    )
    # val_tmp is created from the patched cfg
    assert 'val_cfg = _patch_logs_for_validation(cfg)' in src, (
        "val_cfg must come from patched copy"
    )
    # prod staging writes the ORIGINAL cfg
    assert 'json.dumps(cfg,' in src, (
        "prod staging temp must write ORIGINAL cfg (not val_cfg)"
    )


def test_save_config_temp_file_cleanup(tmp_path, monkeypatch):
    """Prove by source inspection that the finally block cleans up both temp files."""
    import inspect
    src = inspect.getsource(xc._save_config)
    finally_idx = src.index('    finally:')
    finally_block = src[finally_idx:]
    assert 'prod_tmp' in finally_block, "finally must clean up prod_tmp"
    assert 'val_tmp' in finally_block, "finally must clean up val_tmp"
    assert 'unlink' in finally_block, "finally must unlink temp files"
    # Both names must be in the cleanup iteration
    assert 'for name in' in finally_block


def test_patch_and_serialize_correctness():
    """Prove that serializing the patched cfg produces /dev/null and that the
    original cfg dict is not modified.  This is the core correctness guarantee."""
    import copy
    cfg = {
        'log': {
            'access': '/var/log/xray/hy2-access.log',
            'error': '/var/log/xray/hy2-error.log',
        },
        'inbounds': [{'protocol': 'vless', 'port': 443, 'settings': {'clients': []}}],
    }
    val_cfg = xc._patch_logs_for_validation(cfg)
    serialized = json.dumps(val_cfg, indent=2, ensure_ascii=False)
    # Validation temp file must contain /dev/null
    assert '"/dev/null"' in serialized, (
        "validation temp file JSON must contain /dev/null"
    )
    # Must not contain production log paths
    assert '/var/log/xray/hy2-access.log' not in serialized
    assert '/var/log/xray/hy2-error.log' not in serialized
    # Original cfg must be completely unchanged
    assert cfg['log']['access'] == '/var/log/xray/hy2-access.log'
    assert cfg['log']['error'] == '/var/log/xray/hy2-error.log'
    # deep copy isolation
    assert val_cfg['log'] is not cfg['log']
