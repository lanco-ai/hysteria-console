import display


def test_source_placeholder_uses_default_multiplier():
    assert display.DEPLOYED_DISPLAY_MULTIPLIER == display.DEFAULT_DISPLAY_MULTIPLIER


def test_parse_display_multiplier_accepts_valid_values():
    assert display.parse_display_multiplier('1.5') == 1.5
    assert display.parse_display_multiplier(20) == 20.0


def test_parse_display_multiplier_rejects_invalid_values():
    assert display.parse_display_multiplier('__HY_DISPLAY_MULTIPLIER__') == 2.28
    assert display.parse_display_multiplier('not-a-number') == 2.28
    assert display.parse_display_multiplier('0.01') == 2.28
    assert display.parse_display_multiplier('99') == 2.28


def test_runtime_display_multiplier_reads_enabled_state_file(tmp_path):
    state = tmp_path / 'display_multiplier.json'
    state.write_text('{"enabled": true, "multiplier": 1.75}')

    assert display.runtime_display_multiplier(state) == 1.75


def test_runtime_display_multiplier_ignores_invalid_state_file(tmp_path):
    state = tmp_path / 'display_multiplier.json'
    state.write_text('{"enabled": true, "multiplier": 99}')

    assert display.runtime_display_multiplier(state) is None
