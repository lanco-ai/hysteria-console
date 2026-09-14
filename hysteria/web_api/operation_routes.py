"""Focused administrator overview-operation route group."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from overview_mutation_result import OverviewMutationResult

from .operation_models import (
    AdminReloadStatusResponse,
    OverviewOperationConflictResponse,
    OverviewOperationNotFoundResponse,
    OverviewOperationSuccessResponse,
    OverviewOperationValidationResponse,
)
from .services import LoginRequired, StateUnavailable

_PER_USER_ACTIONS = frozenset(('reset-usage', 'refresh-usage', 'pause-user', 'toggle-user'))
_ACTIONS = frozenset(('cycle', *_PER_USER_ACTIONS, 'reset-usage-all'))
_VALIDATION_CODES = {
    'cycle': frozenset(('err:settlement_invalid', 'err:cycle_length_invalid')),
    'toggle-user': frozenset(('invalid_desired',)),
}


def _empty_success_metadata(result):
    return (
        isinstance(result.code, str)
        and result.code == ''
        and result.day is None
        and isinstance(result.disabled_until, str)
        and result.disabled_until == ''
    )


def _overview_operation_response(result: OverviewMutationResult, *, action: str):
    if action not in _ACTIONS or not isinstance(result, OverviewMutationResult):
        raise ValueError('invalid overview operation result type')

    if result.outcome == 'success':
        if not isinstance(result.code, str) or result.code != '':
            raise ValueError('invalid overview operation success code')
        if action in _PER_USER_ACTIONS:
            if not isinstance(result.username, str) or not result.username:
                raise ValueError('invalid overview operation success user')
        elif not isinstance(result.username, str) or result.username != '':
            raise ValueError('invalid overview operation success user')

        if action == 'cycle':
            if (
                isinstance(result.day, bool)
                or not isinstance(result.day, int)
                or result.day < 1
                or result.day > 28
                or not isinstance(result.disabled_until, str)
                or result.disabled_until != ''
            ):
                raise ValueError('invalid cycle operation success metadata')
        elif result.day is not None:
            raise ValueError('invalid overview operation success day')

        if action == 'pause-user':
            if not isinstance(result.disabled_until, str) or not result.disabled_until:
                raise ValueError('invalid pause operation success metadata')
        elif not isinstance(result.disabled_until, str) or result.disabled_until != '':
            raise ValueError('invalid overview operation success until')

        model = OverviewOperationSuccessResponse(
            ok=True,
            action=action,
            user=result.username,
            day=result.day,
            disabled_until=result.disabled_until,
        )
        return JSONResponse(model.model_dump())

    if result.outcome == 'invalid':
        allowed_codes = _VALIDATION_CODES.get(action, frozenset())
        if (
            not isinstance(result.username, str)
            or result.username != ''
            or not isinstance(result.code, str)
            or result.code not in allowed_codes
            or result.day is not None
            or not isinstance(result.disabled_until, str)
            or result.disabled_until != ''
        ):
            raise ValueError('invalid overview operation validation result')
        model = OverviewOperationValidationResponse(
            ok=False,
            error='validation_error',
            code=result.code,
        )
        return JSONResponse(status_code=422, content=model.model_dump())

    if result.outcome in ('conflict', 'not_found'):
        if (
            action not in _PER_USER_ACTIONS
            or not isinstance(result.username, str)
            or (result.outcome == 'conflict' and not result.username)
            or not _empty_success_metadata(result)
        ):
            raise ValueError('invalid rejected overview operation result')
        if result.outcome == 'conflict':
            model = OverviewOperationConflictResponse(ok=False, error='revision_conflict')
            return JSONResponse(status_code=409, content=model.model_dump())
        model = OverviewOperationNotFoundResponse(ok=False, error='user_not_found')
        return JSONResponse(status_code=404, content=model.model_dump())

    raise ValueError('invalid overview operation result')


def _reload_status_response(payload):
    model = AdminReloadStatusResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def register_operation_routes(app, services, dispatch_form_write, dispatch):
    router = APIRouter()

    async def dispatch_operation(request, *, action):
        try:
            return await dispatch_form_write(
                services.submit_overview_operation,
                request,
                partial(_overview_operation_response, action=action),
                action=action,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    @router.post('/api/v1/admin/operations/cycle')
    async def configure_cycle(request: Request):
        return await dispatch_operation(request, action='cycle')

    @router.post('/api/v1/admin/operations/reset-usage')
    async def reset_usage(request: Request):
        return await dispatch_operation(request, action='reset-usage')

    @router.post('/api/v1/admin/operations/refresh-usage')
    async def refresh_usage(request: Request):
        return await dispatch_operation(request, action='refresh-usage')

    @router.post('/api/v1/admin/operations/reset-usage-all')
    async def reset_usage_all(request: Request):
        return await dispatch_operation(request, action='reset-usage-all')

    @router.post('/api/v1/admin/operations/pause-user')
    async def pause_user(request: Request):
        return await dispatch_operation(request, action='pause-user')

    @router.post('/api/v1/admin/operations/toggle-user')
    async def toggle_user(request: Request):
        return await dispatch_operation(request, action='toggle-user')

    @router.api_route('/api/v1/admin/reload-status', methods=['GET', 'HEAD'])
    async def reload_status(request: Request):
        try:
            payload = await dispatch(services.read_admin_reload_status, request)
        except (LoginRequired, StateUnavailable) as exc:
            if isinstance(exc, LoginRequired):
                return JSONResponse(status_code=401, content={'error': 'login_required'})
            return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
        if isinstance(payload, JSONResponse):
            return payload
        return _reload_status_response(payload)

    app.include_router(router)
