"""Account creation and plan editing HTTP endpoints."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    account_mutation_service: Callable[..., object]
    configured_public_host: Callable[..., object]
    is_logged_in: Callable[..., object]
    render_admin: Callable[..., object]
    safe_base_url: Callable[..., object]


def _render_update_error(handler, ctx, code):
    host = ctx.configured_public_host(handler.headers.get('Host', '127.0.0.1'))
    handler.send_response_body(
        422,
        ctx.render_admin(
            host,
            ctx.safe_base_url(
                host,
                handler.headers.get('X-Forwarded-Proto', 'http'),
                handler.headers.get('X-Forwarded-Port', ''),
            ),
            flash=code,
        ),
        'text/html; charset=utf-8',
    )


def _update(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    result = ctx.account_mutation_service().update(
        form=form,
        expected_revision=request_user_revision,
    )
    if result.outcome == 'invalid':
        if result.code in {
            'err:panel_password_short',
            'err:panel_password_long',
            'err:proxy_password_long',
        }:
            handler.redirect('/admin?msg=' + result.code)
            return
        _render_update_error(handler, ctx, result.code)
        return
    if result.outcome == 'not_found':
        handler.redirect('/admin?msg=user+not+found')
        return
    if result.outcome == 'conflict':
        handler.send_user_state_conflict('/admin', draft=result.draft)
        return
    handler.redirect('/admin?msg=updated+' + result.username)


def _add(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    result = ctx.account_mutation_service().create(form=form)
    if result.outcome == 'invalid':
        host = ctx.configured_public_host(handler.headers.get('Host', '127.0.0.1'))
        base_url = ctx.safe_base_url(
            host,
            handler.headers.get('X-Forwarded-Proto', 'http'),
            handler.headers.get('X-Forwarded-Port', ''),
        )
        handler.send_response_body(
            422,
            ctx.render_admin(
                host,
                base_url,
                flash=result.code,
                create_draft=result.draft,
                create_error_field=result.field_id,
            ),
            'text/html; charset=utf-8',
        )
        return
    handler.redirect('/admin?msg=created+' + result.username)


_ROUTES = {
    '/admin/update': _update,
    '/admin/add': _add,
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
