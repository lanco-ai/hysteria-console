"""Explicit public response models for the administrator health page."""

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from .models import PublicModel


class HealthStatusResponse(PublicModel):
    title: StrictStr
    ok: StrictBool
    label: StrictStr


class HealthLineRadarRowResponse(PublicModel):
    key: StrictStr
    label: StrictStr
    status: StrictStr
    ok: StrictBool
    bytes: StrictInt
    share: StrictFloat
    active_users: StrictInt
    online: StrictInt | None = None
    profile: StrictStr
    note: StrictStr

    @field_validator('bytes', 'active_users')
    @classmethod
    def counts_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('health counters must be non-negative')
        return value

    @field_validator('share')
    @classmethod
    def share_must_be_bounded(cls, value):
        if value < 0 or value > 100:
            raise ValueError('health share must be between 0 and 100')
        return value


class HealthLineRadarResponse(PublicModel):
    window_hours: StrictInt
    total_bytes: StrictInt
    recommendation: StrictStr
    reason: StrictStr
    rows: list[HealthLineRadarRowResponse]

    @field_validator('window_hours')
    @classmethod
    def window_must_be_positive(cls, value):
        if value <= 0:
            raise ValueError('health window must be positive')
        return value

    @field_validator('total_bytes')
    @classmethod
    def total_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('health total must be non-negative')
        return value


class HealthCalibrationWindowResponse(PublicModel):
    window_hours: StrictInt
    suggested_multiplier: StrictFloat | None = None
    egress_multiplier: StrictFloat | None = None
    app_raw_bytes: StrictInt
    included_sample_count: StrictInt
    sample_count: StrictInt
    confidence: StrictStr

    @field_validator(
        'app_raw_bytes',
        'included_sample_count',
        'sample_count',
    )
    @classmethod
    def sample_counts_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('calibration counts must be non-negative')
        return value


class HealthCalibrationPolicyResponse(PublicModel):
    enabled: StrictBool
    mode: StrictStr
    min_confidence: StrictStr
    max_delta_percent: StrictFloat
    min_delta_percent: StrictFloat
    cooldown_hours: StrictFloat


class HealthCalibrationResponse(PublicModel):
    window_hours: StrictInt
    sample_count: StrictInt
    included_sample_count: StrictInt
    app_raw_bytes: StrictInt
    net_total_bytes: StrictInt
    net_tx_bytes: StrictInt
    current_multiplier: StrictFloat
    suggested_multiplier: StrictFloat | None = None
    egress_multiplier: StrictFloat | None = None
    delta_percent: StrictFloat | None = None
    confidence: StrictStr
    ifaces: list[StrictStr]
    last_ts: StrictStr
    method: StrictStr
    egress_sample_count: StrictInt
    windows: list[HealthCalibrationWindowResponse]
    policy: HealthCalibrationPolicyResponse

    @field_validator(
        'window_hours',
        'sample_count',
        'included_sample_count',
        'app_raw_bytes',
        'net_total_bytes',
        'net_tx_bytes',
        'egress_sample_count',
    )
    @classmethod
    def calibration_values_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('calibration values must be non-negative')
        return value


class HealthUpdateResponse(PublicModel):
    status: StrictStr
    reason: StrictStr
    ts: StrictStr
    pending: StrictBool
    version: StrictStr
    previous_version: StrictStr


class AdminHealthResponse(PublicModel):
    ts: StrictStr
    kpis: list[HealthStatusResponse]
    services: list[HealthStatusResponse]
    line_radar: HealthLineRadarResponse
    calibration: HealthCalibrationResponse
    update: HealthUpdateResponse
