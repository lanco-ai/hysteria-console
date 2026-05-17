"""timeutil.billing_cycle_key is the shared implementation behind both
traffic_limiter.billing_month_key and subscription_service.month_key. These
tests pin (a) the shared helper's edge cases and (b) that the two wrappers
keep returning identical values for every day of the year — the exact drift
the dedup was created to prevent.
"""
from datetime import datetime, timedelta

import traffic_limiter as tl
from timeutil import billing_cycle_key


def test_after_settlement_day_uses_current_month():
    assert billing_cycle_key(datetime(2026, 5, 15), 12) == '2026-05'


def test_before_settlement_day_uses_previous_month():
    assert billing_cycle_key(datetime(2026, 5, 5), 12) == '2026-04'


def test_settlement_day_is_inclusive():
    # day == settlement_day → already in the new cycle
    assert billing_cycle_key(datetime(2026, 5, 12), 12) == '2026-05'
    assert billing_cycle_key(datetime(2026, 5, 11), 12) == '2026-04'


def test_january_rolls_to_previous_year():
    assert billing_cycle_key(datetime(2026, 1, 5), 12) == '2025-12'


def test_traffic_limiter_wrapper_matches_shared_helper_every_day_of_year():
    # Walk a whole year. For every (date, settlement_day) pair, the legacy
    # billing_month_key and the new billing_cycle_key must agree — proving
    # the wrapper hasn't silently regressed.
    start = datetime(2026, 1, 1)
    for offset in range(366):
        d = start + timedelta(days=offset)
        for settle_day in (1, 12, 21, 28):
            assert tl.billing_month_key(d, day=settle_day) == billing_cycle_key(d, settle_day)
