"""Administrative operations endpoints; scheduling and policy logic are injected."""

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import state_store


class OperationsHandler(Protocol):
    def redirect(self, to: str) -> None: ...
    def get_admin_actor(self) -> str: ...
    def _mutation_unauthorized(self) -> None: ...
    def _send_mutation_json(self, code: int, payload: dict) -> None: ...


@dataclass(frozen=True)
class OperationsContext:
    is_logged_in: Callable[[OperationsHandler], bool]
    load_alert_config: Callable[[], dict]
    fire_test_alert: Callable[[dict, str], object]
    json_request: Callable[[OperationsHandler], bool]
    check_update: Callable[[], dict]
    schedule_update: Callable[[], dict]
    update_public_status: Callable[[dict], dict]
    apply_multiplier: Callable[..., str]
    save_auto_policy: Callable[[Mapping[str, list[str]]], object]


def _test_alert(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    cfg = ctx.load_alert_config()
    if not isinstance(cfg, dict) or not (cfg.get('telegram') or cfg.get('webhook')):
        handler.redirect('/admin/health?msg=err:alert_no_channels')
        return
    # Fire on a background thread; we redirect immediately rather than
    # block the request on outbound HTTP. Delivery is confirmed at the
    # receiver, so we report "dispatched" rather than guaranteed-sent.
    ctx.fire_test_alert(cfg, handler.get_admin_actor())
    handler.redirect('/admin/health?msg=alert+dispatched')
    return


def _check_update(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    try:
        info = ctx.check_update()
        flash = 'checked ' + (info.get('latest') or '')
    except state_store.LockTimeout:
        if ctx.json_request(handler):
            handler._send_mutation_json(
                409,
                {'ok': False, 'reason': 'update_busy'},
            )
            return
        flash = 'err:hysteria_update_busy'
    except Exception:
        if ctx.json_request(handler):
            handler._send_mutation_json(
                502,
                {'ok': False, 'reason': 'update_check_failed'},
            )
            return
        flash = 'err:hysteria_update_check_failed'
    else:
        if ctx.json_request(handler):
            handler._send_mutation_json(
                200,
                {
                    'ok': True,
                    'status': 'checked',
                    'current': info.get('current') or '',
                    'latest': info.get('latest') or '',
                    'update_available': bool(info.get('update_available')),
                    'pending': False,
                },
            )
            return
    handler.redirect('/admin/health?msg=' + flash.replace(' ', '+'))
    return


def _apply_update(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    try:
        state = ctx.schedule_update()
    except state_store.LockTimeout:
        if ctx.json_request(handler):
            handler._send_mutation_json(
                409,
                {'ok': False, 'reason': 'update_busy'},
            )
            return
        handler.redirect('/admin/health?msg=err:hysteria_update_busy')
        return
    except Exception:
        if ctx.json_request(handler):
            handler._send_mutation_json(
                503,
                {'ok': False, 'reason': 'update_schedule_failed'},
            )
            return
        handler.redirect('/admin/health?msg=err:hysteria_update_failed')
        return
    if ctx.json_request(handler):
        handler._send_mutation_json(
            202,
            ctx.update_public_status(state),
        )
        return
    handler.redirect('/admin/health?msg=hysteria_update_scheduled')
    return


def _apply_multiplier(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    result = ctx.apply_multiplier(actor=handler.get_admin_actor())
    prefix = 'err:' if result != 'multiplier_applied' else ''
    handler.redirect(f'/admin/health?msg={prefix}{result}')
    return


def _save_auto_policy(handler, ctx, form):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    ctx.save_auto_policy(form)
    handler.redirect('/admin/health?msg=multiplier_auto_saved')
    return


_ROUTES = {
    '/admin/test-alert': _test_alert,
    '/admin/hysteria-update/check': _check_update,
    '/admin/hysteria-update/apply': _apply_update,
    '/admin/cost-multiplier/apply': _apply_multiplier,
    '/admin/cost-multiplier/auto': _save_auto_policy,
}


def handle_write(
    handler: OperationsHandler,
    context: OperationsContext,
    *,
    path: str,
    form: Mapping[str, list[str]],
) -> bool:
    route = _ROUTES.get(path)
    if route is None:
        return False
    route(handler, context, form)
    return True
