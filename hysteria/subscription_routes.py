"""subscription routes; existing response and authorization contracts."""

import json
from dataclasses import dataclass
from typing import Callable, Mapping

import subscription_profiles as profile_defs
import user_compat


@dataclass(frozen=True)
class Context:
    USER_SESSION_SUBSCRIPTION_TOKEN: str
    _build_panel_json_payload: Callable[..., object]
    _credential_generation: Callable[..., object]
    build_yaml: Callable[..., object]
    check_user_token: Callable[..., object]
    create_user_session: Callable[..., object]
    is_secure_request: Callable[..., object]
    local_now: Callable[..., object]
    normalize_subscription_profile: Callable[..., object]
    render_profile_qr_svg: Callable[..., object]
    render_user_panel: Callable[..., object]
    scaled_usage_for_user: Callable[..., object]
    subscription_template_mtime: Callable[..., object]
    user_session_cookie: Callable[..., object]
    user_total_quota: Callable[..., object]


def _download(handler, ctx, path, q, host, base_url, send_payload):
    user = path.split('/', 2)[2]
    token = (q.get('token') or [''])[0]
    cfg = ctx.check_user_token(user, token)
    if not cfg:
        handler.send_response_body(403, '无权限访问', send_body=send_payload)
        return
    if cfg.get('disabled'):
        handler.send_response_body(403, '账号已停用，请联系管理员', send_body=send_payload)
        return
    if user_compat.is_expired(cfg, today=ctx.local_now().date()):
        handler.send_response_body(403, '账号已到期，请联系管理员续费', send_body=send_payload)
        return
    profile = ctx.normalize_subscription_profile((q.get('profile') or ['default'])[0])
    generated_at = profile_defs.utc_now_iso()
    template_mtime = ctx.subscription_template_mtime()
    yml = ctx.build_yaml(
        user, str(cfg.get('sub_token') or ''), profile=profile, generated_at=generated_at
    )
    tx, rx, used = ctx.scaled_usage_for_user(user)
    total = ctx.user_total_quota(cfg)
    filename = f'{user}.yaml' if profile == 'default' else f'{user}-{profile}.yaml'
    handler.send_response_body(
        200,
        yml,
        'text/yaml; charset=utf-8',
        send_payload,
        extra_headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{filename}",
            'x-subscription-profile': profile,
            'x-subscription-generated-at': generated_at,
            'x-subscription-template-mtime': template_mtime,
            'profile-update-interval': '24',
            'subscription-userinfo': (f'upload={tx}; download={rx}; total={total}; expire=0'),
            'x-usage-total-bytes': str(used),
        },
    )
    return


def _qr(handler, ctx, path, q, host, base_url, send_payload):
    user = path[len('/panel/') : -len('/qr.svg')]
    token = (q.get('token') or [''])[0]
    cfg = ctx.check_user_token(user, token)
    if not cfg:
        handler.send_response_body(403, '无权限访问', send_body=send_payload)
        return
    if cfg.get('disabled'):
        handler.send_response_body(403, '账号已停用', send_body=send_payload)
        return
    if user_compat.is_expired(cfg, today=ctx.local_now().date()):
        handler.send_response_body(403, '账号已到期', send_body=send_payload)
        return
    profile = ctx.normalize_subscription_profile((q.get('profile') or ['default'])[0])
    svg = ctx.render_profile_qr_svg(base_url, user, token, profile)
    if not svg:
        handler.send_response_body(503, '二维码暂不可用', send_body=send_payload)
        return
    handler.send_response_body(
        200,
        svg,
        'image/svg+xml; charset=utf-8',
        send_payload,
        extra_headers={'Cache-Control': 'private, no-store'},
    )
    return


def _json(handler, ctx, path, q, host, base_url, send_payload):
    user = path[len('/panel/') : -len('.json')]
    token = (q.get('token') or [''])[0]
    cfg = ctx.check_user_token(user, token)
    if not cfg:
        handler.send_response_body(
            403, '{"error":"forbidden"}', 'application/json; charset=utf-8', send_payload
        )
        return
    if cfg.get('disabled'):
        handler.send_response_body(
            403, '{"error":"disabled"}', 'application/json; charset=utf-8', send_payload
        )
        return
    if user_compat.is_expired(cfg, today=ctx.local_now().date()):
        handler.send_response_body(
            403, '{"error":"expired"}', 'application/json; charset=utf-8', send_payload
        )
        return
    payload = ctx._build_panel_json_payload(user, cfg, now=ctx.local_now())
    handler.send_response_body(
        200, json.dumps(payload), 'application/json; charset=utf-8', send_payload
    )
    return


def _exchange(handler, ctx, path, q, host, base_url, send_payload):
    user = path.split('/', 2)[2]
    token = (q.get('token') or [''])[0]
    cfg = ctx.check_user_token(user, token)
    if not cfg:
        handler.send_response_body(403, '无权限访问', send_body=send_payload)
        return
    if cfg.get('disabled'):
        handler.send_response_body(403, '账号已停用，请联系管理员', send_body=send_payload)
        return
    if user_compat.is_expired(cfg, today=ctx.local_now().date()):
        handler.send_response_body(403, '账号已到期，请联系管理员续费', send_body=send_payload)
        return
    if send_payload:
        sid = ctx.create_user_session(
            user,
            ctx._credential_generation(
                str(cfg.get('sub_token') or ''),
            ),
            ctx.USER_SESSION_SUBSCRIPTION_TOKEN,
        )
        handler.redirect(
            '/user/panel',
            cookie=ctx.user_session_cookie(
                sid,
                secure=ctx.is_secure_request(handler),
            ),
            status=303,
        )
        return
    handler.send_response_body(
        200,
        ctx.render_user_panel(host, base_url, user, token, cfg),
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
    if path.startswith('/sub/'):
        route = _download
    elif path.startswith('/panel/') and path.endswith('/qr.svg'):
        route = _qr
    elif path.startswith('/panel/') and path.endswith('.json'):
        route = _json
    elif path.startswith('/panel/'):
        route = _exchange
    else:
        return False
    route(handler, context, path, query, host, base_url, send_payload)
    return True
