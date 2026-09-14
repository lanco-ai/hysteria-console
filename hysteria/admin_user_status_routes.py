"""User pause and enable/disable operations HTTP endpoints."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    _build_overview_user: Callable[..., object]
    _json_request: Callable[..., object]
    _static_reload_status: Callable[..., object]
    is_logged_in: Callable[..., object]
    local_now: Callable[..., object]
    safe_admin_next: Callable[..., object]
    user_status_service: Callable[..., object]
    with_flash: Callable[..., object]


def _service(handler, ctx):
    def audit(action, target, before, after):
        handler.write_reset_log(
            handler.get_admin_actor(),
            action,
            target,
            before,
            after,
        )

    return ctx.user_status_service(audit)


def _pause_user(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    next_to = ctx.safe_admin_next((form.get('next') or [''])[0])
    result = _service(handler, ctx).pause(
        form=form,
        expected_revision=request_user_revision,
    )
    username = result.username
    if result.outcome == 'not_found':
        handler._mutation_user_not_found(username, next_to)
        return
    if result.outcome == 'conflict':
        handler._mutation_conflict(username, next_to)
        return
    if result.outcome != 'success':
        raise ValueError('invalid pause user result')
    until_text = result.disabled_until
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
    next_to = ctx.safe_admin_next((form.get('next') or [''])[0])
    desired = (query.get('desired') or [''])[0]
    result = _service(handler, ctx).toggle(
        form=form,
        desired=desired,
        expected_revision=request_user_revision,
    )
    if result.outcome == 'invalid':
        if ctx._json_request(handler):
            handler.send_response_body(
                422, '{"ok":false,"reason":"invalid_desired"}', 'application/json; charset=utf-8'
            )
        else:
            handler.send_response_body(422, '目标用户状态无效')
        return
    username = result.username
    if result.outcome == 'not_found':
        handler._send_toggle_json(404, username, 'user_not_found', next_to)
        return
    if result.outcome == 'conflict':
        handler._send_toggle_json(409, username, 'conflict', next_to)
        return
    if result.outcome != 'success':
        raise ValueError('invalid toggle user result')
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
