"""Pure SVG generators for usage analytics dashboards.

All functions are I/O-free string builders. They consume already-DISPLAY-MULTIPLIED
byte counts; they do not multiply themselves. Output uses only inline SVG with
class hooks the polling JS / CSS can target.
"""
import html

from subscription_service import fmt_bytes


def mini_sparkline_svg(values, *, height=24):
    """Render a series of (date, bytes) as a low-node compact line SVG.

    The earlier bar version emitted up to 30 rect/title pairs per user. A
    filled path, line path, and current-value dot preserve the trend while
    keeping DOM cost constant as the history window grows.
    """
    n = len(values)
    label = f'{n} 天趋势' if n else ''
    if n == 0:
        return f'<svg class="spark" viewBox="0 0 0 {height}" aria-hidden="true"></svg>'
    max_v = max((int(v) for _, v in values), default=0) or 1
    bar_w = 3
    gap = 1
    width = n * bar_w + (n - 1) * gap
    floor_y = max(1, height - 1)
    draw_h = max(1, height - 2)
    points = []
    for i, (dk, v) in enumerate(values):
        x = width / 2 if n == 1 else i * width / (n - 1)
        y = floor_y - draw_h * max(0, int(v)) / max_v
        points.append((x, max(1, min(floor_y, y))))
    line_d = ' '.join(
        ('M' if i == 0 else 'L') + f'{x:.2f},{y:.2f}'
        for i, (x, y) in enumerate(points)
    )
    area_points = ' '.join(f'L{x:.2f},{y:.2f}' for x, y in points)
    area_d = f'M0,{floor_y} {area_points} L{width},{floor_y} Z'
    last_x, last_y = points[-1]
    last_date, last_value = values[-1]
    peak_date, peak_value = max(values, key=lambda item: int(item[1]))
    title = (f'{n} 天趋势 · 最新 {last_date}: {fmt_bytes(int(last_value))} · '
             f'峰值 {peak_date}: {fmt_bytes(int(peak_value))}')
    return (f'<svg class="spark" viewBox="0 0 {width} {height}" '
            f'aria-label="{html.escape(label)}">'
            f'<title>{html.escape(title)}</title>'
            f'<path class="spark-area" d="{area_d}"/>'
            f'<path class="spark-line" d="{line_d}" vector-effect="non-scaling-stroke"/>'
            f'<circle class="spark-dot today" cx="{last_x:.2f}" cy="{last_y:.2f}" r="1.8"/>'
            f'</svg>')


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


def weekday_hour_heatmap_svg(grid, *, current_hour_iso=None,
                             cell_w=20, cell_h=22, label_w=46):
    """Render a 7×24 heatmap of bytes-per-hour-per-day.

    Args:
        grid: list of 7 {"date": "YYYY-MM-DD", "hours": [24 ints]}, oldest first.
        current_hour_iso: optional "YYYY-MM-DDTHH"; cells in `grid[-1]` after this
                          hour-of-day get class="heat-cell future".
    """
    rows = len(grid)
    width = label_w + 24 * cell_w
    height = rows * cell_h + 28

    max_v = 0
    for row in grid:
        for v in row["hours"]:
            if v > max_v:
                max_v = v
    if max_v <= 0:
        max_v = 1

    today_idx = rows - 1
    cur_hour_of_day = None
    if current_hour_iso and grid and current_hour_iso[:10] == grid[today_idx]["date"]:
        try:
            cur_hour_of_day = int(current_hour_iso[11:13])
        except ValueError:
            cur_hour_of_day = None

    parts = []
    for r, row in enumerate(grid):
        y = r * cell_h + cell_h - 6
        parts.append(
            f'<text class="heat-date" x="{label_w - 6}" y="{y}" '
            f'text-anchor="end">{row["date"][5:]}</text>'
        )

    for r, row in enumerate(grid):
        y = r * cell_h + 1
        for c, v in enumerate(row["hours"]):
            x = label_w + c * cell_w
            is_future = (r == today_idx
                         and cur_hour_of_day is not None
                         and c > cur_hour_of_day)
            if is_future:
                parts.append(
                    f'<rect class="heat-cell future" x="{x}" y="{y}" '
                    f'width="{cell_w - 1}" height="{cell_h - 2}"/>'
                )
            else:
                op = 0.05 + 0.95 * (v / max_v)
                parts.append(
                    f'<rect class="heat-cell" x="{x}" y="{y}" '
                    f'width="{cell_w - 1}" height="{cell_h - 2}" '
                    f'opacity="{op:.2f}"/>'
                )

    for h in (0, 4, 8, 12, 16, 20, 23):
        x = label_w + h * cell_w + cell_w / 2
        ylab = rows * cell_h + 12
        parts.append(
            f'<text class="heat-hour" x="{x:.0f}" y="{ylab}" '
            f'text-anchor="middle">{h}</text>'
        )

    return (f'<svg class="heatmap" viewBox="0 0 {width} {height}" '
            f'aria-label="7 天小时热图">{"".join(parts)}</svg>')
