"""Login, logout and password-change HTTP flows with explicit service dependencies."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlencode

import http_utils
import user_compat


@dataclass(frozen=True)
class Context:
    PASSWORD_MAX_LENGTH: int
    PASSWORD_MIN_LENGTH: int
    SESSIONS_FILE: Path
    USERS_FILE: Path
    USER_SESSIONS_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    _LOGIN_WINDOW: int
    _begin_login_attempt: Callable[..., object]
    _change_admin_password: Callable[..., object]
    _credential_generation: Callable[..., object]
    _finish_login_attempt: Callable[..., object]
    _replace_sessions_with_new: Callable[..., object]
    _user_login_failures: dict
    clear_session_cookie: Callable[..., object]
    clear_user_session_cookie: Callable[..., object]
    configured_public_host: Callable[..., object]
    create_session: Callable[..., object]
    create_user_session: Callable[..., object]
    delete_session: Callable[..., object]
    delete_user_session: Callable[..., object]
    get_logged_in_user_context: Callable[..., object]
    hash_secret: Callable[..., object]
    is_logged_in: Callable[..., object]
    is_secure_request: Callable[..., object]
    is_valid_username: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    parse_cookies: Callable[..., object]
    render_login: Callable[..., object]
    save_json: Callable[..., object]
    session_cookie: Callable[..., object]
    usage_lock: Callable[..., object]
    user_panel_access_error: Callable[..., object]
    user_session_cookie: Callable[..., object]
    verify_secret: Callable[..., object]


def _logout_admin(handler, ctx, form, meta):
    sid = ctx.parse_cookies(handler).get('sid', '')
    ctx.delete_session(sid)
    handler.redirect(
        '/login',
        cookie=ctx.clear_session_cookie(secure=ctx.is_secure_request(handler)),
        status=303,
    )
    return


def _logout_user(handler, ctx, form, meta):
    sid = ctx.parse_cookies(handler).get('usid', '')
    ctx.delete_user_session(sid)
    handler.redirect(
        '/login',
        cookie=ctx.clear_user_session_cookie(secure=ctx.is_secure_request(handler)),
        status=303,
    )
    return


def _login(handler, ctx, form, meta):
    ip = http_utils.request_client_ip(handler)
    host = ctx.configured_public_host(
        handler.headers.get('Host', '127.0.0.1'),
    )

    # Determine which tab was submitted
    admin_username = (form.get('admin_username') or [''])[0].strip()
    admin_password = (form.get('admin_password') or [''])[0]
    user_username = (form.get('user_username') or [''])[0].strip()
    user_password = (form.get('user_password') or [''])[0]

    if admin_username:
        # Admin login
        if not ctx._begin_login_attempt(ip):
            handler.send_response_body(
                429,
                ctx.render_login(
                    host,
                    msg='登录尝试过于频繁，请 1 小时后再试',
                    active_tab='admin',
                    username=admin_username,
                ),
                'text/html; charset=utf-8',
                True,
                extra_headers={'Retry-After': str(ctx._LOGIN_WINDOW)},
            )
            return
        stored_hash = str(meta.get('admin_pass_hash') or '')
        ok = (
            admin_username == meta.get('admin_user')
            and len(admin_password) <= ctx.PASSWORD_MAX_LENGTH
            and stored_hash
            and ctx.verify_secret(admin_password, stored_hash)
        )
        ctx._finish_login_attempt(ip, ok)
        if ok:
            sid = ctx.create_session(
                'admin',
                ctx._credential_generation(stored_hash),
            )
            handler.redirect(
                '/admin?msg=login+success',
                cookie=ctx.session_cookie(sid, secure=ctx.is_secure_request(handler)),
            )
            return
        handler.send_response_body(
            200,
            ctx.render_login(
                host, msg='用户名或密码错误', active_tab='admin', username=admin_username
            ),
            'text/html; charset=utf-8',
            True,
        )
        return

    if user_username:
        # User login
        if not ctx._begin_login_attempt(ip, ctx._user_login_failures):
            handler.send_response_body(
                429,
                ctx.render_login(
                    host,
                    msg='登录尝试过于频繁，请 1 小时后再试',
                    active_tab='user',
                    username=user_username,
                ),
                'text/html; charset=utf-8',
                True,
                extra_headers={'Retry-After': str(ctx._LOGIN_WINDOW)},
            )
            return
        cfg = ctx.load_json(ctx.USERS_FILE, {}).get(user_username)
        stored_hash = str(cfg.get('panel_pass_hash') or '') if isinstance(cfg, dict) else ''
        ok = bool(
            ctx.is_valid_username(user_username)
            and len(user_password) <= ctx.PASSWORD_MAX_LENGTH
            and stored_hash
            and ctx.verify_secret(user_password, stored_hash)
        )
        if ok and cfg.get('disabled'):
            ctx._finish_login_attempt(ip, None, ctx._user_login_failures)
            handler.send_response_body(
                200,
                ctx.render_login(
                    host, msg='账号已停用，请联系管理员', active_tab='user', username=user_username
                ),
                'text/html; charset=utf-8',
                True,
            )
            return
        if ok and user_compat.is_expired(cfg, today=ctx.local_now().date()):
            ctx._finish_login_attempt(ip, None, ctx._user_login_failures)
            handler.send_response_body(
                200,
                ctx.render_login(
                    host,
                    msg='账号已到期，请联系管理员续费',
                    active_tab='user',
                    username=user_username,
                ),
                'text/html; charset=utf-8',
                True,
            )
            return
        ctx._finish_login_attempt(ip, ok, ctx._user_login_failures)
        if ok:
            sid = ctx.create_user_session(
                user_username,
                ctx._credential_generation(stored_hash),
            )
            target = (
                '/user/change-password' if cfg.get('panel_password_must_change') else '/user/panel'
            )
            handler.redirect(
                target, cookie=ctx.user_session_cookie(sid, secure=ctx.is_secure_request(handler))
            )
            return
        handler.send_response_body(
            200,
            ctx.render_login(
                host, msg='用户名或密码错误', active_tab='user', username=user_username
            ),
            'text/html; charset=utf-8',
            True,
        )
        return

    # No credentials provided
    handler.send_response_body(
        200,
        ctx.render_login(host, msg='请输入用户名和密码'),
        'text/html; charset=utf-8',
        True,
    )
    return


def _change_user_password(handler, ctx, form, meta):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    if not user or session_kind != ctx.USER_SESSION_PANEL_PASSWORD:
        handler.redirect('/login')
        return
    current_cfg = ctx.load_json(ctx.USERS_FILE, {}).get(user)
    access_error = ctx.user_panel_access_error(
        current_cfg,
        session_kind,
        today=ctx.local_now().date(),
    )
    if access_error == 'forbidden':
        handler.redirect(
            '/login',
            cookie=ctx.clear_user_session_cookie(
                secure=ctx.is_secure_request(handler),
            ),
        )
        return
    if access_error in ('disabled', 'expired'):
        handler.redirect('/user/panel')
        return
    current = (form.get('current') or [''])[0]
    new = (form.get('new') or [''])[0]
    confirm = (form.get('confirm') or [''])[0]
    if len(new) < ctx.PASSWORD_MIN_LENGTH:
        handler.redirect('/user/change-password?' + urlencode({'msg': 'new password short'}))
        return
    if len(new) > ctx.PASSWORD_MAX_LENGTH:
        handler.redirect('/user/change-password?' + urlencode({'msg': 'new password long'}))
        return
    if new != confirm:
        handler.redirect('/user/change-password?' + urlencode({'msg': 'new password mismatch'}))
        return
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        cfg = users.get(user)
        locked_access_error = ctx.user_panel_access_error(
            cfg,
            session_kind,
            today=ctx.local_now().date(),
        )
        if locked_access_error == 'forbidden':
            handler.redirect(
                '/login',
                cookie=ctx.clear_user_session_cookie(
                    secure=ctx.is_secure_request(handler),
                ),
            )
            return
        if locked_access_error in ('disabled', 'expired'):
            handler.redirect('/user/panel')
            return
        stored_hash = str(cfg.get('panel_pass_hash') or '') if isinstance(cfg, dict) else ''
        if not (
            len(current) <= ctx.PASSWORD_MAX_LENGTH
            and stored_hash
            and ctx.verify_secret(current, stored_hash)
        ):
            handler.redirect(
                '/user/change-password?' + urlencode({'msg': 'current password wrong'})
            )
            return
        if ctx.verify_secret(new, stored_hash):
            handler.redirect('/user/change-password?' + urlencode({'msg': 'new password same'}))
            return
        new_hash = ctx.hash_secret(new)
        cfg['panel_pass_hash'] = new_hash
        cfg.pop('panel_password_must_change', None)
        users[user] = cfg
        ctx.save_json(ctx.USERS_FILE, users)
    sid = ctx._replace_sessions_with_new(
        ctx.USER_SESSIONS_FILE,
        user,
        credential_generation=ctx._credential_generation(new_hash),
        credential_kind=ctx.USER_SESSION_PANEL_PASSWORD,
    )
    handler.redirect(
        '/user/panel', cookie=ctx.user_session_cookie(sid, secure=ctx.is_secure_request(handler))
    )
    return


def _change_admin_password(handler, ctx, form, meta):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    current = (form.get('current') or [''])[0]
    new = (form.get('new') or [''])[0]
    confirm = (form.get('confirm') or [''])[0]
    result, new_hash = ctx._change_admin_password(current, new, confirm)
    if result != 'ok':
        handler.redirect(f'/admin/settings?msg=err:{result}')
        return
    # Revoke ALL existing admin sessions (a stolen sid is now dead),
    # then mint a fresh session for this device so the admin stays
    # logged in here. Mirrors the /login success cookie pattern.
    sid = ctx._replace_sessions_with_new(
        ctx.SESSIONS_FILE,
        'admin',
        revoke_all=True,
        credential_generation=ctx._credential_generation(new_hash),
    )
    handler.redirect(
        '/admin/settings?msg=password+changed',
        cookie=ctx.session_cookie(sid, secure=ctx.is_secure_request(handler)),
    )
    return


_ROUTES = {
    '/logout': _logout_admin,
    '/user/logout': _logout_user,
    '/login': _login,
    '/user/change-password': _change_user_password,
    '/admin/change-password': _change_admin_password,
}


def handle_write(
    handler, context: Context, *, path: str, form: Mapping[str, list[str]], meta: dict
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form, meta)
    return True
