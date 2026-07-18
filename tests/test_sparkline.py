"""Sparkline SVG rendering — pure function on a list of (date, bytes)."""
import re

import subscription_service as ss


def test_empty_returns_minimal_svg():
    out = ss.sparkline_svg([])
    assert '<svg' in out and '</svg>' in out
    assert '<rect' not in out


def test_zero_values_render_constant_size_line():
    vals = [(f'2026-05-0{i+1}', 0) for i in range(5)]
    out = ss.sparkline_svg(vals)
    assert out.count('<path') == 2
    assert out.count('<circle') == 1
    assert '<rect' not in out


def test_today_dot_carries_today_class():
    vals = [(f'2026-05-0{i+1}', i * 1_000_000) for i in range(1, 6)]
    out = ss.sparkline_svg(vals)
    dots = re.findall(r'<circle[^>]*>', out)
    assert len(dots) == 1
    assert 'today' in dots[0]


def test_max_height_does_not_overflow():
    vals = [('2026-05-01', 100), ('2026-05-02', 200), ('2026-05-03', 50)]
    out = ss.sparkline_svg(vals, height=24)
    ys = [float(y) for y in re.findall(r'(?:,|cy=")([0-9]+(?:\.[0-9]+)?)', out)]
    assert ys
    assert all(0 <= y <= 24 for y in ys)


def test_title_contains_date_and_bytes():
    vals = [('2026-05-05', 1_500_000_000)]
    out = ss.sparkline_svg(vals)
    assert '<title>' in out and '2026-05-05' in out
    assert 'GB' in out  # fmt_bytes formats as 1.40 GB


def test_admin_render_includes_sparkline_column(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json', raising=False)
    monkeypatch.setattr(ss, 'USAGE_FILE', tmp_path / 'usage.json', raising=False)
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json', raising=False)
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json', raising=False)

    (tmp_path / 'users.json').write_text('{"alice": {"monthly_quota_bytes": 0}}')
    (tmp_path / 'usage.json').write_text('{}')
    (tmp_path / 'usage_daily.json').write_text(
        '{"2026-05-05": {"alice": {"tx":0,"rx":1000000000,"total":1000000000}}}')
    (tmp_path / 'online.json').write_text('{}')

    out = ss.render_admin('panel.example.com', 'http://panel.example.com')
    # New column header
    assert '30 天趋势' in out or '趋势' in out
    # SVG present in the row
    assert 'class="spark"' in out


def test_admin_render_loads_poll_script_as_external_script(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, 'USERS_FILE', tmp_path / 'users.json', raising=False)
    monkeypatch.setattr(ss, 'USAGE_FILE', tmp_path / 'usage.json', raising=False)
    monkeypatch.setattr(ss, 'USAGE_DAILY_FILE', tmp_path / 'usage_daily.json', raising=False)
    monkeypatch.setattr(ss, 'ONLINE_FILE', tmp_path / 'online.json', raising=False)

    (tmp_path / 'users.json').write_text('{}')
    (tmp_path / 'usage.json').write_text('{}')
    (tmp_path / 'usage_daily.json').write_text('{}')
    (tmp_path / 'online.json').write_text('{}')

    out = ss.render_admin('panel.example.com', 'http://panel.example.com')
    expected = f'<script src="/static/admin-poll.js?v={ss.ADMIN_POLL_JS_ETAG.strip(chr(34))}" defer></script>'
    assert expected in out
    assert '<script>\n<script src="/static/admin-poll.js' not in out
