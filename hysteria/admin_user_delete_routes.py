"""User deletion with durable revocation recovery HTTP endpoints."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    user_deletion_service: Callable[..., object]
    _json_request: Callable[..., object]
    _static_reload_status: Callable[..., object]
    is_logged_in: Callable[..., object]
    with_flash: Callable[..., object]


def _delete(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    result = ctx.user_deletion_service().delete(
        form=form,
        expected_revision=request_user_revision,
    )
    username = result.username
    if result.outcome == 'not_found':
        handler._mutation_user_not_found(username, '/admin')
        return
    if result.outcome == 'conflict':
        handler._mutation_conflict(username, '/admin')
        return
    flash = result.code + ' ' + username
    if ctx._json_request(handler):
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'deleted': True,
                'flash': flash.split(' ', 1)[0],
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect(ctx.with_flash('/admin', flash))
    return


_ROUTES = {
    '/admin/delete': _delete,
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
