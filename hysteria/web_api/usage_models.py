"""Explicit public response models for the administrator usage page."""

from pydantic import StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .models import PublicModel


def _non_negative(values):
    if any(value < 0 for value in values):
        raise ValueError('usage values must be non-negative')
    return values


class UsageStatsResponse(PublicModel):
    current_hour_bytes: StrictInt
    today_bytes: StrictInt
    yesterday_bytes: StrictInt
    last_7d_bytes: StrictInt
    cycle_bytes: StrictInt
    cycle_day: StrictInt
    cycle_total_days: StrictInt
    online: StrictInt

    @field_validator(
        'current_hour_bytes',
        'today_bytes',
        'yesterday_bytes',
        'last_7d_bytes',
        'cycle_bytes',
        'cycle_day',
        'cycle_total_days',
        'online',
    )
    @classmethod
    def values_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('usage values must be non-negative')
        return value


class UsageHourlyPointResponse(PublicModel):
    hour: StrictStr
    bytes: StrictInt

    @field_validator('bytes')
    @classmethod
    def bytes_must_be_non_negative(cls, value):
        if value < 0:
            raise ValueError('usage values must be non-negative')
        return value


class UsageHeatmapRowResponse(PublicModel):
    date: StrictStr
    hours: list[StrictInt]

    @field_validator('hours')
    @classmethod
    def hours_must_be_a_day_of_values(cls, value):
        if len(value) != 24:
            raise ValueError('heatmap rows must contain 24 hours')
        return _non_negative(value)


class UsageTopUserResponse(PublicModel):
    uid: StrictStr
    last_24h_bytes: StrictInt
    spark: list[StrictInt]

    @field_validator('last_24h_bytes', 'spark')
    @classmethod
    def usage_must_be_non_negative(cls, value):
        if isinstance(value, list):
            return _non_negative(value)
        if value < 0:
            raise ValueError('usage values must be non-negative')
        return value


class AdminUsageSummaryResponse(PublicModel):
    ts: StrictStr
    stats: UsageStatsResponse


class AdminUsageResponse(AdminUsageSummaryResponse):
    hourly_totals: list[UsageHourlyPointResponse]
    heatmap: list[UsageHeatmapRowResponse]
    top_n: list[UsageTopUserResponse]


class UsageHistoryUserResponse(PublicModel):
    uid: StrictStr
    values: list[StrictInt]

    @field_validator('values')
    @classmethod
    def values_must_be_non_negative(cls, value):
        return _non_negative(value)


class AdminUsageHistoryResponse(PublicModel):
    ts: StrictStr
    retention_days: StrictInt
    dates: list[StrictStr]
    users: list[UsageHistoryUserResponse]
    totals: list[StrictInt]

    @field_validator('retention_days')
    @classmethod
    def retention_must_be_positive(cls, value):
        if value <= 0:
            raise ValueError('retention_days must be positive')
        return value

    @field_validator('totals')
    @classmethod
    def totals_must_be_non_negative(cls, value):
        return _non_negative(value)

    @model_validator(mode='after')
    def arrays_must_match_retention(self):
        if len(self.dates) != self.retention_days or len(self.totals) != self.retention_days:
            raise ValueError('history arrays must match retention_days')
        for row in self.users:
            if len(row.values) != self.retention_days:
                raise ValueError('history user arrays must match retention_days')
        return self


class HealthStatusResponse(PublicModel):
    title: StrictStr
    ok: StrictBool
    label: StrictStr


class AdminHealthResponse(PublicModel):
    ts: StrictStr
    kpis: list[HealthStatusResponse]
    services: list[HealthStatusResponse]
