"""Explicit public response models for administrator user detail pages."""

from pydantic import StrictBool, StrictInt, StrictStr, field_validator

from .models import PublicModel


def _non_negative(value):
    if value < 0:
        raise ValueError('usage values must be non-negative')
    return value


class UserDetailAlertResponse(PublicModel):
    ts: StrictStr
    kind: StrictStr
    details: StrictStr


class UserDetailHourlyResponse(PublicModel):
    hour: StrictStr
    bytes: StrictInt

    @field_validator('bytes')
    @classmethod
    def bytes_must_be_non_negative(cls, value):
        return _non_negative(value)


class UserDetailHeatmapResponse(PublicModel):
    date: StrictStr
    hours: list[StrictInt]

    @field_validator('hours')
    @classmethod
    def hours_must_be_a_day_of_values(cls, value):
        if len(value) != 24:
            raise ValueError('heatmap rows must contain 24 hours')
        return [_non_negative(item) for item in value]


class AdminUserDetailResponse(PublicModel):
    ts: StrictStr
    uid: StrictStr
    metered: StrictBool
    disabled: StrictBool
    expired: StrictBool
    expires_at: StrictStr | None
    expiry_label: StrictStr
    note: StrictStr
    online: StrictInt
    max_devices: StrictInt
    cycle_used_bytes: StrictInt
    cycle_quota_bytes: StrictInt
    quota_extra_bytes: StrictInt
    current_hour_bytes: StrictInt
    today_bytes: StrictInt
    recent_alerts: list[UserDetailAlertResponse]
    hourly_bars: list[UserDetailHourlyResponse]
    heatmap: list[UserDetailHeatmapResponse]

    @field_validator(
        'online',
        'max_devices',
        'cycle_used_bytes',
        'cycle_quota_bytes',
        'quota_extra_bytes',
        'current_hour_bytes',
        'today_bytes',
    )
    @classmethod
    def numeric_values_must_be_non_negative(cls, value):
        return _non_negative(value)
