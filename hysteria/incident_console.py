"""Incident response console helpers.

This module keeps the incident dashboard's data shaping and HTML rendering out
of subscription_service.py while receiving the legacy service dependencies via a
small context object. That avoids a circular import during the first split.
"""
import html
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class IncidentConsoleContext:
    alerts: object
    display_multiplier: float
    users_file: object
    usage_daily_file: object
    usage_hourly_file: object
    online_file: object
    subscription_profiles: dict
    load_json: object
    local_now: object
    hour_key: object
    entry_total: object
    cycle_raw_for_user: object
    aggregate_stats: object
    user_total_quota: object
    user_expiry_state: object
    pct: object
    fmt_bytes: object
    build_line_radar: object
    summarize_cost_calibration: object
    render_line_radar: object
    render_line_radar_summary: object
    render_cost_calibrator: object
    render_alert: object
    flash_text: object
    render_admin_shell: object
    user_revision: object = None


def _last_window_hour_keys(ctx, now, hours=24):
    return [
        ctx.hour_key(now - timedelta(hours=i))
        for i in reversed(range(hours))
    ]


def _alert_state_rows(ctx, limit=12):
    labels = {
        'quota_80': '配额 80%',
        'quota_100': '配额 100%',
        'anomaly': '异常用量',
        'expiry_soon': '即将到期',
        'expiry_expired': '已过期',
    }
    state = ctx.alerts.load_state()
    rows = []
    for kind, entries in (state or {}).items():
        if not isinstance(entries, dict):
            continue
        for user, key in entries.items():
            rows.append({
                'kind': kind,
                'label': labels.get(kind, kind),
                'user': str(user),
                'key': str(key),
            })
    rows.sort(key=lambda r: (r['key'], r['kind'], r['user']), reverse=True)
    return rows[:limit]


def _incident_peak_from_hourly(ctx, hourly, *, now, hours=24):
    peak = {'hour': '', 'bytes': 0, 'users': []}
    for hk in _last_window_hour_keys(ctx, now, hours=hours):
        bucket = hourly.get(hk) or {}
        raw_total = sum(ctx.entry_total(v) for v in bucket.values())
        scaled_total = int(raw_total * ctx.display_multiplier)
        if scaled_total >= peak['bytes']:
            user_rows = [
                {
                    'user': uid,
                    'bytes': int(ctx.entry_total(entry) * ctx.display_multiplier),
                }
                for uid, entry in bucket.items()
                if ctx.entry_total(entry) > 0
            ]
            user_rows.sort(key=lambda r: r['bytes'], reverse=True)
            peak = {'hour': hk, 'bytes': scaled_total, 'users': user_rows[:8]}
    return peak


def _incident_user_rows(ctx, *, now, hourly, daily, users, online, hours=24, limit=8):
    window_keys = _last_window_hour_keys(ctx, now, hours=hours)
    raw_by_user = {uid: 0 for uid in users}
    for hk in window_keys:
        for uid, entry in (hourly.get(hk) or {}).items():
            if uid in raw_by_user:
                raw_by_user[uid] += ctx.entry_total(entry)

    rows = []
    for uid, raw_total in raw_by_user.items():
        cfg = users.get(uid) or {}
        _tx, _rx, cycle_raw = ctx.cycle_raw_for_user(uid, daily, now=now)
        quota = ctx.user_total_quota(cfg)
        expiry = ctx.user_expiry_state(cfg, today=now.date())
        rows.append({
            'user': uid,
            'revision': (
                ctx.user_revision(cfg)
                if callable(ctx.user_revision)
                else ''
            ),
            'last_24h_bytes': int(raw_total * ctx.display_multiplier),
            'cycle_used_bytes': int(cycle_raw * ctx.display_multiplier),
            'quota_bytes': int(quota),
            'quota_percent': ctx.pct(int(cycle_raw * ctx.display_multiplier), quota),
            'online': int(online.get(uid, 0) or 0),
            'disabled': bool(cfg.get('disabled')),
            'expired': bool(expiry['expired']),
            'expiry_label': expiry['label'],
            'note': str(cfg.get('note') or ''),
        })
    rows.sort(key=lambda r: (r['last_24h_bytes'], r['quota_percent'], r['online']), reverse=True)
    return rows[:limit]


def build_incident_payload(ctx, *, now=None):
    now = now or ctx.local_now()
    hourly = ctx.load_json(ctx.usage_hourly_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})
    users = ctx.load_json(ctx.users_file, {})
    online = ctx.load_json(ctx.online_file, {})
    stats = ctx.aggregate_stats(now=now, online=online)
    peak = _incident_peak_from_hourly(ctx, hourly, now=now)
    users_rows = _incident_user_rows(
        ctx, now=now, hourly=hourly, daily=daily,
        users=users, online=online,
    )
    radar = ctx.build_line_radar(now=now)
    calibration = ctx.summarize_cost_calibration(now=now)
    return {
        'ts': now.isoformat(timespec='seconds'),
        'stats': stats,
        'peak_hour': peak,
        'users': users_rows,
        'line_radar': radar,
        'cost_calibration': calibration,
        'alerts': _alert_state_rows(ctx),
    }


def _incident_status_badges(row):
    badges = []
    if row.get('disabled'):
        badges.append('<span class="badge badge-danger">已停用</span>')
    if row.get('expired'):
        badges.append('<span class="badge badge-danger">已过期</span>')
    if row.get('online', 0) > 0:
        badges.append(f'<span class="badge">{int(row["online"])} 在线</span>')
    return ''.join(badges)


def render_incidents(ctx, host, flash=''):
    now = ctx.local_now()
    payload = build_incident_payload(ctx, now=now)
    alert = ctx.render_alert(ctx.flash_text(flash))
    peak = payload['peak_hour']
    radar = payload['line_radar']
    rec = ctx.subscription_profiles.get(
        radar['recommendation'],
        ctx.subscription_profiles['default'],
    )
    affected_users = len(peak.get('users') or [])
    active_alerts = len(payload['alerts'])

    # --- Peak-hour related users ---
    peak_users = ''.join(
        f'<tr><td class="mono">{html.escape(u["user"])}</td>'
        f'<td><span class="mono">{ctx.fmt_bytes(u["bytes"])}</span></td></tr>'
        for u in peak.get('users') or []
    ) or '<tr><td colspan="2" class="empty">峰值小时暂无用户流量</td></tr>'

    # --- Recent alert states ---
    alert_rows = ''.join(
        '<tr>'
        f'<td>{html.escape(a["label"])}</td>'
        f'<td class="mono">{html.escape(a["user"])}</td>'
        f'<td><code>{html.escape(a["key"])}</code></td>'
        '</tr>'
        for a in payload['alerts']
    ) or '<tr><td colspan="3" class="empty">暂无告警状态</td></tr>'

    # --- Action candidate users ---
    user_rows = []
    for row in payload['users']:
        user_esc = html.escape(row['user'])
        quota_text = (
            f'{row["quota_percent"]:.1f}%'
            if row.get('quota_bytes') else '不限额'
        )
        actions = (
            '<div class="incident-actions">'
            '<form method="post" action="/admin/pause-user" class="inline-form-row" '
            f'data-confirm="暂停 {user_esc} 1 小时会立即拒绝新连接并断开现有会话，确认继续？">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            f'<input type="hidden" name="user_revision" value="{html.escape(row.get("revision") or "", quote=True)}">'
            '<input type="hidden" name="minutes" value="60">'
            '<input type="hidden" name="next" value="/admin/incidents">'
            '<button class="btn secondary btn-sm" type="submit">暂停 1 小时</button></form>'
            '<form method="post" action="/admin/rotate-token" class="inline-form-row" '
            f'data-confirm="轮换 {user_esc} 的 Token 会立即作废旧订阅和面板链接，确认继续？">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            f'<input type="hidden" name="user_revision" value="{html.escape(row.get("revision") or "", quote=True)}">'
            '<input type="hidden" name="next" value="/admin/incidents">'
            '<button class="btn secondary btn-sm" type="submit">轮换 Token</button></form>'
            f'<a class="btn ghost btn-sm" href="/admin/user/{user_esc}">画像</a>'
            '</div>'
        )
        user_rows.append(
            '<tr>'
            f'<td><div class="bold">{user_esc}</div>'
            f'<div class="small faint">{html.escape(row.get("note") or row.get("expiry_label") or "")}</div></td>'
            f'<td><span class="mono">{ctx.fmt_bytes(row["last_24h_bytes"])}</span></td>'
            f'<td><span class="mono">{ctx.fmt_bytes(row["cycle_used_bytes"])}</span> · {quota_text}</td>'
            f'<td>{_incident_status_badges(row)}</td>'
            f'<td>{actions}</td>'
            '</tr>'
        )
    if not user_rows:
        user_rows.append('<tr><td colspan="5" class="empty">暂无用户</td></tr>')

    content = f'''{alert}
<div class="admin-page incidents-page">

  <!-- Page header controls -->
  <div class="incident-page-header">
    <a class="btn ghost btn-sm" href="/admin/incidents/evidence.json">下载证据 JSON</a>
  </div>

  <!-- Top 4 KPIs -->
  <div class="health-top-kpis">
    <div class="health-kpi-card">
      <div class="health-kpi-label">峰值小时</div>
      <div class="health-kpi-value"><span class="mono">{ctx.fmt_bytes(peak["bytes"])}</span></div>
      <div class="health-kpi-sub">{html.escape(peak["hour"] or "—")}</div>
    </div>
    <div class="health-kpi-card">
      <div class="health-kpi-label">当前小时</div>
      <div class="health-kpi-value"><span class="mono">{ctx.fmt_bytes(payload["stats"]["current_hour_bytes"])}</span></div>
      <div class="health-kpi-sub">{payload["stats"]["online"]} 在线</div>
    </div>
    <div class="health-kpi-card">
      <div class="health-kpi-label">受影响用户</div>
      <div class="health-kpi-value"><span class="mono">{affected_users}</span></div>
      <div class="health-kpi-sub">峰值小时内</div>
    </div>
    <div class="health-kpi-card">
      <div class="health-kpi-label">活跃告警</div>
      <div class="health-kpi-value"><span class="mono">{active_alerts}</span></div>
      <div class="health-kpi-sub">当前状态</div>
    </div>
  </div>

  <!-- Recommended action panel -->
  <section class="admin-section">
    <div class="admin-section-header">
      <h2 class="admin-section-title">推荐处置</h2>
    </div>
    <div class="admin-section-body">
      <div class="incident-rec">
        <div class="incident-rec-entry">
          <div class="incident-rec-badge">{html.escape(rec["label"])}</div>
        </div>
        <div class="incident-rec-reason">{html.escape(radar["reason"])}</div>
      </div>
    </div>
  </section>

  <!-- Peak hour users + recent alerts (equal-height dual panel) -->
  <div class="incident-dual-grid">
    <section class="admin-section incident-panel">
      <div class="admin-section-header">
        <h2 class="admin-section-title">峰值小时相关用户</h2>
        <span class="small">Top {affected_users}</span>
      </div>
      <div class="admin-section-body no-pad">
        <div class="data-table-wrap" tabindex="0" aria-label="峰值小时相关用户，可横向滚动">
          <table class="data-table">
            <thead><tr><th>用户</th><th>峰值小时流量</th></tr></thead>
            <tbody>{peak_users}</tbody>
          </table>
        </div>
      </div>
    </section>
    <section class="admin-section incident-panel">
      <div class="admin-section-header">
        <h2 class="admin-section-title">近期告警状态</h2>
        <span class="small">{active_alerts} 条</span>
      </div>
      <div class="admin-section-body no-pad">
        <div class="data-table-wrap" tabindex="0" aria-label="近期告警状态，可横向滚动">
          <table class="data-table">
            <thead><tr><th>类型</th><th>用户</th><th>键</th></tr></thead>
            <tbody>{alert_rows}</tbody>
          </table>
        </div>
      </div>
    </section>
  </div>

  <!-- Action candidate users -->
  <section class="admin-section">
    <div class="admin-section-header">
      <div>
        <h2 class="admin-section-title">处置候选用户</h2>
        <div class="small">按近 24 小时流量排序</div>
      </div>
    </div>
    <div class="admin-section-body no-pad">
      <div class="data-table-wrap" tabindex="0" aria-label="处置候选用户，可横向滚动">
        <table class="data-table">
          <thead><tr><th>用户</th><th>24h 流量</th><th>周期用量</th><th>状态</th><th>操作</th></tr></thead>
          <tbody>{"".join(user_rows)}</tbody>
        </table>
      </div>
    </div>
  </section>

  <!-- Line quality summary -->
  <div class="incident-diagnostic-grid">
    <section class="admin-section incident-panel">
      <div class="admin-section-header">
        <h2 class="admin-section-title">线路质量摘要</h2>
      </div>
      <div class="admin-section-body">
        {ctx.render_line_radar_summary(now=now)}
      </div>
    </section>
  </div>

</div>'''
    return ctx.render_admin_shell(
        'incidents', '事故处理', content,
        badge=host, subtitle='峰值 · 用户 · 证据',
    )
