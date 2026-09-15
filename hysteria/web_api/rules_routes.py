"""FastAPI routes for structured routing-rule mutations."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .config_models import RulesMutationResponse
from .services import LoginRequired


def _rules_mutation_response(payload):
    model = RulesMutationResponse.model_validate(payload)
    if model.ok:
        return JSONResponse(model.model_dump(exclude_none=True))
    status = (
        409
        if model.error == 'revision_conflict'
        else 404
        if model.error == 'user_not_found'
        else 422
    )
    return JSONResponse(status_code=status, content=model.model_dump(exclude_none=True))


def register_rules_routes(app, services, dispatch_form_write):
    router = APIRouter()

    async def dispatch_rules_operation(request, *, action):
        try:
            return await dispatch_form_write(
                services.submit_rules_operation,
                request,
                _rules_mutation_response,
                action=action,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})

    @router.post('/api/v1/admin/rules/add')
    async def add_rule(request: Request):
        return await dispatch_rules_operation(request, action='add')

    @router.post('/api/v1/admin/rules/delete')
    async def delete_rule(request: Request):
        return await dispatch_rules_operation(request, action='delete')

    @router.post('/api/v1/admin/rules/pack')
    async def apply_rule_pack(request: Request):
        return await dispatch_rules_operation(request, action='pack')

    app.include_router(router)
