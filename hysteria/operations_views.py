"""operations views with explicit presentation dependencies."""

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import hysteria_update


@dataclass(frozen=True)
class Context:
    ADMIN_POLL_JS_ETAG: str
    PASSWORD_MAX_LENGTH: int
    PASSWORD_MIN_LENGTH: int
    RESET_LOG_FILE: Path
    _HEALTH_FLASH: dict
    _SETTINGS_FLASH: dict
    _action_label: Callable[..., object]
    _health_card: Callable[..., object]
    _health_top_kpi_card: Callable[..., object]
    _render_health_cards: Callable[..., object]
    _render_health_top_kpis: Callable[..., object]
    fmt_bytes: Callable[..., object]
    load_meta: Callable[..., object]
    probe_cert: Callable[..., object]
    probe_certbot_renewal: Callable[..., object]
    probe_hysteria_update: Callable[..., object]
    probe_online: Callable[..., object]
    probe_panel_tls: Callable[..., object]
    probe_recent_backup: Callable[..., object]
    probe_xray_config_permissions: Callable[..., object]
    render_admin_shell: Callable[..., object]
    render_cost_calibrator: Callable[..., object]
    render_line_radar: Callable[..., object]
    render_prefixed_alert: Callable[..., object]


def render_health(ctx: Context, host, flash=''):
    alert = ctx.render_prefixed_alert(flash, ctx._HEALTH_FLASH)
    kpis = ctx._render_health_top_kpis()
    kpi_cards = ''.join(
        ctx._health_top_kpi_card(title, result, is_text=(title == '整体状态'))
        for title, result in kpis.items()
    )
    content = (
        alert
        + '<div class="admin-page health-page">'
        # --- Page header controls ---
        # (rendered by render_admin_shell topbar_extra)
        # --- Top 4 KPIs (refreshed with the service snapshot) ---
        + '<div class="health-top-kpis" id="health-live-kpis">'
        + kpi_cards
        + '</div>'
        # --- Core services table (live-refreshed) ---
        + '<section class="admin-section">'
        + '<div class="admin-section-header">'
        + '<h2 class="admin-section-title">核心服务</h2>'
        + '</div>'
        + '<div class="admin-section-body no-pad">'
        + '<div class="data-table-wrap" tabindex="0" aria-label="核心服务状态，可横向滚动">'
        + '<table class="data-table" id="health-live-grid">'
        + '<thead><tr><th>服务</th><th>状态</th></tr></thead>'
        + '<tbody>'
        + ctx._render_health_cards()
        + '</tbody>'
        + '</table>'
        + '</div>'
        + '</div>'
        + '</section>'
        # --- Infrastructure ---
        + '<section class="admin-section">'
        + '<div class="admin-section-header">'
        + '<h2 class="admin-section-title">基础设施与生命周期</h2>'
        + '</div>'
        + '<div class="admin-section-body no-pad">'
        + '<div class="data-table-wrap" tabindex="0" aria-label="基础设施状态，可横向滚动">'
        + '<table class="data-table">'
        + '<thead><tr><th>项目</th><th>状态</th></tr></thead>'
        + '<tbody>'
        + ctx._health_card('TLS 证书', ctx.probe_cert())
        + ctx._health_card('面板 HTTPS', ctx.probe_panel_tls())
        + ctx._health_card('证书自动续期', ctx.probe_certbot_renewal())
        + ctx._health_card('最近备份', ctx.probe_recent_backup())
        + ctx._health_card('Xray 配置权限', ctx.probe_xray_config_permissions())
        + ctx._health_card('Hysteria 更新', ctx.probe_hysteria_update())
        + ctx._health_card('在线用户', ctx.probe_online())
        + '</tbody>'
        + '</table>'
        + '</div>'
        + '</div>'
        + '</section>'
        # --- Line radar ---
        + ctx.render_line_radar()
        # --- Cost calibrator (advanced ops) ---
        + ctx.render_cost_calibrator()
        + '<div id="health-live-update">'
        + hysteria_update.render_history()
        + '</div>'
        + '</div>'
        + f"""<script src="/static/admin-poll.js?v={ctx.ADMIN_POLL_JS_ETAG.strip('"')}" defer></script>"""
        + """<span class="sr-only" id="health-refresh-announcer" role="status" aria-live="polite"></span>
<script>
(function(){
  var grid = document.getElementById('health-live-grid');
  var status = document.getElementById('health-refresh-status');
  var announcer = document.getElementById('health-refresh-announcer');
  var button = document.getElementById('health-refresh-now');
  var timer = null, inflight = false, running = false;
  var failures = 0, activeController = null;
  function stamp(){ return new Date().toLocaleTimeString([], {hour12:false,hour:'2-digit',minute:'2-digit',second:'2-digit'}); }
  function retryDelay(){
    var exponent = Math.min(failures, 3);
    var base = Math.min(240000, 30000 * Math.pow(2, exponent));
    return Math.min(240000, base + (failures ? Math.floor(Math.random() * 4001) : 0));
  }
  function clearScheduled(){
    if (timer) { clearTimeout(timer); timer = null; }
  }
  function scheduleNext(){
    if (!running || document.hidden || timer) return;
    timer = setTimeout(function(){ timer = null; refresh(false); }, retryDelay());
  }
  function refresh(manual){
    if (!grid || inflight || !running || document.hidden) return;
    if (manual) failures = 0;
    clearScheduled();
    inflight = true;
    if (button) button.disabled = true;
    if (status) status.textContent = '更新中';
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    activeController = controller;
    var timedOut = false;
    var timeout = setTimeout(function(){
      timedOut = true;
      if (controller) controller.abort();
    }, 10000);
    fetch('/admin/health.fragment?snapshot=1', {credentials:'same-origin',cache:'no-store',signal:controller ? controller.signal : undefined})
      .then(function(response){
        if (response.status === 401) throw new Error('login');
        if (!response.ok) throw new Error('http');
        return response.json();
      })
      .then(function(snapshot){
        if (typeof snapshot.rows !== 'string' || typeof snapshot.kpis !== 'string' || typeof snapshot.update !== 'string') throw new Error('payload');
        var tbody = grid.querySelector('tbody');
        if (tbody) tbody.innerHTML = snapshot.rows;
        if (tbody) tbody.querySelectorAll('[data-health]').forEach(function(row){
          document.querySelectorAll('.health-page [data-health]').forEach(function(other){
            if (!grid.contains(other) && other.getAttribute('data-health') === row.getAttribute('data-health')) other.innerHTML = row.innerHTML;
          });
        });
        document.getElementById('health-live-kpis').innerHTML = snapshot.kpis;
        var update = document.getElementById('health-live-update');
        if (!update.contains(document.activeElement) && !update.querySelector('button:disabled')) update.innerHTML = snapshot.update;
        document.querySelectorAll('[data-local-time]').forEach(function(el){
          var date = new Date(el.getAttribute('datetime'));
          if (!isNaN(date.getTime())) el.textContent = date.toLocaleString();
        });
        failures = 0;
        if (status) status.textContent = '更新 ' + stamp();
      })
      .catch(function(error){
        if (error && error.name === 'AbortError' && !timedOut) return;
        failures = Math.min(failures + 1, 8);
        var message = error.message === 'login' ? '登录已失效' : '更新失败';
        if (timedOut) message = '请求超时';
        if (error.message === 'login') stop();
        if (status) status.textContent = message;
        if (announcer) announcer.textContent = message + (
          error.message === 'login' ? '，请重新登录' : '，系统稍后自动重试'
        );
      })
      .finally(function(){
        clearTimeout(timeout);
        if (activeController === controller) activeController = null;
        inflight = false;
        if (button) button.disabled = false;
        scheduleNext();
      });
  }
  function start(){
    if (running) return;
    running = true;
    failures = 0;
    scheduleNext();
  }
  function stop(){
    running = false;
    clearScheduled();
    if (activeController) activeController.abort();
  }
  if (button) button.addEventListener('click', function(){ refresh(true); });
  document.addEventListener('visibilitychange', function(){
    if (document.hidden) stop();
    else { start(); refresh(true); }
  });
  window.addEventListener('pagehide', stop);
  document.querySelectorAll('[data-local-time]').forEach(function(el){
    var date = new Date(el.getAttribute('datetime'));
    if (!isNaN(date.getTime())) el.textContent = date.toLocaleString();
  });
  start();
})();
</script>"""
    )
    test_btn = (
        '<form method="post" action="/admin/test-alert" class="inline-form-row">'
        '<button class="btn secondary btn-sm" type="submit">发送测试告警</button></form>'
    )
    refresh_controls = (
        '<button class="btn ghost btn-sm" id="health-refresh-now" type="button">立即刷新</button>'
        '<span class="badge poll-status" id="health-refresh-status">自动更新 · 30s</span>'
        '<span class="badge poll-status" data-role="admin-poll-status">更新器就绪</span>'
        '<span class="sr-only" id="admin-poll-announcer" role="status" aria-live="polite"></span>'
    )
    return ctx.render_admin_shell(
        'health',
        '健康状态',
        content,
        badge=host,
        subtitle='状态卡片每 30 秒自动更新',
        topbar_extra=refresh_controls + test_btn,
    )


def render_settings(ctx: Context, host, flash=''):
    meta = ctx.load_meta()
    admin_user = html.escape(str(meta.get('admin_user', 'admin')))
    alert = ctx.render_prefixed_alert(flash, ctx._SETTINGS_FLASH)
    content = f'''{alert}
<div class="admin-page settings-page">
  <section class="form-section">
    <div class="form-section-title">管理员账号</div>
    <div class="form-section-desc">当前登录的管理员账号名称。</div>
    <div class="small">账号：<code>{admin_user}</code></div>
  </section>

  <section class="form-section" style="max-width:560px;">
    <div class="form-section-title">侧边栏动画</div>
    <div class="form-section-desc">默认跟随系统的减少动态效果设置；此偏好仅保存在当前浏览器，且只影响桌面侧边栏。</div>
    <label class="switch"><input type="checkbox" id="sidebar-motion-toggle">本浏览器强制显示侧边栏动画</label>
  </section>

  <section class="form-section" style="max-width:560px;">
    <div class="form-section-title">修改管理员密码</div>
    <div class="form-section-desc">定期更换管理员密码可以降低被泄露的风险。</div>
    <form method="post" action="/admin/change-password" class="inline-form">
      <div class="form-field">
        <label for="settings-current-password">当前密码</label>
        <input id="settings-current-password" name="current" type="password" maxlength="{ctx.PASSWORD_MAX_LENGTH}" autocomplete="current-password" required>
      </div>
      <div class="form-field">
        <label for="settings-new-password">新密码（至少 8 位）</label>
        <input id="settings-new-password" name="new" type="password" minlength="{ctx.PASSWORD_MIN_LENGTH}" maxlength="{ctx.PASSWORD_MAX_LENGTH}" autocomplete="new-password" required>
      </div>
      <div class="form-field">
        <label for="settings-confirm-password">确认新密码</label>
        <input id="settings-confirm-password" name="confirm" type="password" minlength="{ctx.PASSWORD_MIN_LENGTH}" maxlength="{ctx.PASSWORD_MAX_LENGTH}" autocomplete="new-password" required>
      </div>
      <div class="row mt-md">
        <button class="btn btn-primary" type="submit">更新密码</button>
      </div>
    </form>
    <div class="small mt-sm faint">更新后将注销所有已登录会话（其它设备需重新登录），但本设备会保持登录。</div>
  </section>
</div>'''
    return ctx.render_admin_shell('settings', '设置', content, badge=host)


def render_reset_logs(ctx: Context, host, limit=300):
    from collections import deque

    rows = []
    try:
        with ctx.RESET_LOG_FILE.open('r', encoding='utf-8') as f:
            raw_lines = list(deque(f, maxlen=limit))
    except FileNotFoundError:
        raw_lines = []
    for line in reversed(raw_lines):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except Exception:
            continue
        t = html.escape(str(entry.get('time', '')))
        actor = html.escape(str(entry.get('actor', '')))
        ip = html.escape(str(entry.get('ip', '')))
        action = html.escape(ctx._action_label(str(entry.get('action', ''))))
        target = html.escape(str(entry.get('target', '')))
        month = html.escape(str(entry.get('month', '')))
        before = entry.get('before', {})
        after = entry.get('after', {})
        if isinstance(before, dict) and 'total' in before:
            detail = (
                f'{ctx.fmt_bytes(before.get("total", 0))} → {ctx.fmt_bytes(after.get("total", 0))}'
            )
        else:
            detail = ''
        rows.append(
            f'<tr><td class="small">{t}</td><td>{actor}</td><td class="small">{ip}</td>'
            f'<td>{action}</td><td>{target}</td><td class="small">{month}</td>'
            f'<td class="small">{html.escape(detail)}</td></tr>'
        )
    table = ''.join(rows) if rows else '<tr><td colspan="7" class="empty">暂无日志记录</td></tr>'
    content = f"""<div class="admin-section">
  <div class="admin-section-header">
    <h2 class="admin-section-title">最近清零记录</h2>
    <div class="small">最近 {limit} 条 · 最新在上</div>
  </div>
  <div class="data-table-wrap" tabindex="0" aria-label="清零日志，可横向滚动">
    <table class="data-table">
      <thead>
        <tr><th>时间</th><th>操作人</th><th>IP</th><th>操作</th><th>目标</th><th>月份</th><th>流量变化</th></tr>
      </thead>
      <tbody>{table}</tbody>
    </table>
  </div>
</div>"""
    return ctx.render_admin_shell('logs', '清零日志', content, badge=host)
