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
        'note': '端口级总量计量，不参与单用户额度',
    },
}


@dataclass(frozen=True)
class HealthWidgetContext:
    display_multiplier: float
    users_file: object
    online_file: object
    protocol_usage_hourly_file: object
    cost_calibration_file: object
    display_multiplier_state_file: object
    multiplier_auto_policy_file: object
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
    tuic_ok = bool((service_states.get('tuic') or {}).get('ok'))
    udp_ok = hy_ok or tuic_ok
    if not udp_ok and xray_ok:
        return 'work', 'UDP 线路不可用，建议切到 TCP 稳定 profile'
    if udp_ok and not xray_ok:
        return 'game', 'Xray 不可用，建议优先 UDP profile'
    if total_bytes <= 0:
        return 'default', '近 24 小时暂无协议分布数据，保持默认模板'
    udp_share = (totals.get('hysteria', 0) + totals.get('tuic', 0)) / total_bytes
    xray_share = totals.get('xray', 0) / total_bytes
    if udp_share >= 0.55:
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
        traffic = ctx.fmt_bytes(row['bytes'])
        online = str(row['online']) if row['online'] is not None else '—'
        rows.append(
            '<tr>'
            f'<td><div class="bold">{html.escape(row["label"])}</div>'
            f'<div class="small faint mono">{html.escape(row["note"])}</div></td>'
            f'<td><span class="{cls}">{html.escape(row["status"])}</span></td>'
            f'<td><span class="mono">{html.escape(traffic)}</span></td>'
            f'<td><span class="mono">{share}</span></td>'
            f'<td>{row["active_users"]}</td>'
            f'<td>{online}</td>'
            f'<td><code>profile={html.escape(row["profile"])}</code></td>'
            '</tr>'
        )
    rec = ctx.subscription_profiles.get(
        radar['recommendation'],
        ctx.subscription_profiles['default'],
    )
    return (
        '<section class="admin-section health-radar-section">'
        '<div class="admin-section-header">'
        '<div>'
        '<h2 class="admin-section-title">线路质量雷达</h2>'
        f'<div class="small">近 {radar["window_hours"]} 小时协议占比 · 总量 {ctx.fmt_bytes(radar["total_bytes"])}</div>'
        '</div>'
        f'<div class="badge">{html.escape(rec["label"])} · {html.escape(radar["reason"])}</div>'
        '</div>'
        '<div class="admin-section-body no-pad">'
        '<div class="data-table-wrap" tabindex="0" aria-label="线路质量雷达，可横向滚动">'
        '<table class="data-table">'
        '<thead><tr><th>线路</th><th>状态</th><th>24h 流量</th><th>占比</th><th>可用用户</th><th>在线</th><th>推荐入口</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody>'
        '</table>'
        '</div>'
        '</div>'
        '</section>'
    )


def render_line_radar_summary(ctx, now=None):
    """Minimal inline summary for the incidents page."""
    radar = build_line_radar(ctx, now=now)
    items = []
    for row in radar['rows']:
        share = f'{row["share"]:.1f}%' if radar['total_bytes'] > 0 else '—'
        items.append(
            f'<div class="radar-summary-row">'
            f'<span class="bold">{html.escape(row["label"])}</span>'
            f'<span class="mono">{share}</span>'
            f'</div>'
        )
    rec = ctx.subscription_profiles.get(
        radar['recommendation'],
        ctx.subscription_profiles['default'],
    )
    return (
        '<div class="radar-summary">' + ''.join(items) + '</div>'
        f'<div class="small faint" style="margin-top:8px;">推荐：{html.escape(rec["label"])} · {html.escape(radar["reason"])}</div>'
        f'<a class="btn ghost btn-sm" href="/admin/health" style="margin-top:12px;">查看完整健康状态</a>'
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
    current_now = now or ctx.local_now()
    summary = summarize_cost_calibration(ctx, now=current_now)
    windows = cost_calibrator.summarize_windows(
        ctx.cost_calibration_file,
        current_multiplier=ctx.display_multiplier,
        now=current_now,
    )
    policy = cost_calibrator.load_auto_policy(ctx.multiplier_auto_policy_file)
    runtime_state = ctx.load_json(ctx.display_multiplier_state_file, {})
    confidence = _CALIBRATION_CONFIDENCE_LABELS.get(summary['confidence'], summary['confidence'])
    delta = summary.get('delta_percent')
    delta_text = '—' if delta is None else f'{delta:+.1f}%'
    iface_text = ', '.join(summary.get('ifaces') or []) or '未识别'
    advice = '样本不足，先观察一段时间'
    apply_form = ''
    if summary['confidence'] in ('medium', 'high') and summary.get('suggested_multiplier') is not None:
        advice = '可应用为运行时倍率'
        apply_form = (
            '<form method="post" action="/admin/cost-multiplier/apply" class="inline-form-row" '
            'data-confirm="应用建议倍率会修改运行时流量倍率并重启面板服务，确认继续？">'
            '<button class="btn danger-btn btn-sm" type="submit">应用建议倍率</button>'
            '</form>'
        )
    window_rows = ''.join(
        '<tr>'
        f'<td style="padding-left:18px;">{int(w["window_hours"])}h</td>'
        f'<td>{_fmt_optional_multiplier(w["suggested_multiplier"])}</td>'
        f'<td>{_fmt_optional_multiplier(w["egress_multiplier"])}</td>'
        f'<td>{ctx.fmt_bytes(w["app_raw_bytes"])}</td>'
        f'<td>{w["included_sample_count"]}/{w["sample_count"]}</td>'
        f'<td style="padding-right:18px;">{html.escape(_CALIBRATION_CONFIDENCE_LABELS.get(w["confidence"], w["confidence"]))}</td>'
        '</tr>'
        for w in windows
    )
    checked = 'checked' if policy.get('enabled') else ''
    mode_total = 'selected' if policy.get('mode') == 'total' else ''
    mode_egress = 'selected' if policy.get('mode') == 'egress' else ''
    conf_opts = ''.join(
        f'<option value="{key}" {"selected" if policy.get("min_confidence") == key else ""}>{label}</option>'
        for key, label in (('medium', '中'), ('high', '高'))
    )
    last_state = ''
    if runtime_state.get('multiplier'):
        source = '自动' if runtime_state.get('auto') else '手动'
        last_state = (
            f'<div class="small faint">当前运行时覆盖：{_fmt_optional_multiplier(runtime_state.get("multiplier"))}'
            f' · {html.escape(source)} · {html.escape(str(runtime_state.get("applied_at") or ""))}</div>'
        )
    last_policy = ''
    if policy.get('last_checked_at'):
        last_policy = (
            f'<div class="small faint">自动检查：{html.escape(str(policy.get("last_decision") or ""))}'
            f' / {html.escape(str(policy.get("last_reason") or ""))}'
            f' · {html.escape(str(policy.get("last_checked_at") or ""))}</div>'
        )
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
        f'<td>{summary["included_sample_count"]}/{summary["sample_count"]} 个</td>'
        '<th>置信度</th>'
        f'<td>{html.escape(confidence)}</td>'
        '<th>公网网卡</th>'
        f'<td style="padding-right:18px;">{html.escape(iface_text)}</td></tr>'
    )
    return (
        '<section class="admin-section health-calibrator-section">'
        '<div class="admin-section-header">'
        '<div>'
        '<h2 class="admin-section-title">成本校准器</h2>'
        f'<div class="small">近 {summary["window_hours"]} 小时 · 系统网卡 / App 原始流量</div>'
        '</div>'
        f'<div class="row gap-sm"><div class="badge">{html.escape(advice)}</div>{apply_form}</div>'
        '</div>'
        '<div class="admin-section-body no-pad">'
        '<div class="data-table-wrap" tabindex="0" aria-label="成本校准数据，可横向滚动">'
        '<table class="data-table"><tbody>' + rows + '</tbody></table>'
        '<table class="data-table"><thead><tr><th>窗口</th><th>总量建议</th><th>出站建议</th><th>纳入流量</th><th>样本</th><th>置信度</th></tr></thead>'
        f'<tbody>{window_rows}</tbody></table>'
        '</div>'
        '<div class="calibrator-form">'
        '<form method="post" action="/admin/cost-multiplier/auto" class="inline-form" '
        'data-confirm="保存后，启用的自动策略可在满足条件时修改运行时倍率并重启面板服务，确认继续？">'
        '<div class="grid grid-3">'
        f'<label class="switch"><input type="checkbox" name="enabled" {checked}>自动调倍率</label>'
        '<div><label for="multiplier-auto-mode">依据</label><select id="multiplier-auto-mode" name="mode">'
        f'<option value="total" {mode_total}>公网 RX+TX 总量</option>'
        f'<option value="egress" {mode_egress}>公网 TX 出站</option>'
        '</select></div>'
        f'<div><label for="multiplier-auto-confidence">最低置信度</label><select id="multiplier-auto-confidence" name="min_confidence">{conf_opts}</select></div>'
        f'<div><label for="multiplier-auto-max-delta">最大单次变化 (%)</label><input id="multiplier-auto-max-delta" name="max_delta_percent" type="number" min="1" max="100" value="{float(policy["max_delta_percent"]):.0f}"></div>'
        f'<div><label for="multiplier-auto-min-delta">最小变化 (%)</label><input id="multiplier-auto-min-delta" name="min_delta_percent" type="number" min="0" max="50" value="{float(policy["min_delta_percent"]):.0f}"></div>'
        f'<div><label for="multiplier-auto-cooldown">冷却时间 (小时)</label><input id="multiplier-auto-cooldown" name="cooldown_hours" type="number" min="1" max="168" value="{float(policy["cooldown_hours"]):.0f}"></div>'
        '</div>'
        '<button class="btn secondary btn-sm" type="submit">保存自动策略</button>'
        '</form>'
        f'{last_state}{last_policy}'
        '<div class="small faint">'
        '建议倍率使用小样本过滤 + 10% 截尾加权平均；自动模式默认关闭，启用后仍受置信度、单次变化和冷却时间限制。'
        '</div>'
        '</div>'
        '</div>'
        '</section>'
    )
