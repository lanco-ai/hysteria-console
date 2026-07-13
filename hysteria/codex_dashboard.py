"""Server-rendered shell for the Codex quota dashboard."""

import html
from datetime import datetime, timezone


def _esc(value):
    return html.escape(str(value if value is not None else ''))


def _percent(value):
    if not isinstance(value, (int, float)):
        return '未提供'
    return f'{value:g}%'


def _date_time(epoch):
    if not isinstance(epoch, (int, float)) or epoch <= 0:
        return '未提供'
    return datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%d %H:%M UTC')


def _status_copy(status):
    return {
        'live': ('采集正常', 'is-live'),
        'delayed': ('采集延迟', 'is-paused'),
        'stale': ('数据过期', 'is-error'),
        'error': ('采集异常', 'is-error'),
        'empty': ('等待首采', 'is-paused'),
    }.get(status, ('状态未知', 'is-paused'))


def _window_card(key, window, *, tone):
    available = bool(window.get('available'))
    remaining = window.get('remaining_percent') if available else None
    used = window.get('used_percent') if available else None
    resets_at = window.get('resets_at') if available else None
    width = max(0, min(100, float(remaining or 0)))
    reset_copy = (
        '当前账户响应中未提供这个额度窗口'
        if not available else _date_time(resets_at)
    )
    state_class = (
        ' is-unavailable' if not available
        else (' is-low' if isinstance(remaining, (int, float)) and remaining <= 20 else '')
    )
    return f'''<section class="card codex-quota-card tone-{tone}{state_class}" data-quota="{key}" data-available="{'true' if available else 'false'}">
  <div class="codex-quota-card-head">
    <div>
      <div class="k">{_esc(window.get('label'))}</div>
      <div class="codex-quota-value" data-role="remaining">{_percent(remaining)}</div>
    </div>
    <span class="codex-window-orb" aria-hidden="true"></span>
  </div>
  <div class="bar codex-quota-bar" aria-label="剩余额度">
    <div class="fill" data-role="bar" style="width:{width:.2f}%"></div>
  </div>
  <div class="codex-quota-meta">
    <span>已用 <strong data-role="used">{_percent(used)}</strong></span>
    <span>剩余 <strong data-role="remaining-small">{_percent(remaining)}</strong></span>
  </div>
  <div class="codex-reset-block">
    <span class="codex-reset-label">重置倒计时</span>
    <strong data-role="countdown">{'—' if not available else '计算中'}</strong>
    <time data-role="reset-time" data-epoch="{_esc(resets_at or '')}">{_esc(reset_copy)}</time>
  </div>
</section>'''


def render_page(payload, *, render_admin_shell, asset_version=''):
    windows = payload.get('windows') or {}
    account = payload.get('account') or {}
    freshness = payload.get('freshness') or {}
    status_text, status_class = _status_copy(freshness.get('status'))
    plan = str(account.get('plan_type') or 'unknown')
    plan_label = plan.replace('_', ' ').title()
    reset_credits = account.get('reset_credits_available')
    reset_credits_copy = str(reset_credits) if isinstance(reset_credits, int) else '—'
    last_success = _date_time(freshness.get('last_success_at'))
    last_error = freshness.get('last_error')
    error_html = (
        f'<div class="codex-collector-error" id="codex-collector-error">'
        f'<strong>最近一次采集失败</strong><span>{_esc(last_error)}</span></div>'
        if last_error else
        '<div class="codex-collector-error" id="codex-collector-error" hidden></div>'
    )

    script_version = f'?v={_esc(asset_version)}' if asset_version else ''
    content = f'''<div class="codex-dashboard" id="codex-dashboard" data-endpoint="/admin/codex.json">
  {error_html}
  <section class="codex-intro codex-intro-v2">
    <div>
      <span class="codex-eyebrow">CODEX USAGE INTELLIGENCE</span>
      <h2>额度中心</h2>
      <p>同时追踪短周期与周周期余量，自动计算重置时间并保留历史趋势。</p>
    </div>
    <div class="codex-live-cluster">
      <span class="badge poll-status {status_class}" data-role="collector-status">{status_text}</span>
      <span class="small" data-role="last-success">最近采集：{_esc(last_success)}</span>
    </div>
  </section>

  <div class="codex-primary-grid">
    {_window_card('five_hour', windows.get('five_hour') or {}, tone='violet')}
    {_window_card('weekly', windows.get('weekly') or {}, tone='cyan')}
  </div>

  <div class="grid grid-3 codex-context-grid mt-md">
    <section class="card codex-context-card">
      <span class="codex-context-icon">P</span>
      <div><div class="k">当前方案</div><div class="v" data-role="plan-type">{_esc(plan_label)}</div><div class="small">额度组 <span data-role="limit-id">{_esc(account.get('limit_id') or 'codex')}</span></div></div>
    </section>
    <section class="card codex-context-card">
      <span class="codex-context-icon">↻</span>
      <div><div class="k">采集节奏</div><div class="v">3 分钟</div><div class="small">下次采集 <strong data-role="next-poll">计算中</strong></div></div>
    </section>
    <section class="card codex-context-card">
      <span class="codex-context-icon">R</span>
      <div><div class="k">可用重置次数</div><div class="v" data-role="reset-credits">{_esc(reset_credits_copy)}</div><div class="small">由 Codex 当前账户响应提供</div></div>
    </section>
  </div>

  <section class="card codex-chart-card codex-chart-card-v2 mt-md">
    <header class="codex-chart-head">
      <div>
        <div class="k">QUOTA TREND</div>
        <h3>额度余量趋势</h3>
        <p>纵轴固定为 0–100%，浅红区域表示余量低于 20%。每个圆点均可悬停查看精确值。</p>
      </div>
      <div class="codex-range-switch" role="group" aria-label="图表时间范围">
        <button type="button" class="active" data-range="day" aria-pressed="true">日</button>
        <button type="button" data-range="week" aria-pressed="false">周</button>
        <button type="button" data-range="month" aria-pressed="false">月</button>
        <button type="button" data-range="year" aria-pressed="false">年</button>
      </div>
    </header>
    <div class="codex-chart-legend" aria-label="图例">
      <span><i class="series-five"></i>5 小时窗口</span>
      <span><i class="series-week"></i>每周窗口</span>
      <span class="codex-chart-summary" data-role="chart-summary">等待数据</span>
    </div>
    <div class="codex-chart-frame" id="codex-chart-frame">
      <svg id="codex-quota-chart" class="codex-quota-chart" viewBox="0 0 1200 460" role="img" aria-label="Codex 剩余额度历史折线图"></svg>
      <div class="codex-chart-empty" id="codex-chart-empty">采集第一条数据后，这里会自动形成趋势图</div>
      <div class="codex-chart-tooltip" id="codex-chart-tooltip" hidden></div>
    </div>
    <footer class="codex-chart-foot">
      <span data-role="history-start">历史记录：等待首采</span>
      <span>日图保留 3 分钟粒度；更长视角自动聚合，降低内存与传输开销</span>
    </footer>
  </section>

  <section class="card codex-records-card mt-md">
    <header class="codex-records-head">
      <div>
        <div class="k">RECENT SAMPLES</div>
        <h3>最近采集明细</h3>
      </div>
      <span class="badge gray" data-role="records-count">0 条</span>
    </header>
    <div class="scroll-x">
      <table class="codex-records-table">
        <thead><tr><th>采集时间</th><th>5 小时余额</th><th>周余额</th><th>5 小时重置</th><th>周额度重置</th></tr></thead>
        <tbody data-role="records-body"><tr><td colspan="5" class="empty">等待采集数据</td></tr></tbody>
      </table>
    </div>
  </section>

  <section class="codex-data-note mt-md">
    <span class="codex-data-note-mark">i</span>
    <div><strong>关于额度窗口</strong><p>面板按窗口时长识别 5 小时与周额度。若 Codex 当前账户响应暂时不提供某个窗口，会显示“未提供”，不会用旧数据或推算值冒充实时余额。</p></div>
  </section>
</div>
<script src="/static/codex-quota.js{script_version}" defer></script>'''

    topbar_extra = (
        '<button class="btn secondary btn-sm" id="codex-refresh-now" type="button">'
        '<span aria-hidden="true">↻</span><span>刷新视图</span></button>'
    )
    return render_admin_shell(
        'codex',
        'Codex 额度',
        content,
        badge=f'Codex {plan_label}',
        subtitle='5 小时与周额度趋势',
        topbar_extra=topbar_extra,
    )
