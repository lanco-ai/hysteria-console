"""Pure SVG generators for usage analytics dashboards.

All functions are I/O-free string builders. They consume already-DISPLAY-MULTIPLIED
byte counts; they do not multiply themselves. Output uses only inline SVG with
class hooks the polling JS / CSS can target.
"""
import html

from subscription_service import fmt_bytes


def mini_sparkline_svg(values, *, height=24):
    """Render a series of (date, bytes) into a compact bar SVG.

    Last entry carries the `today` class; zero-valued days render no bar.
    Width/height come from the viewBox so the caller's CSS can size the SVG.

    Output contract (relied on by the admin dashboard's polling JS):
    - Outermost element is `<svg class="spark" ...>` — JS uses this class.
    - Each non-empty bar is `<rect class="spark-bar [today]" ...>` — CSS uses these.
    Default height=24 matches the legacy 30-day per-user-row sparkline; Top-N
    rows pass height=14 for the compact variant.
    """
    n = len(values)
    label = f'{n} 天趋势' if n else ''
    if n == 0:
        return f'<svg class="spark" viewBox="0 0 0 {height}" aria-hidden="true"></svg>'
    max_v = max((v for _, v in values), default=0) or 1
    bar_w = 3
    gap = 1
    width = n * bar_w + (n - 1) * gap
    parts = []
    for i, (dk, v) in enumerate(values):
        if v <= 0:
            continue
        h = max(1, int(round(height * v / max_v)))
        x = i * (bar_w + gap)
        y = height - h
        cls = 'spark-bar today' if i == n - 1 else 'spark-bar'
        title = f'{dk}: {fmt_bytes(v)}'
        parts.append(
            f'<rect class="{cls}" x="{x}" y="{y}" width="{bar_w}" height="{h}">'
            f'<title>{html.escape(title)}</title></rect>'
        )
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'aria-label="{html.escape(label)}">'
            f'{"".join(parts)}</svg>')


def hourly_bars_svg(series, *, peak_hour=None, height=120, bar_w=3, gap=1):
    """Render an hourly time-series as a compact bar chart with day separators.

    Args:
        series: list of {"hour": "YYYY-MM-DDTHH", "bytes": int}, oldest first.
                bytes already × DISPLAY_MULTIPLIER.
        peak_hour: optional hour key to highlight with the `peak` class.
        height: SVG drawable height (excluding day-label strip).
    """
    n = len(series)
    if n == 0:
        return '<svg class="hourly-bars" viewBox="0 0 0 0" aria-hidden="true"></svg>'

    max_v = max((s["bytes"] for s in series), default=0)
    if max_v <= 0:
        max_v = 1
    width = n * bar_w + (n - 1) * gap
    label_strip = 16
    total_h = height + label_strip

    seen_days = set()
    seps = []
    for i, s in enumerate(series):
        day = s["hour"][:10]
        if day not in seen_days:
            seen_days.add(day)
            if i > 0:
                x = i * (bar_w + gap)
                seps.append(f'<line x1="{x}" y1="0" x2="{x}" y2="{height}"/>')
    sep_svg = f'<g class="day-separators">{"".join(seps)}</g>'

    bars = []
    for i, s in enumerate(series):
        v = int(s["bytes"])
        if v <= 0:
            continue
        h = max(1, int(round(height * v / max_v)))
        x = i * (bar_w + gap)
        y = height - h
        cls = "hourly-bar peak" if s["hour"] == peak_hour else "hourly-bar"
        bars.append(
            f'<rect class="{cls}" x="{x}" y="{y}" width="{bar_w}" height="{h}" '
            f'data-hour="{s["hour"]}" data-bytes="{v}"/>'
        )
    bar_svg = f'<g class="bars">{"".join(bars)}</g>'

    days_in_order = []
    for s in series:
        d = s["hour"][:10]
        if not days_in_order or days_in_order[-1] != d:
            days_in_order.append(d)
    label_parts = []
    cursor = 0
    for d in days_in_order:
        run = sum(1 for s in series if s["hour"][:10] == d)
        midx = (cursor + run / 2) * (bar_w + gap)
        label_parts.append(
            f'<text class="day-label" x="{midx:.1f}" y="{total_h - 3}" '
            f'text-anchor="middle">{d[5:]}</text>'
        )
        cursor += run
    label_svg = f'<g class="day-labels">{"".join(label_parts)}</g>'

    return (f'<svg class="hourly-bars" viewBox="0 0 {width} {total_h}" '
            f'aria-label="过去 {n} 小时流量">'
            f'{sep_svg}{bar_svg}{label_svg}</svg>')
