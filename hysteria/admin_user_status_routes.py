"""User pause and enable/disable operations HTTP endpoints."""

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable, Mapping

import tuic_config
import xray_config


@dataclass(frozen=True)
class Context:
    USERS_FILE: Path
    _build_overview_user: Callable[..., object]
    _json_request: Callable[..., object]
    _static_reload_status: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    delete_user_sessions_for: Callable[..., object]
    hy_kick: Callable[..., object]
    is_logged_in: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    parse_int_field: Callable[..., object]
    revision_matches: Callable[..., object]
    safe_admin_next: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]
    with_flash: Callable[..., object]


def _pause_user(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    username = (form.get('user') or [''])[0].strip()
    minutes = ctx.parse_int_field((form.get('minutes') or ['60'])[0], 60, 1, 1440)
    next_to = ctx.safe_admin_next((form.get('next') or [''])[0])
    until = ctx.local_now() + timedelta(minutes=minutes)
    until_text = until.isoformat(timespec='seconds')
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._mutation_user_not_found(username, next_to)
            return
        if not isinstance(users.get(username), dict):
            handler._mutation_user_not_found(username, next_to)
            return
        if not ctx.revision_matches(
            users.get(username),
            request_user_revision,
        ):
            handler._mutation_conflict(username, next_to)
            return
        users[username]['disabled'] = True
        users[username]['disabled_until'] = until_text
        ctx.save_json(ctx.USERS_FILE, users)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    ctx.delete_user_sessions_for(username)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    ctx.hy_kick([username])
    handler.write_reset_log(
        handler.get_admin_actor(),
        'pause_user',
        username,
        {},
        {'disabled_until': until_text},
    )
    if ctx._json_request(handler):
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'disabled_until': until_text,
                'user': ctx._build_overview_user(username, now=ctx.local_now()),
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect(ctx.with_flash(next_to, 'paused ' + username))
    return


def _toggle_user(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        if ctx._json_request(handler):
            handler.send_response_body(
                401, '{"ok":false,"reason":"login_required"}', 'application/json; charset=utf-8'
            )
        else:
            handler.redirect('/login')
        return
    username = (form.get('user') or [''])[0].strip()
    next_to = ctx.safe_admin_next((form.get('next') or [''])[0])
    desired = (query.get('desired') or [''])[0]
    if desired not in ('disabled', 'enabled'):
        if ctx._json_request(handler):
            handler.send_response_body(
                422, '{"ok":false,"reason":"invalid_desired"}', 'application/json; charset=utf-8'
            )
        else:
            handler.send_response_body(422, '目标用户状态无效')
        return
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._send_toggle_json(404, username, 'user_not_found', next_to)
            return
        if not isinstance(users.get(username), dict):
            handler._send_toggle_json(404, username, 'user_not_found', next_to)
            return
        if not ctx.revision_matches(
            users.get(username),
            request_user_revision,
        ):
            handler._send_toggle_json(409, username, 'conflict', next_to)
            return
        disable = desired == 'disabled'
        users[username]['disabled'] = disable
        users[username].pop('disabled_until', None)
        ctx.save_json(ctx.USERS_FILE, users)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    # Config commits share the user-state lock above.
    if disable:
        ctx.delete_user_sessions_for(username)
        if xray_changed:
            xray_config.reload_async()
        if tuic_changed:
            tuic_config.reload_async()
        ctx.hy_kick([username])
        handler.write_reset_log(handler.get_admin_actor(), 'disable_user', username, {}, {})
    else:
        if xray_changed:
            xray_config.reload_async()
        if tuic_changed:
            tuic_config.reload_async()
        handler.write_reset_log(handler.get_admin_actor(), 'enable_user', username, {}, {})
    handler._send_toggle_json(200, username, desired, next_to)
    return


_ROUTES = {
    '/admin/pause-user': _pause_user,
    '/admin/toggle-user': _toggle_user,
}


def handle_write(
    handler,
    context: Context,
    *,
    path: str,
    form: Mapping[str, list[str]],
    query: Mapping[str, list[str]],
    request_user_revision: str,
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form, query, request_user_revision)
    return True
