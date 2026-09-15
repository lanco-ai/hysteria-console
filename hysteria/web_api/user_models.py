"""Explicit public models for the authenticated user panel."""

import math

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from .models import PublicModel


class UserSubscriptionProfileResponse(PublicModel):
    key: StrictStr
    label: StrictStr
    description: StrictStr
    url: StrictStr
    qr_path: StrictStr


class UserLandingNodeResponse(PublicModel):
    id: StrictStr
    name: StrictStr
    exit_ip: StrictStr
    isp: StrictStr
    region: StrictStr
    enabled: StrictBool
    selected: StrictBool
    health_status: StrictStr


class UserPanelResponse(PublicModel):
    ts: StrictStr
    username: StrictStr
    revision: StrictStr
    used_bytes: StrictInt
    total_bytes: StrictInt
    remain_bytes: StrictInt
    tx_bytes: StrictInt
    rx_bytes: StrictInt
    online: StrictInt
    max_devices: StrictInt
    percent: StrictFloat
    cycle_reset_date: StrictStr
    cycle_days_left: StrictInt
    cycle_length_days: StrictInt
    disabled: StrictBool
    expired: StrictBool
    expiry_label: StrictStr
    can_change_password: StrictBool
    can_select_egress: StrictBool
    subscription_profiles: list[UserSubscriptionProfileResponse]
    landing_nodes: list[UserLandingNodeResponse]

    @field_validator('used_bytes', 'total_bytes', 'tx_bytes', 'rx_bytes', 'online', 'max_devices', 'cycle_days_left', 'cycle_length_days')
    @classmethod
    def counters_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('user panel counters must be non-negative')
        return value

    @field_validator('remain_bytes')
    @classmethod
    def remain_must_be_non_negative_or_unlimited(cls, value):
        if value < -1:
            raise ValueError('user panel remaining bytes must be non-negative or -1')
        return value

    @field_validator('percent')
    @classmethod
    def percent_must_be_finite(cls, value):
        if not math.isfinite(value) or value < 0:
            raise ValueError('user panel percent must be finite')
        return value
