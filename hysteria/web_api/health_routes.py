"""Health-page maintenance actions over the shared form boundary."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .operation_models import (
    AlertSettingsMutationResponse,
    AlertSettingsResponse,
    HealthOperationResponse,
)
from .services import LoginRequired, StateUnavailable

_ERROR_STATUS = {
    'update_busy': 409,
    'update_check_failed': 502,
    'update_schedule_failed': 503,
    'alert_no_channels': 422,
}


def _health_operation_response(payload, *, action):
    if not isinstance(payload, dict):
        raise ValueError('invalid health operation result')
    data = dict(payload)
    data.setdefault('status', action)
    model = HealthOperationResponse.model_validate(data)
    if model.ok:
        return JSONResponse(model.model_dump())
    status = _ERROR_STATUS.get(model.reason, 422)
    return JSONResponse(status_code=status, content=model.model_dump())


def _alert_settings_response(payload):
    model = AlertSettingsMutationResponse.model_validate(payload)
    if not model.ok:
        status = 409 if model.error == 'revision_conflict' else 422
        return JSONResponse(status_code=status, content=model.model_dump(exclude_none=True))
    return JSONResponse(model.model_dump(exclude_none=True))


def register_health_routes(app, services, dispatch_form_write, dispatch=None):
    router = APIRouter()

    async def dispatch_health(request: Request, *, action):
        try:
            return await dispatch_form_write(
                services.submit_health_operation,
                request,
                partial(_health_operation_response, action=action),
                action=action,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    @router.post('/api/v1/admin/health/update-check')
    async def check_update(request: Request):
        return await dispatch_health(request, action='update-check')

    @router.post('/api/v1/admin/health/update-apply')
    async def apply_update(request: Request):
        return await dispatch_health(request, action='update-apply')

    @router.post('/api/v1/admin/health/test-alert')
    async def test_alert(request: Request):
        return await dispatch_health(request, action='test-alert')

    @router.post('/api/v1/admin/health/multiplier-apply')
    async def apply_multiplier(request: Request):
        return await dispatch_health(request, action='multiplier-apply')

    @router.post('/api/v1/admin/health/multiplier-auto')
    async def save_multiplier_policy(request: Request):
        return await dispatch_health(request, action='multiplier-auto')

    if dispatch is not None:

        @router.api_route('/api/v1/admin/alerts', methods=['GET', 'HEAD'])
        async def alert_settings(request: Request):
            try:
                payload = await dispatch(services.read_admin_alerts, request)
            except LoginRequired:
                return JSONResponse(status_code=401, content={'error': 'login_required'})
            except StateUnavailable:
                return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
            if isinstance(payload, JSONResponse):
                return payload
            return JSONResponse(
                AlertSettingsResponse.model_validate(payload).model_dump(),
                headers={'Cache-Control': 'no-store'},
            )

    @router.post('/api/v1/admin/alerts/save')
    async def save_alert_settings(request: Request):
        try:
            return await dispatch_form_write(
                services.submit_alert_settings, request, _alert_settings_response
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    app.include_router(router)
