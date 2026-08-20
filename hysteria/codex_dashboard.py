"""Server-rendered shell for the Codex quota dashboard."""

import html
from datetime import datetime, timezone


def _esc(value):
    return html.escape(str(value if value is not None else ''))


def _percent(value):
    if not isinstance(value, (int, float)):
        return '—'
    return f'{value:g}%'


def _date_time(epoch):
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return '—'
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _status_copy(status):
    return {
        'live': ('采集正常', 'is-live'),
        'delayed': ('采集延迟', 'is-paused'),
        'stale': ('数据过期', 'is-error'),
        'error': ('采集异常', 'is-error'),
        'empty': ('等待首采', 'is-paused'),
    }.get(status, ('等待额度信息', 'is-paused'))


def _plan_display(account):
    """Return (plan_label, is_unknown)."""
    raw = account.get('plan_type')
    if not raw or not isinstance(raw, str) or raw.lower() == 'unknown':
        return '方案未识别', True
    return raw.replace('_', ' ').title(), False


def _window_panel(key, window, *, tone):
    """Render a single quota window as a Panel.

    Layout:
      title | big remaining % | small orb
      progress bar
      used / remaining row
      reset row

    When unavailable: shows a unified empty state inside the panel.
    """
    available = bool(window.get('available'))
    label = window.get('label') or ''
    remaining = window.get('remaining_percent') if available else None
    used = window.get('used_percent') if available else None
    resets_at = window.get('resets_at') if available else None

    if not available:
        return f'''<section class="codex-quota-panel tone-{tone} is-unavailable"
       data-quota="{key}" data-available="false">
  <div class="codex-quota-panel-head">
    <div>
      <div class="k">{_esc(label)}</div>
      <div class="codex-quota-remaining">暂无额度数据</div>
    </div>
    <span class="codex-window-orb" aria-hidden="true"></span>
  </div>
  <div class="codex-quota-empty">
    等待 Codex 返回该额度窗口。
  </div>
</section>'''

    width = max(0, min(100, float(remaining or 0)))
    state_class = (
        ' is-low' if isinstance(remaining, (int, float)) and remaining <= 20 else ''
    )
    return f'''<section class="codex-quota-panel tone-{tone}{state_class}"
       data-quota="{key}" data-available="true">
  <div class="codex-quota-panel-head">
    <div>
      <div class="k">{_esc(label)}</div>
      <div class="codex-quota-remaining" data-role="remaining">{_percent(remaining)}</div>
    </div>
    <span class="codex-window-orb" aria-hidden="true"></span>
  </div>
  <div class="codex-quota-bar" role="progressbar"
       aria-label="{_esc(label)}剩余额度"
       aria-valuemin="0" aria-valuemax="100" aria-valuenow="{width:.2f}"
       aria-valuetext="{_esc(_percent(remaining))}">
    <div class="fill" data-role="bar" style="width:{width:.2f}%"></div>
  </div>
  <div class="codex-quota-meta">
    <span>已用 <strong data-role="used">{_percent(used)}</strong></span>
    <span>剩余 <strong data-role="remaining-small">{_percent(remaining)}</strong></span>
  </div>
  <div class="codex-reset-block">
    <span class="codex-reset-label">重置时间</span>
    <strong data-role="countdown">计算中</strong>
    <time data-role="reset-time" data-epoch="{_esc(resets_at or '')}">{_esc(_date_time(resets_at))}</time>
  </div>
</section>'''


def render_page(payload, *, render_admin_shell, asset_version=''):
    windows = payload.get('windows') or {}
    account = payload.get('account') or {}
    freshness = payload.get('freshness') or {}
    status_text, status_class = _status_copy(freshness.get('status'))

    plan_label, plan_unknown = _plan_display(account)
    reset_credits = account.get('reset_credits_available')
    reset_credits_copy = str(reset_credits) if isinstance(reset_credits, int) else '—'

    last_success = _date_time(freshness.get('last_success_at'))
    last_error = freshness.get('last_error')

    week_only_class = (
        ' is-week-only'
        if not bool((windows.get('five_hour') or {}).get('available')) else ''
    )

    # If the plan itself is unknown, prefer the friendly product copy in the
    # topbar; keep the raw value available via data-role for the JS to use.
    badge_text = plan_label if plan_unknown is False else 'Codex'

    error_html = (
        f'<div class="codex-collector-error" id="codex-collector-error" '
        f'role="alert" aria-live="assertive" aria-atomic="true">'
        f'<strong>最近一次采集失败</strong><span>{_esc(last_error)}</span></div>'
        if last_error else
        '<div class="codex-collector-error" id="codex-collector-error" '
        'role="alert" aria-live="assertive" aria-atomic="true" hidden></div>'
    )

    script_version = f'?v={_esc(asset_version)}' if asset_version else ''
    content = f'''<div class="admin-page codex-page">
<div class="codex-dashboard{week_only_class}" id="codex-dashboard" data-endpoint="/admin/codex.json">
  {error_html}

  <header class="admin-page-header">
    <div class="admin-page-header-main">
      <div class="k">CODEX USAGE INTELLIGENCE</div>
      <h1 class="admin-page-title">Codex 额度</h1>
      <div class="admin-page-desc">周额度变化与重置时间</div>
    </div>
    <div class="admin-page-actions">
      <span class="poll-status {status_class}" data-role="collector-status"
            role="status" aria-live="polite" aria-atomic="true">{status_text}</span>
      <span class="small" data-role="last-success">最近采集：{_esc(last_success)}</span>
      <button class="btn secondary btn-sm" id="codex-refresh-now" type="button">
        <span>刷新视图</span></button>
    </div>
  </header>

  <div class="codex-primary-grid">
    {_window_panel('weekly', windows.get('weekly') or {}, tone='cyan')}
    {_window_panel('five_hour', windows.get('five_hour') or {}, tone='violet')}
  </div>

  <div class="codex-context-grid">
    <div class="codex-context-item">
      <div class="k">当前方案</div>
      <div class="v" data-role="plan-type">{_esc(plan_label)}</div>
      <div class="small">额度组 <span data-role="limit-id">{_esc(account.get('limit_id') or 'codex')}</span></div>
    </div>
    <div class="codex-context-item">
      <div class="k">采集节奏</div>
      <div class="v">每 3 分钟</div>
      <div class="small">下次采集 <strong data-role="next-poll">计算中</strong></div>
    </div>
    <div class="codex-context-item">
      <div class="k">可重置次数</div>
      <div class="v" data-role="reset-credits">{_esc(reset_credits_copy)}</div>
      <div class="small">由 Codex 当前账户响应提供</div>
    </div>
  </div>

  <section class="admin-section codex-chart-section">
    <div class="admin-section-header">
      <div>
        <h2 class="admin-section-title">周额度趋势</h2>
        <div class="small">WEEKLY QUOTA TREND · 阶梯跳转表示真实变化</div>
      </div>
      <div class="codex-range-switch" role="group" aria-label="图表时间范围">
        <button type="button" class="active" data-range="day" aria-pressed="true">日</button>
        <button type="button" data-range="week" aria-pressed="false">周</button>
        <button type="button" data-range="month" aria-pressed="false">月</button>
        <button type="button" data-range="year" aria-pressed="false">年</button>
      </div>
    </div>
    <div class="admin-section-body no-pad">
      <div class="codex-chart-overview">
        <div class="codex-chart-stat is-primary">
          <span>当前周额度</span>
          <strong data-role="chart-current">{_percent((windows.get('weekly') or {}).get('remaining_percent'))}</strong>
        </div>
        <div class="codex-chart-stat">
          <span>视图净变化</span>
          <strong data-role="chart-net-change">—</strong>
        </div>
        <div class="codex-chart-stat">
          <span>额度变化次数</span>
          <strong data-role="chart-change-count">—</strong>
        </div>
        <div class="codex-chart-key" aria-label="图例">
          <span><i class="series-week"></i>周额度</span>
          <span data-series="five-hour"><i class="series-five"></i>5 小时额度</span>
        </div>
      </div>
      <div class="codex-chart-frame" id="codex-chart-frame" tabindex="0" aria-label="额度趋势图，可横向滚动">
        <svg id="codex-quota-chart" class="codex-quota-chart" viewBox="0 0 1200 470"
             role="img" tabindex="-1" aria-describedby="codex-chart-tooltip"
             aria-label="Codex 周额度阶梯趋势和变化事件图；聚焦后可用方向键浏览采样"></svg>
        <div class="codex-chart-empty" id="codex-chart-empty">暂无趋势数据 · 等待下一次有效额度采样后生成趋势。</div>
        <div class="codex-chart-tooltip" id="codex-chart-tooltip" role="status"
             aria-live="polite" aria-atomic="true" hidden></div>
      </div>
      <div class="codex-chart-foot">
        <span data-role="history-start">历史记录：等待首采</span>
        <span>事件线连接变化节点与"时间 · 变化后余额"；悬停或聚焦图表后使用方向键可查看任意采样详情</span>
      </div>
    </div>
  </section>

  <section class="admin-section">
    <div class="admin-section-header">
      <div>
        <h2 class="admin-section-title">最近采集明细</h2>
        <div class="small">RECENT SAMPLES</div>
      </div>
      <span class="badge" data-role="records-count">0 条</span>
    </div>
    <div class="admin-section-body no-pad">
      <div class="data-table-wrap" tabindex="0" aria-label="Codex 最近采集明细，可横向滚动">
        <table class="data-table codex-records-table">
          <thead><tr><th>采集时间</th><th>周余额</th><th>周额度重置</th><th data-col="five-hour">5 小时余额</th><th data-col="five-hour">5 小时重置</th></tr></thead>
          <tbody data-role="records-body"><tr><td colspan="5" class="empty">暂无采集数据 · 有效采样将在此展示</td></tr></tbody>
        </table>
      </div>
    </div>
  </section>

  <section class="form-section mt-md">
    <div class="form-section-title">关于额度窗口</div>
    <div class="form-section-desc">面板按窗口时长识别 5 小时与周额度。若 Codex 当前账户响应暂时不提供某个窗口，会显示"未提供"，不会用旧数据或推算值冒充实时余额。</div>
  </section>
</div>
</div>
<script src="/static/codex-quota.js{script_version}" defer></script>'''

    return render_admin_shell(
        'codex',
        'Codex 额度',
        content,
        badge=badge_text,
        subtitle='周额度变化与重置时间',
    )
