"""Focused administrator account-mutation route group."""

from functools import partial
from typing import Literal

from account_mutation_service import AccountMutationResult
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .account_models import (
    AccountMutationConflictResponse,
    AccountMutationNotFoundResponse,
    AccountMutationSuccessResponse,
    AccountMutationValidationResponse,
)
from .services import LoginRequired


def _account_mutation_response(
    result: AccountMutationResult,
    *,
    action: Literal['create', 'update'],
):
    if not isinstance(result, AccountMutationResult):
        raise ValueError('invalid account mutation result type')

    expected_success = 'created' if action == 'create' else 'updated'
    if result.outcome in ('created', 'updated'):
        if (
            result.outcome != expected_success
            or not result.username
            or result.code
            or result.field_id
            or result.draft is not None
        ):
            raise ValueError('invalid account mutation success result')
        model = AccountMutationSuccessResponse(
            ok=True,
            outcome=result.outcome,
            user=result.username,
        )
        return JSONResponse(model.model_dump())

    if result.outcome == 'invalid':
        model = AccountMutationValidationResponse(
            ok=False,
            error='validation_error',
            code=result.code,
            field_id=result.field_id,
        )
        return JSONResponse(status_code=422, content=model.model_dump())

    if result.outcome == 'conflict':
        model = AccountMutationConflictResponse(ok=False, error='revision_conflict')
        return JSONResponse(status_code=409, content=model.model_dump())

    if result.outcome == 'not_found':
        model = AccountMutationNotFoundResponse(ok=False, error='user_not_found')
        return JSONResponse(status_code=404, content=model.model_dump())

    raise ValueError('invalid account mutation result')


def register_account_routes(app, services, dispatch_form_write):
    router = APIRouter()

    async def dispatch_account_mutation(request, *, action):
        try:
            return await dispatch_form_write(
                services.submit_account_mutation,
                request,
                partial(_account_mutation_response, action=action),
                action=action,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    @router.post('/api/v1/admin/users/create')
    async def create_account(request: Request):
        return await dispatch_account_mutation(request, action='create')

    @router.post('/api/v1/admin/users/update')
    async def update_account(request: Request):
        return await dispatch_account_mutation(request, action='update')

    app.include_router(router)
