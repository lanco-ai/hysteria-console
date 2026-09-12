"""Authentication route ownership boundary."""

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import subscription_service as ss
from login_service import LoginResult
from password_change_service import PasswordChangeResult


def test_unknown_auth_route_does_not_access_credentials():
    module = importlib.import_module('auth_routes')
    assert (
        module.handle_write(object(), object(), path='/admin/rotate-token', form={}, meta={})
        is False
    )


class _LoginHandler:
    def __init__(self, *, cookie='', path='/login'):
        self.client_address = ('198.51.100.17', 12345)
        self.headers = {'Host': 'panel.test', 'Cookie': cookie}
        self.path = path
        self.response = None

    def send_response_body(
        self, status, body, content_type=None, is_html=False, extra_headers=None
    ):
        self.response = (
            status,
            body,
            content_type,
            is_html,
            extra_headers,
        )

    def redirect(self, target, *, cookie=None, status=302):
        self.response = (status, target, cookie)


@pytest.mark.parametrize(
    ('result', 'expected_status', 'expected_message', 'expected_tab'),
    [
        (LoginResult(outcome='invalid', username='admin'), 200, '用户名或密码错误', 'admin'),
        (LoginResult(outcome='missing'), 200, '请输入用户名和密码', 'admin'),
        (
            LoginResult(outcome='disabled', realm='user', username='alice'),
            200,
            '账号已停用，请联系管理员',
            'user',
        ),
        (
            LoginResult(outcome='expired', realm='user', username='alice'),
            200,
            '账号已到期，请联系管理员续费',
            'user',
        ),
        (
            LoginResult(
                outcome='throttled',
                realm='user',
                username='alice',
                retry_after=3600,
            ),
            429,
            '登录尝试过于频繁，请 1 小时后再试',
            'user',
        ),
    ],
)
def test_login_adapter_calls_injected_service_once_and_maps_failure(
    result, expected_status, expected_message, expected_tab
):
    module = importlib.import_module('auth_routes')
    calls = []
    rendered = []

    def authenticate_login(**kwargs):
        calls.append(kwargs)
        return result

    def render_login(host, **kwargs):
        rendered.append((host, kwargs))
        return b'rendered-login'

    context = SimpleNamespace(
        authenticate_login=authenticate_login,
        configured_public_host=lambda host: host,
        is_secure_request=lambda _handler: False,
        render_login=render_login,
        session_cookie=lambda sid, *, secure: f'sid={sid}; secure={secure}',
        user_session_cookie=lambda sid, *, secure: f'usid={sid}; secure={secure}',
    )
    handler = _LoginHandler()
    form = {'user_username': ['alice'], 'user_password': ['secret']}
    meta = {'admin_user': 'admin'}

    assert module.handle_write(handler, context, path='/login', form=form, meta=meta)

    assert calls == [{'form': form, 'meta': meta, 'client_ip': '198.51.100.17'}]
    assert rendered == [
        (
            'panel.test',
            {
                'msg': expected_message,
                'active_tab': expected_tab,
                'username': result.username,
            },
        )
    ]
    assert handler.response[:4] == (
        expected_status,
        b'rendered-login',
        'text/html; charset=utf-8',
        True,
    )
    expected_headers = {'Retry-After': '3600'} if result.outcome == 'throttled' else None
    assert handler.response[4] == expected_headers


def _write_json(path, value):
    Path(path).write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def real_password_state(tmp_path, monkeypatch):
    paths = {
        'META_FILE': tmp_path / 'meta.json',
        'USERS_FILE': tmp_path / 'users.json',
        'SESSIONS_FILE': tmp_path / 'sessions.json',
        'USER_SESSIONS_FILE': tmp_path / 'user_sessions.json',
        'USAGE_LOCK_FILE': tmp_path / 'usage.lock',
    }
    for name, path in paths.items():
        monkeypatch.setattr(ss, name, path)
    admin_hash = ss.hash_secret('old-admin-password')
    alice_hash = ss.hash_secret('old-alice-password')
    bob_hash = ss.hash_secret('old-bob-password')
    _write_json(
        paths['META_FILE'],
        {
            'admin_user': 'admin',
            'admin_pass_hash': admin_hash,
            'admin_token': 'admin-subscription-token',
            'unrelated': {'preserved': True},
        },
    )
    _write_json(
        paths['USERS_FILE'],
        {
            'alice': {
                'panel_pass_hash': alice_hash,
                'panel_password_must_change': True,
                'sub_token': 'alice-subscription-token',
                'monthly_quota_bytes': 1234,
                'max_devices': 3,
                'proxy': {'host': 'private.example'},
            },
            'bob': {
                'panel_pass_hash': bob_hash,
                'sub_token': 'bob-subscription-token',
                'monthly_quota_bytes': 5678,
            },
        },
    )
    _write_json(paths['SESSIONS_FILE'], {})
    _write_json(paths['USER_SESSIONS_FILE'], {})
    return {
        **paths,
        'admin_hash': admin_hash,
        'alice_hash': alice_hash,
        'bob_hash': bob_hash,
    }


def _run_real_password_handler(path, form, *, cookie):
    module = importlib.import_module('auth_routes')
    handler = _LoginHandler(cookie=cookie, path=path)
    assert module.handle_write(
        handler,
        ss._auth_routes_context(),
        path=path,
        form=form,
        meta=ss.load_meta(),
    )
    return handler


def _cookie_value(raw_cookie, name):
    return raw_cookie.split(f'{name}=', 1)[1].split(';', 1)[0]


def test_real_legacy_admin_change_persists_hash_and_replaces_every_admin_session(
    real_password_state,
):
    old_sid = ss.create_session(
        'admin', ss._credential_generation(real_password_state['admin_hash'])
    )
    second_sid = ss.create_session(
        'admin', ss._credential_generation(real_password_state['admin_hash'])
    )
    user_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(real_password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    handler = _run_real_password_handler(
        '/admin/change-password',
        {
            'current': ['old-admin-password'],
            'new': ['new-admin-password'],
            'confirm': ['new-admin-password'],
        },
        cookie=f'sid={old_sid}',
    )

    assert handler.response[0:2] == (302, '/admin/settings?msg=password+changed')
    new_sid = _cookie_value(handler.response[2], 'sid')
    meta = json.loads(real_password_state['META_FILE'].read_text(encoding='utf-8'))
    assert ss.verify_secret('new-admin-password', meta['admin_pass_hash'])
    assert meta['admin_token'] == 'admin-subscription-token'
    assert meta['unrelated'] == {'preserved': True}
    sessions = ss.get_sessions()
    assert old_sid not in sessions
    assert second_sid not in sessions
    assert sessions[new_sid]['credential_generation'] == ss._credential_generation(
        meta['admin_pass_hash']
    )
    assert user_sid in ss.get_user_sessions()


def test_real_legacy_admin_change_checks_current_password_before_new_password_length(
    real_password_state,
):
    sid = ss.create_session('admin', ss._credential_generation(real_password_state['admin_hash']))
    before_meta = real_password_state['META_FILE'].read_bytes()
    before_sessions = real_password_state['SESSIONS_FILE'].read_bytes()

    handler = _run_real_password_handler(
        '/admin/change-password',
        {'current': ['wrong'], 'new': ['short'], 'confirm': ['different']},
        cookie=f'sid={sid}',
    )

    assert handler.response == (302, '/admin/settings?msg=err:password_wrong', None)
    assert real_password_state['META_FILE'].read_bytes() == before_meta
    assert real_password_state['SESSIONS_FILE'].read_bytes() == before_sessions


def test_real_legacy_user_change_allows_must_change_and_replaces_only_that_users_sessions(
    real_password_state,
):
    admin_sid = ss.create_session(
        'admin', ss._credential_generation(real_password_state['admin_hash'])
    )
    alice_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(real_password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    alice_second = ss.create_user_session(
        'alice',
        ss._credential_generation(real_password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    bob_sid = ss.create_user_session(
        'bob',
        ss._credential_generation(real_password_state['bob_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )

    handler = _run_real_password_handler(
        '/user/change-password',
        {
            'current': ['old-alice-password'],
            'new': ['new-alice-password'],
            'confirm': ['new-alice-password'],
        },
        cookie=f'usid={alice_sid}',
    )

    assert handler.response[0:2] == (302, '/user/panel')
    new_sid = _cookie_value(handler.response[2], 'usid')
    users = json.loads(real_password_state['USERS_FILE'].read_text(encoding='utf-8'))
    alice = users['alice']
    assert ss.verify_secret('new-alice-password', alice['panel_pass_hash'])
    assert 'panel_password_must_change' not in alice
    assert alice['sub_token'] == 'alice-subscription-token'
    assert alice['monthly_quota_bytes'] == 1234
    assert alice['max_devices'] == 3
    assert alice['proxy'] == {'host': 'private.example'}
    assert users['bob']['panel_pass_hash'] == real_password_state['bob_hash']
    sessions = ss.get_user_sessions()
    assert alice_sid not in sessions
    assert alice_second not in sessions
    assert bob_sid in sessions
    assert sessions[new_sid]['credential_kind'] == ss.USER_SESSION_PANEL_PASSWORD
    assert sessions[new_sid]['credential_generation'] == ss._credential_generation(
        alice['panel_pass_hash']
    )
    assert admin_sid in ss.get_sessions()


def test_real_legacy_admin_change_never_accepts_a_user_cookie(real_password_state):
    user_sid = ss.create_user_session(
        'alice',
        ss._credential_generation(real_password_state['alice_hash']),
        ss.USER_SESSION_PANEL_PASSWORD,
    )
    before_meta = real_password_state['META_FILE'].read_bytes()

    handler = _run_real_password_handler(
        '/admin/change-password',
        {
            'current': ['old-admin-password'],
            'new': ['new-admin-password'],
            'confirm': ['new-admin-password'],
        },
        cookie=f'usid={user_sid}',
    )

    assert handler.response == (302, '/login', None)
    assert real_password_state['META_FILE'].read_bytes() == before_meta
    assert user_sid in ss.get_user_sessions()


@pytest.mark.parametrize(
    ('result', 'expected'),
    [
        (PasswordChangeResult(outcome='login_required'), (302, '/login', None)),
        (
            PasswordChangeResult(outcome='forbidden'),
            (302, '/login', 'usid=; secure=False'),
        ),
        (PasswordChangeResult(outcome='disabled'), (302, '/user/panel', None)),
        (PasswordChangeResult(outcome='expired'), (302, '/user/panel', None)),
        (
            PasswordChangeResult(outcome='invalid', code='current password wrong'),
            (302, '/user/change-password?msg=current+password+wrong', None),
        ),
        (
            PasswordChangeResult(outcome='success', session_id='new-user-session'),
            (302, '/user/panel', 'usid=new-user-session; secure=False'),
        ),
    ],
)
def test_legacy_user_password_adapter_preserves_redirect_and_cookie_presentation(
    result,
    expected,
):
    module = importlib.import_module('auth_routes')
    calls = []
    context = SimpleNamespace(
        change_user_password=lambda **kwargs: calls.append(kwargs) or result,
        clear_user_session_cookie=lambda *, secure: f'usid=; secure={secure}',
        get_logged_in_user_context=lambda _handler: ('alice', 'panel_password'),
        is_secure_request=lambda _handler: False,
        user_session_cookie=lambda sid, *, secure: f'usid={sid}; secure={secure}',
    )
    handler = _LoginHandler(path='/user/change-password')
    form = {'current': ['private-current']}

    assert module.handle_write(
        handler,
        context,
        path='/user/change-password',
        form=form,
        meta={},
    )

    assert handler.response == expected
    assert calls == [
        {
            'username': 'alice',
            'session_kind': 'panel_password',
            'form': form,
        }
    ]


@pytest.mark.parametrize(
    ('result', 'expected'),
    [
        (
            PasswordChangeResult(outcome='invalid', code='password_mismatch'),
            (302, '/admin/settings?msg=err:password_mismatch', None),
        ),
        (
            PasswordChangeResult(outcome='success', session_id='new-admin-session'),
            (
                302,
                '/admin/settings?msg=password+changed',
                'sid=new-admin-session; secure=False',
            ),
        ),
    ],
)
def test_legacy_admin_password_adapter_preserves_redirect_and_cookie_presentation(
    result,
    expected,
):
    module = importlib.import_module('auth_routes')
    calls = []
    context = SimpleNamespace(
        change_admin_password=lambda **kwargs: calls.append(kwargs) or result,
        is_logged_in=lambda _handler: True,
        is_secure_request=lambda _handler: False,
        session_cookie=lambda sid, *, secure: f'sid={sid}; secure={secure}',
    )
    handler = _LoginHandler(path='/admin/change-password')
    form = {'current': ['private-current']}

    assert module.handle_write(
        handler,
        context,
        path='/admin/change-password',
        form=form,
        meta={},
    )

    assert handler.response == expected
    assert calls == [{'form': form}]
