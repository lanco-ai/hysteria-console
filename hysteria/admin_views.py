"""Administrator user table and dashboard presentation; no runtime service import."""

import html
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

import user_compat


@dataclass(frozen=True)
class Context:
    ADMIN_POLL_JS_ETAG: str
    CYCLE_LENGTH_MAX: int
    CYCLE_LENGTH_MIN: int
    ONLINE_FILE: Path
    USAGE_DAILY_FILE: Path
    USERS_FILE: Path
    _enabled_landing_public_nodes: Callable[..., object]
    _landing_registry_or_empty: Callable[..., object]
    base_quota_bytes: Callable[..., object]
    configured_max_devices: Callable[..., object]
    current_display_multiplier: Callable[..., object]
    cycle_start_for: Callable[..., object]
    daily_window_for_user: Callable[..., object]
    flash_text: Callable[..., object]
    fmt_bytes: Callable[..., object]
    get_cycle_length_days: Callable[..., object]
    get_settlement_day: Callable[..., object]
    icon: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    month_key: Callable[..., object]
    pct: Callable[..., object]
    preserved_raw_for_cycle: Callable[..., object]
    quota_extra_gb: Callable[..., object]
    render_admin_shell: Callable[..., object]
    render_alert: Callable[..., object]
    row_form: Callable[..., object]
    scaled_usage_for_user: Callable[..., object]
    sparkline_svg: Callable[..., object]
    user_config_revision: Callable[..., object]
    user_expiry_state: Callable[..., object]
    user_total_quota: Callable[..., object]


def row_form(
    ctx: Context, user, cfg, online, host, base_url, usage_month=None, daily=None, now=None
):
    tx, rx, used = ctx.scaled_usage_for_user(user, daily=daily, now=now)
    spark_cell = ''
    # Sparklines are initial-render only. The five-second overview payload
    # intentionally excludes SVG so polling does not rebuild this cell.
    if daily is not None:
        spark_cell = f'<td class="spark-cell" headers="users-col-trend" data-label="30 天趋势" data-role="spark">{ctx.sparkline_svg(ctx.daily_window_for_user(user, daily, days=30))}</td>'
    total = ctx.user_total_quota(cfg)
    quota_label = '不限' if total <= 0 else ctx.fmt_bytes(total)
    max_devices = ctx.configured_max_devices(cfg)
    user_revision = ctx.user_config_revision(cfg)
    revision_query = f'revision={user_revision}'
    device_limit_summary = '不限设备' if max_devices == 0 else f'{max_devices} 设备'
    online_device_summary = (
        f'在线 <span data-role="online">{int(online.get(user, 0) or 0)}</span> · 设备不限'
        if max_devices == 0
        else (
            f'在线 <span data-role="online">{int(online.get(user, 0) or 0)}</span>'
            f' / {max_devices} 设备'
        )
    )
    base_gb = (
        int(round(ctx.base_quota_bytes(cfg) / 1024 / 1024 / 1024))
        if ctx.base_quota_bytes(cfg) > 0
        else 0
    )
    extra_gb = ctx.quota_extra_gb(cfg)
    panel = f'{base_url}/panel/{user}?token={cfg.get("sub_token", "")}'
    sub_http = f'{base_url}/sub/{user}?token={cfg.get("sub_token", "")}'
    metered = user_compat.is_metered(cfg)
    tuic_allowed = user_compat.tuic_enabled(cfg)
    expiry = ctx.user_expiry_state(cfg, today=(now or ctx.local_now()).date())
    expires_at = expiry['expires_at']
    expired_badge = '<span class="badge badge-danger">已过期</span>' if expiry['expired'] else ''
    expires_preview = f' · {expiry["label"]}' if expires_at else ''
    extra_preview = f' · 加量 {extra_gb} GB' if extra_gb else ''
    note = str(cfg.get('note') or '')
    note_preview = f'<div class="small faint">{html.escape(note)}</div>' if note else ''
    percent = ctx.pct(used, total)
    bar_cls = 'unlimited' if total <= 0 else ('danger' if percent >= 90 else '')
    bar_w = '0.0' if total <= 0 else f'{percent:.1f}'
    user_esc = html.escape(user)
    guest_badge = '<span class="badge badge-info">按量</span>' if metered else ''
    tuic_badge = (
        '<span class="badge">TUIC</span>'
        if tuic_allowed
        else '<span class="badge badge-danger">TUIC 关闭</span>'
    )
    disabled = bool(cfg.get('disabled'))
    disabled_badge = (
        '<span class="badge badge-danger" data-role="disabled-badge">已停用</span>'
        if disabled
        else '<span class="badge badge-danger" data-role="disabled-badge" hidden>已停用</span>'
    )
    guest_preview = ' · 按量' if metered else ''
    quota_preview = '不限' if total <= 0 else f'{base_gb} GB{extra_preview}'
    summary_preview = f'<span class="summary-preview">{quota_preview} · {device_limit_summary}{guest_preview}{expires_preview}</span>'
    expires_attr = html.escape(expires_at, quote=True)
    note_attr = html.escape(note, quote=True)
    percent_label = '不限' if total <= 0 else f'{percent:.1f}%'
    if disabled:
        toggle_button = (
            f'<button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" '
            f'name="user" value="{user_esc}" data-user="{user_esc}" '
            f'formaction="/admin/toggle-user?{revision_query}&amp;desired=enabled" '
            'data-action="enable-user" '
            'title="恢复该用户的连接权限">启用</button>'
        )
    else:
        toggle_button = (
            f'<button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" '
            f'name="user" value="{user_esc}" data-user="{user_esc}" '
            f'formaction="/admin/toggle-user?{revision_query}&amp;desired=disabled" '
            'data-action="disable-user" '
            'title="临时停用：拒绝新连接并断开现有会话，不删除用户">暂停</button>'
        )
    online_n = int(online.get(user, 0) or 0)
    return f'''<tr data-user="{user_esc}" data-online="{online_n}" data-percent="{percent:.1f}" data-revision="{user_revision}">
<td headers="users-col-user" data-label="用户">
  <div class="row gap-sm" style="flex-wrap:nowrap;">
    <div class="user-avatar" aria-hidden="true">{html.escape(user[:1].upper())}</div>
    <div style="min-width:0;">
      <div class="bold">{user_esc} {guest_badge}{tuic_badge}{disabled_badge}{expired_badge}</div>
      <div class="small">{online_device_summary}</div>
      {note_preview}
    </div>
  </div>
</td>
{spark_cell}
<td headers="users-col-usage" data-label="本周期用量">
  <div class="row" style="justify-content:space-between;margin-bottom:4px;">
    <span class="bold" data-role="used">{ctx.fmt_bytes(used)}</span>
    <span class="small">/ {quota_label}</span>
  </div>
  <div class="mini-bar"><div class="mini-fill {bar_cls}" data-role="bar" role="progressbar"
       aria-label="{user_esc} 本周期流量" aria-valuemin="0" aria-valuemax="100"
       aria-valuenow="{bar_w}" aria-valuetext="{html.escape(percent_label)}" style="width:{bar_w}%"></div></div>
  <div class="small mt-sm" data-role="detail">{percent_label} · ↑{ctx.fmt_bytes(tx)} ↓{ctx.fmt_bytes(rx)}</div>
</td>
<td headers="users-col-actions" data-label="操作">
<div class="edit-user-control">
  <button type="button" class="btn secondary btn-sm edit-user"
          data-edit-user="{user_esc}" data-user-revision="{user_revision}" data-max-devices="{max_devices}"
          data-quota-gb="{base_gb}" data-quota-extra-gb="{extra_gb}"
          data-expires-at="{expires_attr}" data-note="{note_attr}"
          data-landing-isp="{html.escape(user_compat.landing_field(cfg, 'landing_isp'), quote=True)}"
          data-landing-region="{html.escape(user_compat.landing_field(cfg, 'landing_region'), quote=True)}"
          data-landing-note="{html.escape(user_compat.landing_field(cfg, 'landing_note'), quote=True)}"
          data-landing-ip="{html.escape(user_compat.landing_field(cfg, 'landing_ip'), quote=True)}"
          data-metered="{'1' if metered else '0'}" data-tuic-enabled="{'1' if tuic_allowed else '0'}">编辑套餐</button>
  {summary_preview}
</div>
<div class="row gap-sm mt-sm user-actions">
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/reset-usage?{revision_query}"
          data-action="reset-user-usage"
          title="清空该用户已用流量，且从服务器总流量中扣除">清流量</button>
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/refresh-usage?{revision_query}"
          data-action="refresh-user-usage"
          title="清空该用户已用流量，但保留在服务器总流量中">刷新流量</button>
  <button class="btn ghost btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/rotate-token?{revision_query}" data-action="rotate-user-token"
          title="重置该用户订阅令牌，旧订阅/面板链接立即失效">重置订阅</button>
  {toggle_button}
  <button class="btn danger-btn btn-sm user-action" type="submit" form="user-action-form" name="user"
          value="{user_esc}" data-user="{user_esc}" formaction="/admin/delete?{revision_query}" data-action="delete-user">删除</button>
</div>
<div class="row-error small" style="display:none;color:var(--danger);margin-top:4px;"></div>
</td>
<td class="link-cell" headers="users-col-links" data-label="链接">
  <div class="link-row">
    <a href="{html.escape(panel)}" target="_blank" rel="noopener">{ctx.icon('dashboard')}<span>面板</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(panel)}"
            title="复制专属面板链接（首次打开后地址栏不再含密钥）" aria-label="复制 {user_esc} 的专属面板链接">{ctx.icon('copy')}<span class="copy-label">复制专属面板</span></button>
  </div>
  <div class="link-row">
    <a href="{html.escape(sub_http)}" target="_blank" rel="noopener">{ctx.icon('open')}<span>订阅</span></a>
    <button type="button" class="btn ghost btn-sm copy-link" data-copy="{html.escape(sub_http)}"
            title="复制订阅链接" aria-label="复制 {user_esc} 的订阅链接">{ctx.icon('copy')}</button>
  </div>
</td>
</tr>'''


def render_admin(
    ctx: Context, host, base_url, flash='', *, create_draft=None, create_error_field=''
):
    users = ctx.load_json(ctx.USERS_FILE, {})
    landing_registry = ctx._landing_registry_or_empty()
    online = ctx.load_json(ctx.ONLINE_FILE, {})
    now = ctx.local_now()
    mk = ctx.month_key(now)
    daily = ctx.load_json(ctx.USAGE_DAILY_FILE, {})
    total_used = sum(ctx.scaled_usage_for_user(u, daily=daily, now=now)[2] for u in users)
    total_used += int(ctx.preserved_raw_for_cycle(now=now) * ctx.current_display_multiplier())
    settlement_day = ctx.get_settlement_day()
    cycle_length = ctx.get_cycle_length_days()
    cycle_start = ctx.cycle_start_for(now)
    cycle_end = cycle_start + timedelta(days=cycle_length - 1)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_range = f'{cycle_start.strftime("%m/%d")} → {cycle_end.strftime("%m/%d")} · 第 {cycle_day}/{cycle_length} 天'
    settle_form = (
        f'<form method="post" action="/admin/cycle-config" class="inline-form-row cycle-config-form" '
        f'data-confirm="更改结算日或周期会重新锚定计费日历，确认保存？" style="margin:0;">'
        f'<label for="cycle-day" class="small" style="margin-right:6px;">结算日</label>'
        f'<input id="cycle-day" name="day" type="number" min="1" max="28" value="{settlement_day}" '
        f'style="width:60px;margin-right:6px;" required>'
        f'<label for="cycle-length" class="small" style="margin-right:6px;">周期</label>'
        f'<input id="cycle-length" name="length" type="number" min="{ctx.CYCLE_LENGTH_MIN}" max="{ctx.CYCLE_LENGTH_MAX}" '
        f'value="{cycle_length}" style="width:60px;margin-right:2px;" required>'
        f'<span class="small" style="margin-right:6px;">天</span>'
        f'<button class="btn ghost btn-sm" type="submit">保存</button>'
        f'</form>'
    )
    recovering_create = isinstance(create_draft, dict)
    if recovering_create:
        # Only the explicit, non-sensitive fields below are ever read back from
        # the draft. Passwords and tokens therefore cannot become HTML values.
        draft = create_draft
        alert = ''
        create_error = ctx.render_alert(
            ctx.flash_text(flash),
            'err',
            element_id='create-add-error',
        )
    else:
        draft = {}
        alert = ctx.render_alert(
            ctx.flash_text(flash),
            'err' if flash.startswith('err:') else 'flash',
        )
        create_error = ''

    def draft_value(name, default=''):
        return html.escape(str(draft.get(name, default)), quote=True)

    def validation_attrs(field_id):
        if recovering_create and create_error_field == field_id:
            return ' aria-invalid="true" aria-describedby="create-add-error" autofocus'
        return ''

    create_user = draft_value('user')
    create_quota_gb = draft_value('quota_gb', 150)
    create_quota_extra_gb = draft_value('quota_extra_gb', 0)
    create_expires_at = draft_value('expires_at')
    create_note = draft_value('note')
    create_landing_initial = str(draft.get('landing_initial_egress_id') or '')
    landing_options = ['<option value="">暂不分配</option>']
    for public_node in ctx._enabled_landing_public_nodes(landing_registry):
        node_id = str(public_node['id'])
        selected = ' selected' if node_id == create_landing_initial else ''
        landing_options.append(
            f'<option value="{html.escape(node_id, quote=True)}"{selected}>'
            f'{html.escape(public_node["name"])}</option>'
        )
    if len(landing_options) > 1:
        create_landing_field = (
            '<div class="form-field"><label for="create-landing-initial-egress">'
            '初始家宽出口</label><select class="select" '
            'id="create-landing-initial-egress" name="landing_initial_egress_id"'
            f'{validation_attrs("create-landing-initial-egress")}>'
            + ''.join(landing_options)
            + '</select><span class="hint">选中后立即授权并设为初始出口；后续可增加更多节点</span></div>'
        )
    else:
        create_landing_field = (
            '<div class="form-field"><label for="create-landing-initial-egress">'
            '初始家宽出口</label><select class="select" '
            'id="create-landing-initial-egress" name="landing_initial_egress_id" disabled>'
            '<option value="">暂无可用节点</option></select>'
            '<span class="hint"><a href="/admin/landing-egresses">先添加或启用家宽出口节点</a></span></div>'
        )
    create_open = ' open' if recovering_create else ''
    create_guest_checked = (
        ' checked' if (bool(draft.get('guest')) if recovering_create else True) else ''
    )
    create_tuic_checked = ' checked' if bool(draft.get('tuic_enabled')) else ''
    rows = ''.join(
        ctx.row_form(u, cfg, online, host, base_url, daily=daily, now=now)
        for u, cfg in users.items()
    )
    if rows:
        rows += '<tr id="filter-empty" hidden><td colspan="5" class="empty">没有符合当前筛选条件的用户</td></tr>'
    else:
        rows = '<tr><td colspan="5" class="empty">暂无用户，使用下方表单创建第一个用户</td></tr>'
    content = f'''{alert}
<div class="overview-stats">
  <div class="overview-stat">
    <div class="label">本周期总流量</div>
    <div class="value" id="total-used">{ctx.fmt_bytes(total_used)}</div>
    <div class="sub">{html.escape(cycle_range)}</div>
  </div>
  <div class="overview-stat">
    <div class="label">计费周期</div>
    <div class="value">{mk}</div>
    <div class="sub">每 {cycle_length} 天结算 · 第 {settlement_day} 日</div>
  </div>
  <div class="overview-stat">
    <div class="label">快速操作</div>
    <form method="post" action="/admin/reset-usage-all" data-action="reset-all">
      <button class="btn btn-sm danger-btn" type="submit" style="margin-top:10px;">清空本周期用量</button>
    </form>
    <div class="quick-actions">
      <a class="btn btn-sm" href="/admin/usage.csv?window=cycle">导出 CSV</a>
    </div>
  </div>
</div>
<!-- Shared hidden form used by all per-user action buttons (清流量 / 刷新流量 /
     重置订阅 / 暂停 / 启用 / 删除).  Buttons declare form="user-action-form"
     so clicks route through this form; JS reads ev.submitter to determine which
     action (formaction) to POST.  Must appear before .users-section so the
     form is in the DOM when any button is clicked. -->
<form method="post" id="user-action-form" hidden></form>
<!-- Edit-user dialog: opened by .edit-user buttons via showModal().  The JS
     reads data-* from the clicked button and pre-fills every field.  Must not
     expose sub_token or password_hash. -->
<dialog id="user-edit-dialog" class="admin-dialog">
  <div class="dialog-inner">
    <div class="dialog-head">
      <h2 class="dialog-title" id="user-edit-title">编辑用户</h2>
      <button type="button" class="dialog-close" data-dialog-close aria-label="关闭">×</button>
    </div>
      <form method="post" id="user-edit-form" action="/admin/update">
      <input type="hidden" name="user" id="edit-user-name">
      <input type="hidden" name="user_revision" id="edit-user-revision">
      <div class="form-grid">
        <div class="form-field"><label for="edit-max-devices">最大设备数</label>
          <input id="edit-max-devices" name="max_devices" type="number" min="0" max="999" value="2"></div>
        <div class="form-field"><label for="edit-quota-gb">流量上限 GB</label>
          <input id="edit-quota-gb" name="quota_gb" type="number" min="0" max="10240" value="150"></div>
        <div class="form-field"><label for="edit-quota-extra-gb">加量包 GB</label>
          <input id="edit-quota-extra-gb" name="quota_extra_gb" type="number" min="0" max="10240" value="0"></div>
        <div class="form-field"><label for="edit-expires-at">到期日</label>
          <input id="edit-expires-at" name="expires_at" type="date" min="2000-01-01" max="2099-12-31"><span class="hint">留空 = 不限期；年份范围 2000–2099</span></div>
        <div class="form-field" style="grid-column:1/-1"><label for="edit-note">备注</label>
          <input id="edit-note" name="note" maxlength="200" placeholder="可选"></div>
        <div class="form-field"><label for="edit-landing-isp">落地运营商</label>
          <input id="edit-landing-isp" name="landing_isp" maxlength="120" placeholder="可选，仅展示"></div>
        <div class="form-field"><label for="edit-landing-region">落地地区</label>
          <input id="edit-landing-region" name="landing_region" maxlength="120" placeholder="可选，仅展示"></div>
        <div class="form-field"><label for="edit-landing-ip">家宽 IP</label>
          <input id="edit-landing-ip" name="landing_ip" maxlength="45" autocomplete="off" spellcheck="false" placeholder="可选，仅支持 IPv4 / IPv6"></div>
        <div class="form-field" style="grid-column:1/-1"><label for="edit-landing-note">落地说明</label>
          <input id="edit-landing-note" name="landing_note" maxlength="120" placeholder="可选，仅展示，不影响出口"></div>
        <div class="form-field"><label for="edit-panel-password">面板密码</label>
          <input id="edit-panel-password" name="panel_password" type="password" minlength="8" maxlength="256"
                 autocomplete="new-password" placeholder="留空保持不变"><span class="hint">至少 8 位</span></div>
        <div class="form-field"><label for="edit-proxy-password">代理密码</label>
          <input id="edit-proxy-password" name="password" type="password" maxlength="256"
                 autocomplete="new-password" placeholder="留空保持不变"></div>
      </div>
      <div class="form-options">
        <label class="switch"><input type="checkbox" name="guest" id="edit-guest">按量用户</label>
        <label class="switch"><input type="checkbox" name="tuic_enabled" id="edit-tuic-enabled">允许 TUIC</label>
      </div>
      <div class="dialog-foot">
        <button type="button" class="btn ghost btn-sm" data-dialog-close>取消</button>
        <button type="submit" class="btn primary btn-sm">保存更改</button>
      </div>
    </form>
  </div>
</dialog>
<div class="users-section">
  <div class="users-header">
    <h2 class="users-title">用户列表</h2>
    <div class="users-toolbar">
      <input id="user-filter" type="search" placeholder="搜索…" aria-label="搜索用户名" autocomplete="off" class="user-filter-input">
      <div class="filter-chips" role="group" aria-label="状态筛选">
        <button type="button" class="chip active" data-filter="all" aria-pressed="true">全部</button>
        <button type="button" class="chip" data-filter="online" aria-pressed="false">在线</button>
        <button type="button" class="chip" data-filter="over" aria-pressed="false">超限</button>
      </div>
      <span class="filter-count" id="filter-count" role="status" aria-live="polite">{len(users)} 用户</span>
    </div>
  </div>
  <div class="small faint mt-sm">“复制专属面板”会复制每位用户各自的安全入口；打开后地址栏会安全归一为 <code>/user/panel</code>。</div>
  <div class="users-table-wrap">
    <table class="users-table" data-user-count="{len(users)}"><caption class="sr-only">用户、套餐用量、管理操作与订阅链接</caption><thead><tr><th>用户</th><th>趋势</th><th>用量</th><th>操作</th><th>链接</th></tr></thead><tbody>{rows}</tbody></table>
  </div>
</div>
<details class="create-section"{create_open}>
  <summary class="create-toggle">+ 新增用户</summary>
  <div class="create-form">
    {create_error}
    <form method="post" action="/admin/add" class="inline-form">
      <div class="create-grid">
        <div class="form-field"><label for="create-user">用户名</label><input id="create-user" name="user" value="{create_user}" maxlength="64" required autocomplete="off"{validation_attrs('create-user')}></div>
        <div class="form-field"><label for="create-panel-password">面板密码</label><input id="create-panel-password" name="panel_password" type="password" minlength="8" maxlength="256" autocomplete="new-password" placeholder="可选"{validation_attrs('create-panel-password')}><span class="hint">至少 8 位</span></div>
        <div class="form-field"><label for="create-proxy-password">代理密码</label><input id="create-proxy-password" name="password" type="password" maxlength="256" autocomplete="new-password" placeholder="可选"{validation_attrs('create-proxy-password')}></div>
        <div class="form-field"><label for="create-quota-gb">流量上限 GB</label><input id="create-quota-gb" name="quota_gb" type="number" value="{create_quota_gb}" min="0" max="10240" required{validation_attrs('create-quota-gb')}><span class="hint">0 = 不限</span></div>
        <div class="form-field"><label for="create-quota-extra-gb">加量包 GB</label><input id="create-quota-extra-gb" name="quota_extra_gb" type="number" value="{create_quota_extra_gb}" min="0" max="10240" required{validation_attrs('create-quota-extra-gb')}></div>
        <div class="form-field"><label for="create-expires-at">到期日</label><input id="create-expires-at" name="expires_at" type="date" value="{create_expires_at}" min="2000-01-01" max="2099-12-31"><span class="hint">留空 = 不限期；年份范围 2000–2099</span></div>
        {create_landing_field}
        <div class="form-field" style="grid-column:1/-1"><label for="create-note">备注</label><input id="create-note" name="note" value="{create_note}" maxlength="200" placeholder="可选"></div>
      </div>
      <div class="form-options">
        <label class="switch"><input type="checkbox" name="guest"{create_guest_checked}>按量用户</label>
        <label class="switch"><input type="checkbox" name="tuic_enabled"{create_tuic_checked}>允许 TUIC</label>
      </div>
      <button class="btn" type="submit">创建</button>
    </form>
  </div>
</details>
<script src="/static/admin-poll.js?v={ctx.ADMIN_POLL_JS_ETAG.strip('"')}" defer></script>
'''
    poll_status = (
        '<button class="badge poll-status poll-status-button" data-role="admin-poll-status" '
        'type="button" title="立即更新">自动更新 · 30 s</button>'
        '<span class="sr-only" id="admin-poll-announcer" role="status" aria-live="polite"></span>'
    )
    return ctx.render_admin_shell(
        'dashboard',
        '总览',
        content,
        badge=f'{len(users)} 个用户',
        subtitle=f'{host} · 计费周期 {mk}',
        topbar_extra=settle_form + poll_status,
    )
