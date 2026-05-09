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
