"""Explicit public models for the administrator overview bootstrap."""

import math

from pydantic import StrictBool, StrictFloat, StrictInt, StrictStr, field_validator

from .models import PublicModel


class OverviewCycleResponse(PublicModel):
    key: StrictStr
    total_used: StrictInt
    range: StrictStr
    settlement_day: StrictInt
    length_days: StrictInt
    length_min: StrictInt
    length_max: StrictInt


class OverviewBootstrapUserResponse(PublicModel):
    user: StrictStr
    tx: StrictInt
    rx: StrictInt
    used: StrictInt
    total: StrictInt
    percent: StrictFloat
    online: StrictInt
    revision: StrictStr
    disabled: StrictBool
    max_devices: StrictInt
    base_quota_gb: StrictInt
    quota_extra_gb: StrictInt
    metered: StrictBool
    tuic_enabled: StrictBool
    expires_at: StrictStr
    expired: StrictBool
    expiry_label: StrictStr
    note: StrictStr
    landing_isp: StrictStr
    landing_region: StrictStr
    landing_note: StrictStr
    landing_ip: StrictStr
    panel_url: StrictStr
    subscription_url: StrictStr
    spark: list[tuple[StrictStr, StrictInt]] | None

    @field_validator('percent')
    @classmethod
    def percent_must_be_finite(cls, value):
        if not math.isfinite(value):
            raise ValueError('percent must be finite')
        return value


class OverviewLandingChoiceResponse(PublicModel):
    id: StrictStr
    name: StrictStr


class AdminOverviewPageResponse(PublicModel):
    cycle: OverviewCycleResponse
    users: list[OverviewBootstrapUserResponse]
    landing_options: list[OverviewLandingChoiceResponse]
