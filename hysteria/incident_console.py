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
    render_cost_calibrator: object
    render_alert: object
    flash_text: object
    render_admin_shell: object


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

    peak_users = ''.join(
        f'<tr><td>{html.escape(u["user"])}</td><td>{ctx.fmt_bytes(u["bytes"])}</td></tr>'
        for u in peak.get('users') or []
    ) or '<tr><td colspan="2" class="empty">峰值小时暂无用户流量</td></tr>'

    user_rows = []
    for row in payload['users']:
        user_esc = html.escape(row['user'])
        quota_text = (
            f'{row["quota_percent"]:.1f}%'
            if row.get('quota_bytes') else '不限额'
        )
        actions = (
            '<div class="row gap-sm">'
            '<form method="post" action="/admin/pause-user" class="inline-form-row" data-action="disable-user">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            '<input type="hidden" name="minutes" value="60">'
            '<input type="hidden" name="next" value="/admin/incidents">'
            '<button class="btn ghost btn-sm" type="submit">暂停 1 小时</button></form>'
            '<form method="post" action="/admin/rotate-token" class="inline-form-row" data-action="rotate-user-token">'
            f'<input type="hidden" name="user" value="{user_esc}">'
            '<input type="hidden" name="next" value="/admin/incidents">'
            '<button class="btn ghost btn-sm" type="submit">轮换 Token</button></form>'
            f'<a class="btn ghost btn-sm" href="/admin/user/{user_esc}">画像</a>'
            '</div>'
        )
        user_rows.append(
            '<tr>'
            f'<td style="padding-left:18px;"><div class="bold">{user_esc}</div>'
            f'<div class="small faint">{html.escape(row.get("note") or row.get("expiry_label") or "")}</div></td>'
            f'<td>{ctx.fmt_bytes(row["last_24h_bytes"])}</td>'
            f'<td>{ctx.fmt_bytes(row["cycle_used_bytes"])} · {quota_text}</td>'
            f'<td>{_incident_status_badges(row)}</td>'
            f'<td style="padding-right:18px;">{actions}</td>'
            '</tr>'
        )
    if not user_rows:
        user_rows.append('<tr><td colspan="5" class="empty">暂无用户</td></tr>')

    alert_rows = ''.join(
        '<tr>'
        f'<td style="padding-left:18px;">{html.escape(a["label"])}</td>'
        f'<td>{html.escape(a["user"])}</td>'
        f'<td style="padding-right:18px;"><code>{html.escape(a["key"])}</code></td>'
        '</tr>'
        for a in payload['alerts']
    ) or '<tr><td colspan="3" class="empty">暂无告警状态</td></tr>'

    content = f'''{alert}
<div class="grid grid-4">
  <div class="card stat"><div class="k">峰值小时</div><div class="v">{ctx.fmt_bytes(peak["bytes"])}</div><div class="small">{html.escape(peak["hour"] or "—")}</div></div>
  <div class="card stat"><div class="k">当小时</div><div class="v">{ctx.fmt_bytes(payload["stats"]["current_hour_bytes"])}</div><div class="small">{payload["stats"]["online"]} 在线</div></div>
  <div class="card stat"><div class="k">推荐处置</div><div class="v">{html.escape(rec["label"])}</div><div class="small">{html.escape(radar["reason"])}</div></div>
  <div class="card stat"><div class="k">证据导出</div><a class="btn secondary btn-sm mt-sm" href="/admin/incidents/evidence.json">下载 JSON</a></div>
</div>

<div class="grid grid-2 mt-md">
  <div class="card card-flush scroll-x">
    <div class="row" style="padding:14px 18px;justify-content:space-between;border-bottom:1px solid var(--line);">
      <div class="bold">峰值小时相关用户</div><span class="small">Top {len(peak.get('users') or [])}</span>
    </div>
    <table class="table"><thead><tr><th style="padding-left:18px;">用户</th><th>峰值小时流量</th></tr></thead><tbody>{peak_users}</tbody></table>
  </div>
  <div class="card card-flush scroll-x">
    <div class="row" style="padding:14px 18px;justify-content:space-between;border-bottom:1px solid var(--line);">
      <div class="bold">近期告警状态</div><span class="small">去重状态</span>
    </div>
    <table class="table"><thead><tr><th style="padding-left:18px;">类型</th><th>用户</th><th style="padding-right:18px;">键</th></tr></thead><tbody>{alert_rows}</tbody></table>
  </div>
</div>

<div class="card card-flush scroll-x mt-md">
  <div class="row" style="padding:14px 18px;justify-content:space-between;border-bottom:1px solid var(--line);">
    <div class="bold">处置候选用户</div>
    <div class="small">按近 24 小时流量排序；暂停/轮换会复用现有安全动作</div>
  </div>
  <table class="table"><thead><tr><th style="padding-left:18px;">用户</th><th>24h 流量</th><th>周期用量</th><th>状态</th><th style="padding-right:18px;">操作</th></tr></thead><tbody>{''.join(user_rows)}</tbody></table>
</div>

{ctx.render_line_radar(now=now)}
{ctx.render_cost_calibrator(now=now)}'''
    return ctx.render_admin_shell(
        'incidents', '事故处理', content,
        badge=host, subtitle='峰值 · 用户 · 证据',
    )
