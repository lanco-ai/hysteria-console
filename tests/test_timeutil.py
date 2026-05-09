from datetime import timezone, timedelta
from zoneinfo import ZoneInfo

import timeutil


def test_local_now_returns_aware_shanghai_datetime():
    now = timeutil.local_now()
    assert now.tzinfo is not None, "local_now() must return tz-aware datetime"
    assert now.tzinfo.key == "Asia/Shanghai"


def test_local_now_offset_is_plus_8():
    now = timeutil.local_now()
    assert now.utcoffset() == timedelta(hours=8)


def test_local_tz_constant_is_shanghai():
    assert timeutil.LOCAL_TZ == ZoneInfo("Asia/Shanghai")
