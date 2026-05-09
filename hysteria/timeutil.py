"""Single source of truth for time-bucket 'now' across the project.

All daily/hourly/cycle bucket keys are computed from local_now(). Audit log
timestamps continue to use datetime.utcnow() — that's a separate concern.
"""
from datetime import datetime
from zoneinfo import ZoneInfo

LOCAL_TZ = ZoneInfo("Asia/Shanghai")


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)
