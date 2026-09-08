"""Landing egress management and user selection HTTP endpoints."""

import hmac
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping
from urllib.parse import urlencode

import landing_egress
import state_store
import tuic_config
import xray_config


@dataclass(frozen=True)
class Context:
    USERS_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    _ensure_landing_vless_uuid: Callable[..., object]
    _landing_registry_or_empty: Callable[..., object]
    _sync_static_access_from_users: Callable[..., object]
    content_revision: Callable[..., object]
    get_logged_in_user_context: Callable[..., object]
    is_logged_in: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    revision_matches: Callable[..., object]
    save_json: Callable[..., object]
    usage_lock: Callable[..., object]
    user_panel_access_error: Callable[..., object]


def _select(handler, ctx, form, query, request_user_revision):
    user, session_kind = ctx.get_logged_in_user_context(handler)
    if not user or session_kind != ctx.USER_SESSION_PANEL_PASSWORD:
        handler.send_response_body(403, '仅面板密码会话可以切换家宽出口')
        return
    requested_id = (form.get('egress_id') or [''])[0].strip()
    users = ctx.load_json(ctx.USERS_FILE, {})
    cfg = users.get(user)
    if not isinstance(cfg, dict):
        handler.send_response_body(403, '用户状态无效')
        return
    if ctx.user_panel_access_error(
        cfg,
        session_kind,
        today=ctx.local_now().date(),
    ):
        handler.send_response_body(403, '当前账户不能切换家宽出口')
        return
    if not ctx.revision_matches(cfg, request_user_revision):
        handler.send_response_body(409, '用户配置已更新，请刷新后重试')
        return
    registry = ctx._landing_registry_or_empty()
    nodes = registry.get('nodes', {})
    allowed = cfg.get('landing_allowed_egress_ids', [])
    node = nodes.get(requested_id)
    if (
        not isinstance(allowed, list)
        or requested_id not in allowed
        or not isinstance(node, dict)
        or node.get('enabled') is not True
    ):
        handler.send_response_body(403, '无权选择该家宽出口')
        return
    probed_node_revision = ctx.content_revision(node)
    changed_at = str(cfg.get('landing_egress_changed_at') or '')
    if changed_at:
        try:
            previous_change = datetime.fromisoformat(changed_at)
            elapsed = (ctx.local_now() - previous_change).total_seconds()
        except (TypeError, ValueError):
            handler.send_response_body(409, '家宽出口状态无法确认')
            return
        if elapsed < 60:
            handler.send_response_body(
                429,
                '切换过于频繁，请稍后重试',
                extra_headers={'Retry-After': str(max(1, int(60 - elapsed)))},
            )
            return
    try:
        landing_egress.probe_exit(node)
    except landing_egress.LandingEgressProbeError:
        handler.send_response_body(422, '家宽出口健康检查失败，未修改选择')
        return
    with ctx.usage_lock():
        original_users_text = Path(ctx.USERS_FILE).read_text(
            encoding='utf-8',
        )
        users = ctx.load_json(ctx.USERS_FILE, {})
        cfg = users.get(user)
        registry = ctx._landing_registry_or_empty()
        node = registry.get('nodes', {}).get(requested_id)
        allowed = cfg.get('landing_allowed_egress_ids', []) if isinstance(cfg, dict) else []
        if not isinstance(cfg, dict) or not ctx.revision_matches(cfg, request_user_revision):
            handler.send_response_body(409, '用户配置已更新，请刷新后重试')
            return
        if (
            not isinstance(allowed, list)
            or requested_id not in allowed
            or not isinstance(node, dict)
            or node.get('enabled') is not True
        ):
            handler.send_response_body(403, '无权选择该家宽出口')
            return
        if ctx.content_revision(node) != probed_node_revision:
            handler.send_response_body(409, '家宽出口配置已更新，请重试')
            return
        cfg['landing_selected_egress_id'] = requested_id
        cfg['landing_egress_changed_at'] = ctx.local_now().isoformat()
        users[user] = cfg
        ctx.save_json(ctx.USERS_FILE, users)
        try:
            xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
        except Exception:
            state_store.save_text_atomic(
                ctx.USERS_FILE,
                original_users_text,
            )
            original_users = json.loads(original_users_text)
            try:
                ctx._sync_static_access_from_users(original_users)
            except Exception:
                pass
            raise
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect(
        '/user/panel?' + urlencode({'msg': 'landing_egress_selected'}),
        status=303,
    )
    return


def _save(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    node_id = (form.get('id') or [''])[0].strip()
    expected_registry_revision = (form.get('registry_revision') or [''])[0]
    with ctx.usage_lock():
        registry = landing_egress.load_registry()
        if not hmac.compare_digest(
            ctx.content_revision(registry),
            str(expected_registry_revision),
        ):
            handler.send_response_body(409, '节点列表已更新，请刷新后重试')
            return
        existing = registry.get('nodes', {}).get(node_id, {})
        username = (form.get('socks_username') or [''])[0]
        password = (form.get('socks_password') or [''])[0]
        if isinstance(existing, dict):
            if not username and existing.get('socks_username'):
                username = existing['socks_username']
            if not password and existing.get('socks_password'):
                password = existing['socks_password']
        raw_node = {
            'id': node_id,
            'name': (form.get('name') or [''])[0],
            'socks_ip': (form.get('socks_ip') or [''])[0],
            'socks_port': (form.get('socks_port') or [''])[0],
            'socks_username': username,
            'socks_password': password,
            'expected_exit_ip': (form.get('expected_exit_ip') or [''])[0],
            'isp': (form.get('isp') or [''])[0],
            'region': (form.get('region') or [''])[0],
            'enabled': 'enabled' in form,
        }
        if isinstance(existing, dict) and existing.get('health'):
            raw_node['health'] = existing['health']
        try:
            node = landing_egress.validate_node(raw_node)
        except landing_egress.LandingEgressValidationError as exc:
            handler.send_response_body(422, '节点配置无效：' + exc.code)
            return
        registry['nodes'][node_id] = node
        landing_egress.save_registry(registry)
        users = ctx.load_json(ctx.USERS_FILE, {})
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect(
        '/admin/landing-egresses?' + urlencode({'msg': '节点已保存'}),
        status=303,
    )
    return


def _delete(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    node_id = (form.get('id') or [''])[0].strip()
    with ctx.usage_lock():
        users = ctx.load_json(ctx.USERS_FILE, {})
        referenced = any(
            isinstance(cfg, dict)
            and (
                node_id in cfg.get('landing_allowed_egress_ids', [])
                or cfg.get('landing_selected_egress_id') == node_id
            )
            for cfg in users.values()
        )
        if referenced:
            handler.send_response_body(409, '节点仍被用户引用，无法删除')
            return
        registry = landing_egress.load_registry()
        registry.get('nodes', {}).pop(node_id, None)
        landing_egress.save_registry(registry)
        xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect(
        '/admin/landing-egresses?' + urlencode({'msg': '节点已删除'}),
        status=303,
    )
    return


def _check(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    node_id = (form.get('id') or [''])[0].strip()
    registry = landing_egress.load_registry()
    node = registry.get('nodes', {}).get(node_id)
    if not isinstance(node, dict):
        handler.send_response_body(404, '节点不存在')
        return
    probed_node_revision = ctx.content_revision(node)
    try:
        observed = landing_egress.probe_exit(node)
    except landing_egress.LandingEgressProbeError as exc:
        status = 'unhealthy'
        observed = ''
        error_code = exc.code
    else:
        status = 'healthy'
        error_code = ''
    with ctx.usage_lock():
        registry = landing_egress.load_registry()
        current = registry.get('nodes', {}).get(node_id)
        if not isinstance(current, dict):
            handler.send_response_body(409, '节点已被修改')
            return
        if ctx.content_revision(current) != probed_node_revision:
            handler.send_response_body(409, '节点已被修改')
            return
        current['health'] = {
            'status': status,
            'observed_ip': observed,
            'checked_at': ctx.local_now().isoformat(),
            'error_code': error_code,
        }
        registry['nodes'][node_id] = current
        landing_egress.save_registry(registry)
    handler.redirect(
        '/admin/landing-egresses?'
        + urlencode(
            {
                'msg': '节点健康' if status == 'healthy' else '节点不可用',
            }
        ),
        status=303,
    )
    return


def _user_access(handler, ctx, form, query, request_user_revision):
    if not ctx.is_logged_in(handler):
        handler.redirect('/login')
        return
    username = (form.get('user') or [''])[0].strip()
    requested_ids = list(dict.fromkeys(form.get('egress_id') or []))
    with ctx.usage_lock():
        original_users_text = Path(ctx.USERS_FILE).read_text(
            encoding='utf-8',
        )
        users = ctx.load_json(ctx.USERS_FILE, {})
        cfg = users.get(username)
        if not isinstance(cfg, dict):
            handler.send_response_body(404, '用户不存在')
            return
        if not ctx.revision_matches(cfg, request_user_revision):
            handler.send_response_body(409, '用户配置已更新，请刷新后重试')
            return
        registry = landing_egress.load_registry()
        nodes = registry.get('nodes', {})
        if any(
            node_id not in nodes or nodes[node_id].get('enabled') is not True
            for node_id in requested_ids
        ):
            handler.send_response_body(422, '授权节点无效或已禁用')
            return
        cfg['landing_allowed_egress_ids'] = requested_ids
        if requested_ids:
            ctx._ensure_landing_vless_uuid(cfg, users)
        if cfg.get('landing_selected_egress_id') not in requested_ids:
            cfg.pop('landing_selected_egress_id', None)
        users[username] = cfg
        ctx.save_json(ctx.USERS_FILE, users)
        try:
            xray_changed, tuic_changed = ctx._sync_static_access_from_users(users)
        except Exception:
            state_store.save_text_atomic(
                ctx.USERS_FILE,
                original_users_text,
            )
            try:
                ctx._sync_static_access_from_users(
                    json.loads(original_users_text),
                )
            except Exception:
                pass
            raise
    if xray_changed:
        xray_config.reload_async()
    if tuic_changed:
        tuic_config.reload_async()
    handler.redirect(
        '/admin/landing-egresses?' + urlencode({'msg': '用户授权已更新'}),
        status=303,
    )
    return


_ROUTES = {
    '/user/landing-egress/select': _select,
    '/admin/landing-egress/save': _save,
    '/admin/landing-egress/delete': _delete,
    '/admin/landing-egress/check': _check,
    '/admin/user-landing-access': _user_access,
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
