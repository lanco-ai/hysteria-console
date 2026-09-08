"""user panel routes; existing response and authorization contracts."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    USERS_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    _build_panel_json_payload: Callable[..., object]
    clear_user_session_cookie: Callable[..., object]
    get_logged_in_user_context: Callable[..., object]
    is_secure_request: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    render_panel_link_required: Callable[..., object]
    render_user_change_password: Callable[..., object]
    render_user_panel: Callable[..., object]
    user_panel_access_error: Callable[..., object]


def _password(handler, ctx, path, q, host, base_url, send_payload):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    if not user or session_kind != ctx.USER_SESSION_PANEL_PASSWORD:
        handler.redirect('/login')
        return
    cfg = ctx.load_json(ctx.USERS_FILE, {}).get(user)
    if not isinstance(cfg, dict):
        handler.redirect(
            '/login', cookie=ctx.clear_user_session_cookie(secure=ctx.is_secure_request(handler))
        )
        return
    access_error = ctx.user_panel_access_error(
        cfg,
        session_kind,
        today=ctx.local_now().date(),
    )
    if access_error in ('disabled', 'expired'):
        handler.redirect('/user/panel')
        return
    msg = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200,
        ctx.render_user_change_password(host, user, msg=msg),
        'text/html; charset=utf-8',
        send_payload,
    )
    return


def _json(handler, ctx, path, q, host, base_url, send_payload):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    if not user:
        handler.send_response_body(
            401, '{"error":"login_required"}', 'application/json; charset=utf-8', send_payload
        )
        return
    cfg = ctx.load_json(ctx.USERS_FILE, {}).get(user)
    access_error = ctx.user_panel_access_error(
        cfg,
        session_kind,
        today=ctx.local_now().date(),
    )
    if access_error:
        handler.send_response_body(
            403,
            json.dumps(
                {'error': access_error},
                ensure_ascii=True,
                separators=(',', ':'),
            ),
            'application/json; charset=utf-8',
            send_payload,
        )
        return
    payload = ctx._build_panel_json_payload(user, cfg, now=ctx.local_now())
    handler.send_response_body(
        200, json.dumps(payload), 'application/json; charset=utf-8', send_payload
    )
    return


def _panel(handler, ctx, path, q, host, base_url, send_payload):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    if not user:
        handler.send_response_body(
            403,
            ctx.render_panel_link_required(),
            'text/html; charset=utf-8',
            send_payload,
        )
        return
    cfg = ctx.load_json(ctx.USERS_FILE, {}).get(user)
    if not isinstance(cfg, dict):
        handler.redirect(
            '/login', cookie=ctx.clear_user_session_cookie(secure=ctx.is_secure_request(handler))
        )
        return
    access_error = ctx.user_panel_access_error(
        cfg,
        session_kind,
        today=ctx.local_now().date(),
    )
    if access_error == 'password_change_required':
        handler.redirect('/user/change-password')
        return
    # Disabled/expired users receive a helpful status-only page with an
    # authorization status.  Never pass their bearer into the renderer:
    # this makes the no-secret property explicit even if the template is
    # extended later.
    inactive = access_error in ('disabled', 'expired')
    token = '' if inactive else str(cfg.get('sub_token') or '')
    handler.send_response_body(
        403 if inactive else 200,
        ctx.render_user_panel(
            host,
            base_url,
            user,
            token,
            cfg,
            session_auth=True,
            session_kind=session_kind,
            notice=(q.get('msg') or [''])[0],
        ),
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
    if path == '/user/change-password':
        route = _password
    elif path == '/user/panel.json':
        route = _json
    elif path == '/user/panel':
        route = _panel
    else:
        return False
    route(handler, context, path, query, host, base_url, send_payload)
    return True
