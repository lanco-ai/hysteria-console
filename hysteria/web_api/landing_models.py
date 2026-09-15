"""Explicit public models for administrator residential-egress state."""

from typing import Literal

from pydantic import ConfigDict, StrictBool, StrictInt, StrictStr, field_validator, model_validator

from .models import PublicModel


class LandingHealthResponse(PublicModel):
    status: StrictStr | None = None
    observed_ip: StrictStr | None = None
    checked_at: StrictStr | None = None
    error_code: StrictStr | None = None


class LandingNodeResponse(PublicModel):
    id: StrictStr
    name: StrictStr
    exit_ip: StrictStr
    isp: StrictStr
    region: StrictStr
    enabled: StrictBool
    health: LandingHealthResponse | None = None


class LandingAccessResponse(PublicModel):
    user: StrictStr
    revision: StrictStr
    allowed_ids: list[StrictStr]


class AdminLandingResponse(PublicModel):
    ts: StrictStr
    revision: StrictStr
    nodes: list[LandingNodeResponse]
    users: list[LandingAccessResponse]


class LandingMutationResponse(PublicModel):
    """Secret-free result for admin and user egress writes."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    ok: StrictBool
    action: Literal['save', 'delete', 'check', 'access', 'select']
    revision: StrictStr | None = None
    error: (
        Literal[
            'forbidden',
            'not_found',
            'validation_error',
            'revision_conflict',
            'rate_limited',
        ]
        | None
    ) = None
    code: StrictStr | None = None
    retry_after: StrictInt | None = None

    @field_validator('revision')
    @classmethod
    def optional_revision_must_be_sha256(cls, value):
        if value is not None and (
            len(value) != 64 or any(char not in '0123456789abcdef' for char in value)
        ):
            raise ValueError('invalid registry revision')
        return value

    @field_validator('retry_after')
    @classmethod
    def retry_after_must_be_positive(cls, value):
        if value is not None and value <= 0:
            raise ValueError('invalid retry-after')
        return value

    @model_validator(mode='after')
    def validate_outcome(self):
        if self.ok:
            if self.error is not None or self.code is not None or self.retry_after is not None:
                raise ValueError('invalid landing mutation success')
        elif self.error is None:
            raise ValueError('invalid landing mutation failure')
        elif self.revision is not None:
            raise ValueError('invalid landing mutation failure revision')
        return self
