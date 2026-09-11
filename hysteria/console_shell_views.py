"""console shell views with explicit presentation dependencies."""

import html
from dataclasses import dataclass
from typing import Callable

import web_assets


@dataclass(frozen=True)
class Context:
    _SIDEBAR_NAV: list
    html_page: Callable[..., object]
    icon: Callable[..., object]


def render_panel_link_required(ctx: Context):
    """Shown when /user/panel is opened without a user session.

    Share recipients should not land on the admin/user login form. This page
    has no username, no token field, and no credential prompt — only a
    pointer back to the dedicated login route for password users who
    arrived here by mistake.
    """
    body = """<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>
<div class="auth-scene auth-scene-single">
  <div class="auth-card">
    <div class="auth-card-brand">
      <div class="auth-card-logo">H</div>
      <div class="auth-card-brand-text">
        <strong>Hysteria</strong>
        <small>用户面板</small>
      </div>
    </div>
    <h1 class="auth-card-title">请使用管理员提供的专属链接</h1>
    <p class="auth-card-subtitle">此页面需要通过管理员发给你的专属面板链接打开。链接打开后地址栏不再包含密钥。</p>
    <a class="auth-back" href="/login">前往登录</a>
  </div>
</div>"""
    return ctx.html_page('专属链接 · Hysteria', body, body_class='page-auth')


def render_logout_confirmation(ctx: Context, host, *, user_panel=False):
    action = '/user/logout' if user_panel else '/logout'
    cancel = '/user/panel' if user_panel else '/admin'
    title = '退出用户面板？' if user_panel else '退出管理后台？'
    body = f'''<div class="auth-page">
<div class="auth-wrap">
<div class="auth-card">
  <div class="auth-brand">
    <span class="auth-logo">H</span>
    <div class="auth-brand-text">
      <strong>{html.escape(host)}</strong>
      <small>安全退出</small>
    </div>
  </div>
  <h1 class="auth-title">{title}</h1>
  <p class="auth-subtitle">确认后会结束当前设备的登录会话；其他设备不受影响。</p>
  <form method="post" action="{action}">
    <button class="btn danger-btn mt-md btn-full" type="submit">确认退出</button>
  </form>
  <a class="auth-back" href="{cancel}">返回</a>
</div>
</div>'''
    return ctx.html_page('确认退出', body)


def render_admin_shell(
    ctx: Context, active, page_title, content, *, badge='', subtitle='', topbar_extra=''
):
    """Wrap admin page content in the sidebar + topbar app shell."""
    nav_parts = []
    groups = (
        ('概览与用量', ('dashboard', 'usage')),
        ('运行维护', ('health', 'incidents', 'logs', 'settings')),
        ('网络配置', ('config', 'rules', 'landing-egresses')),
    )
    entries = {entry[0]: entry for entry in ctx._SIDEBAR_NAV}
    grouped_entries = []
    for group_title, keys in groups:
        grouped_entries.append((None, '', group_title, ''))
        grouped_entries.extend(entries[key] for key in keys)
    for key, href, label, icon_name in grouped_entries:
        if key is None:
            nav_parts.append(f'<div class="sidebar-section">{html.escape(label)}</div>')
            continue
        current = ' aria-current="page"' if key == active else ''
        active_class = 'active' if key == active else ''
        nav_parts.append(
            f'<a href="{href}" class="sidebar-link {active_class}"{current} title="{html.escape(label, quote=True)}" aria-label="{html.escape(label, quote=True)}">'
            f'{ctx.icon(icon_name)}<span>{html.escape(label)}</span></a>'
        )
    nav_items = ''.join(nav_parts)
    badge_html = f'<span class="badge">{html.escape(badge)}</span>' if badge else ''
    sub_html = f'<small>{html.escape(subtitle)}</small>' if subtitle else ''
    body = f"""{web_assets.script_tag('shell-preferences', defer=False)}
{web_assets.script_tag('shell')}
<a class="skip-link" href="#main-content">跳到主内容</a>
<div class="app">
<aside class="sidebar" id="sidebar">
  <div class="sidebar-brand">
    <span class="sidebar-logo">H</span>
    <div class="sidebar-brand-text">
      <strong>Hysteria</strong>
      <small>Network Console</small>
    </div>
    <button class="sidebar-close" id="sidebar-close" type="button" aria-label="关闭导航" aria-controls="sidebar">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
    </button>
  </div>
  <button class="sidebar-collapse" id="sidebar-collapse" type="button" aria-label="折叠侧边栏" aria-pressed="false" title="折叠侧边栏">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6"/></svg>
  </button>
  <nav class="sidebar-nav" aria-label="管理导航">
    {nav_items}
  </nav>
  <div class="sidebar-footer">
    <form method="post" action="/logout">
      <button type="submit" class="sidebar-logout" title="退出登录" aria-label="退出登录">{ctx.icon('logout')}<span>退出登录</span></button>
    </form>
  </div>
</aside>
<div class="scrim" id="scrim" aria-hidden="true"></div>
<div class="main">
  <header class="topbar">
    <div class="topbar-inner">
      <div class="topbar-left">
        <button class="sidebar-toggle" id="sidebar-toggle" type="button" aria-label="切换侧边栏" aria-expanded="false">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/></svg>
        </button>
        <h1 class="page-title">{html.escape(page_title)}{sub_html}</h1>
      </div>
      <div class="topbar-right">{topbar_extra}{badge_html}</div>
    </div>
  </header>
  <main class="content" id="main-content" tabindex="-1">{content}</main>
</div>
</div>
"""
    return ctx.html_page(page_title, body, body_class='has-shell')
