"""Explicit public response models for administrator overview operations."""

from typing import Literal

from pydantic import ConfigDict, StrictBool, StrictFloat, StrictInt, StrictStr

from .models import PublicModel

OverviewOperationAction = Literal[
    'cycle',
    'reset-usage',
    'refresh-usage',
    'reset-usage-all',
    'pause-user',
    'toggle-user',
]

OverviewValidationCode = Literal[
    'err:settlement_invalid',
    'err:cycle_length_invalid',
    'invalid_desired',
]


class OverviewOperationSuccessResponse(PublicModel):
    ok: Literal[True]
    action: OverviewOperationAction
    user: StrictStr
    day: StrictInt | None
    disabled_until: StrictStr


class CredentialOperationSuccessResponse(PublicModel):
    ok: Literal[True]
    action: Literal['rotate-token', 'delete']
    user: StrictStr
    code: Literal[
        'rotated',
        'err:rotated_retry',
        'err:rotated_pending',
        'err:rotated_static_pending',
        'deleted',
        'err:deleted_retry',
    ]


class OverviewOperationValidationResponse(PublicModel):
    ok: Literal[False]
    error: Literal['validation_error']
    code: OverviewValidationCode


class OverviewOperationConflictResponse(PublicModel):
    ok: Literal[False]
    error: Literal['revision_conflict']


class OverviewOperationNotFoundResponse(PublicModel):
    ok: Literal[False]
    error: Literal['user_not_found']


class AdminReloadStatusResponse(PublicModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    pending: StrictBool
    xray: StrictBool
    tuic: StrictBool


class HealthOperationResponse(PublicModel):
    """Secret-free result for health-page maintenance actions."""

    ok: StrictBool
    status: StrictStr
    reason: StrictStr = ''
    ts: StrictStr = ''
    pending: StrictBool = False
    current: StrictStr = ''
    latest: StrictStr = ''
    update_available: StrictBool = False


class AlertTelegramStatus(PublicModel):
    configured: StrictBool
    chat_id: StrictStr


class AlertWebhookStatus(PublicModel):
    configured: StrictBool
    host: StrictStr
    signed: StrictBool


class AlertSettingsResponse(PublicModel):
    """Alert channel status for 设置 · 告警通知; never carries a token, URL or secret."""

    telegram: AlertTelegramStatus
    webhook: AlertWebhookStatus
    anomaly_z_threshold: StrictFloat
    anomaly_min_gib: StrictFloat
    revision: StrictStr


class AlertSettingsMutationResponse(PublicModel):
    ok: StrictBool
    error: StrictStr | None = None
    code: StrictStr | None = None
    telegram: AlertTelegramStatus | None = None
    webhook: AlertWebhookStatus | None = None
    anomaly_z_threshold: StrictFloat | None = None
    anomaly_min_gib: StrictFloat | None = None
    revision: StrictStr | None = None
