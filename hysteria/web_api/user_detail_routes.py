"""Authenticated administrator user-detail API route."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .services import LoginRequired
from .user_detail_models import AdminUserDetailResponse


def register_user_detail_routes(app, services, dispatch):
    router = APIRouter()

    @router.api_route('/api/v1/admin/user/{uid}', methods=['GET', 'HEAD'])
    async def admin_user_detail(request: Request, uid: str):
        try:
            payload = await dispatch(
                partial(services.read_admin_user_detail, uid=uid),
                request,
            )
        except LoginRequired:
            return JSONResponse(status_code=401, content={'error': 'login_required'})
        if isinstance(payload, JSONResponse):
            return payload
        if payload is None:
            return JSONResponse(status_code=404, content={'error': 'not_found'})
        model = AdminUserDetailResponse.model_validate(payload)
        return JSONResponse(model.model_dump())

    app.include_router(router)
