"""Traffic reset, refresh and billing-cycle HTTP operations."""

from dataclasses import dataclass
from typing import Callable, Mapping


@dataclass(frozen=True)
class Context:
    _build_overview_json_payload: Callable[..., object]
    _build_overview_user: Callable[..., object]
    _json_request: Callable[..., object]
    _static_reload_status: Callable[..., object]
    is_logged_in: Callable[..., object]
    local_now: Callable[..., object]
    traffic_mutation_service: Callable[..., object]


def _service(handler, ctx):
    def audit(action, target, before, after):
        handler.write_reset_log(
            handler.get_admin_actor(),
            action,
            target,
            before,
            after,
        )

    return ctx.traffic_mutation_service(audit)


def _configure_cycle(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    result = _service(handler, ctx).configure_cycle(form=form)
    if result.outcome == 'invalid':
        handler.redirect('/admin?msg=' + result.code)
        return
    if result.outcome != 'success':
        raise ValueError('invalid cycle mutation result')
    handler.redirect(f'/admin?msg=settlement+{result.day}')
    return


def _reset_user(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    result = _service(handler, ctx).reset_user(
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
    if result.outcome != 'success':
        raise ValueError('invalid reset usage result')
    if ctx._json_request(handler):
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'user': ctx._build_overview_user(username, now=ctx.local_now()),
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect('/admin?msg=reset+usage+' + username)
    return


def _refresh_user(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    result = _service(handler, ctx).refresh_user(
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
    if result.outcome != 'success':
        raise ValueError('invalid refresh usage result')
    if ctx._json_request(handler):
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'username': username,
                'user': ctx._build_overview_user(username, now=ctx.local_now()),
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect('/admin?msg=refresh+usage+' + username)
    return


def _reset_all(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    result = _service(handler, ctx).reset_all()
    if result.outcome != 'success':
        raise ValueError('invalid reset all result')
    if ctx._json_request(handler):
        # Global reset touches every row, so return the full user
        # list in the overview schema for the client to patch.
        overview = ctx._build_overview_json_payload(now=ctx.local_now())
        handler._send_mutation_json(
            200,
            {
                'ok': True,
                'users': overview['users'],
                'total_used': overview['total_used'],
                'reload': ctx._static_reload_status(),
            },
        )
    else:
        handler.redirect('/admin?msg=reset+usage+all')
    return


_ROUTES = {
    '/admin/cycle-config': _configure_cycle,
    '/admin/settlement-day': _configure_cycle,
    '/admin/reset-usage': _reset_user,
    '/admin/refresh-usage': _refresh_user,
    '/admin/reset-usage-all': _reset_all,
}


def handle_write(
    handler,
    context: Context,
    *,
    path: str,
    form: Mapping[str, list[str]],
    request_user_revision: str,
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form, request_user_revision)
    return True
