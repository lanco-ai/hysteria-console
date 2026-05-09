import re

import charts


def test_mini_sparkline_svg_empty_input_returns_empty_svg():
    out = charts.mini_sparkline_svg([])
    assert '<svg class="spark"' in out
    assert "<rect" not in out


def test_mini_sparkline_svg_renders_one_rect_per_nonzero_value():
    values = [("2026-05-01", 100), ("2026-05-02", 0), ("2026-05-03", 50)]
    out = charts.mini_sparkline_svg(values)
    rects = re.findall(r"<rect ", out)
    assert len(rects) == 2  # zero day skipped


def test_mini_sparkline_svg_marks_today_class_on_last_bar():
    values = [("2026-05-01", 100), ("2026-05-02", 50)]
    out = charts.mini_sparkline_svg(values)
    assert 'class="spark-bar today"' in out


def test_mini_sparkline_svg_height_param_default_24():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values)
    assert "viewBox=\"0 0 3 24\"" in out, "default height is 24 (legacy 30-day sparkline)"


def test_mini_sparkline_svg_height_14_for_topn():
    values = [("2026-05-01", 100)]
    out = charts.mini_sparkline_svg(values, height=14)
    assert "viewBox=\"0 0 3 14\"" in out
