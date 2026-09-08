"""Login presentation only; authentication and sessions remain in the HTTP service."""

import html
from typing import Callable


def render_login(
    *,
    html_page: Callable[..., str],
    render_alert: Callable[..., str],
    icon: Callable[[str], str],
    password_max_length: int,
    msg: str = '',
    msg_kind: str = 'err',
    active_tab: str = 'admin',
    username: str = '',
) -> str:
    """Administrator-only entry; preserve the existing admin POST contract."""
    username_esc = html.escape(username if active_tab == 'admin' else '', quote=True)
    message = msg if active_tab == 'admin' else '请使用管理员账号登录控制台。'
    error = render_alert(message, msg_kind) if message else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo"><span class="login-mark" aria-hidden="true">H</span>Hysteria</a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>
<main class="login-stage">
  <div class="login-layout">
    <section class="login-story" aria-labelledby="login-story-title">
      <div class="login-eyebrow"><span></span> NETWORK CONSOLE</div>
      <h1 id="login-story-title">连接网络，<br><span>掌控全局。</span></h1>
      <p class="login-description">让每一次连接，清晰可见。<br>在一个控制台中，管理你的网络。</p>
      <div class="login-network" aria-hidden="true">
        <svg viewBox="0 0 460 220" fill="none">
          <defs><linearGradient id="login-line"><stop stop-color="#b9d8e1"/><stop offset="1" stop-color="#68a8bf"/></linearGradient></defs>
          <path d="M28 162H94L155 101H253L314 40H425M94 162H235L285 112H422M155 101V46H212M253 101V182H375" stroke="#d1dce0" stroke-width="1"/>
          <path d="M28 162H94L155 101H253L314 40H425" stroke="url(#login-line)" stroke-width="2"/>
          <circle cx="155" cy="101" r="20" fill="#dceef3" fill-opacity=".65"/>
          <circle cx="155" cy="101" r="6" fill="#508da3" stroke="white" stroke-width="3"/>
          <circle cx="253" cy="101" r="5" fill="#fff" stroke="#7aa5b5" stroke-width="2"/>
          <circle cx="314" cy="40" r="5" fill="#fff" stroke="#7aa5b5" stroke-width="2"/>
          <circle cx="285" cy="112" r="4" fill="#9bbbc6"/>
          <rect x="365" y="170" width="30" height="24" rx="5" fill="#fff" stroke="#cad8dd"/>
          <path d="M375 178h10m-10 7h6" stroke="#83a4b0" stroke-width="2" stroke-linecap="round"/>
          <circle cx="425" cy="40" r="3" fill="#83a4b0"/>
          <circle cx="28" cy="162" r="3" fill="#83a4b0"/>
        </svg>
      </div>
      <div class="login-story-foot"><span>HYSTERIA</span><span>连接 · 洞察 · 管理</span></div>
    </section>
    <section class="login-panel" aria-labelledby="login-title">
      <div class="login-panel-kicker">{icon('lock')}<span>管理员访问</span></div>
      <h2 id="login-title">登录控制台</h2>
      <p class="login-subtitle">使用管理员账号登录。</p>
      <form method="post" action="/login" class="login-form" id="form-admin">
        <div class="login-feedback">{error}</div>
        <div class="field">
          <label class="label" for="admin-username">管理员账号</label>
          <input class="input" id="admin-username" name="admin_username" value="{username_esc}" required autocomplete="username" autocapitalize="none" spellcheck="false" placeholder="输入管理员账号">
        </div>
        <div class="field">
          <label class="label" for="admin-password">密码</label>
          <div class="login-password">
            <input class="input" id="admin-password" name="admin_password" type="password" required maxlength="{password_max_length}" autocomplete="current-password" placeholder="输入密码">
            <button type="button" id="login-password-toggle" aria-controls="admin-password" aria-label="显示密码" aria-pressed="false" hidden>显示</button>
          </div>
        </div>
        <button class="btn btn-primary login-submit auth-submit" type="submit"><span class="auth-submit-text">登录控制台</span><span aria-hidden="true">→</span></button>
        <span id="login-progress" class="sr-only" role="status" aria-live="polite"></span>
      </form>
      <div class="login-panel-foot">{icon('lock')}<span>仅限授权管理员访问</span></div>
    </section>
  </div>
  <footer class="login-footer">Hysteria <span>／</span> Network Console</footer>
</main>
<script>
(function() {{
  var form = document.getElementById('form-admin');
  var password = document.getElementById('admin-password');
  var toggle = document.getElementById('login-password-toggle');
  var button = form.querySelector('.auth-submit');
  var progress = document.getElementById('login-progress');
  toggle.hidden = false;
  toggle.addEventListener('click', function() {{
    var visible = password.type === 'password';
    password.type = visible ? 'text' : 'password';
    toggle.textContent = visible ? '隐藏' : '显示';
    toggle.setAttribute('aria-label', visible ? '隐藏密码' : '显示密码');
    toggle.setAttribute('aria-pressed', String(visible));
  }});
  function reset() {{
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.querySelector('.auth-submit-text').textContent = '登录控制台';
    progress.textContent = '';
    password.type = 'password';
    toggle.textContent = '显示';
    toggle.setAttribute('aria-label', '显示密码');
    toggle.setAttribute('aria-pressed', 'false');
  }}
  form.addEventListener('submit', function(event) {{
    if (button.disabled) {{ event.preventDefault(); return; }}
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.querySelector('.auth-submit-text').textContent = '正在验证…';
    progress.textContent = '正在验证登录信息';
  }});
  window.addEventListener('pageshow', reset);
}})();
</script>'''
    return html_page('管理员登录 · Hysteria', body, body_class='page-auth page-admin-login')


def render_user_login(
    *,
    html_page: Callable[..., str],
    render_alert: Callable[..., str],
    password_max_length: int,
    msg: str = '',
    username: str = '',
) -> str:
    alert = render_alert(msg, 'err') if msg else ''
    body = f'''<header class="auth-header">
  <div class="auth-header-inner">
    <a href="/" class="auth-header-logo">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
      Hysteria
    </a>
    <a href="/" class="auth-header-back">← 返回首页</a>
  </div>
</header>

<div class="auth-scene">
  <div class="auth-body">
    <div class="auth-brand">
      <div class="auth-brand-content">
        <div class="auth-brand-eyebrow">User · Panel</div>
        <h1 class="auth-brand-title">查看用量，<br>管理订阅。</h1>
        <div class="auth-brand-features">
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 20V10M18 20V4M6 20v-4"/></svg>
            实时流量统计
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
            独立访问密码
          </div>
          <div class="auth-brand-feature">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/></svg>
            统一订阅链接
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
        <h2 class="auth-card-title">用户登录</h2>
        <p class="auth-card-subtitle">登录后查看用量和订阅信息</p>
        {alert}
        <form method="post" action="/user/login" class="auth-form">
          <div class="field">
            <label class="label" for="user-username">用户名</label>
            <input class="input" id="user-username" name="username" value="{html.escape(username, quote=True)}" required autofocus autocomplete="username" placeholder="输入用户名">
          </div>
          <div class="field">
            <label class="label" for="user-password">面板密码</label>
            <input class="input" id="user-password" name="password" type="password" required maxlength="{password_max_length}" autocomplete="current-password" placeholder="输入密码">
          </div>
          <button class="btn btn-primary btn-full" type="submit">登录</button>
        </form>
        <div class="auth-note">面板密码由管理员设置；原订阅链接仍可继续使用。</div>
        <a class="auth-back" href="/">返回首页</a>
      </div>
    </div>
  </div>
</div>'''
    return html_page('用户登录', body, body_class='page-auth')
