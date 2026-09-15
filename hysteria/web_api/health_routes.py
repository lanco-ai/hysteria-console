"""Health-page maintenance actions over the shared form boundary."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .operation_models import HealthOperationResponse
from .services import LoginRequired

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


def register_health_routes(app, services, dispatch_form_write):
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

    app.include_router(router)
