"""FastAPI transport for subscription and user-panel compatibility URLs."""

from functools import partial

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .services import LoginRequired, StateUnavailable, SubscriptionAccessDenied, UserAccessDenied


def _error(exc, *, json_body=False):
    if isinstance(exc, SubscriptionAccessDenied):
        if json_body:
            return JSONResponse(status_code=403, content={'error': exc.code})
        return Response(content=exc.message, status_code=403, media_type='text/plain')
    if isinstance(exc, (LoginRequired, UserAccessDenied)):
        headers = (
            {'Set-Cookie': exc.cookie} if isinstance(exc, UserAccessDenied) and exc.cookie else None
        )
        return JSONResponse(
            status_code=401 if isinstance(exc, LoginRequired) else 403,
            content={'error': 'login_required'}
            if isinstance(exc, LoginRequired)
            else {'error': exc.code},
            headers=headers,
        )
    if isinstance(exc, StateUnavailable):
        return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
    raise exc


def register_subscription_routes(app, services, dispatch):
    router = APIRouter()

    @router.api_route('/sub/{username}', methods=['GET', 'HEAD'])
    async def subscription(request: Request, username: str):
        try:
            payload = await dispatch(
                partial(
                    services.read_subscription_download,
                    username=username,
                    token=request.query_params.get('token', ''),
                    profile=request.query_params.get('profile', 'default'),
                ),
                request,
            )
        except (SubscriptionAccessDenied, StateUnavailable) as exc:
            return _error(exc)
        return Response(
            content=payload['body'],
            media_type='text/yaml',
            headers={
                'Content-Disposition': f"attachment; filename*=UTF-8''{payload['filename']}",
                'X-Subscription-Profile': payload['profile'],
                'X-Subscription-Generated-At': payload['generated_at'],
                'X-Subscription-Template-Mtime': payload['template_mtime'],
                'Profile-Update-Interval': '24',
                'Subscription-Userinfo': payload['userinfo'],
                'X-Usage-Total-Bytes': payload['usage_total'],
            },
        )

    @router.api_route('/panel/{username}/qr.svg', methods=['GET', 'HEAD'])
    async def panel_qr(request: Request, username: str):
        try:
            payload = await dispatch(
                partial(
                    services.read_panel_qr,
                    username=username,
                    token=request.query_params.get('token', ''),
                    profile=request.query_params.get('profile', 'default'),
                ),
                request,
            )
        except (SubscriptionAccessDenied, StateUnavailable) as exc:
            return _error(exc)
        return Response(
            content=payload['body'],
            media_type='image/svg+xml',
            headers={'Cache-Control': 'private, no-store'},
        )

    @router.api_route('/panel/{username}.json', methods=['GET', 'HEAD'])
    async def panel_json(request: Request, username: str):
        try:
            payload = await dispatch(
                partial(
                    services.read_panel_json,
                    username=username,
                    token=request.query_params.get('token', ''),
                ),
                request,
            )
        except (SubscriptionAccessDenied, StateUnavailable) as exc:
            return _error(exc, json_body=True)
        return JSONResponse(payload)

    @router.api_route('/panel/{username}', methods=['GET', 'HEAD'])
    async def panel_exchange(request: Request, username: str):
        try:
            payload = await dispatch(
                partial(
                    services.exchange_panel_token,
                    username=username,
                    token=request.query_params.get('token', ''),
                ),
                request,
            )
        except (SubscriptionAccessDenied, StateUnavailable) as exc:
            return _error(exc)
        return RedirectResponse(
            '/user/panel', status_code=303, headers={'Set-Cookie': payload['cookie']}
        )

    @router.api_route('/user/panel.json', methods=['GET', 'HEAD'])
    async def user_panel_json(request: Request):
        try:
            payload = await dispatch(services.read_user_panel, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _error(exc, json_body=True)
        return JSONResponse(payload)

    app.include_router(router)
