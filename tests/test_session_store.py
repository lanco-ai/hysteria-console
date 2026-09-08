"""Session transactions use explicit storage and policy dependencies."""

import importlib.util
import json
import secrets
import time

import state_store


def test_session_store_can_be_imported_independently():
    assert importlib.util.find_spec('session_store') is not None


def test_replace_revokes_only_target_identity(tmp_path):
    from session_store import SessionStore

    path = tmp_path / 'sessions.json'
    store = SessionStore(
        state_store.load_json,
        state_store.save_json,
        state_store.file_lock,
        time.time,
        secrets.token_urlsafe,
        15.0,
        3600,
        2,
        10,
    )
    first = store._create_session(path, 'alice')
    other = store._create_session(path, 'bob')
    replacement = store._replace_sessions_with_new(
        path, 'alice', credential_generation='new', credential_kind='password'
    )
    sessions = json.loads(path.read_text())
    assert set(sessions) == {other, replacement}
    assert first not in sessions
    assert sessions[replacement]['credential_generation'] == 'new'
    assert sessions[replacement]['credential_kind'] == 'password'


def test_session_limit_and_expired_cleanup(tmp_path):
    from session_store import SessionStore

    path = tmp_path / 'sessions.json'
    store = SessionStore(
        state_store.load_json,
        state_store.save_json,
        state_store.file_lock,
        lambda: 100,
        secrets.token_urlsafe,
        15.0,
        3600,
        2,
        10,
    )
    state_store.save_json(path, {'expired': {'user': 'alice', 'exp': 99}})
    for _ in range(4):
        store._create_session(path, 'alice')
    sessions = store._get_sessions(path)
    assert len(sessions) == 2
    assert 'expired' not in sessions
    assert all(info['exp'] == 3700 for info in sessions.values())
    store._delete_sessions_for(path, 'alice')
    assert store._get_sessions(path) == {}
