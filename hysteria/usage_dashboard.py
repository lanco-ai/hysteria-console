"""Usage analytics dashboard helpers.

This module owns the usage-page data payloads and HTML rendering while
subscription_service.py keeps thin compatibility wrappers for existing routes
and tests.
"""
import csv
import html
import io
from dataclasses import dataclass
from datetime import datetime, timedelta

import user_compat


@dataclass(frozen=True)
class UsageDashboardContext:
    display_multiplier: float
    hourly_retention_hours: int
    daily_retention_days: int
    local_tz_label: str
    users_file: object
    usage_daily_file: object
    usage_hourly_file: object
    online_file: object
    load_json: object
    local_now: object
    cycle_days: object
    cycle_start_for: object
    get_cycle_length_days: object
    preserved_raw_for_cycle: object
    scaled_usage_for_user: object
    cycle_raw_for_user: object
    user_total_quota: object
    user_expiry_state: object
    pct: object
    fmt_bytes: object
    render_admin_shell: object


def scale_daily_entry(ctx, entry):
    """Scale a raw daily usage entry by display multiplier."""
    if not entry:
        return 0, 0, 0
    if isinstance(entry, dict):
        tx = int(entry.get('tx', 0))
        rx = int(entry.get('rx', 0))
        total = int(entry.get('total', tx + rx))
    else:
        total = int(entry or 0)
        tx, rx = 0, total
    m = ctx.display_multiplier
    return int(tx * m), int(rx * m), int(total * m)


def hour_key(dt):
    return dt.strftime("%Y-%m-%dT%H")


def entry_total(entry):
    """Extract `total` from a per-user usage entry, tolerating int and dict shapes."""
    if isinstance(entry, dict):
        return int(entry.get("total", 0))
    return int(entry or 0)


def load_hourly_totals(ctx, *, now):
    """Return retention-window {hour, bytes} entries, oldest first."""
    hourly = ctx.load_json(ctx.usage_hourly_file, {})
    out = []
    for i in reversed(range(ctx.hourly_retention_hours)):
        h = now - timedelta(hours=i)
        hk = hour_key(h)
        bucket = hourly.get(hk) or {}
        raw_total = sum(entry_total(v) for v in bucket.values())
        out.append({"hour": hk, "bytes": int(raw_total * ctx.display_multiplier)})
    return out


def load_heatmap_grid(ctx, *, now):
    hourly = ctx.load_json(ctx.usage_hourly_file, {})
    today = now.date()
    rows = []
    for d in reversed(range(7)):
        day = today - timedelta(days=d)
        date_str = day.strftime("%Y-%m-%d")
        hours = []
        for hh in range(24):
            hk = f"{date_str}T{hh:02d}"
            bucket = hourly.get(hk) or {}
            raw = sum(entry_total(v) for v in bucket.values())
            hours.append(int(raw * ctx.display_multiplier))
        rows.append({"date": date_str, "hours": hours})
    return rows


def top_n_users(ctx, *, n=5, window_hours=24, now):
    hourly = ctx.load_json(ctx.usage_hourly_file, {})
    users = ctx.load_json(ctx.users_file, {})
    known_users = set(users.keys())

    buckets = []
    for i in reversed(range(window_hours)):
        h = now - timedelta(hours=i)
        buckets.append(hourly.get(hour_key(h)) or {})

    per_user_totals = {}
    for bucket in buckets:
        for uid, entry in bucket.items():
            if uid not in known_users:
                continue
            per_user_totals[uid] = per_user_totals.get(uid, 0) + entry_total(entry)
    for uid in known_users:
        per_user_totals.setdefault(uid, 0)

    ranked = sorted(per_user_totals.items(), key=lambda kv: kv[1], reverse=True)
    selected = ranked[:n]
    top_uids = [uid for uid, _ in selected]
    top_set = set(top_uids)

    spark = {uid: [0] * window_hours for uid in top_uids}
    for idx, bucket in enumerate(buckets):
        for uid, entry in bucket.items():
            if uid in top_set:
                spark[uid][idx] = int(entry_total(entry) * ctx.display_multiplier)

    return [
        {
            "uid": uid,
            "last_24h_bytes": int(raw_total * ctx.display_multiplier),
            "spark": spark[uid],
        }
        for uid, raw_total in selected
    ]


def aggregate_stats(ctx, *, now, online):
    hourly = ctx.load_json(ctx.usage_hourly_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})

    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    cur_bucket = hourly.get(hour_key(now)) or {}
    current_hour_raw = sum(entry_total(v) for v in cur_bucket.values())

    today_raw = 0
    for hh in range(24):
        b = hourly.get(f"{today_str}T{hh:02d}") or {}
        today_raw += sum(entry_total(v) for v in b.values())

    yest_bucket = daily.get(yesterday_str) or {}
    yesterday_raw = sum(entry_total(v) for v in yest_bucket.values())

    last_7d_raw = 0
    for d in range(7):
        dk = (now.date() - timedelta(days=d)).strftime("%Y-%m-%d")
        last_7d_raw += sum(entry_total(v) for v in (daily.get(dk) or {}).values())

    cycle_raw = sum(
        entry_total(v)
        for dk in ctx.cycle_days(now)
        for v in (daily.get(dk) or {}).values()
    )
    cycle_raw += ctx.preserved_raw_for_cycle(now=now)

    cycle_start = ctx.cycle_start_for(now)
    cycle_day = (now.date() - cycle_start.date()).days + 1
    cycle_total_days = ctx.get_cycle_length_days()

    return {
        "current_hour_bytes": int(current_hour_raw * ctx.display_multiplier),
        "today_bytes": int(today_raw * ctx.display_multiplier),
        "yesterday_bytes": int(yesterday_raw * ctx.display_multiplier),
        "last_7d_bytes": int(last_7d_raw * ctx.display_multiplier),
        "cycle_bytes": int(cycle_raw * ctx.display_multiplier),
        "cycle_day": cycle_day,
        "cycle_total_days": cycle_total_days,
        "online": int(sum(1 for v in (online or {}).values() if int(v or 0) > 0)),
    }


def build_usage_csv(ctx, *, now, window='cycle'):
    daily = ctx.load_json(ctx.usage_daily_file, {})
    if window == '30d':
        today = now.date()
        days = [(today - timedelta(days=i)).strftime('%Y-%m-%d')
                for i in range(ctx.daily_retention_days - 1, -1, -1)]
    else:
        days = ctx.cycle_days(now)

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator='\n')
    writer.writerow(['date', 'user', 'tx_bytes', 'rx_bytes', 'total_bytes', 'displayed_bytes'])
    for dk in days:
        bucket = daily.get(dk) or {}
        for uid, entry in sorted(bucket.items()):
            if isinstance(entry, dict):
                tx = int(entry.get('tx', 0))
                rx = int(entry.get('rx', 0))
                total = int(entry.get('total', tx + rx))
            else:
                tx = 0
                rx = int(entry or 0)
                total = rx
            displayed = int(total * ctx.display_multiplier)
            writer.writerow([dk, uid, tx, rx, total, displayed])
    return buf.getvalue()


def build_usage_json_payload(ctx, *, now):
    users = ctx.load_json(ctx.users_file, {})
    online = ctx.load_json(ctx.online_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})
    series = load_hourly_totals(ctx, now=now)
    grid = load_heatmap_grid(ctx, now=now)
    stats = aggregate_stats(ctx, now=now, online=online)
    top = top_n_users(ctx, n=5, window_hours=24, now=now)
    user_list = []
    total_used = 0
    for uid, cfg in users.items():
        tx, rx, used = ctx.scaled_usage_for_user(uid, daily=daily, now=now)
        total = ctx.user_total_quota(cfg)
        expiry = ctx.user_expiry_state(cfg, today=now.date())
        total_used += used
        user_list.append({
            'user': uid,
            'tx': tx,
            'rx': rx,
            'used': used,
            'total': total,
            'percent': ctx.pct(used, total),
            'online': int(online.get(uid, 0)),
            'disabled': bool((cfg or {}).get('disabled')),
            'expired': bool(expiry['expired']),
            'expires_at': expiry['expires_at'],
            'expiry_label': expiry['label'],
            'quota_extra_bytes': user_compat.quota_extra_bytes(cfg),
            'note': str((cfg or {}).get('note') or ''),
            'spark_html': sparkline_svg(daily_window_for_user(ctx, uid, daily, days=30)),
        })
    total_used += int(ctx.preserved_raw_for_cycle(now=now) * ctx.display_multiplier)
    return {
        "ts": now.isoformat(timespec="seconds"),
        "stats": stats,
        "total_used": total_used,
        "users": user_list,
        "hourly_totals": series,
        "heatmap": grid,
        "top_n": top,
    }


def build_user_json_payload(ctx, uid, *, now):
    users = ctx.load_json(ctx.users_file, {})
    if uid not in users:
        return None
    cfg = users[uid] or {}
    expiry = ctx.user_expiry_state(cfg, today=now.date())

    online = ctx.load_json(ctx.online_file, {})
    hourly = ctx.load_json(ctx.usage_hourly_file, {})

    bars = []
    for i in reversed(range(ctx.hourly_retention_hours)):
        h = now - timedelta(hours=i)
        hk = hour_key(h)
        v = entry_total((hourly.get(hk) or {}).get(uid))
        bars.append({"hour": hk, "bytes": int(v * ctx.display_multiplier)})

    heat_grid = []
    today = now.date()
    for d in reversed(range(7)):
        day = today - timedelta(days=d)
        date_str = day.strftime("%Y-%m-%d")
        hours = []
        for hh in range(24):
            v = entry_total((hourly.get(f"{date_str}T{hh:02d}") or {}).get(uid))
            hours.append(int(v * ctx.display_multiplier))
        heat_grid.append({"date": date_str, "hours": hours})

    daily = ctx.load_json(ctx.usage_daily_file, {})
    _tx, _rx, cycle_raw = ctx.cycle_raw_for_user(uid, daily, now=now)

    today_str = today.strftime("%Y-%m-%d")
    today_raw = sum(
        entry_total((hourly.get(f"{today_str}T{hh:02d}") or {}).get(uid))
        for hh in range(24)
    )
    cur_raw = entry_total((hourly.get(hour_key(now)) or {}).get(uid))

    return {
        "ts": now.isoformat(timespec="seconds"),
        "uid": uid,
        "metered": bool(cfg.get("metered", cfg.get("guest", False))),
        "disabled": bool(cfg.get("disabled")),
        "expired": bool(expiry["expired"]),
        "expires_at": expiry["expires_at"],
        "expiry_label": expiry["label"],
        "note": str(cfg.get("note") or ""),
        "online": int(online.get(uid, 0) or 0),
        "max_devices": int(cfg.get("max_devices", 2)),
        "cycle_used_bytes": int(cycle_raw * ctx.display_multiplier),
        "cycle_quota_bytes": int(ctx.user_total_quota(cfg)),
        "quota_extra_bytes": int(user_compat.quota_extra_bytes(cfg)),
        "current_hour_bytes": int(cur_raw * ctx.display_multiplier),
        "today_bytes": int(today_raw * ctx.display_multiplier),
        "hourly_bars": bars,
        "heatmap": heat_grid,
        "recent_alerts": [],
    }


def daily_window_for_user(ctx, uid, daily, *, days=30, today=None):
    today = today or ctx.local_now().date()
    out = []
    for i in reversed(range(days)):
        dk = (today - timedelta(days=i)).strftime('%Y-%m-%d')
        _tx, _rx, total = scale_daily_entry(ctx, (daily.get(dk) or {}).get(uid))
        out.append((dk, total))
    return out


def sparkline_svg(values, *, height=24):
    from charts import mini_sparkline_svg
    return mini_sparkline_svg(values, height=height)


def render_daily_usage(ctx, host, days=14):
    days = max(1, min(ctx.daily_retention_days, int(days)))
    users = ctx.load_json(ctx.users_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})

    today = ctx.local_now().date()
    today_key = today.strftime('%Y-%m-%d')
    window = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in reversed(range(days))]
    weekday_labels = ['一', '二', '三', '四', '五', '六', '日']

    per_user = {}
    user_window_total = {}
    day_totals = {dk: 0 for dk in window}
    overall_total = 0
    for uid in users.keys():
        per_user[uid] = {}
        utot = 0
        for dk in window:
            tx, rx, total = scale_daily_entry(ctx, (daily.get(dk) or {}).get(uid))
            per_user[uid][dk] = (tx, rx, total)
            utot += total
            day_totals[dk] += total
            overall_total += total
        user_window_total[uid] = utot

    sorted_uids = sorted(users.keys(), key=lambda u: user_window_total[u], reverse=True)

    col_headers = []
    for dk in window:
        wd = weekday_labels[datetime.strptime(dk, '%Y-%m-%d').weekday()]
        cls = ' day-today' if dk == today_key else ''
        col_headers.append(
            f'<th class="day-col{cls}" title="{dk}">'
            f'<div class="day-mmdd">{dk[5:]}</div>'
            f'<div class="day-weekday">周{wd}</div></th>'
        )

    rows = []
    for uid in sorted_uids:
        cells = []
        for dk in window:
            tx, rx, total = per_user[uid][dk]
            today_cls = ' day-today' if dk == today_key else ''
            if total <= 0:
                cells.append(f'<td class="day-cell empty-day{today_cls}">—</td>')
            else:
                title = f'{dk} · ↑ {ctx.fmt_bytes(tx)} · ↓ {ctx.fmt_bytes(rx)}'
                cells.append(
                    f'<td class="day-cell{today_cls}" title="{html.escape(title)}">{ctx.fmt_bytes(total)}</td>'
                )
        utot = user_window_total[uid]
        utot_disp = ctx.fmt_bytes(utot) if utot > 0 else '—'
        rows.append(
            f'<tr><th class="user-col" scope="row">{html.escape(uid)}</th>'
            f'<td class="num user-total">{utot_disp}</td>'
            f'{"".join(cells)}</tr>'
        )

    if not rows:
        rows.append(f'<tr><td colspan="{2 + days}" class="empty">暂无用户</td></tr>')

    foot_cells = []
    peak_day = None
    peak_val = 0
    for dk in window:
        value = day_totals[dk]
        if value > peak_val:
            peak_val = value
            peak_day = dk
        today_cls = ' day-today' if dk == today_key else ''
        foot_cells.append(
            f'<td class="day-cell{today_cls}">{ctx.fmt_bytes(value) if value else "—"}</td>'
        )

    today_total = day_totals.get(today_key, 0)
    avg_per_day = int(overall_total / days) if days else 0
    switcher = ''.join(
        f'<a class="btn btn-sm {"primary" if d == days else "secondary"}" '
        f'href="/admin/daily?days={d}">{d} 天</a>'
        for d in (7, 14, 30)
    )
    earliest_recorded = min(daily.keys()) if daily else '—'

    content = f'''<div class="grid grid-4">
  <div class="card stat"><div class="k">{days} 天总流量</div><div class="v big">{ctx.fmt_bytes(overall_total)}</div><div class="accent-bar"></div></div>
  <div class="card stat"><div class="k">今日已用</div><div class="v">{ctx.fmt_bytes(today_total)}</div><div class="small">{today_key}</div></div>
  <div class="card stat"><div class="k">日均</div><div class="v">{ctx.fmt_bytes(avg_per_day)}</div></div>
  <div class="card stat"><div class="k">峰值日</div><div class="v">{ctx.fmt_bytes(peak_val) if peak_val else "—"}</div><div class="small">{peak_day or "—"}</div></div>
</div>
<div class="card mt-md" style="padding:14px 18px;">
  <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:10px;">
    <div>
      <div class="bold">每日流量明细 · 最近 {days} 天</div>
      <div class="small">最早数据：{earliest_recorded} · 保留 {ctx.daily_retention_days} 天</div>
    </div>
    <div class="row gap-sm">{switcher}</div>
  </div>
</div>
<div class="card mt-md scroll-x" style="padding:0;overflow:auto;">
  <table class="table daily-table">
    <thead><tr>
      <th class="user-col" style="padding-left:18px;">用户</th>
      <th class="num">{days} 天累计</th>
      {"".join(col_headers)}
    </tr></thead>
    <tbody>{"".join(rows)}</tbody>
    <tfoot><tr>
      <th class="user-col" style="padding-left:18px;">合计</th>
      <td class="num user-total">{ctx.fmt_bytes(overall_total) if overall_total else "—"}</td>
      {"".join(foot_cells)}
    </tr></tfoot>
  </table>
</div>'''
    return ctx.render_admin_shell(
        'daily', '每日流量', content,
        badge=f'最近 {days} 天',
        subtitle=f'{host} · 滚动窗口 {ctx.daily_retention_days} 天',
    )


def render_usage_page(ctx, host):
    from charts import hourly_bars_svg, mini_sparkline_svg, weekday_hour_heatmap_svg

    now = ctx.local_now()
    payload = build_usage_json_payload(ctx, now=now)
    stats = payload["stats"]
    series = payload["hourly_totals"]
    grid = payload["heatmap"]
    top = payload["top_n"]

    peak_hour = max(series, key=lambda s: s["bytes"])["hour"] if any(s["bytes"] for s in series) else None
    bars_svg = hourly_bars_svg(series, peak_hour=peak_hour)
    heat_svg = weekday_hour_heatmap_svg(grid, current_hour_iso=hour_key(now))

    def _spark_to_pairs(arr):
        return [("h", v) for v in arr]

    top_rows = []
    for user in top:
        spark_html = mini_sparkline_svg(_spark_to_pairs(user["spark"]), height=14)
        top_rows.append(
            f'<a class="top-row" href="/admin/user/{html.escape(user["uid"])}">'
            f'<span class="top-uid">{html.escape(user["uid"])} ↗</span>'
            f'<span class="top-spark">{spark_html}</span>'
            f'<span class="top-bytes">{ctx.fmt_bytes(user["last_24h_bytes"])}</span>'
            f'</a>'
        )
    top_html = "".join(top_rows) or '<div class="empty">暂无数据</div>'

    historical = render_daily_table_collapsed(ctx, host)
    poll_controls = (
        '<button class="btn ghost btn-sm" type="button" id="usage-refresh-now">立即刷新</button>'
        '<span class="badge poll-status" data-role="poll-status" aria-live="polite" aria-atomic="true">已加载</span>'
    )

    content = f'''<div class="grid grid-4">
  <div class="card stat" data-stat="current_hour"><div class="k">当小时</div><div class="v big">{ctx.fmt_bytes(stats["current_hour_bytes"])}</div><div class="small"><span data-role="usage-online">{stats["online"]}</span> 在线</div></div>
  <div class="card stat" data-stat="today"><div class="k">今日</div><div class="v">{ctx.fmt_bytes(stats["today_bytes"])}</div><div class="small">昨日 <span data-role="usage-yesterday">{ctx.fmt_bytes(stats["yesterday_bytes"])}</span></div></div>
  <div class="card stat" data-stat="last_7d"><div class="k">近 7 天</div><div class="v">{ctx.fmt_bytes(stats["last_7d_bytes"])}</div><div class="small">日均 <span data-role="usage-7d-average">{ctx.fmt_bytes(stats["last_7d_bytes"] // 7)}</span></div></div>
  <div class="card stat" data-stat="cycle"><div class="k">本周期</div><div class="v">{ctx.fmt_bytes(stats["cycle_bytes"])}</div><div class="small" data-role="cycle-progress">第 {stats["cycle_day"]} / {stats["cycle_total_days"]} 天</div></div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">过去 7 天 · 每小时</div>
  <div id="hourly-bars-host" style="margin-top:10px;">{bars_svg}</div>
</div>

<div class="grid grid-2 mt-md">
  <div class="card" style="padding:14px 18px;">
    <div class="bold">7 天 × 24 小时 热图</div>
    <div id="heatmap-host" style="margin-top:10px;">{heat_svg}</div>
  </div>
  <div class="card" style="padding:14px 0;">
    <div class="bold" style="padding:0 18px;">Top 5 · 近 24 小时</div>
    <div id="top-n-host" style="margin-top:10px;">{top_html}</div>
  </div>
</div>

<details class="card mt-md" style="padding:8px 18px;">
  <summary style="cursor:pointer;">历史每日明细（可展开）</summary>
  <div style="margin-top:10px;">{historical}</div>
</details>

<div class="hover-tip" id="usage-hover-tip" style="display:none;position:absolute;"></div>
<script src="/static/usage.js" defer></script>
'''
    return ctx.render_admin_shell(
        'usage', '流量分析', content,
        subtitle=f'{host} · {ctx.local_tz_label}',
        topbar_extra=poll_controls,
    )


def render_user_detail_page(ctx, uid, host):
    from charts import hourly_bars_svg, weekday_hour_heatmap_svg

    now = ctx.local_now()
    payload = build_user_json_payload(ctx, uid, now=now)
    if payload is None:
        return None

    peak_hour = (max(payload["hourly_bars"], key=lambda s: s["bytes"])["hour"]
                 if any(s["bytes"] for s in payload["hourly_bars"]) else None)
    bars_svg = hourly_bars_svg(payload["hourly_bars"], peak_hour=peak_hour)
    heat_svg = weekday_hour_heatmap_svg(payload["heatmap"], current_hour_iso=hour_key(now))

    badge = '<span class="badge yellow">按量</span>' if payload["metered"] else '<span class="badge gray">免计</span>'
    state_badges = ''
    if payload.get('disabled'):
        state_badges += '<span class="badge badge-danger">已停用</span>'
    if payload.get('expired'):
        state_badges += '<span class="badge badge-danger">已过期</span>'
    note_line = (f'<div class="small faint mt-sm">备注：{html.escape(payload["note"])}</div>'
                 if payload.get('note') else '')
    quota_line = (f'{ctx.fmt_bytes(payload["cycle_used_bytes"])} / '
                  f'{ctx.fmt_bytes(payload["cycle_quota_bytes"])}'
                  if payload["cycle_quota_bytes"] else
                  f'{ctx.fmt_bytes(payload["cycle_used_bytes"])} (无限)')

    alert_html = "".join(
        f'<div class="alert-row">{html.escape(a.get("ts", ""))} — '
        f'{html.escape(a.get("kind", ""))}: {html.escape(a.get("details", ""))}</div>'
        for a in payload["recent_alerts"]
    ) or '<div class="empty">无近期告警</div>'
    poll_controls = (
        '<button class="btn ghost btn-sm" type="button" id="usage-refresh-now">立即刷新</button>'
        '<span class="badge poll-status" data-role="poll-status" aria-live="polite" aria-atomic="true">已加载</span>'
    )

    content = f'''<a class="back-link" href="/admin/usage">← 返回 /admin/usage</a>
    <h2 class="user-title">{html.escape(uid)} {badge}{state_badges}
      <span class="small"><span data-role="detail-online">{payload["online"]}</span> / {payload["max_devices"]} 在线</span>
    </h2>
    <div class="small faint">有效期：{html.escape(payload["expiry_label"])}</div>{note_line}

<div class="grid grid-3">
  <div class="card stat" data-stat="user_cycle"><div class="k">本周期</div><div class="v">{quota_line}</div></div>
  <div class="card stat" data-stat="today"><div class="k">今日</div><div class="v">{ctx.fmt_bytes(payload["today_bytes"])}</div></div>
  <div class="card stat" data-stat="current_hour"><div class="k">当小时</div><div class="v">{ctx.fmt_bytes(payload["current_hour_bytes"])}</div></div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">7 天小时柱</div>
  <div id="hourly-bars-host" style="margin-top:10px;">{bars_svg}</div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">个人 7×24 热图</div>
  <div id="heatmap-host" style="margin-top:10px;">{heat_svg}</div>
</div>

<div class="card mt-md" style="padding:14px 18px;">
  <div class="bold">最近告警</div>
  <div style="margin-top:10px;">{alert_html}</div>
</div>

<div class="hover-tip" id="usage-hover-tip" style="display:none;position:absolute;"></div>
<script src="/static/usage.js" defer></script>
'''
    return ctx.render_admin_shell(
        'usage', f'{uid} · 用量画像', content,
        subtitle=f'{host} · {ctx.local_tz_label}',
        topbar_extra=poll_controls,
    )


def render_daily_table_collapsed(ctx, host):
    days = ctx.daily_retention_days
    users = ctx.load_json(ctx.users_file, {})
    daily = ctx.load_json(ctx.usage_daily_file, {})
    today = ctx.local_now().date()
    window = [(today - timedelta(days=i)).strftime('%Y-%m-%d') for i in reversed(range(days))]

    rows_html = []
    for uid, _cfg in users.items():
        cells = []
        for dk in window:
            tx, rx, total = scale_daily_entry(ctx, (daily.get(dk) or {}).get(uid))
            cells.append(f'<td>{ctx.fmt_bytes(total) if total else "—"}</td>')
        rows_html.append(f'<tr><th>{html.escape(uid)}</th>{"".join(cells)}</tr>')

    headers = "".join(f'<th>{dk[5:]}</th>' for dk in window)
    return (f'<div class="scroll-x">'
            f'<table class="table daily-table-collapsed">'
            f'<thead><tr><th>用户</th>{headers}</tr></thead>'
            f'<tbody>{"".join(rows_html) or f"<tr><td colspan={days + 1}>暂无数据</td></tr>"}</tbody>'
            f'</table>'
            f'</div>')
