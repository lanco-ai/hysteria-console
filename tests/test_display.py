import display


def test_source_placeholder_uses_default_multiplier():
    assert display.DISPLAY_MULTIPLIER == display.DEFAULT_DISPLAY_MULTIPLIER


def test_parse_display_multiplier_accepts_valid_values():
    assert display.parse_display_multiplier('1.5') == 1.5
    assert display.parse_display_multiplier(20) == 20.0


def test_parse_display_multiplier_rejects_invalid_values():
    assert display.parse_display_multiplier('__HY_DISPLAY_MULTIPLIER__') == 2.28
    assert display.parse_display_multiplier('not-a-number') == 2.28
    assert display.parse_display_multiplier('0.01') == 2.28
    assert display.parse_display_multiplier('99') == 2.28
