"""Billing days and cycles follow UTC, the clock the VPS provider meters on.

00:00 UTC is 08:00 in Asia/Shanghai, so every boundary below is checked one
minute either side of 08:00 Shanghai time on the settlement day.
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import cycle
import timeutil
import traffic_limiter as tl

SH = ZoneInfo('Asia/Shanghai')
META = {'settlement_day': 15, 'cycle_length_days': 30}
BEFORE = datetime(2026, 10, 15, 7, 59, tzinfo=SH)  # 2026-10-14 23:59 UTC
AFTER = datetime(2026, 10, 15, 8, 0, tzinfo=SH)  # 2026-10-15 00:00 UTC


def test_billing_clock_is_utc_and_naive_values_pass_through():
    assert timeutil.BILLING_TZ == timezone.utc
    assert timeutil.billing_now(AFTER) == datetime(2026, 10, 15, tzinfo=timezone.utc)
    naive = datetime(2026, 10, 15, 7, 59)
    assert timeutil.billing_now(naive) is naive


def test_billing_day_key_rolls_at_utc_midnight():
    assert timeutil.billing_day_key(BEFORE) == '2026-10-14'
    assert timeutil.billing_day_key(AFTER) == '2026-10-15'


def test_hour_keys_stay_on_local_time():
    assert timeutil.local_hour_key(datetime(2026, 10, 15, tzinfo=timezone.utc)) == '2026-10-15T08'
    assert timeutil.local_hour_key(datetime(2026, 10, 15, 8)) == '2026-10-15T08'


def test_cycle_key_and_boundaries_follow_utc_settlement():
    assert timeutil.billing_cycle_key(BEFORE, 15) == '2026-09'
    assert timeutil.billing_cycle_key(AFTER, 15) == '2026-10'
    assert cycle.cycle_start_for(BEFORE, meta=META) == datetime(2026, 9, 15, tzinfo=timezone.utc)
    assert cycle.next_cycle_start_for(BEFORE, meta=META) == datetime(
        2026, 10, 15, tzinfo=timezone.utc
    )
    assert cycle.cycle_start_for(AFTER, meta=META) == datetime(2026, 10, 15, tzinfo=timezone.utc)


def test_cycle_days_are_utc_days():
    before = cycle.cycle_days(BEFORE, meta=META)
    assert (before[0], before[-1], len(before)) == ('2026-09-15', '2026-10-14', 30)
    assert cycle.cycle_days(AFTER, meta=META) == ['2026-10-15']


def test_limiter_credits_the_utc_billing_day(tmp_path, monkeypatch):
    path = tmp_path / 'usage_daily.json'
    monkeypatch.setattr(tl, 'USAGE_DAILY_FILE', str(path))
    daily = tl.accumulate_daily({'alice': {'tx': 1, 'rx': 2}}, BEFORE, daily={})
    daily = tl.accumulate_daily({'alice': {'tx': 10, 'rx': 20}}, AFTER, daily=daily)
    assert daily['2026-10-14']['alice'] == {'tx': 1, 'rx': 2, 'total': 3}
    assert daily['2026-10-15']['alice'] == {'tx': 10, 'rx': 20, 'total': 30}
    assert json.loads(path.read_text()) == daily
    assert tl.cycle_used_raw_for('alice', daily, now=BEFORE, meta=META) == 3
    assert tl.cycle_used_raw_for('alice', daily, now=AFTER, meta=META) == 30


def test_auto_reset_waits_for_the_utc_settlement_day(tmp_path, monkeypatch):
    usage_path = tmp_path / 'usage.json'
    state_path = tmp_path / 'auto_reset_state.json'
    state_path.write_text('{}')
    monkeypatch.setattr(tl, 'USAGE_FILE', str(usage_path))
    monkeypatch.setattr(tl, 'RESET_STATE_FILE', str(state_path))
    monkeypatch.setattr(tl, 'append_reset_log', lambda **kwargs: None)
    users = {'alice': {}}
    usage = {'2026-10': {'alice': {'tx': 5, 'rx': 5, 'total': 10}}}

    tl.maybe_reset_all_usage_on_day_21(BEFORE, users, usage, '2026-10', day=15)
    assert usage['2026-10']['alice']['total'] == 10
    assert not usage_path.exists()

    tl.maybe_reset_all_usage_on_day_21(AFTER, users, usage, '2026-10', day=15)
    assert usage['2026-10']['alice'] == {'tx': 0, 'rx': 0, 'total': 0}
    assert json.loads(state_path.read_text())['last_reset_month'] == '2026-10'
