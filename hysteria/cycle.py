"""Shared billing-cycle helpers.

This module is pure calculation: callers own their own file I/O and pass the
already-loaded meta dict in. Keeping the cycle math here prevents the admin UI,
cron limiter, and auth backend from drifting at settlement boundaries.
"""
from datetime import datetime, timedelta

SETTLEMENT_DAY_DEFAULT = 12
CYCLE_LENGTH_DAYS_DEFAULT = 30
CYCLE_LENGTH_MIN = 1
CYCLE_LENGTH_MAX = 90


def clamp_settlement_day(value, default=SETTLEMENT_DAY_DEFAULT):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(28, v))


def clamp_cycle_length(value, default=CYCLE_LENGTH_DAYS_DEFAULT):
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return max(CYCLE_LENGTH_MIN, min(CYCLE_LENGTH_MAX, v))


def settlement_day_from_meta(meta):
    return clamp_settlement_day((meta or {}).get("settlement_day", SETTLEMENT_DAY_DEFAULT))


def cycle_length_from_meta(meta):
    return clamp_cycle_length((meta or {}).get("cycle_length_days", CYCLE_LENGTH_DAYS_DEFAULT))


def settlement_anchor_date(now, settlement_day):
    """Most recent date with day-of-month == settlement_day, on/before now."""
    day = clamp_settlement_day(settlement_day)
    if now.day >= day:
        return now.date().replace(day=day)
    prev_month_end = now.replace(day=1) - timedelta(days=1)
    return prev_month_end.date().replace(day=day)


def cycle_anchor_date(now, meta=None, settlement_day=None):
    """Persisted cycle-calendar origin, falling back to the current settlement day."""
    m = meta or {}
    raw = m.get("cycle_anchor_date")
    if raw:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d").date()
        except (TypeError, ValueError):
            pass
    day = settlement_day_from_meta(m) if settlement_day is None else clamp_settlement_day(settlement_day)
    return settlement_anchor_date(now, day)


def cycle_start_for(now, day=None, length=None, anchor=None, meta=None):
    """Datetime at 00:00 local of the current fixed-length billing cycle."""
    m = meta or {}
    if anchor is None:
        anchor = (
            cycle_anchor_date(now, m)
            if day is None else settlement_anchor_date(now, day)
        )
    cycle_len = clamp_cycle_length(length, cycle_length_from_meta(m))
    today = now.date()
    if today < anchor:
        start_date = anchor
    else:
        offset_days = (today - anchor).days
        start_date = anchor + timedelta(days=(offset_days // cycle_len) * cycle_len)
    return datetime.combine(start_date, datetime.min.time(), tzinfo=now.tzinfo)


def cycle_days(now, day=None, length=None, anchor=None, meta=None):
    """List of YYYY-MM-DD date keys in the current cycle, oldest first."""
    cycle_len = clamp_cycle_length(length, cycle_length_from_meta(meta or {}))
    start = cycle_start_for(now, day=day, length=cycle_len, anchor=anchor, meta=meta).date()
    end = min(start + timedelta(days=cycle_len - 1), now.date())
    out = []
    cur = start
    while cur <= end:
        out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out
