"""Preserve login form contracts and escaping across script extraction."""

import hashlib
import html
import importlib
from html.parser import HTMLParser

import pytest


@pytest.mark.parametrize(
    ('outcome', 'realm', 'expected'),
    [
        ('invalid', 'admin', '用户名或密码错误'),
        ('missing', 'admin', '请输入用户名和密码'),
        ('throttled', 'admin', '登录尝试过于频繁，请 1 小时后再试'),
        ('disabled', 'admin', '账号已停用，请联系管理员'),
        ('expired', 'admin', '账号已到期，请联系管理员续费'),
        ('invalid', 'user', '请使用管理员账号登录控制台。'),
        ('disabled', 'user', '请使用管理员账号登录控制台。'),
    ],
)
def test_login_feedback_message_preserves_admin_copy_and_neutralizes_user_failures(
    outcome,
    realm,
    expected,
):
    views = importlib.import_module('auth_views')

    assert views.login_feedback_message(outcome, realm=realm) == expected


CASES = [
    {
        'name': 'render_login',
        'kwargs': {},
        'hash': 'b90ebe7ee444620c4494f4aab79b3e9407e20f34f69898feea53b30559c6ea44',
    },
    {
        'name': 'render_login',
        'kwargs': {'msg': '<bad>', 'username': 'a" autofocus="x'},
        'hash': 'b1fcb5123233490b53d0cf67dd21137043e14bec957b147df443807434e3e2ea',
    },
    {
        'name': 'render_login',
        'kwargs': {'active_tab': 'user', 'msg': 'private error', 'username': 'alice'},
        'hash': '2569269639fb886ba338b21f35fe7a4ee55a4f49a74feceb63c3a8fa4b585da4',
    },
    {
        'name': 'render_user_login',
        'kwargs': {'msg': '<bad>', 'username': 'a" autofocus="x'},
        'hash': 'cfcabb94f75c94008ffbdfb934e1140c38deb63764450ccf7141bcfc5b09a25c',
    },
]


@pytest.mark.parametrize('case', CASES)
def test_login_body_matches_pre_extraction_baseline(case):
    views = importlib.import_module('auth_views')
    dependencies = dict(
        html_page=lambda title, body, **kwargs: body,
        render_alert=lambda message, kind: '<p>' + html.escape(message) + '</p>',
        password_max_length=256,
    )
    if case['name'] == 'render_login':
        dependencies['icon'] = lambda name: '<i></i>'
    body = getattr(views, case['name'])(**dependencies, **case['kwargs'])
    if case['name'] == 'render_user_login':
        assert hashlib.sha256(body.encode()).hexdigest() == case['hash']
        return

    class Fields(HTMLParser):
        def __init__(self):
            super().__init__()
            self.inputs = {}
            self.forms = []
            self.scripts = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == 'input':
                self.inputs[attributes['name']] = attributes
            elif tag == 'form':
                self.forms.append(attributes)
            elif tag == 'script':
                self.scripts.append(attributes)

    fields = Fields()
    fields.feed(body)
    assert fields.forms[0]['action'] == '/login'
    assert fields.forms[0]['method'] == 'post'
    assert fields.inputs['admin_password']['type'] == 'password'
    expected = (
        case['kwargs'].get('username', '')
        if case['kwargs'].get('active_tab', 'admin') == 'admin'
        else ''
    )
    assert fields.inputs['admin_username']['value'] == expected
    assert 'autofocus' not in fields.inputs['admin_username']
    assert len(fields.scripts) == 1
    assert fields.scripts[0]['src'].startswith('/static/login.js?v=')
    assert 'private error' not in body
