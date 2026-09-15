"""Small compatibility routes retained while the React panel becomes canonical."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .services import LoginRequired, StateUnavailable, UserAccessDenied


def register_compatibility_routes(app, services, dispatch):
    """Register read-only URLs that existing bookmarks and links still use."""
    router = APIRouter()

    @router.api_route('/admin/daily', methods=['GET', 'HEAD'])
    async def legacy_daily():
        return RedirectResponse('/admin/usage', status_code=301)

    @router.api_route('/admin/usage-history', methods=['GET', 'HEAD'])
    async def legacy_usage_history():
        # The React usage page owns the same retention table behind this
        # fragment; keep old bookmarks useful without rendering a second page.
        return RedirectResponse('/admin/usage#usage-history', status_code=301)

    @router.api_route('/admin/usage.csv', methods=['GET', 'HEAD'])
    async def usage_csv(request: Request):
        window = request.query_params.get('window', 'cycle').strip().lower()
        try:
            payload = await dispatch(
                partial(services.read_admin_usage_csv, window=window),
                request,
            )
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            if isinstance(exc, LoginRequired):
                return JSONResponse(status_code=401, content={'error': 'login_required'})
            if isinstance(exc, UserAccessDenied):
                headers = {'Set-Cookie': exc.cookie} if exc.cookie is not None else None
                return JSONResponse(status_code=403, content={'error': exc.code}, headers=headers)
            return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
        except ValueError:
            return JSONResponse(status_code=400, content={'error': 'invalid_window'})
        if isinstance(payload, JSONResponse):
            return payload
        if not isinstance(payload, dict) or not isinstance(payload.get('body'), str):
            raise ValueError('invalid usage csv payload')
        filename = payload.get('filename')
        if not isinstance(filename, str) or not filename:
            raise ValueError('invalid usage csv filename')
        return Response(
            content=payload['body'],
            media_type='text/csv',
            headers={'Content-Disposition': f'attachment; filename="{filename}"'},
        )

    app.include_router(router)
