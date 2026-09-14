"""Account mutations shared by legacy and forthcoming API presenters."""

import json
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import account_mutation_service
import pytest
import subscription_service as ss


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def account_state(tmp_path, monkeypatch):
    users_file = tmp_path / 'users.json'
    sessions_file = tmp_path / 'user_sessions.json'
    _write_json(users_file, {})
    _write_json(sessions_file, {})
    monkeypatch.setattr(ss, 'USERS_FILE', users_file)
    monkeypatch.setattr(ss, 'USER_SESSIONS_FILE', sessions_file)
    monkeypatch.setattr(ss, 'USAGE_LOCK_FILE', tmp_path / 'usage.lock')
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: (False, False),
    )
    monkeypatch.setattr(ss, '_landing_registry_or_empty', lambda: {'nodes': {}})
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: None)
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: None)
    return {
        'USERS_FILE': users_file,
        'USER_SESSIONS_FILE': sessions_file,
        'make_service': ss._account_mutation_service,
    }


@pytest.fixture
def account_service(account_state):
    return account_state['make_service']()


@pytest.fixture
def good_form():
    return {
        'user': ['fixture_new'],
        'panel_password': ['fixture-panel-password'],
        'password': [' fixture-connection-password '],
        'quota_gb': ['10'],
        'quota_extra_gb': ['2'],
        'expires_at': ['2027-04-05'],
        'note': ['fixture note'],
        'guest': ['on'],
        'tuic_enabled': ['on'],
    }


def test_create_does_not_return_password_or_token_fields(account_service, good_form):
    result = account_service.create(form=good_form)
    assert result.outcome == 'created'
    assert result.username == 'fixture_new'
    assert result.draft is None
    assert 'fixture-panel-password' not in repr(result)
    assert not hasattr(result, '__dict__')
    assert account_mutation_service.AccountMutationResult


def test_result_is_frozen_slotted_and_hides_conflict_draft():
    result = account_mutation_service.AccountMutationResult(
        outcome='conflict',
        draft={'note': 'private draft'},
    )

    assert 'private draft' not in repr(result)
    assert not hasattr(result, '__dict__')
    with pytest.raises(FrozenInstanceError):
        result.code = 'replacement'


@pytest.mark.parametrize(
    ('overrides', 'code', 'field_id'),
    [
        ({'user': ['  ']}, 'user empty', 'create-user'),
        ({'user': ['bad user']}, 'err:username_invalid', 'create-user'),
        ({'quota_gb': ['-1']}, 'err:quota_invalid', 'create-quota-gb'),
        ({'quota_extra_gb': ['10241']}, 'err:quota_extra_invalid', 'create-quota-extra-gb'),
        ({'expires_at': ['2026-02-30']}, 'err:expiry_invalid', 'create-expires-at'),
        ({'note': ['x' * 201]}, 'err:note_too_long', 'create-note'),
        (
            {'panel_password': ['1234567']},
            'err:panel_password_short',
            'create-panel-password',
        ),
        (
            {'panel_password': ['x' * 257]},
            'err:panel_password_long',
            'create-panel-password',
        ),
        (
            {'password': ['x' * 257]},
            'err:proxy_password_long',
            'create-proxy-password',
        ),
    ],
)
def test_create_validation_returns_exact_safe_draft_without_writes(
    account_state,
    good_form,
    monkeypatch,
    overrides,
    code,
    field_id,
):
    form = {**good_form, **overrides, 'token': ['sensitive-token']}
    before = account_state['USERS_FILE'].read_bytes()
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: pytest.fail('invalid create must not sync'),
    )

    result = account_state['make_service']().create(form=form)

    assert (result.outcome, result.code, result.field_id) == ('invalid', code, field_id)
    assert result.draft == {
        'user': form['user'][0].strip(),
        'quota_gb': form['quota_gb'][0],
        'quota_extra_gb': form['quota_extra_gb'][0],
        'expires_at': form['expires_at'][0],
        'note': form['note'][0],
        'landing_initial_egress_id': '',
        'guest': True,
        'tuic_enabled': True,
    }
    assert 'fixture-panel-password' not in repr(result)
    assert 'fixture-connection-password' not in repr(result)
    assert 'sensitive-token' not in repr(result)
    assert account_state['USERS_FILE'].read_bytes() == before


def test_create_uses_only_first_values_and_presence_booleans(account_state, good_form):
    form = {
        **good_form,
        'user': ['first_user', 'bad user'],
        'panel_password': [' 12345678 ', 'x'],
        'password': [' first-proxy ', 'x' * 257],
        'quota_gb': ['3', '-1'],
        'quota_extra_gb': ['4', '10241'],
        'guest': [''],
        'tuic_enabled': ['false'],
    }

    result = account_state['make_service']().create(form=form)

    assert result == account_mutation_service.AccountMutationResult(
        outcome='created', username='first_user'
    )
    cfg = json.loads(account_state['USERS_FILE'].read_text())['first_user']
    assert cfg['monthly_quota_bytes'] == 3 * 1024**3
    assert cfg['quota_extra_bytes'] == 4 * 1024**3
    assert cfg['guest'] is True and cfg['metered'] is True
    assert cfg['tuic_enabled'] is True
    assert ss.verify_secret(' 12345678 ', cfg['panel_pass_hash'])
    assert ss.verify_secret('first-proxy', cfg['password_hash'])


def test_create_success_generates_credentials_authorizes_landing_and_revokes_sessions(
    account_state,
    good_form,
    monkeypatch,
):
    session_id = ss.create_user_session(
        'fixture_new',
        'obsolete-generation',
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    form = {**good_form, 'landing_initial_egress_id': ['home-one']}
    registry = {'nodes': {'home-one': {'enabled': True}}}
    monkeypatch.setattr(ss, '_landing_registry_or_empty', lambda: registry)
    monkeypatch.setattr(account_mutation_service.landing_egress, 'load_registry', lambda: registry)

    result = account_state['make_service']().create(form=form)

    assert result.outcome == 'created'
    cfg = json.loads(account_state['USERS_FILE'].read_text())['fixture_new']
    assert cfg['max_devices'] == 2
    assert cfg['disabled'] is False
    assert cfg['sub_token'] and cfg['sub_token'] not in repr(result)
    assert cfg['vless_uuid'] and cfg['vless_uuid'] not in repr(result)
    assert cfg['landing_allowed_egress_ids'] == ['home-one']
    assert cfg['landing_selected_egress_id'] == 'home-one'
    assert cfg['landing_vless_uuid']
    assert session_id not in ss.get_user_sessions()


def test_create_side_effect_order_is_locked_write_then_sessions_then_reloads(
    account_state,
    good_form,
    monkeypatch,
):
    calls = []
    held = False
    real_save_json = ss.save_json

    @contextmanager
    def tracked_lock():
        nonlocal held
        held = True
        calls.append('lock-enter')
        try:
            yield
        finally:
            held = False
            calls.append('lock-exit')

    def save_json(path, users):
        assert held
        calls.append('save')
        real_save_json(path, users)

    def sync(_users):
        assert held
        calls.append('sync')
        return True, True

    def delete(_username):
        assert not held
        calls.append('sessions')

    monkeypatch.setattr(ss, 'usage_lock', tracked_lock)
    monkeypatch.setattr(ss, 'save_json', save_json)
    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss, 'delete_user_sessions_for', delete)
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: calls.append('xray'))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: calls.append('tuic'))

    result = account_state['make_service']().create(form=good_form)

    assert result.outcome == 'created'
    assert calls == [
        'lock-enter',
        'save',
        'sync',
        'lock-exit',
        'sessions',
        'xray',
        'tuic',
    ]


def test_create_session_invalidation_failure_propagates_after_commit(
    account_state,
    good_form,
    monkeypatch,
):
    monkeypatch.setattr(
        ss,
        'delete_user_sessions_for',
        lambda _username: (_ for _ in ()).throw(OSError('create session boom')),
    )

    with pytest.raises(OSError, match='create session boom'):
        account_state['make_service']().create(form=good_form)

    saved = json.loads(account_state['USERS_FILE'].read_text())['fixture_new']
    assert saved['monthly_quota_bytes'] == 10 * 1024**3
    assert ss.verify_secret('fixture-panel-password', saved['panel_pass_hash'])


def test_create_without_passwords_omits_optional_hash_fields(account_state, good_form):
    form = {**good_form, 'panel_password': [''], 'password': ['']}

    result = account_state['make_service']().create(form=form)

    assert result.outcome == 'created'
    cfg = json.loads(account_state['USERS_FILE'].read_text())['fixture_new']
    assert 'panel_pass_hash' not in cfg
    assert 'panel_password_must_change' not in cfg
    assert 'password_hash' not in cfg


def test_create_rejects_existing_user_inside_lock_without_sync(
    account_state,
    good_form,
    monkeypatch,
):
    existing = {'fixture_new': {'sub_token': 'keep', 'disabled': True}}
    _write_json(account_state['USERS_FILE'], existing)
    form = {**good_form, 'token': ['posted-sensitive-token']}
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: pytest.fail('duplicate create must not sync'),
    )

    result = account_state['make_service']().create(form=form)

    assert result == account_mutation_service.AccountMutationResult(
        outcome='invalid',
        code='user_exists_use_reset_token',
        field_id='create-user',
        draft={
            'user': 'fixture_new',
            'quota_gb': '10',
            'quota_extra_gb': '2',
            'expires_at': '2027-04-05',
            'note': 'fixture note',
            'landing_initial_egress_id': '',
            'guest': True,
            'tuic_enabled': True,
        },
    )
    for secret in (
        'fixture-panel-password',
        ' fixture-connection-password ',
        'posted-sensitive-token',
    ):
        assert secret not in repr(result)
    assert json.loads(account_state['USERS_FILE'].read_text()) == existing


def test_create_rechecks_selected_egress_inside_lock(account_state, good_form, monkeypatch):
    form = {
        **good_form,
        'landing_initial_egress_id': ['vanished'],
        'token': ['posted-sensitive-token'],
    }
    monkeypatch.setattr(
        ss,
        '_landing_registry_or_empty',
        lambda: {'nodes': {'vanished': {'enabled': True}}},
    )
    monkeypatch.setattr(account_mutation_service.landing_egress, 'load_registry', lambda: {})

    result = account_state['make_service']().create(form=form)

    assert result == account_mutation_service.AccountMutationResult(
        outcome='invalid',
        code='家宽出口已不可用，请重新选择',
        field_id='create-landing-initial-egress',
        draft={
            'user': 'fixture_new',
            'quota_gb': '10',
            'quota_extra_gb': '2',
            'expires_at': '2027-04-05',
            'note': 'fixture note',
            'landing_initial_egress_id': 'vanished',
            'guest': True,
            'tuic_enabled': True,
        },
    )
    for secret in (
        'fixture-panel-password',
        ' fixture-connection-password ',
        'posted-sensitive-token',
    ):
        assert secret not in repr(result)
    assert json.loads(account_state['USERS_FILE'].read_text()) == {}


def test_create_rolls_back_exact_raw_users_text_and_resyncs_after_sync_failure(
    account_state,
    good_form,
    monkeypatch,
):
    raw = '{\n  "preserved": {"sub_token": "old"}\n}\n'
    account_state['USERS_FILE'].write_text(raw, encoding='utf-8')
    calls = []

    def fail_then_resync(users):
        calls.append(users)
        if len(calls) == 1:
            raise RuntimeError('sync boom')
        return False, False

    monkeypatch.setattr(ss, '_sync_static_access_from_users', fail_then_resync)
    with pytest.raises(RuntimeError, match='sync boom'):
        account_state['make_service']().create(form=good_form)

    assert account_state['USERS_FILE'].read_text(encoding='utf-8') == raw
    assert calls[1] == {'preserved': {'sub_token': 'old'}}


def test_create_resync_failure_reraises_original_sync_error(
    account_state,
    good_form,
    monkeypatch,
):
    calls = 0

    def fail_both(_users):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('original sync error')
        raise OSError('rollback resync error')

    monkeypatch.setattr(ss, '_sync_static_access_from_users', fail_both)
    with pytest.raises(RuntimeError, match='original sync error'):
        account_state['make_service']().create(form=good_form)
    assert calls == 2


def test_create_save_failure_propagates_without_sync(account_state, good_form, monkeypatch):
    monkeypatch.setattr(ss, 'save_json', lambda *_args: (_ for _ in ()).throw(OSError('save boom')))
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: pytest.fail('failed save must not sync'),
    )

    with pytest.raises(OSError, match='save boom'):
        account_state['make_service']().create(form=good_form)


def _existing_user():
    return {
        'sub_token': 'existing-token',
        'vless_uuid': 'f4fdbcea-e382-49d2-9adf-e72c6d27d0fb',
        'password': 'legacy-plaintext',
        'password_hash': 'old-proxy-hash',
        'panel_pass_hash': 'old-panel-hash',
        'max_devices': 7,
        'monthly_quota_bytes': 6 * 1024**3,
        'quota_extra_bytes': 2 * 1024**3,
        'expires_at': '2026-12-31',
        'note': 'old note',
        'landing_isp': 'old isp',
        'landing_region': 'old region',
        'landing_note': 'old landing note',
        'landing_ip': '192.0.2.1',
        'metered': True,
        'guest': True,
        'tuic_enabled': True,
        'disabled': True,
        'usage_history': {'2026-08': 99},
        'unrelated': {'keep': True},
    }


def _update_form(cfg, **overrides):
    form = {
        'user': ['alice'],
        'max_devices': ['5'],
        'quota_gb': ['8'],
        'quota_extra_gb': ['3'],
        'expires_at': [''],
        'note': [''],
        'landing_isp': [''],
        'landing_region': ['new region'],
        'landing_note': [''],
        'landing_ip': ['2001:0db8::1'],
    }
    form.update(overrides)
    return form, ss.user_config_revision(cfg)


@pytest.mark.parametrize(
    ('overrides', 'code'),
    [
        ({'max_devices': ['101']}, 'err:max_devices_invalid'),
        ({'quota_gb': ['-1']}, 'err:quota_invalid'),
        ({'quota_extra_gb': ['10241']}, 'err:quota_extra_invalid'),
        ({'expires_at': ['bad-date']}, 'err:expiry_invalid'),
        ({'note': ['x' * 201]}, 'err:note_too_long'),
        ({'landing_isp': ['<bad>']}, 'err:landing_invalid'),
        ({'landing_ip': ['not-an-ip']}, 'err:landing_ip_invalid'),
        ({'panel_password': ['short']}, 'err:panel_password_short'),
        ({'panel_password': ['x' * 257]}, 'err:panel_password_long'),
        ({'password': ['x' * 257]}, 'err:proxy_password_long'),
    ],
)
def test_update_validation_returns_exact_code_without_protected_writes(
    account_state,
    monkeypatch,
    overrides,
    code,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(cfg, **overrides)
    before = account_state['USERS_FILE'].read_bytes()
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: pytest.fail('invalid update must not sync'),
    )

    result = account_state['make_service']().update(
        form=form,
        expected_revision=revision,
    )

    assert (result.outcome, result.code, result.draft) == ('invalid', code, None)
    assert account_state['USERS_FILE'].read_bytes() == before


def test_update_not_found_and_conflict_preserve_state(account_state, monkeypatch):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users, **_kwargs: pytest.fail('rejected update must not sync'),
    )
    missing_form, revision = _update_form(cfg, user=['missing'])
    missing = account_state['make_service']().update(
        form=missing_form,
        expected_revision=revision,
    )
    form, _ = _update_form(cfg)
    conflict = account_state['make_service']().update(
        form=form,
        expected_revision='0' * 64,
    )

    assert missing == account_mutation_service.AccountMutationResult(
        outcome='not_found', username='missing'
    )
    assert conflict.outcome == 'conflict'
    assert conflict.username == 'alice'
    assert conflict.draft == {
        'user': 'alice',
        'max_devices': 5,
        'quota_gb': 8,
        'quota_extra_gb': 3,
        'expires_at': '',
        'note': '',
        'guest': '否',
        'tuic_enabled': '否',
    }
    assert json.loads(account_state['USERS_FILE'].read_text()) == {'alice': cfg}


def test_update_first_values_preserve_unrelated_state_and_replace_requested_fields(
    account_state,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(
        cfg,
        user=['alice', 'missing'],
        max_devices=['0', '101'],
        quota_gb=['9', '-1'],
        quota_extra_gb=['4', '10241'],
        panel_password=[' new-panel-password ', 'short'],
        password=[' new-proxy-password ', 'x' * 257],
        guest=[''],
        tuic_enabled=['false'],
    )

    result = account_state['make_service']().update(
        form=form,
        expected_revision=revision,
    )

    assert result == account_mutation_service.AccountMutationResult(
        outcome='updated', username='alice'
    )
    saved = json.loads(account_state['USERS_FILE'].read_text())['alice']
    assert saved['max_devices'] == 0
    assert saved['monthly_quota_bytes'] == 9 * 1024**3
    assert saved['quota_extra_bytes'] == 4 * 1024**3
    assert saved['metered'] is True and saved['guest'] is True
    assert saved['tuic_enabled'] is True
    assert saved['disabled'] is True
    assert saved['usage_history'] == {'2026-08': 99}
    assert saved['unrelated'] == {'keep': True}
    assert saved['sub_token'] == cfg['sub_token']
    assert saved['vless_uuid'] == cfg['vless_uuid']
    assert 'password' not in saved
    assert 'expires_at' not in saved
    assert 'note' not in saved
    assert 'landing_isp' not in saved
    assert saved['landing_region'] == 'new region'
    assert 'landing_note' not in saved
    assert saved['landing_ip'] == '2001:db8::1'
    assert saved['panel_password_must_change'] is True
    assert ss.verify_secret(' new-panel-password ', saved['panel_pass_hash'])
    assert ss.verify_secret('new-proxy-password', saved['password_hash'])


def test_update_generates_only_missing_subscription_credentials(account_state):
    cfg = _existing_user()
    cfg['sub_token'] = ''
    cfg['vless_uuid'] = '  '
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(cfg)

    result = account_state['make_service']().update(form=form, expected_revision=revision)

    saved = json.loads(account_state['USERS_FILE'].read_text())['alice']
    assert result.outcome == 'updated'
    assert saved['sub_token'] and saved['sub_token'] not in repr(result)
    assert saved['vless_uuid'].strip() and saved['vless_uuid'] not in repr(result)


def test_update_side_effect_order_is_locked_then_sessions_then_reloads(
    account_state,
    monkeypatch,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(cfg, panel_password=['new-panel-password'])
    calls = []
    held = False

    @contextmanager
    def tracked_lock():
        nonlocal held
        held = True
        calls.append('lock-enter')
        try:
            yield
        finally:
            held = False
            calls.append('lock-exit')

    def sync(_users):
        assert held
        calls.append('sync')
        return True, True

    def delete(_username):
        assert not held
        calls.append('sessions')

    monkeypatch.setattr(ss, 'usage_lock', tracked_lock)
    monkeypatch.setattr(ss, '_sync_static_access_from_users', sync)
    monkeypatch.setattr(ss, 'delete_user_sessions_for', delete)
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: calls.append('xray'))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: calls.append('tuic'))

    result = account_state['make_service']().update(form=form, expected_revision=revision)

    assert result.outcome == 'updated'
    assert calls == ['lock-enter', 'sync', 'lock-exit', 'sessions', 'xray', 'tuic']


def test_update_session_invalidation_failure_propagates_after_commit(
    account_state,
    monkeypatch,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(cfg, panel_password=['new-panel-password'])
    monkeypatch.setattr(
        ss,
        'delete_user_sessions_for',
        lambda _username: (_ for _ in ()).throw(OSError('session boom')),
    )

    with pytest.raises(OSError, match='session boom'):
        account_state['make_service']().update(form=form, expected_revision=revision)

    saved = json.loads(account_state['USERS_FILE'].read_text())['alice']
    assert ss.verify_secret('new-panel-password', saved['panel_pass_hash'])


def test_update_sync_failure_propagates_without_rolling_back_commit(
    account_state,
    monkeypatch,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(
        cfg,
        quota_gb=['11'],
        panel_password=['new-panel-password'],
    )
    calls = []
    monkeypatch.setattr(
        ss,
        '_sync_static_access_from_users',
        lambda _users: (_ for _ in ()).throw(RuntimeError('update sync boom')),
    )
    monkeypatch.setattr(ss, 'delete_user_sessions_for', lambda _username: calls.append('sessions'))
    monkeypatch.setattr(ss.xray_config, 'reload_async', lambda: calls.append('xray'))
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: calls.append('tuic'))

    with pytest.raises(RuntimeError, match='update sync boom'):
        account_state['make_service']().update(form=form, expected_revision=revision)

    saved = json.loads(account_state['USERS_FILE'].read_text())['alice']
    assert saved['monthly_quota_bytes'] == 11 * 1024**3
    assert ss.verify_secret('new-panel-password', saved['panel_pass_hash'])
    assert calls == []


def test_update_reload_failure_propagates_after_commit_and_session_invalidation(
    account_state,
    monkeypatch,
):
    cfg = _existing_user()
    _write_json(account_state['USERS_FILE'], {'alice': cfg})
    form, revision = _update_form(
        cfg,
        quota_extra_gb=['6'],
        panel_password=['new-panel-password'],
    )
    calls = []
    monkeypatch.setattr(ss, '_sync_static_access_from_users', lambda _users: (True, True))
    monkeypatch.setattr(ss, 'delete_user_sessions_for', lambda _username: calls.append('sessions'))

    def fail_xray_reload():
        calls.append('xray')
        raise RuntimeError('update reload boom')

    monkeypatch.setattr(ss.xray_config, 'reload_async', fail_xray_reload)
    monkeypatch.setattr(ss.tuic_config, 'reload_async', lambda: calls.append('tuic'))

    with pytest.raises(RuntimeError, match='update reload boom'):
        account_state['make_service']().update(form=form, expected_revision=revision)

    saved = json.loads(account_state['USERS_FILE'].read_text())['alice']
    assert saved['quota_extra_bytes'] == 6 * 1024**3
    assert ss.verify_secret('new-panel-password', saved['panel_pass_hash'])
    assert calls == ['sessions', 'xray']
