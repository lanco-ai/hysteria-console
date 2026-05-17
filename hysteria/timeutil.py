"""Single source of truth for time-bucket 'now' across the project.

All daily/hourly/cycle bucket keys are computed from local_now(). Audit log
timestamps continue to use datetime.utcnow() — that's a separate concern.
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def billing_cycle_key(now, settlement_day):
    """Cycle key 'YYYY-MM' where MM is the month containing the cycle's end day.

    Before `settlement_day` of the month the current traffic still belongs to
    the previous cycle. Shared by traffic_limiter.billing_month_key and
    subscription_service.month_key — both used to maintain their own copy of
    this calculation with a comment saying "must match", which is exactly the
    drift hazard this helper exists to eliminate.
    """
    if now.day >= settlement_day:
        return now.strftime("%Y-%m")
    prev = now.replace(day=1) - timedelta(days=1)
    return prev.strftime("%Y-%m")
