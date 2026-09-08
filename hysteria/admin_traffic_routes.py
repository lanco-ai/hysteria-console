"""Traffic reset, refresh and billing-cycle HTTP operations."""

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import tuic_config
import xray_config


@dataclass(frozen=True)
class Context:
    CYCLE_LENGTH_MAX: int
    CYCLE_LENGTH_MIN: int
    USAGE_FILE: Path
    USERS_FILE: Path
    _build_overview_json_payload: Callable[..., object]
    _build_overview_user: Callable[..., object]
    _clear_alert_dedup_for_users: Callable[..., object]
    _json_request: Callable[..., object]
    _static_reload_status: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    _update_cycle_meta: Callable[..., object]
    _zero_cycle_daily_hourly_for: Callable[..., object]
    add_preserved_for_user: Callable[..., object]
    is_logged_in: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    month_key: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    usage_for_user: Callable[..., object]
    usage_lock: Callable[..., object]


def _configure_cycle(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    try:
        day = int((form.get('day') or [''])[0])
    except (ValueError, TypeError):
        handler.redirect('/admin?msg=err:settlement_invalid')
        return
    if day < 1 or day > 28:
        handler.redirect('/admin?msg=err:settlement_invalid')
        return
    raw_len = (form.get('length') or [''])[0].strip()
    length = None
    if raw_len:
        try:
            length = int(raw_len)
        except (ValueError, TypeError):
            handler.redirect('/admin?msg=err:cycle_length_invalid')
            return
        if length < ctx.CYCLE_LENGTH_MIN or length > ctx.CYCLE_LENGTH_MAX:
            handler.redirect('/admin?msg=err:cycle_length_invalid')
            return
    # Re-read under META.lock so this RMW cannot restore an older
    # password hash saved by a concurrent request.
    ctx._update_cycle_meta(day, length)
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect(f'/admin?msg=settlement+{day}')
    return


def _reset_user(handler, ctx, form, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler._mutation_unauthorized()
        return
    username = (form.get('user') or [''])[0].strip()
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._mutation_user_not_found(username, '/admin')
            return
        if not ctx.revision_matches(
            users.get(username),
            request_user_revision,
        ):
            handler._mutation_conflict(username, '/admin')
            return
        now = ctx.local_now()
        usage = ctx.load_json(ctx.USAGE_FILE, {})
        mk = ctx.month_key(now)
        usage.setdefault(mk, {})
        tx, rx, total = ctx.usage_for_user(username, now=now)
        before = {'tx': tx, 'rx': rx, 'total': total}
        usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
        after = {'tx': 0, 'rx': 0, 'total': 0}
        ctx.save_json(ctx.USAGE_FILE, usage)
        ctx._zero_cycle_daily_hourly_for([username], now=now)
        # Clear quota alert dedup so subsequent crossings re-fire (ADR-0001).
        ctx._clear_alert_dedup_for_users([username], quota_only=True)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users, now=now)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.write_reset_log(handler.get_admin_actor(), 'reset_usage_user', username, before, after)
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
    username = (form.get('user') or [''])[0].strip()
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        if username not in users:
            handler._mutation_user_not_found(username, '/admin')
            return
        if not ctx.revision_matches(
            users.get(username),
            request_user_revision,
        ):
            handler._mutation_conflict(username, '/admin')
            return
        now = ctx.local_now()
        usage = ctx.load_json(ctx.USAGE_FILE, {})
        mk = ctx.month_key(now)
        usage.setdefault(mk, {})
        tx, rx, total = ctx.usage_for_user(username, now=now)
        before = {'tx': tx, 'rx': rx, 'total': total}
        # Bank the cleared bytes into the preserved bucket so the
        # dashboard's '本周期总流量' stays put after this refresh.
        ctx.add_preserved_for_user(username, tx, rx, total, now=now)
        usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
        after = {'tx': 0, 'rx': 0, 'total': 0}
        ctx.save_json(ctx.USAGE_FILE, usage)
        ctx._zero_cycle_daily_hourly_for([username], now=now)
        ctx._clear_alert_dedup_for_users([username], quota_only=True)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users, now=now)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.write_reset_log(
        handler.get_admin_actor(), 'refresh_usage_user', username, before, after
    )
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
    with ctx.usage_lock():
        now = ctx.local_now()
        usage = ctx.load_json(ctx.USAGE_FILE, {})
        mk = ctx.month_key(now)
        usage.setdefault(mk, {})
        before_all = {}
        users = ctx.load_json(ctx.USERS_FILE, {})
        for username in users.keys():
            tx, rx, total = ctx.usage_for_user(username, now=now)
            before_all[username] = {'tx': tx, 'rx': rx, 'total': total}
            usage[mk][username] = {'tx': 0, 'rx': 0, 'total': 0}
        ctx.save_json(ctx.USAGE_FILE, usage)
        ctx._zero_cycle_daily_hourly_for(list(users.keys()), now=now)
        # Clear quota alert dedup for all users (ADR-0001).
        ctx._clear_alert_dedup_for_users(list(users.keys()), quota_only=True)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users, now=now)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.write_reset_log(
        handler.get_admin_actor(),
        'reset_usage_all',
        'all_users',
        before_all,
        {u: {'tx': 0, 'rx': 0, 'total': 0} for u in users.keys()},
    )
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
