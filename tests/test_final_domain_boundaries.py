"""Final response and persistence boundaries retain their public contracts."""

import importlib.util
import json
import stat

import shared_views


def test_panel_data_module_exists():
    assert importlib.util.find_spec('user_panel_data') is not None


def test_conflict_view_escapes_draft_and_omits_passwords():
    assert hasattr(shared_views, 'render_user_state_conflict')
    rendered = shared_views.render_user_state_conflict(
        '/admin',
        'host',
        {'user': '<alice>', 'password': 'secret-not-for-html'},
        render_admin_shell=lambda *args, **kwargs: args[2],
    )
    assert '&lt;alice&gt;' in rendered
    assert 'secret-not-for-html' not in rendered


def test_audit_append_is_private_and_preserves_fields(tmp_path):
    from audit_log import append_reset_log

    path = tmp_path / 'audit.jsonl'
    append_reset_log(
        path, 'admin', 'reset', 'alice', 10, 0, client_ip='127.0.0.1', month=lambda: '2026-09'
    )
    entry = json.loads(path.read_text())
    assert entry['before'] == 10 and entry['after'] == 0
    assert entry['target'] == 'alice'
    assert entry['ip'] == '127.0.0.1'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
