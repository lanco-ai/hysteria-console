"""Explicit public response models for administrator overview operations."""

from typing import Literal

from pydantic import ConfigDict, StrictBool, StrictInt, StrictStr

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
