"""parse_date_field — strict YYYY-MM-DD with year range 2000..2099."""
import subscription_service as ss


def _assert_valid(raw):
    assert ss.parse_date_field(raw) == raw, repr(raw)


def _assert_invalid(raw):
    assert ss.parse_date_field(raw) == '', repr(raw)


def test_empty_returns_empty():
    assert ss.parse_date_field('') == ''
    assert ss.parse_date_field('   ') == ''
    assert ss.parse_date_field(None) == ''


def test_valid_leading_edge_2000_01_01():
    _assert_valid('2000-01-01')


def test_valid_trailing_edge_2099_12_31():
    _assert_valid('2099-12-31')


def test_valid_mid_range():
    _assert_valid('2026-08-22')


def test_valid_leap_year_2028():
    _assert_valid('2028-02-29')


def test_invalid_year_too_low():
    _assert_invalid('1999-12-31')


def test_invalid_year_too_high_2100():
    _assert_invalid('2100-01-01')


def test_invalid_year_too_high_9999():
    _assert_invalid('9999-01-01')


def test_invalid_six_digit_year():
    _assert_invalid('111111-11-11')
    _assert_invalid('200001-11-11')


def test_invalid_non_leap_year():
    _assert_invalid('2026-02-29')


def test_invalid_wrong_separator():
    _assert_invalid('2026/08/22')


def test_invalid_short_year():
    _assert_invalid('26-08-22')


def test_invalid_garbage():
    _assert_invalid('abc')
    _assert_invalid('not-a-date')


def test_invalid_two_digit_year_pattern():
    # 4-digit boundary but year below 2000
    _assert_invalid('1999-12-31')


def test_valid_returns_normalized_iso():
    assert ss.parse_date_field('  2026-08-22  ') == '2026-08-22'
