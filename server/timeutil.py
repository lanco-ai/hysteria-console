"""Single source of truth for time-bucket 'now' across the project.

Hourly bucket keys and displayed local times are computed from local_now().
Daily quota buckets and billing-cycle boundaries use the billing clock
(BILLING_TZ) instead. Audit log timestamps continue to use datetime.utcnow() —
that's a separate concern.
"""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")
# The VPS provider meters traffic per UTC day and rolls its allowance at
# 00:00 UTC on the settlement day, so billing days and cycles follow UTC.
BILLING_TZ = timezone.utc


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def billing_now(now=None) -> datetime:
    """`now` (default: the current time) on the billing clock.

    Naive datetimes carry no instant to convert and are treated as values
    that are already on the billing clock.
    """
    current = local_now() if now is None else now
    if current.tzinfo is None:
        return current
    return current.astimezone(BILLING_TZ)


def billing_day_key(now=None) -> str:
    """Daily quota bucket key 'YYYY-MM-DD' for `now` on the billing clock."""
    return billing_now(now).strftime("%Y-%m-%d")


def local_hour_key(moment) -> str:
    """Hourly bucket key 'YYYY-MM-DDTHH' for an instant; hours stay on LOCAL_TZ."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(LOCAL_TZ)
    return moment.strftime("%Y-%m-%dT%H")


def billing_cycle_key(now, settlement_day):
    """Cycle key 'YYYY-MM' where MM is the month containing the cycle's end day.

    Before `settlement_day` of the month the current traffic still belongs to
    the previous cycle. Shared by traffic_limiter.billing_month_key and
    subscription_service.month_key — both used to maintain their own copy of
    this calculation with a comment saying "must match", which is exactly the
    drift hazard this helper exists to eliminate.
    """
    now = billing_now(now)
    if now.day >= settlement_day:
        return now.strftime("%Y-%m")
    prev = now.replace(day=1) - timedelta(days=1)
    return prev.strftime("%Y-%m")
