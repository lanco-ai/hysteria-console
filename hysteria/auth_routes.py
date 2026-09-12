"""Login, logout and password-change HTTP flows with explicit service dependencies."""

from dataclasses import dataclass
from typing import Callable, Mapping
from urllib.parse import urlencode

import auth_views
import http_utils


@dataclass(frozen=True)
class Context:
    authenticate_login: Callable[..., object]
    change_admin_password: Callable[..., object]
    change_user_password: Callable[..., object]
    clear_session_cookie: Callable[..., object]
    clear_user_session_cookie: Callable[..., object]
    configured_public_host: Callable[..., object]
    delete_session: Callable[..., object]
    delete_user_session: Callable[..., object]
    get_logged_in_user_context: Callable[..., object]
    is_logged_in: Callable[..., object]
    is_secure_request: Callable[..., object]
    parse_cookies: Callable[..., object]
    render_login: Callable[..., object]
    session_cookie: Callable[..., object]
    user_session_cookie: Callable[..., object]


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
    result = ctx.authenticate_login(form=form, meta=meta, client_ip=ip)
    if result.outcome == 'success':
        cookie_helper = ctx.user_session_cookie if result.realm == 'user' else ctx.session_cookie
        handler.redirect(
            result.redirect_to,
            cookie=cookie_helper(
                result.session_id,
                secure=ctx.is_secure_request(handler),
            ),
        )
        return

    status = 429 if result.outcome == 'throttled' else 200
    extra_headers = (
        {'Retry-After': str(result.retry_after)} if result.outcome == 'throttled' else None
    )
    handler.send_response_body(
        status,
        ctx.render_login(
            host,
            msg=auth_views.login_feedback_message(result.outcome),
            active_tab=result.realm,
            username=result.username,
        ),
        'text/html; charset=utf-8',
        True,
        extra_headers=extra_headers,
    )
    return


def _change_user_password(handler, ctx, form, meta):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    result = ctx.change_user_password(
        username=user,
        session_kind=session_kind,
        form=form,
    )
    if result.outcome == 'login_required':
        handler.redirect('/login')
        return
    if result.outcome == 'forbidden':
        handler.redirect(
            '/login',
            cookie=ctx.clear_user_session_cookie(
                secure=ctx.is_secure_request(handler),
            ),
        )
        return
    if result.outcome in ('disabled', 'expired'):
        handler.redirect('/user/panel')
        return
    if result.outcome == 'invalid':
        handler.redirect('/user/change-password?' + urlencode({'msg': result.code}))
        return
    handler.redirect(
        '/user/panel',
        cookie=ctx.user_session_cookie(
            result.session_id,
            secure=ctx.is_secure_request(handler),
        ),
    )
    return


def _change_admin_password(handler, ctx, form, meta):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    result = ctx.change_admin_password(form=form)
    if result.outcome == 'invalid':
        handler.redirect(f'/admin/settings?msg=err:{result.code}')
        return
    handler.redirect(
        '/admin/settings?msg=password+changed',
        cookie=ctx.session_cookie(
            result.session_id,
            secure=ctx.is_secure_request(handler),
        ),
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
