"""FastAPI routes for administrator and user residential-egress writes."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .landing_models import LandingMutationResponse
from .services import LoginRequired


def _landing_mutation_response(payload):
    model = LandingMutationResponse.model_validate(payload)
    if model.ok:
        return JSONResponse(model.model_dump(exclude_none=True))
    status = {
        'revision_conflict': 409,
        'not_found': 404,
        'forbidden': 403,
        'rate_limited': 429,
    }.get(model.error, 422)
    headers = {'Retry-After': str(model.retry_after)} if model.retry_after else None
    return JSONResponse(
        status_code=status,
        content=model.model_dump(exclude_none=True),
        headers=headers,
    )


def register_landing_routes(app, services, dispatch_form_write):
    router = APIRouter()

    async def dispatch_landing_operation(request, *, action):
        try:
            return await dispatch_form_write(
                services.submit_landing_operation,
                request,
                _landing_mutation_response,
                action=action,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    @router.post('/api/v1/admin/landing-egresses/save')
    async def save_landing_egress(request: Request):
        return await dispatch_landing_operation(request, action='save')

    @router.post('/api/v1/admin/landing-egresses/delete')
    async def delete_landing_egress(request: Request):
        return await dispatch_landing_operation(request, action='delete')

    @router.post('/api/v1/admin/landing-egresses/check')
    async def check_landing_egress(request: Request):
        return await dispatch_landing_operation(request, action='check')

    @router.post('/api/v1/admin/landing-egresses/access')
    async def update_landing_access(request: Request):
        return await dispatch_landing_operation(request, action='access')

    @router.post('/api/v1/user/landing-egress/select')
    async def select_landing_egress(request: Request):
        return await dispatch_landing_operation(request, action='select')

    app.include_router(router)
