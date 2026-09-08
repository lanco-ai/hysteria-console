"""Shared HTML framing, alerts, icons and subscription-link presentation."""

from __future__ import annotations

import html
from dataclasses import dataclass
from typing import Callable

import web_assets


@dataclass(frozen=True)
class Context:
    BASE_CSS_ETAG: str
    CYCLE_LENGTH_MAX: int
    CYCLE_LENGTH_MIN: int
    PASSWORD_MAX_LENGTH: int
    SUBSCRIPTION_PROFILES: dict
    SUBSCRIPTION_PROFILE_ORDER: object
    _ICONS: dict
    subscription_profile_qr_path: Callable[..., str]
    subscription_profile_url: Callable[..., str]


def render_user_state_conflict(target, host, draft=None, *, render_admin_shell):
    draft_html = ''
    if isinstance(draft, dict):
        labels = (
            ('user', '用户'),
            ('max_devices', '设备数上限'),
            ('quota_gb', '基础流量（GB）'),
            ('quota_extra_gb', '加量包（GB）'),
            ('expires_at', '到期日'),
            ('note', '备注'),
            ('guest', '按量用户'),
            ('tuic_enabled', '允许 TUIC'),
        )
        items = ''.join(
            '<dt class="small faint">'
            f'{html.escape(label)}</dt><dd class="break">'
            f'{html.escape(str(draft.get(key, "")))}</dd>'
            for key, label in labels
        )
        draft_html = (
            '<div class="card mt-md">'
            '<h2 class="section-title mb-sm">未保存的非敏感草稿</h2>'
            '<p class="small mb-md">可先复制这些值，再刷新并合并。'
            '出于安全考虑，密码字段不会回显；如有填写请重新输入。</p>'
            f'<dl class="draft-summary">{items}</dl></div>'
        )
    content = (
        '<div class="card">'
        '<div class="err" role="alert">'
        '这名用户在页面打开后已被其他操作修改。'
        '为避免覆盖新状态，本次操作没有执行；请刷新后确认最新信息。'
        '</div><div class="row mt-md">'
        f'<a class="btn" href="{html.escape(target, quote=True)}">'
        '刷新并返回</a></div></div>'
        f'{draft_html}'
    )
    return render_admin_shell('dashboard', '用户状态已变化', content, badge=host)


def html_page(ctx: Context, title, body, body_class=''):
    cls = f' class="{body_class}"' if body_class else ''
    css_version = ctx.BASE_CSS_ETAG.strip('"')
    page_body = (
        body
        if body_class == 'has-shell'
        else (
            '<a class="skip-link" href="#main-content">跳到主内容</a>'
            f'<main id="main-content" tabindex="-1">{body}</main>'
        )
    )
    return (
        f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f'<meta name="color-scheme" content="light">'
        f'<meta name="theme-color" content="#F8F7F3">'
        f'<title>{html.escape(title)}</title>'
        f'<link rel="stylesheet" href="/static/style.css?v={css_version}">'
        f'{web_assets.script_tag("ui-core")}'
        f'</head><body{cls}>{page_body}</body></html>'
    )


def icon(ctx: Context, name):
    raw = ctx._ICONS.get(name, '')
    return raw.replace('<svg ', '<svg aria-hidden="true" focusable="false" ', 1) if raw else ''


def render_alert(ctx: Context, msg, kind='flash', *, element_id=''):
    if not msg:
        return ''
    role = 'alert' if kind == 'err' else 'status'
    live = 'assertive' if kind == 'err' else 'polite'
    id_attr = f' id="{html.escape(str(element_id), quote=True)}"' if element_id else ''
    return (
        f'<div{id_attr} class="{kind}" role="{role}" aria-live="{live}" aria-atomic="true">'
        f'{html.escape(msg)}</div>'
    )


def render_prefixed_alert(ctx: Context, flash, msg_map):
    """Resolve a flash code that may carry an 'err:' prefix and render the alert."""
    if not flash:
        return ''
    is_err = flash.startswith('err:')
    key = flash.removeprefix('err:')
    msg = msg_map.get(key, key)
    return render_alert(ctx, msg, 'err' if is_err else 'flash')


def back_to_admin(ctx: Context, label='返回管理后台'):
    return f'<a class="btn secondary" href="/admin">{icon(ctx, "back")}<span>{html.escape(label)}</span></a>'


def flash_text(ctx: Context, msg):
    if not msg:
        return ''
    if msg.startswith('err:'):
        msg = msg[4:]
    if msg == 'login success':
        return '登录成功'
    if msg.startswith('updated '):
        return f'已更新用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('created '):
        return f'已创建用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('reset usage '):
        return f'已清除用户本周期已用流量：{msg.split(" ", 2)[2]}'
    if msg == 'reset usage all':
        return '已清除全部用户本周期已用流量'
    if msg.startswith('refresh usage '):
        return f'已刷新用户本周期已用流量（服务器总流量不变）：{msg.split(" ", 2)[2]}'
    if msg.startswith('deleted_retry '):
        return (
            '删除请求已安全记录；旧授权、历史数据或连接仍在后台复核，'
            '系统会持续自动重试，直至确认完成：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('deleted '):
        return f'已删除用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('rotated_pending '):
        return (
            '已重置订阅令牌；已确认暂停 Xray/TUIC，正在等待安全同步，'
            'Hysteria 连接断开请求将延迟复核：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated_static_pending '):
        return (
            '已重置订阅令牌；受影响的静态代理因重载未能安排而已确认暂停，'
            '正在等待安全同步：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated_retry '):
        return (
            '已重置订阅令牌，但未能确认所有旧连接或静态代理均已停止；'
            '系统会持续自动重试，直至确认完成：'
            f'{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('rotated '):
        return (
            f'已重置订阅令牌（旧订阅/面板链接已失效，连接断开请求正在复核）：{msg.split(" ", 1)[1]}'
        )
    if msg.startswith('disabled '):
        return f'已停用用户（已请求断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('paused '):
        return f'已暂停用户 1 小时（已请求断开连接）：{msg.split(" ", 1)[1]}'
    if msg.startswith('enabled '):
        return f'已启用用户：{msg.split(" ", 1)[1]}'
    if msg.startswith('settlement '):
        return f'已更新结算日：每月 {msg.split(" ", 1)[1]} 日'
    maps = {
        'user not found': '用户不存在',
        'user empty': '用户名不能为空',
        'user_exists_use_reset_token': '用户已存在；请在用户列表中使用“重置订阅”，以执行完整的连接撤销与审计流程',
        'username_invalid': '用户名只能包含字母、数字、点、下划线、连字符，且不能以 .json 结尾',
        'panel_password_short': '用户面板登录密码至少需要 8 位',
        'panel_password_long': f'用户面板登录密码不能超过 {ctx.PASSWORD_MAX_LENGTH} 位',
        'proxy_password_long': f'代理连接密码不能超过 {ctx.PASSWORD_MAX_LENGTH} 位',
        'max_devices_invalid': '设备数上限必须是 0–100 之间的整数；0 表示不限设备',
        'quota_invalid': '基础流量上限必须是 0–10240 之间的整数；0 表示不限流量',
        'quota_extra_invalid': '加量包必须是 0–10240 之间的整数',
        'expiry_invalid': '到期日无效，请使用 YYYY-MM-DD 格式',
        'note_too_long': '备注不能超过 200 个字符',
        'landing_too_long': '落地家宽信息不能超过 120 个字符',
        'landing_invalid': '落地家宽信息不能包含控制字符',
        'landing_ip_invalid': '请输入合法的 IPv4 或 IPv6 地址',
        'settlement_invalid': '结算日无效（请输入 1–28 之间的整数）',
        'cycle_length_invalid': f'周期长度无效（请输入 {ctx.CYCLE_LENGTH_MIN}–{ctx.CYCLE_LENGTH_MAX} 之间的整数）',
    }
    return maps.get(msg, msg)


def render_subscription_profile_links(ctx: Context, base_url, user, token):
    items = []
    for key in ctx.SUBSCRIPTION_PROFILE_ORDER:
        meta = ctx.SUBSCRIPTION_PROFILES[key]
        url = ctx.subscription_profile_url(base_url, user, token, key)
        qr_path = ctx.subscription_profile_qr_path(user, token, key)
        selected = key == 'default'
        items.append(
            f'<button type="button" class="profile-tab{" is-active" if selected else ""}" '
            f'data-profile-option data-profile="{key}" '
            f'data-profile-label="{html.escape(meta["label"], quote=True)}" '
            f'data-profile-desc="{html.escape(meta["desc"], quote=True)}" '
            f'data-profile-url="{html.escape(url, quote=True)}" '
            f'data-profile-qr="{html.escape(qr_path, quote=True)}" '
            f'aria-current="{"true" if selected else "false"}">'
            f'<span class="profile-tab-label">{html.escape(meta["label"])}</span>'
            f'</button>'
        )
    default_meta = ctx.SUBSCRIPTION_PROFILES['default']
    default_url = ctx.subscription_profile_url(base_url, user, token, 'default')
    default_qr = ctx.subscription_profile_qr_path(user, token, 'default')
    return (
        '<div class="connection-panel">'
        '<div class="connection-head">'
        '<div><div class="k">订阅模式</div>'
        f'<div class="connection-desc small" id="profile-selected-desc">{html.escape(default_meta["desc"])}</div>'
        '</div>'
        f'<div class="connection-head-right">'
        f'<span class="badge connection-badge" id="profile-selected-badge">{html.escape(default_meta["label"])}</span>'
        f'<span class="connection-muted small faint sr-only" id="profile-selected-title">{html.escape(default_meta["label"])}</span>'
        f'</div>'
        '</div>'
        f'<div class="profile-tabs" role="group" aria-label="选择订阅模式">{"".join(items)}</div>'
        '<div class="connection-url">'
        f'<code class="mono url-text" id="sub">{html.escape(default_url)}</code>'
        f'<button class="btn primary btn-sm" type="button" id="profile-copy" data-copy="{html.escape(default_url, quote=True)}">'
        f'{icon(ctx, "copy")}<span>复制链接</span></button>'
        '</div>'
        '<div class="connection-actions">'
        f'<a class="btn secondary btn-sm" id="profile-open" href="{html.escape(default_url, quote=True)}">'
        f'{icon(ctx, "open")}<span>打开配置</span></a>'
        f'<button class="btn ghost btn-sm" type="button" id="profile-show-qr" '
        f'data-qr="{html.escape(default_qr, quote=True)}" aria-expanded="false" aria-controls="profile-qr-panel">'
        f'<span>二维码</span></button>'
        '</div>'
        '<div class="profile-qr-panel" id="profile-qr-panel" hidden>'
        '<div class="qr-wrap"><img id="profile-qr-image" width="220" height="220" alt="当前订阅模式二维码"></div>'
        '<div class="small faint" id="profile-qr-status" role="status" aria-live="polite">在另一台设备上用客户端扫码导入；二维码仅在这里按需生成。</div>'
        '</div>'
        '</div>'
    )


def _action_label(ctx: Context, action):
    return {
        'reset_usage_user': '清除用户流量',
        'reset_usage_all': '清空全部流量',
        'refresh_usage_user': '刷新用户流量（保留总计）',
        'rotate_token': '重置订阅令牌',
        'disable_user': '停用用户',
        'enable_user': '启用用户',
    }.get(action, action)
