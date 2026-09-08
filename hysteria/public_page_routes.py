"""public page routes; existing response and authorization contracts."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    get_logged_in_user: Callable[..., object]
    is_logged_in: Callable[..., object]
    render_home: Callable[..., object]
    render_login: Callable[..., object]
    render_logout_confirmation: Callable[..., object]


def _home(handler, ctx, path, q, host, base_url, send_payload):
    handler.send_response_body(200, ctx.render_home(host), 'text/html; charset=utf-8', send_payload)
    return


def _login(handler, ctx, path, q, host, base_url, send_payload):
    handler.send_response_body(
        200, ctx.render_login(host), 'text/html; charset=utf-8', send_payload
    )
    return


def _legacy_login(handler, ctx, path, q, host, base_url, send_payload):
    handler.redirect('/login', status=303)
    return


def _logout(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    handler.send_response_body(
        200,
        ctx.render_logout_confirmation(host),
        'text/html; charset=utf-8',
        send_payload,
    )
    return


def _user_logout(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.get_logged_in_user(handler):
        handler.redirect('/login')
        return
    handler.send_response_body(
        200,
        ctx.render_logout_confirmation(host, user_panel=True),
        'text/html; charset=utf-8',
        send_payload,
    )
    return


def handle_read(
    handler,
    context: Context,
    *,
    path: str,
    query: Mapping[str, list[str]],
    host: str,
    base_url: str,
    send_payload: bool,
) -> bool:
    if path == '/':
        route = _home
    elif path == '/login':
        route = _login
    elif path == '/user/login':
        route = _legacy_login
    elif path == '/logout':
        route = _logout
    elif path == '/user/logout':
        route = _user_logout
    else:
        return False
    route(handler, context, path, query, host, base_url, send_payload)
    return True
