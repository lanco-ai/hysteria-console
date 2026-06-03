"""Reusable health-page widgets.

Line quality and cost calibration are shared by the health page and incident
console. Keep their data shaping/rendering here so subscription_service.py can
stay focused on request handling.
"""
import html
from dataclasses import dataclass
from datetime import timedelta

import cost_calibrator
import user_compat


LINE_PROTOCOLS = {
    'hysteria': {
        'label': 'Hysteria UDP',
        'unit': 'hysteria-server.service',
        'profile': 'game',
        'note': '低延迟与端口跳跃优先',
    },
    'xray': {
        'label': 'Xray VLESS TCP',
        'unit': 'xray.service',
        'profile': 'work',
        'note': '稳定 TCP / Reality 备用',
    },
    'tuic': {
        'label': 'TUIC UDP',
        'unit': 'tuic-server.service',
        'profile': 'game',
        'note': '当前仅展示可用性，暂无流量计量',
    },
}


@dataclass(frozen=True)
class HealthWidgetContext:
    display_multiplier: float
    users_file: object
    online_file: object
    protocol_usage_hourly_file: object
    cost_calibration_file: object
    subscription_profiles: dict
    load_json: object
    local_now: object
    entry_total: object
    probe_systemd: object
    fmt_bytes: object


def _line_radar_hour_keys(now, hours=24):
    return [
        (now - timedelta(hours=i)).strftime('%Y-%m-%dT%H')
        for i in reversed(range(hours))
    ]


def _line_radar_protocol_totals(ctx, *, now, hours=24):
    hourly = ctx.load_json(ctx.protocol_usage_hourly_file, {})
    totals = {key: 0 for key in LINE_PROTOCOLS}
    for hk in _line_radar_hour_keys(now, hours=hours):
        bucket = hourly.get(hk) or {}
        for proto in totals:
            totals[proto] += ctx.entry_total(bucket.get(proto))
    scaled = {proto: int(total * ctx.display_multiplier) for proto, total in totals.items()}
    total_scaled = sum(scaled.values())
    return scaled, total_scaled


def _active_line_users(users, *, today):
    active = {
        uid: cfg for uid, cfg in (users or {}).items()
        if not user_compat.is_inactive(cfg, today=today)
    }
    return {
        'hysteria': len(active),
        'xray': sum(1 for cfg in active.values() if str((cfg or {}).get('vless_uuid') or '').strip()),
        'tuic': len(active),
    }


def _line_recommendation(service_states, totals, total_bytes):
    hy_ok = bool((service_states.get('hysteria') or {}).get('ok'))
    xray_ok = bool((service_states.get('xray') or {}).get('ok'))
    if not hy_ok and xray_ok:
        return 'work', 'Hysteria 不可用，建议切到 TCP 稳定 profile'
    if hy_ok and not xray_ok:
        return 'game', 'Xray 不可用，建议优先 UDP profile'
    if total_bytes <= 0:
        return 'default', '近 24 小时暂无协议分布数据，保持默认模板'
    hy_share = totals.get('hysteria', 0) / total_bytes
    xray_share = totals.get('xray', 0) / total_bytes
    if hy_share >= 0.55:
        return 'game', '近 24 小时 UDP 承载占比更高'
    if xray_share >= 0.55:
        return 'work', '近 24 小时 TCP/VLESS 承载占比更高'
    return 'default', '协议分布较均衡，保持默认自动选择'


def build_line_radar(ctx, *, now=None):
    now = now or ctx.local_now()
    users = ctx.load_json(ctx.users_file, {})
    online = ctx.load_json(ctx.online_file, {})
    totals, total_bytes = _line_radar_protocol_totals(ctx, now=now)
    service_states = {
        key: ctx.probe_systemd(meta['unit'])
        for key, meta in LINE_PROTOCOLS.items()
    }
    active_users = _active_line_users(users, today=now.date())
    recommendation, reason = _line_recommendation(service_states, totals, total_bytes)
    rows = []
    for key, meta in LINE_PROTOCOLS.items():
        bytes_n = totals.get(key, 0)
        share = (bytes_n * 100 / total_bytes) if total_bytes > 0 else 0.0
        state = service_states[key]
        rows.append({
            'key': key,
            'label': meta['label'],
            'status': state.get('label') or '未知',
            'ok': bool(state.get('ok')),
            'bytes': bytes_n,
            'share': share,
            'active_users': active_users.get(key, 0),
            'online': int(sum(int(v or 0) for v in online.values())) if key == 'hysteria' else None,
            'profile': meta['profile'],
            'note': meta['note'],
        })
    return {
        'window_hours': 24,
        'total_bytes': total_bytes,
        'recommendation': recommendation,
        'reason': reason,
        'rows': rows,
    }


def render_line_radar(ctx, now=None):
    radar = build_line_radar(ctx, now=now)
    rows = []
    for row in radar['rows']:
        cls = 'badge' if row['ok'] else 'badge badge-danger'
        share = f'{row["share"]:.1f}%' if radar['total_bytes'] > 0 else '—'
        traffic = ctx.fmt_bytes(row['bytes']) if row['key'] != 'tuic' else '暂不可计量'
        online = str(row['online']) if row['online'] is not None else '—'
        rows.append(
            '<tr>'
            f'<td style="padding-left:18px;"><div class="bold">{html.escape(row["label"])}</div>'
            f'<div class="small faint">{html.escape(row["note"])}</div></td>'
            f'<td><span class="{cls}">{html.escape(row["status"])}</span></td>'
            f'<td>{html.escape(traffic)}</td>'
            f'<td>{share}</td>'
            f'<td>{row["active_users"]}</td>'
            f'<td>{online}</td>'
            f'<td style="padding-right:18px;"><code>profile={html.escape(row["profile"])}</code></td>'
            '</tr>'
        )
    rec = ctx.subscription_profiles.get(
        radar['recommendation'],
        ctx.subscription_profiles['default'],
    )
    return (
        '<div class="card mt-md" style="padding:0;overflow:hidden;">'
        '<div class="row" style="padding:14px 18px;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--line);">'
        '<div><div class="bold">线路质量雷达</div>'
        f'<div class="small">近 {radar["window_hours"]} 小时协议占比 · 总量 {ctx.fmt_bytes(radar["total_bytes"])}</div></div>'
        f'<div class="badge">推荐：{html.escape(rec["label"])} · {html.escape(radar["reason"])}</div>'
        '</div>'
        '<table class="table"><thead><tr>'
        '<th style="padding-left:18px;">线路</th><th>状态</th><th>24h 流量</th>'
        '<th>占比</th><th>可用用户</th><th>在线</th><th style="padding-right:18px;">推荐入口</th>'
        f'</tr></thead><tbody>{"".join(rows)}</tbody></table>'
        '</div>'
    )


_CALIBRATION_CONFIDENCE_LABELS = {
    'high': '高',
    'medium': '中',
    'low': '低',
    'none': '无样本',
}


def _fmt_optional_multiplier(value):
    if value is None:
        return '—'
    return f'{float(value):.2f}x'


def summarize_cost_calibration(ctx, *, now=None):
    return cost_calibrator.summarize(
        ctx.cost_calibration_file,
        current_multiplier=ctx.display_multiplier,
        now=now or ctx.local_now(),
    )


def render_cost_calibrator(ctx, now=None):
    summary = summarize_cost_calibration(ctx, now=now)
    confidence = _CALIBRATION_CONFIDENCE_LABELS.get(summary['confidence'], summary['confidence'])
    delta = summary.get('delta_percent')
    delta_text = '—' if delta is None else f'{delta:+.1f}%'
    iface_text = ', '.join(summary.get('ifaces') or []) or '未识别'
    advice = '样本不足，先观察一段时间'
    if summary['confidence'] in ('medium', 'high') and summary.get('suggested_multiplier') is not None:
        advice = '可作为调整 HY_DISPLAY_MULTIPLIER 的参考'
    rows = (
        '<tr><th style="padding-left:18px;">当前倍率</th>'
        f'<td>{_fmt_optional_multiplier(summary["current_multiplier"])}</td>'
        '<th>建议倍率</th>'
        f'<td>{_fmt_optional_multiplier(summary["suggested_multiplier"])}</td>'
        '<th>相对当前</th>'
        f'<td style="padding-right:18px;">{delta_text}</td></tr>'
        '<tr><th style="padding-left:18px;">App 原始流量</th>'
        f'<td>{ctx.fmt_bytes(summary["app_raw_bytes"])}</td>'
        '<th>系统总流量</th>'
        f'<td>{ctx.fmt_bytes(summary["net_total_bytes"])}</td>'
        '<th>系统出站参考</th>'
        f'<td style="padding-right:18px;">{_fmt_optional_multiplier(summary["egress_multiplier"])}</td></tr>'
        '<tr><th style="padding-left:18px;">样本</th>'
        f'<td>{summary["sample_count"]} 个</td>'
        '<th>置信度</th>'
        f'<td>{html.escape(confidence)}</td>'
        '<th>公网网卡</th>'
        f'<td style="padding-right:18px;">{html.escape(iface_text)}</td></tr>'
    )
    return (
        '<div class="card mt-md" style="padding:0;overflow:hidden;">'
        '<div class="row" style="padding:14px 18px;justify-content:space-between;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--line);">'
        '<div><div class="bold">成本校准器</div>'
        f'<div class="small">近 {summary["window_hours"]} 小时 · 系统网卡 / App 原始流量</div></div>'
        f'<div class="badge">{html.escape(advice)}</div>'
        '</div>'
        f'<table class="table"><tbody>{rows}</tbody></table>'
        '<div class="small faint" style="padding:0 18px 14px;">'
        '建议倍率使用公网网卡 RX+TX 与应用原始流量的加权比值；不会自动修改 .env。'
        '</div>'
        '</div>'
    )
