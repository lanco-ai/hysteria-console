"""Authentication route ownership boundary."""

import importlib
from types import SimpleNamespace

import pytest
from login_service import LoginResult


def test_unknown_auth_route_does_not_access_credentials():
    module = importlib.import_module('auth_routes')
    assert (
        module.handle_write(object(), object(), path='/admin/rotate-token', form={}, meta={})
        is False
    )


class _LoginHandler:
    def __init__(self):
        self.client_address = ('198.51.100.17', 12345)
        self.headers = {'Host': 'panel.test'}
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
