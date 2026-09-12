"""Side-effect-free FastAPI application factory."""

import threading
from functools import partial

import anyio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException

from .models import (
    AdminLogsResponse,
    AdminOverviewResponse,
    AdminSessionResponse,
    UserSessionResponse,
)
from .services import LoginRequired, StateUnavailable, UserAccessDenied

_SECURITY_HEADERS = {
    'Cache-Control': 'no-store',
    'X-Content-Type-Options': 'nosniff',
    'Referrer-Policy': 'no-referrer',
    'X-Frame-Options': 'DENY',
    'Cross-Origin-Opener-Policy': 'same-origin',
}


class _SecurityAndErrorBoundary:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return

        response_started = False

        async def hardened_send(message):
            nonlocal response_started
            if message['type'] == 'http.response.start':
                response_started = True
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
            elif message['type'] == 'http.response.body' and scope['method'] == 'HEAD':
                message = {**message, 'body': b''}
            await send(message)

        try:
            await self.app(scope, receive, hardened_send)
        except Exception:
            if response_started:
                raise
            response = JSONResponse(
                status_code=500,
                content={'error': 'internal_error'},
            )
            await response(scope, receive, hardened_send)


def _request_headers(request):
    return dict(request.headers.items())


def _session_response(payload):
    if isinstance(payload, dict) and payload.get('role') == 'admin':
        model = AdminSessionResponse.model_validate(payload)
    else:
        model = UserSessionResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _overview_response(payload):
    model = AdminOverviewResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _logs_response(payload):
    model = AdminLogsResponse.model_validate(payload)
    return JSONResponse(model.model_dump())


def _read_error_response(exc):
    if isinstance(exc, LoginRequired):
        return JSONResponse(status_code=401, content={'error': 'login_required'})
    if isinstance(exc, UserAccessDenied):
        return JSONResponse(status_code=403, content={'error': exc.code})
    if isinstance(exc, StateUnavailable):
        return JSONResponse(status_code=503, content={'error': 'state_unavailable'})
    raise exc


def create_app(services, *, max_requests=32):
    if isinstance(max_requests, bool) or not isinstance(max_requests, int) or max_requests <= 0:
        raise ValueError('max_requests must be a positive integer')

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(_SecurityAndErrorBoundary)
    capacity = threading.BoundedSemaphore(max_requests)

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc):
        if exc.status_code == 404:
            error = 'not_found'
        elif exc.status_code == 405:
            error = 'method_not_allowed'
        else:
            error = 'request_error'
        return JSONResponse(status_code=exc.status_code, content={'error': error})

    async def dispatch(function, request):
        if not capacity.acquire(blocking=False):
            return JSONResponse(
                status_code=503,
                content={'error': 'server_busy'},
                headers={'Retry-After': '1'},
            )
        released = False

        def release_capacity():
            nonlocal released
            if not released:
                released = True
                capacity.release()

        try:
            call = partial(
                function,
                headers=_request_headers(request),
                path=request.scope['path'],
            )
            outcome = {}
            complete = anyio.Event()

            async def run_and_release():
                try:
                    outcome['payload'] = await anyio.to_thread.run_sync(
                        call,
                        abandon_on_cancel=False,
                    )
                except Exception as exc:
                    outcome['error'] = exc
                finally:
                    release_capacity()
                    complete.set()

            async with anyio.create_task_group() as workers:
                workers.start_soon(run_and_release)
                await complete.wait()
            if 'error' in outcome:
                raise outcome['error']
            return outcome['payload']
        finally:
            release_capacity()

    @app.api_route('/api/v1/session', methods=['GET', 'HEAD'])
    async def session(request: Request):
        try:
            payload = await dispatch(services.read_session, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _session_response(payload)

    @app.api_route('/api/v1/admin/overview', methods=['GET', 'HEAD'])
    async def admin_overview(request: Request):
        try:
            payload = await dispatch(services.read_admin_overview, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _overview_response(payload)

    @app.api_route('/api/v1/admin/logs', methods=['GET', 'HEAD'])
    async def admin_logs(request: Request):
        try:
            payload = await dispatch(services.read_admin_logs, request)
        except (LoginRequired, UserAccessDenied, StateUnavailable) as exc:
            return _read_error_response(exc)
        if isinstance(payload, JSONResponse):
            return payload
        return _logs_response(payload)

    return app
