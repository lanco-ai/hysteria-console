import display
import pytest


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


@pytest.mark.parametrize(
    'raw',
    ['nan', 'NaN', 'inf', '+Infinity', '-Infinity', float('nan'), float('inf')],
)
def test_non_finite_multipliers_are_rejected_by_permissive_parsers(raw):
    assert display.parse_display_multiplier(raw, default=1.25) == 1.25
    assert display.parse_optional_display_multiplier(raw) is None


@pytest.mark.parametrize('raw', ['NaN', 'Infinity', '-Infinity'])
def test_non_finite_runtime_multiplier_is_rejected_permissively_and_strictly(
    tmp_path, raw
):
    state = tmp_path / 'display_multiplier.json'
    state.write_text(
        f'{{"enabled": true, "multiplier": "{raw}"}}',
        encoding='utf-8',
    )

    assert display.runtime_display_multiplier(state) is None
    with pytest.raises(ValueError, match='invalid display multiplier value'):
        display.runtime_display_multiplier_strict(state)


def test_runtime_display_multiplier_reads_enabled_state_file(tmp_path):
    state = tmp_path / 'display_multiplier.json'
    state.write_text('{"enabled": true, "multiplier": 1.75}')

    assert display.runtime_display_multiplier(state) == 1.75


def test_runtime_display_multiplier_ignores_invalid_state_file(tmp_path):
    state = tmp_path / 'display_multiplier.json'
    state.write_text('{"enabled": true, "multiplier": 99}')

    assert display.runtime_display_multiplier(state) is None


def test_strict_runtime_multiplier_distinguishes_missing_from_corrupt(tmp_path):
    missing = tmp_path / 'missing.json'
    assert display.runtime_display_multiplier_strict(missing) is None

    corrupt = tmp_path / 'corrupt.json'
    corrupt.write_text('{"enabled": true, "multiplier": 99}')
    with pytest.raises(ValueError, match='invalid display multiplier value'):
        display.runtime_display_multiplier_strict(corrupt)

    malformed = tmp_path / 'malformed.json'
    malformed.write_text('{"enabled":')
    with pytest.raises(ValueError, match='invalid display multiplier JSON'):
        display.runtime_display_multiplier_strict(malformed)
