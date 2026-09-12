"""Explicit public response models for the read-only panel API."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


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
