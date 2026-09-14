"""Explicit public response models for administrator account mutations."""

from typing import Literal

from pydantic import StrictStr

from .models import PublicModel

AccountValidationCode = Literal[
    'user empty',
    'err:username_invalid',
    'err:max_devices_invalid',
    'err:quota_invalid',
    'err:quota_extra_invalid',
    'err:expiry_invalid',
    'err:note_too_long',
    'err:landing_invalid',
    'err:landing_too_long',
    'err:landing_ip_invalid',
    'err:panel_password_short',
    'err:panel_password_long',
    'err:proxy_password_long',
    'user_exists_use_reset_token',
    '家宽出口已不可用，请重新选择',
]

AccountValidationFieldId = Literal[
    '',
    'create-user',
    'create-quota-gb',
    'create-quota-extra-gb',
    'create-expires-at',
    'create-note',
    'create-panel-password',
    'create-proxy-password',
    'create-landing-initial-egress',
]


class AccountMutationSuccessResponse(PublicModel):
    ok: Literal[True]
    outcome: Literal['created', 'updated']
    user: StrictStr


class AccountMutationValidationResponse(PublicModel):
    ok: Literal[False]
    error: Literal['validation_error']
    code: AccountValidationCode
    field_id: AccountValidationFieldId


class AccountMutationConflictResponse(PublicModel):
    ok: Literal[False]
    error: Literal['revision_conflict']


class AccountMutationNotFoundResponse(PublicModel):
    ok: Literal[False]
    error: Literal['user_not_found']
