"""Explicit public response models for the read-only panel API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictInt, StrictStr, model_validator


class PublicModel(BaseModel):
    model_config = ConfigDict(extra='ignore', frozen=True)


class AdminSessionResponse(PublicModel):
    role: Literal['admin']


class UserSessionResponse(PublicModel):
    role: Literal['user']
    username: str


class LoginSuccessResponse(PublicModel):
    ok: Literal[True]
    redirect_to: Literal[
        '/admin?msg=login+success',
        '/user/panel',
        '/user/change-password',
    ]


class LoginFailureResponse(PublicModel):
    ok: Literal[False]
    message: str


class LogoutResponse(PublicModel):
    ok: Literal[True]
    redirect_to: Literal['/login']


class PasswordChangeSuccessResponse(PublicModel):
    ok: Literal[True]
    redirect_to: Literal[
        '/admin/settings?msg=password+changed',
        '/user/panel',
    ]


class AdminPasswordChangeValidationResponse(PublicModel):
    ok: Literal[False]
    code: Literal[
        'password_wrong',
        'password_short',
        'password_long',
        'password_mismatch',
    ]


class UserPasswordChangeValidationResponse(PublicModel):
    ok: Literal[False]
    code: Literal[
        'current password wrong',
        'new password short',
        'new password long',
        'new password mismatch',
        'new password same',
    ]


class PasswordChangeAccessErrorResponse(PublicModel):
    error: Literal['login_required', 'forbidden', 'disabled', 'expired']


class PasswordPageResponse(PublicModel):
    username: StrictStr
    password_min_length: StrictInt
    password_max_length: StrictInt

    @model_validator(mode='after')
    def validate_password_lengths(self):
        if self.password_min_length <= 0 or self.password_max_length < self.password_min_length:
            raise ValueError('invalid password length bounds')
        return self


class OverviewUserResponse(PublicModel):
    user: str
    tx: int
    rx: int
    used: int
    total: int
    percent: float
    online: int
    revision: str
    disabled: bool


class AdminOverviewResponse(PublicModel):
    ts: str
    total_used: int
    users: list[OverviewUserResponse]


class AdminLogRowResponse(PublicModel):
    time: str
    actor: str
    ip: str
    action: str
    target: str
    month: str
    detail: str


class AdminLogsResponse(PublicModel):
    limit: int
    rows: list[AdminLogRowResponse]
