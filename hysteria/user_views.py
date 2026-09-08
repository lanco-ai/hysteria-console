"""User panel and password presentation; no runtime service import."""

import html
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import user_compat
import web_assets


@dataclass(frozen=True)
class Context:
    ONLINE_FILE: Path
    PASSWORD_MAX_LENGTH: int
    PASSWORD_MIN_LENGTH: int
    SUBSCRIPTION_PROFILES: dict
    USAGE_DAILY_FILE: Path
    USER_SESSION_PANEL_PASSWORD: str
    _cycle_reset_info: Callable[..., object]
    configured_max_devices: Callable[..., object]
    daily_window_for_user: Callable[..., object]
    fmt_bytes: Callable[..., object]
    html_page: Callable[..., object]
    icon: Callable[..., object]
    load_json: Callable[..., object]
    local_now: Callable[..., object]
    pct: Callable[..., object]
    render_alert: Callable[..., object]
    render_landing_egress_selector: Callable[..., object]
    render_subscription_profile_links: Callable[..., object]
    scaled_usage_for_user: Callable[..., object]
    sparkline_svg: Callable[..., object]
    user_expiry_state: Callable[..., object]
    user_total_quota: Callable[..., object]


def render_user_change_password(ctx: Context, host, user, msg=''):
    messages = {
        'current password wrong': '当前密码不正确',
        'new password short': '新密码至少需要 8 位',
        'new password long': f'新密码不能超过 {ctx.PASSWORD_MAX_LENGTH} 位',
        'new password mismatch': '两次输入的新密码不一致',
        'new password same': '新密码不能与当前密码相同',
    }
    alert = ctx.render_alert(messages.get(msg, msg), 'err') if msg else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/user/panel" class="auth-header-back">← 返回面板</a>
  </div>
</header>

<div class="auth-scene">
  <div class="auth-body">
    <div class="auth-brand">
      <div class="auth-brand-content">
        <div class="auth-brand-eyebrow">Security · 安全</div>
        <h1 class="auth-brand-title">保护你的<br>账户安全。</h1>
        <div class="auth-brand-features">
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            使用强密码
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
            保护订阅访问
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>
            实时生效
          </div>
        </div>
      </div>
    </div>

    <div class="auth-form-panel">
      <div class="auth-card">
        <div class="auth-card-brand">
          <div class="auth-card-logo">H</div>
          <div class="auth-card-brand-text">
            <strong>Hysteria</strong>
            <small>用户面板</small>
          </div>
        </div>
        <h2 class="auth-card-title">修改面板密码</h2>
        <p class="auth-card-subtitle">{html.escape(user)} · {html.escape(host)}</p>
        {alert}
      <form method="post" action="/user/change-password" class="auth-form">
        <div class="field">
          <label class="label" for="user-current-password">当前密码</label>
          <input class="input" id="user-current-password" name="current" type="password" required maxlength="{ctx.PASSWORD_MAX_LENGTH}" autofocus autocomplete="current-password" placeholder="输入当前密码">
        </div>
        <div class="field">
          <label class="label" for="user-new-password">新密码</label>
          <input class="input" id="user-new-password" name="new" type="password" required minlength="{ctx.PASSWORD_MIN_LENGTH}" maxlength="{ctx.PASSWORD_MAX_LENGTH}" autocomplete="new-password" placeholder="输入新密码">
        </div>
        <div class="field">
          <label class="label" for="user-confirm-password">再次输入新密码</label>
          <input class="input" id="user-confirm-password" name="confirm" type="password" required minlength="{ctx.PASSWORD_MIN_LENGTH}" maxlength="{ctx.PASSWORD_MAX_LENGTH}" autocomplete="new-password" placeholder="再次输入新密码">
        </div>
        <button class="btn btn-primary btn-full" type="submit">保存新密码</button>
        <div class="auth-note">使用至少 8 位、且未在其他网站使用的密码。保存后其他设备上的用户面板会话将自动退出。</div>
      </form>
      <a class="auth-back" href="/user/panel">返回用户面板</a>
    </div>
  </div>
</div>
</div>'''
    return ctx.html_page('修改面板密码', body, body_class='page-auth')


def render_user_panel(
    ctx: Context, host, base_url, user, token, cfg, *, session_auth=False, session_kind, notice=''
):
    now = ctx.local_now()
    daily = ctx.load_json(ctx.USAGE_DAILY_FILE, {})
    tx, rx, used = ctx.scaled_usage_for_user(user, daily=daily, now=now)
    total = ctx.user_total_quota(cfg)
    remain = max(total - used, 0) if total > 0 else -1
    online = int(ctx.load_json(ctx.ONLINE_FILE, {}).get(user, 0) or 0)
    percent = ctx.pct(used, total)
    quota_unlimited = total <= 0
    cls = 'unlimited' if quota_unlimited else ('danger' if percent >= 90 else '')
    total_label = '不限' if quota_unlimited else ctx.fmt_bytes(total)
    remain_label = '不限' if quota_unlimited else ctx.fmt_bytes(remain)
    percent_label = '不限' if quota_unlimited else f'{percent:.2f}%'
    reset_date, days_left, cycle_len = ctx._cycle_reset_info(now)
    spark = ctx.sparkline_svg(ctx.daily_window_for_user(user, daily, days=30, today=now.date()))
    panel_path = '/user/panel' if session_auth else f'/panel/{user}?token={token}'
    json_path = '/user/panel.json' if session_auth else f'/panel/{user}.json?token={token}'
    panel_http = f'{base_url}{panel_path}'
    max_devices_n = ctx.configured_max_devices(cfg)
    max_devices_label = '· 设备不限' if max_devices_n == 0 else f'/ {max_devices_n}'
    is_disabled = bool(cfg.get('disabled'))
    expiry = ctx.user_expiry_state(cfg, today=now.date())
    is_expired = bool(expiry['expired'])
    disabled_banner = ''
    if is_disabled:
        disabled_banner = ctx.render_alert('账号已停用，请联系管理员', 'err')
    elif is_expired:
        disabled_banner = ctx.render_alert('账号已到期，请联系管理员续费', 'err')
    inactive = is_disabled or is_expired
    notice_messages = {
        'token_rotated': (
            'Token 已重置，旧订阅与面板链接已失效。系统已请求断开'
            '现有 Hysteria 连接，并将在短暂延迟后再次复核；'
            '静态代理的新凭证正在应用。'
        ),
        'token_rotated_sync_pending': (
            'Token 已重置。已确认暂停 Xray/TUIC，待安全同步后恢复；'
            'Hysteria 已切换为新凭证，现有连接的断开请求正在复核。'
        ),
        'token_rotated_static_pending': (
            'Token 已重置。受影响的静态代理因重载未能安排而已确认暂停，'
            '将在下一次安全同步后恢复；Hysteria 连接断开请求正在复核。'
        ),
        'token_rotated_revocation_retry': (
            'Token 已重置，但未能确认所有旧连接或静态代理均已停止。'
            '系统会持续自动重试，直至确认完成；完成前请勿继续使用'
            '旧的 Xray/TUIC 凭证。'
        ),
        'token_rotated_session_recovery': (
            'Token 已重置，但新的登录会话未能保存。请立即复制下方的'
            '新订阅链接或面板链接；原浏览器可在 5 分钟内重放同一'
            '操作取回这枚 Token。'
        ),
        'token_rotated_sync_pending_recovery': (
            'Token 已重置，但新的登录会话未能保存；同时 Xray/TUIC '
            '正在等待安全同步。请立即复制下方的新订阅链接或面板'
            '链接；原浏览器可在 5 分钟内重放同一操作取回这枚 Token。'
        ),
        'token_rotated_static_pending_recovery': (
            'Token 已重置，但新的登录会话未能保存；受影响的静态代理'
            '已确认暂停并等待安全同步。请立即复制下方的新链接；'
            '原浏览器可在 5 分钟内重放同一操作取回这枚 Token。'
        ),
        'token_rotated_revocation_retry_recovery': (
            'Token 已重置，但新的登录会话未能保存，且旧连接撤销仍在'
            '后台重试。请立即复制下方的新链接；原浏览器可在 5 分钟'
            '内重放同一操作取回这枚 Token。'
        ),
    }
    notice_code = str(notice or '')
    notice_message = notice_messages.get(notice_code, '')
    notice_banner = ctx.render_alert(
        notice_message,
        'flash' if notice_code == 'token_rotated' else 'err',
    )
    if inactive:
        import_assistant = (
            '<div class="card mt-md">'
            '<h2 class="section-title">订阅操作已暂停</h2>'
            '<div class="small">当前账号不可拉取订阅、生成二维码或重置令牌。'
            '恢复或续费后，这些操作会重新出现；历史用量仍可查看。</div>'
            '</div>'
        )
    else:
        import_assistant = ctx.render_subscription_profile_links(base_url, user, token)
    password_session = session_auth and session_kind == ctx.USER_SESSION_PANEL_PASSWORD
    account_action = ''
    if session_auth:
        change_password_action = (
            f'<a class="btn ghost btn-sm" href="/user/change-password">'
            f'{ctx.icon("lock")}<span>修改密码</span></a>'
            if password_session and not inactive
            else ''
        )
        account_action = (
            f'<div class="row gap-sm mt-sm">'
            f'{change_password_action}'
            f'<form method="post" action="/user/logout" '
            f'class="inline-form-row">'
            f'<button class="btn ghost btn-sm" type="submit">'
            f'{ctx.icon("logout")}<span>退出登录</span></button></form>'
            f'</div>'
        )
    # Inactive accounts never load the polling script or expose a poll URL.
    poll_attrs = '' if inactive else f' data-poll-url="{html.escape(json_path, quote=True)}"'
    poll_script = '' if inactive else web_assets.script_tag('user-poll')
    if password_session:
        panel_link_hint = '此地址不含订阅令牌，其他设备需要先使用用户名和面板密码登录。'
    elif session_auth:
        panel_link_hint = (
            '此地址不含订阅令牌，仅当前设备的登录会话可直接访问；其他设备仍需使用原面板链接。'
        )
    else:
        panel_link_hint = '重置后旧链接立即失效，需用新链接重新订阅。'
    rotation_request_id = secrets.token_urlsafe(24)

    # Rotate-token form goes into the Account & Security section.
    token_action_html = ''
    session_url_html = ''
    if not inactive:
        token_action_html = (
            f'<form method="post" action="/panel/{html.escape(user)}/rotate-token" '
            f'data-action="rotate-token" class="inline-form-row">'
            f'<input type="hidden" name="token" value="{html.escape(token)}">'
            f'<input type="hidden" name="rotation_id" value="{html.escape(rotation_request_id)}">'
            f'<button class="btn danger-btn btn-sm" type="submit">'
            f'{ctx.icon("lock")}<span>重置 Token</span></button>'
            f'</form>'
        )
        # Session URL stays inside a details block — collapsed by default,
        # never shown as naked large body text.
        session_url_html = (
            f'<details class="account-session-details">'
            f'<summary>查看当前会话详情</summary>'
            f'<div class="copy-mono"><code class="mono">{html.escape(panel_http)}</code></div>'
            f'<div class="small faint mt-sm">{html.escape(panel_link_hint)}</div>'
            f'</details>'
        )
    initial_poll_status = '已暂停更新' if inactive else '自动更新 · 30 s'

    # Cycle day index (e.g. "第 6 / 30 天") — purely cosmetic, derived from
    # the same cycle data the page already exposes via reset_date.
    days_into_cycle = max(1, cycle_len - max(days_left, 0))

    def _status_label():
        if is_disabled:
            return '停用', 'is-error'
        if is_expired:
            return '已到期', 'is-error'
        return '正常', 'is-live'

    account_status_label, account_status_class = _status_label()

    profile_meta = ctx.SUBSCRIPTION_PROFILES.get('default', {})
    profile_label = profile_meta.get('label', '默认')
    landing = user_compat.landing_fields(cfg)
    landing_section = ''
    if landing:
        rows = ''
        if landing.get('landing_isp'):
            rows += f'<div><dt>运营商</dt><dd>{html.escape(landing["landing_isp"])}</dd></div>'
        if landing.get('landing_region'):
            rows += f'<div><dt>地区</dt><dd>{html.escape(landing["landing_region"])}</dd></div>'
        if landing.get('landing_ip'):
            rows += (
                '<div><dt>家宽 IP</dt><dd><code class="mono">'
                f'{html.escape(landing["landing_ip"])}</code></dd></div>'
            )
        if landing.get('landing_note'):
            rows += f'<div><dt>说明</dt><dd>{html.escape(landing["landing_note"])}</dd></div>'
        landing_section = (
            '<aside class="plan-section" aria-label="落地家宽">'
            '<header class="section-head">'
            '<h2 class="section-title">落地家宽</h2>'
            '</header>'
            f'<dl class="user-kv">{rows}</dl>'
            '</aside>'
        )
    real_landing_section = ctx.render_landing_egress_selector(
        cfg,
        password_session=password_session,
    )

    body = f'''<div class="wrap user-panel"{poll_attrs}>
{notice_banner}
{disabled_banner}
<header class="user-panel-header">
  <div class="user-panel-brand">
    <div>
      <div class="small faint">Hysteria</div>
      <h1 class="user-panel-title">个人控制台</h1>
      <div class="user-panel-subtitle faint">订阅、用量与设备</div>
    </div>
  </div>
  <div class="user-panel-account">
    <div class="user-panel-name mono">{html.escape(user)}</div>
    <div class="user-panel-status">
      <span class="badge {account_status_class}">{html.escape(account_status_label)}</span>
    </div>
    <div class="user-panel-actions">
      {account_action}
    </div>
  </div>
</header>

<section class="usage-section" aria-label="本周期用量">
  <header class="section-head">
    <h2 class="section-title">本周期用量</h2>
    <div class="poll-status small" data-role="poll-status">{initial_poll_status}</div>
    <span class="sr-only" id="panel-status-announcer" role="status" aria-live="polite"></span>
  </header>
  <div class="usage-kpis">
    <div class="usage-kpi">
      <div class="k">已用流量</div>
      <div class="v" data-role="used">{ctx.fmt_bytes(used)}</div>
      <div class="sub">{('不限额' if quota_unlimited else f'{percent:.1f}%')}</div>
    </div>
    <div class="usage-kpi">
      <div class="k">剩余额度</div>
      <div class="v" data-role="remain">{remain_label}</div>
      <div class="sub">{('100.0%' if quota_unlimited else (f'{100 - percent:.1f}%' if percent > 0 else '0.0%'))}</div>
    </div>
    <div class="usage-kpi">
      <div class="k">计费周期</div>
      <div class="v usage-date">{html.escape(reset_date)}</div>
      <div class="sub">第 {days_into_cycle} / {cycle_len} 天 · 还剩 {days_left} 天</div>
    </div>
  </div>
  <div class="usage-progress">
    <div class="usage-progress-head">
      <span class="k">本周期</span>
      <span class="bold numeric" data-role="percent">{percent_label}</span>
    </div>
    <div class="bar"><div class="fill {cls}" data-role="bar" role="progressbar"
         aria-label="本周期流量" aria-valuemin="0" aria-valuemax="100"
         aria-valuenow="{'0' if quota_unlimited else f'{percent:.2f}'}"
         aria-valuetext="{html.escape(percent_label)}"
         style="width:{'0' if quota_unlimited else f'{percent:.2f}'}%"></div></div>
    <div class="small mt-sm" data-role="txrx">上传 {ctx.fmt_bytes(tx)} · 下载 {ctx.fmt_bytes(rx)}</div>
  </div>
</section>

<section class="connection-section" aria-label="连接与订阅">
  <header class="section-head">
    <h2 class="section-title">连接与订阅</h2>
  </header>
  {import_assistant}
</section>

<aside class="plan-section" aria-label="套餐与设备">
  <header class="section-head">
    <h2 class="section-title">套餐与设备</h2>
  </header>
  <dl class="user-kv">
    <div><dt>套餐</dt><dd>{html.escape(profile_label)}</dd></div>
    <div><dt>总额度</dt><dd class="mono">{total_label}</dd></div>
    <div><dt>设备</dt><dd><span data-role="online">{online}</span> <span class="faint" data-role="device-limit">{html.escape(max_devices_label)}</span></dd></div>
    <div><dt>周期</dt><dd>{cycle_len} 天</dd></div>
    <div><dt>重置</dt><dd class="mono">{html.escape(reset_date)}</dd></div>
    <div><dt>有效期</dt><dd>{html.escape(expiry['label'])}</dd></div>
  </dl>
</aside>
{landing_section}
{real_landing_section}

<section class="trend-section" aria-label="近 30 天用量趋势">
  <header class="section-head">
    <h2 class="section-title">近 30 天用量趋势</h2>
    <div class="small faint">轻量辅助阅读</div>
  </header>
  <div class="trend-body">{spark}</div>
</section>

<section class="account-section" aria-label="账户与安全">
  <header class="section-head">
    <h2 class="section-title">账户与安全</h2>
  </header>
  <div class="account-actions">
    {account_action}
    {token_action_html}
  </div>
  {session_url_html}
</section>

</div>
{web_assets.script_tag('user-panel')}
{poll_script}'''
    return ctx.html_page(f'{user} 用户面板', body)
