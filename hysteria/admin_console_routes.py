"""Administrative console reads; bearer exchange stays at the request boundary."""

import json
from dataclasses import dataclass
from typing import Callable, Mapping

import hysteria_update


@dataclass(frozen=True)
class Context:
    _build_overview_json_payload: Callable[..., object]
    _build_user_json_payload: Callable[..., object]
    _handle_legacy_daily_redirect: Callable[..., object]
    _static_reload_status: Callable[..., object]
    back_to_admin: Callable[..., object]
    build_incident_payload: Callable[..., object]
    is_logged_in: Callable[..., object]
    local_now: Callable[..., object]
    render_admin: Callable[..., object]
    render_admin_shell: Callable[..., object]
    render_config_editor: Callable[..., object]
    render_incidents: Callable[..., object]
    render_landing_egresses: Callable[..., object]
    render_reset_logs: Callable[..., object]
    render_rules: Callable[..., object]
    render_settings: Callable[..., object]
    render_user_detail_page: Callable[..., object]


def _overview_page(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200, ctx.render_admin(host, base_url, flash=flash), 'text/html; charset=utf-8', send_payload
    )
    return


def _logs(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    handler.send_response_body(
        200, ctx.render_reset_logs(host), 'text/html; charset=utf-8', send_payload
    )
    return


def _overview_json(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.send_response_body(
            401,
            '{"error":"login_required"}',
            'application/json; charset=utf-8',
            send_payload,
        )
        return
    payload = ctx._build_overview_json_payload(now=ctx.local_now())
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        'application/json; charset=utf-8',
        send_payload,
    )
    return


def _reload_status(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.send_response_body(
            401,
            '{"ok":false,"reason":"login_required"}',
            'application/json; charset=utf-8',
            send_payload,
        )
        return
    payload = {'ok': True}
    payload.update(ctx._static_reload_status())
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        'application/json; charset=utf-8',
        send_payload,
        extra_headers={'Cache-Control': 'no-store'},
    )
    return


def _update_status(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.send_response_body(
            401,
            '{"ok":false,"reason":"login_required"}',
            'application/json; charset=utf-8',
            send_payload,
            extra_headers={'Cache-Control': 'no-store'},
        )
        return
    payload = hysteria_update.public_status()
    handler.send_response_body(
        200,
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(',', ':'),
        ),
        'application/json; charset=utf-8',
        send_payload,
        extra_headers={'Cache-Control': 'no-store'},
    )
    return


def _incidents(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200, ctx.render_incidents(host, flash=flash), 'text/html; charset=utf-8', send_payload
    )
    return


def _evidence(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    now = ctx.local_now()
    payload = ctx.build_incident_payload(now=now)
    filename = f'incident-evidence-{now.strftime("%Y%m%dT%H%M%S")}.json'
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, indent=2),
        'application/json; charset=utf-8',
        send_payload,
        extra_headers={'Content-Disposition': f'attachment; filename="{filename}"'},
    )
    return


def _user_page(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    uid = path[len('/admin/user/') :]
    out = ctx.render_user_detail_page(uid, host)
    if out is None:
        content = (
            '<div class="card">'
            '<div class="err" role="alert">找不到该用户，可能已被删除或链接已过期。</div>'
            '<div class="row mt-md">'
            f'{ctx.back_to_admin("返回用户列表")}'
            '</div></div>'
        )
        handler.send_response_body(
            404,
            ctx.render_admin_shell(
                'dashboard',
                '用户不存在',
                content,
                badge=host,
            ),
            'text/html; charset=utf-8',
            send_payload,
        )
        return
    handler.send_response_body(200, out, 'text/html; charset=utf-8', send_payload)
    return


def _user_json(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.send_response_body(
            401,
            '{"error":"login_required"}',
            'application/json; charset=utf-8',
            send_payload,
        )
        return
    uid = path[len('/admin/user/') : -len('.json')]
    summary_only = (q.get('summary') or ['0'])[0].lower() in ('1', 'true', 'yes')
    payload = ctx._build_user_json_payload(
        uid,
        now=ctx.local_now(),
        include_charts=not summary_only,
    )
    if payload is None:
        handler.send_response_body(
            404, '{"error":"not found"}', 'application/json; charset=utf-8', send_payload
        )
        return
    handler.send_response_body(
        200,
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        'application/json; charset=utf-8',
        send_payload,
    )
    return


def _legacy_daily(handler, ctx, path, q, host, base_url, send_payload):
    ctx._handle_legacy_daily_redirect(handler)
    return


def _settings(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200, ctx.render_settings(host, flash=flash), 'text/html; charset=utf-8', send_payload
    )
    return


def _landing(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200,
        ctx.render_landing_egresses(host, flash=flash),
        'text/html; charset=utf-8',
        send_payload,
    )
    return


def _config(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200, ctx.render_config_editor(host, flash=flash), 'text/html; charset=utf-8', send_payload
    )
    return


def _rules(handler, ctx, path, q, host, base_url, send_payload):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    flash = (q.get('msg') or [''])[0]
    handler.send_response_body(
        200, ctx.render_rules(host, flash=flash), 'text/html; charset=utf-8', send_payload
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
    if path == '/admin':
        route = _overview_page
    elif path == '/admin/logs':
        route = _logs
    elif path == '/admin/overview.json':
        route = _overview_json
    elif path == '/admin/reload-status.json':
        route = _reload_status
    elif path == '/admin/hysteria-update/status.json':
        route = _update_status
    elif path == '/admin/incidents':
        route = _incidents
    elif path == '/admin/incidents/evidence.json':
        route = _evidence
    elif path.startswith('/admin/user/') and (not path.endswith('.json')):
        route = _user_page
    elif path.startswith('/admin/user/') and path.endswith('.json'):
        route = _user_json
    elif path == '/admin/daily':
        route = _legacy_daily
    elif path == '/admin/settings':
        route = _settings
    elif path == '/admin/landing-egresses':
        route = _landing
    elif path == '/admin/config':
        route = _config
    elif path == '/admin/rules':
        route = _rules
    else:
        return False
    route(handler, context, path, query, host, base_url, send_payload)
    return True
