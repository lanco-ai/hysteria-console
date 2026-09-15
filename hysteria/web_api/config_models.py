"""Explicit public models for authenticated template editing."""

from pydantic import StrictBool, StrictStr, field_validator, model_validator

from .models import PublicModel


class AdminTemplateResponse(PublicModel):
    config: dict[str, object]
    revision: StrictStr

    @field_validator('revision')
    @classmethod
    def revision_must_be_sha256(cls, value):
        if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
            raise ValueError('invalid template revision')
        return value


class AdminRulesResponse(PublicModel):
    rules: list[StrictStr]
    revision: StrictStr

    @field_validator('revision')
    @classmethod
    def revision_must_be_sha256(cls, value):
        if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
            raise ValueError('invalid template revision')
        return value


class TemplateMutationResponse(PublicModel):
    ok: StrictBool
    revision: StrictStr | None = None
    error: StrictStr | None = None
    code: StrictStr | None = None

    @model_validator(mode='after')
    def validate_outcome(self):
        if self.ok and (not self.revision or self.error is not None or self.code is not None):
            raise ValueError('invalid template mutation success')
        if not self.ok and self.revision is not None:
            raise ValueError('invalid template mutation failure')
        return self
