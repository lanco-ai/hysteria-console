"""Residential-egress administrator and user-panel presentation."""

from __future__ import annotations

import html
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import landing_egress


@dataclass(frozen=True)
class Context:
    USERS_FILE: Path
    _authorized_landing_nodes: Callable[..., object]
    _landing_registry_or_empty: Callable[..., object]
    content_revision: Callable[..., str]
    load_json: Callable[..., object]
    render_admin_shell: Callable[..., str]
    render_alert: Callable[..., str]
    user_config_revision: Callable[..., str]


def render_landing_egress_selector(ctx: Context, cfg, *, password_session):
    nodes = ctx._authorized_landing_nodes(cfg)
    if not nodes:
        return ''
    selected = str(cfg.get('landing_selected_egress_id') or '')
    rows = []
    options = []
    for node in nodes:
        public = landing_egress.public_node(node)
        node_id = public['id']
        checked = ' selected' if node_id == selected else ''
        options.append(
            f'<option value="{html.escape(node_id, quote=True)}"{checked}>'
            f'{html.escape(public["name"])}</option>'
        )
        health = public.get('health') or {}
        health_label = str(health.get('status') or '未探测')
        rows.append(
            '<div><dt>' + html.escape(public['name']) + '</dt><dd>'
            '<code class="mono">'
            + html.escape(public['exit_ip'])
            + '</code>'
            + (' · ' + html.escape(public.get('isp', '')) if public.get('isp') else '')
            + (' · ' + html.escape(public.get('region', '')) if public.get('region') else '')
            + ' · '
            + html.escape(health_label)
            + '</dd></div>'
        )
    if password_session:
        action = (
            '<form method="post" action="/user/landing-egress/select" class="mt-md">'
            f'<input type="hidden" name="user_revision" value="{ctx.user_config_revision(cfg)}">'
            '<label>选择家宽出口<select name="egress_id" required>'
            + ''.join(options)
            + '</select></label>'
            '<button class="btn primary mt-sm" type="submit">切换出口</button>'
            '<div class="small faint mt-sm">切换前会验证真实出口 IP；60 秒内只能切换一次。</div>'
            '</form>'
        )
    else:
        action = '<div class="small faint mt-md">使用面板密码登录后可切换。</div>'
    return (
        '<aside class="plan-section" aria-label="真实家宽出口">'
        '<header class="section-head"><h2 class="section-title">真实家宽出口</h2></header>'
        '<dl class="user-kv">' + ''.join(rows) + '</dl>' + action + '</aside>'
    )


def render_landing_egresses(ctx: Context, host, flash=''):
    registry = ctx._landing_registry_or_empty()
    users = ctx.load_json(ctx.USERS_FILE, {})
    cards = []
    for node_id, node in sorted(registry.get('nodes', {}).items()):
        public = landing_egress.public_node(node)
        endpoint = f'{node["socks_ip"]}:{node["socks_port"]}'
        cards.append(
            '<tr><td>' + html.escape(public['name']) + '</td>'
            '<td><code class="mono">' + html.escape(endpoint) + '</code></td>'
            '<td><code class="mono">' + html.escape(public['exit_ip']) + '</code></td>'
            '<td><span class="badge '
            + ('badge-success">启用' if public.get('enabled') else 'badge-neutral">禁用')
            + '</span></td>'
            '<td><div class="row gap-sm">'
            '<form method="post" action="/admin/landing-egress/check">'
            f'<input type="hidden" name="id" value="{html.escape(node_id, quote=True)}">'
            '<button class="btn btn-ghost btn-sm" type="submit">健康检查</button></form>'
            '<form method="post" action="/admin/landing-egress/delete">'
            f'<input type="hidden" name="id" value="{html.escape(node_id, quote=True)}">'
            '<button class="btn danger-btn btn-sm" type="submit">删除</button></form>'
            '</div></td></tr>'
        )
    rows = ''.join(cards) or ('<tr><td colspan="5" class="empty">尚未配置家宽出口</td></tr>')
    access_forms = []
    registry_nodes = registry.get('nodes', {})
    if registry_nodes:
        for username, cfg in sorted(users.items()):
            if not isinstance(cfg, dict):
                continue
            allowed = cfg.get('landing_allowed_egress_ids', [])
            allowed = allowed if isinstance(allowed, list) else []
            choices = []
            for node_id, node in sorted(registry_nodes.items()):
                checked = ' checked' if node_id in allowed else ''
                disabled = ' disabled' if node.get('enabled') is not True else ''
                state = '（已禁用）' if disabled else ''
                choices.append(
                    '<label class="landing-access-choice">'
                    f'<input type="checkbox" name="egress_id" value="{html.escape(node_id, quote=True)}"{checked}{disabled}>'
                    f'<span>{html.escape(node.get("name", node_id))}{state}</span></label>'
                )
            access_forms.append(
                '<form method="post" action="/admin/user-landing-access" class="landing-access-row">'
                '<div class="landing-access-user"><strong>'
                f'{html.escape(str(username))}</strong>'
                '<span class="small faint">可授权一个或多个出口</span></div>'
                f'<input type="hidden" name="user" value="{html.escape(str(username), quote=True)}">'
                f'<input type="hidden" name="user_revision" value="{ctx.user_config_revision(cfg)}">'
                '<fieldset class="landing-access-choices"><legend class="sr-only">'
                f'{html.escape(str(username))} 可使用的家宽出口</legend>'
                + ''.join(choices)
                + '</fieldset><button class="btn btn-secondary btn-sm" type="submit">保存授权</button></form>'
            )
    if not registry_nodes:
        access_content = (
            '<div class="landing-empty">请先添加家宽出口节点，再为用户分配访问权限。</div>'
        )
    elif access_forms:
        access_content = '<div class="landing-access-list">' + ''.join(access_forms) + '</div>'
    else:
        access_content = '<div class="landing-empty">暂无用户</div>'
    flash_html = ctx.render_alert(flash) if flash else ''
    content = f'''{flash_html}
<section class="form-section landing-node-list" aria-labelledby="landing-node-list-title">
  <h2 class="form-section-title" id="landing-node-list-title">家宽出口节点</h2>
  <p class="form-section-desc">管理真实 SOCKS5 出口。密码仅写入服务器状态文件，不会在页面中回显。</p>
  <div class="data-table-wrap" tabindex="0" aria-label="家宽出口节点表格，可横向滚动"><table class="data-table"><caption class="sr-only">家宽出口节点列表</caption><thead><tr><th scope="col">名称</th><th scope="col">SOCKS5 入口</th><th scope="col">预期出口</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody>{rows}</tbody></table></div>
</section>
<section class="form-section" aria-labelledby="landing-node-form-title">
  <h2 class="form-section-title" id="landing-node-form-title">新增或更新节点</h2>
  <p class="form-section-desc">节点 ID 保存后保持不变；更新凭据时填写新值，留空则保留原值。</p>
  <form method="post" action="/admin/landing-egress/save" class="landing-node-form">
    <input type="hidden" name="registry_revision" value="{ctx.content_revision(registry)}">
    <div class="form-grid landing-node-grid">
      <div class="form-field"><label for="landing-node-id">节点 ID</label><input id="landing-node-id" name="id" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-node-name">显示名称</label><input id="landing-node-name" name="name" required></div>
      <div class="form-field"><label for="landing-socks-ip">SOCKS5 IP</label><input id="landing-socks-ip" name="socks_ip" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-socks-port">SOCKS5 端口</label><input id="landing-socks-port" name="socks_port" type="number" min="1" max="65535" required></div>
      <div class="form-field"><label for="landing-socks-username">SOCKS5 用户名</label><input id="landing-socks-username" name="socks_username" autocomplete="off"></div>
      <div class="form-field"><label for="landing-socks-password">SOCKS5 密码</label><input id="landing-socks-password" name="socks_password" type="password" autocomplete="new-password"></div>
      <div class="form-field"><label for="landing-expected-exit-ip">预期出口 IP</label><input id="landing-expected-exit-ip" name="expected_exit_ip" required autocomplete="off" spellcheck="false"></div>
      <div class="form-field"><label for="landing-isp">运营商</label><input id="landing-isp" name="isp"></div>
      <div class="form-field"><label for="landing-region">地区</label><input id="landing-region" name="region"></div>
    </div>
    <div class="landing-node-actions"><label class="switch"><input name="enabled" type="checkbox" value="1" checked>启用节点</label><button class="btn btn-primary" type="submit">保存节点</button></div>
  </form>
</section>
<section class="form-section" aria-labelledby="landing-access-title">
  <h2 class="form-section-title" id="landing-access-title">用户授权</h2>
  <p class="form-section-desc">用户只能在这里授权的节点中切换；SOCKS5 地址和凭据不会显示在用户面板。</p>
  {access_content}
</section>'''
    return ctx.render_admin_shell(
        'landing-egresses',
        '家宽出口',
        content,
        badge=host,
        subtitle='真实 SOCKS5 出口与授权',
    )
