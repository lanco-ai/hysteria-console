"""Explicit public response models for incident triage data."""

import math

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from .models import PublicModel
from .usage_models import UsageStatsResponse


class IncidentPeakUserResponse(PublicModel):
    user: StrictStr
    bytes: StrictInt

    @field_validator('bytes')
    @classmethod
    def bytes_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('incident bytes must be non-negative')
        return value


class IncidentPeakResponse(PublicModel):
    hour: StrictStr
    bytes: StrictInt
    users: list[IncidentPeakUserResponse]

    @field_validator('bytes')
    @classmethod
    def bytes_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('incident bytes must be non-negative')
        return value


class IncidentUserResponse(PublicModel):
    user: StrictStr
    revision: StrictStr
    last_24h_bytes: StrictInt
    cycle_used_bytes: StrictInt
    quota_bytes: StrictInt
    quota_percent: StrictFloat
    online: StrictInt
    disabled: StrictBool
    expired: StrictBool
    expiry_label: StrictStr
    note: StrictStr

    @field_validator('last_24h_bytes', 'cycle_used_bytes', 'quota_bytes', 'online')
    @classmethod
    def counters_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('incident counters must be non-negative')
        return value

    @field_validator('quota_percent')
    @classmethod
    def percentage_must_be_finite(cls, value):
        if not math.isfinite(value) or value < 0:
            raise ValueError('incident percentage must be finite')
        return value


class IncidentRadarRowResponse(PublicModel):
    key: StrictStr
    label: StrictStr
    status: StrictStr
    ok: StrictBool
    bytes: StrictInt
    share: StrictFloat
    active_users: StrictInt
    online: StrictInt | None
    profile: StrictStr
    note: StrictStr


class IncidentRadarResponse(PublicModel):
    window_hours: StrictInt
    total_bytes: StrictInt
    recommendation: StrictStr
    reason: StrictStr
    rows: list[IncidentRadarRowResponse]


class IncidentAlertResponse(PublicModel):
    kind: StrictStr
    label: StrictStr
    user: StrictStr
    key: StrictStr


class AdminIncidentResponse(PublicModel):
    ts: StrictStr
    stats: UsageStatsResponse
    peak_hour: IncidentPeakResponse
    users: list[IncidentUserResponse]
    line_radar: IncidentRadarResponse
    cost_calibration: dict[str, object]
    alerts: list[IncidentAlertResponse]
