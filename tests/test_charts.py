import re

import charts


def test_mini_sparkline_svg_empty_input_returns_empty_svg():
    out = charts.mini_sparkline_svg([])
    assert '<svg class="spark"' in out
    assert "<rect" not in out


def test_mini_sparkline_svg_uses_constant_low_node_paths():
    values = [("2026-05-01", 100), ("2026-05-02", 0), ("2026-05-03", 50)]
    out = charts.mini_sparkline_svg(values)
    assert out.count("<path ") == 2
    assert out.count("<circle ") == 1
    assert "<rect" not in out


def test_mini_sparkline_svg_marks_today_class_on_last_dot():
    values = [("2026-05-01", 100), ("2026-05-02", 50)]
    out = charts.mini_sparkline_svg(values)
    assert 'class="spark-dot today"' in out


def test_mini_sparkline_svg_height_param_default_24():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values)
    assert "viewBox=\"0 0 3 24\"" in out, "default height is 24 (legacy 30-day sparkline)"


def test_mini_sparkline_svg_height_14_for_topn():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values, height=14)
    assert "viewBox=\"0 0 3 14\"" in out


def test_hourly_bars_svg_empty_input():
    out = charts.hourly_bars_svg([])
    assert '<svg class="hourly-bars"' in out
    assert "<rect" not in out


def test_hourly_bars_svg_renders_one_rect_per_nonzero_hour():
    series = [{"hour": f"2026-05-0{(i//24)+2}T{i%24:02d}", "bytes": (i % 5) * 1_000_000_000}
              for i in range(168)]
    out = charts.hourly_bars_svg(series)
    rects = re.findall(r"<rect ", out)
    expected_nonzero = sum(1 for s in series if s["bytes"] > 0)
    assert len(rects) == expected_nonzero


def test_hourly_bars_svg_marks_peak_with_alert_class():
    series = [{"hour": "2026-05-08T00", "bytes": 1_000_000_000},
              {"hour": "2026-05-08T01", "bytes": 5_000_000_000},
              {"hour": "2026-05-08T02", "bytes": 2_000_000_000}]
    out = charts.hourly_bars_svg(series, peak_hour="2026-05-08T01")
    assert 'class="hourly-bar peak"' in out


def test_hourly_bars_svg_attaches_data_attrs_for_hover():
    series = [{"hour": "2026-05-08T00", "bytes": 1_073_741_824}]
    out = charts.hourly_bars_svg(series)
    assert 'data-hour="2026-05-08T00"' in out
    assert 'data-bytes="1073741824"' in out


def test_hourly_bars_svg_handles_all_zero_input():
    series = [{"hour": f"2026-05-08T{i:02d}", "bytes": 0} for i in range(24)]
    out = charts.hourly_bars_svg(series)
    assert "<rect" not in out


def test_heatmap_svg_renders_7x24_cells():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [j * 1_000_000 for j in range(24)]}
            for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    rects = re.findall(r"<rect ", out)
    assert len(rects) == 7 * 24


def test_heatmap_svg_dashes_future_cells_in_today_row():
    today = "2026-05-08"
    grid = [{"date": f"2026-05-0{i+2}", "hours": [0] * 24} for i in range(6)] + [
        {"date": today, "hours": [1, 1, 1, 0, 0, 0] + [0] * 18}
    ]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=f"{today}T02")
    assert 'class="heat-cell future"' in out
    assert out.count('class="heat-cell future"') == 21  # hours 3..23


def test_heatmap_svg_intensity_proportional_to_value():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [j for j in range(24)]} for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    assert 'opacity="0.16"' in out
    assert 'opacity="0.60"' in out or 'opacity="0.62"' in out
    assert 'opacity="1.00"' in out


def test_heatmap_svg_handles_all_zero_input():
    grid = [{"date": f"2026-05-0{i+2}", "hours": [0] * 24} for i in range(7)]
    out = charts.weekday_hour_heatmap_svg(grid, current_hour_iso=None)
    assert 'class="heat-cell"' in out
