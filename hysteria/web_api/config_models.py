"""Explicit public models for authenticated template editing."""

from typing import Literal

from pydantic import ConfigDict, StrictBool, StrictStr, field_validator, model_validator

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


class AdminRulePackResponse(PublicModel):
    """Safe rule-pack metadata; rule contents stay server-side."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    key: StrictStr
    label: StrictStr
    description: StrictStr


class AdminRulesResponse(PublicModel):
    rules: list[StrictStr]
    revision: StrictStr
    packs: list['AdminRulePackResponse'] | None = None
    users: list[StrictStr] | None = None

    @field_validator('revision')
    @classmethod
    def revision_must_be_sha256(cls, value):
        if len(value) != 64 or any(char not in '0123456789abcdef' for char in value):
            raise ValueError('invalid template revision')
        return value


class RulesMutationResponse(PublicModel):
    """Explicit result for add/delete/rule-pack mutations."""

    model_config = ConfigDict(extra='forbid', frozen=True)

    ok: StrictBool
    action: Literal['add', 'delete', 'pack']
    revision: StrictStr | None = None
    user: StrictStr | None = None
    error: Literal['validation_error', 'revision_conflict', 'user_not_found'] | None = None
    code: StrictStr | None = None

    @field_validator('revision')
    @classmethod
    def optional_revision_must_be_sha256(cls, value):
        if value is not None and (
            len(value) != 64 or any(char not in '0123456789abcdef' for char in value)
        ):
            raise ValueError('invalid template revision')
        return value

    @model_validator(mode='after')
    def validate_outcome(self):
        if self.ok:
            if self.error is not None or self.code is not None:
                raise ValueError('invalid rules mutation success')
            if self.action == 'pack' and self.user is not None and not self.user:
                raise ValueError('invalid rules mutation user')
        elif self.error is None:
            raise ValueError('invalid rules mutation failure')
        elif self.revision is not None or self.user is not None:
            raise ValueError('invalid rules mutation failure metadata')
        return self


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
